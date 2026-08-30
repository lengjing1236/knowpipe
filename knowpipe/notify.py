"""Podcast watcher 的可选通知后端（Webhook / SMTP）。

通知默认关闭；只有显式传入 URL 或 SMTP 主机才会产生外部网络副作用。RSS、
transcript 和模型生成内容都被当作不可信数据，仅作为通知正文，不执行其中的
任何指令。
"""
from __future__ import annotations

import email.message
import json
import os
import smtplib
import urllib.request


class NotifyError(Exception):
    pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def build_payload(*, feed_title: str, title: str, digest: str,
                  report_path: str, source: str = "") -> dict:
    """生成 webhook 和邮件共用的稳定 payload。"""
    summary = " ".join(str(digest or "").split())
    if len(summary) > 1200:
        summary = summary[:1197] + "..."
    message = (
        f"{title or '未命名节目'}\n"
        f"摘要：{summary}\n"
        f"报告：{report_path}"
    )
    return {
        "event": "podcast.updated",
        "feed_title": feed_title or "Podcast",
        "title": title or "未命名节目",
        "summary": summary,
        "report_path": report_path,
        "source": source,
        # ntfy/PushPlus 等服务通常直接读取 message/text；保留 summary/report_path
        # 结构化字段，便于自建 webhook 继续使用。
        "message": message,
        "text": message,
    }


def send_webhook(url: str, payload: dict, timeout: int = 20):
    """POST JSON 到 ntfy、Telegram 中转、PushPlus 等 webhook。"""
    if not url:
        return
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "knowpipe/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            # Consume the body so HTTP clients can reuse the connection.
            response.read()
    except Exception as exc:  # noqa: BLE001
        raise NotifyError(f"Webhook 通知失败: {exc}") from exc


def send_smtp(*, host: str, port: int, username: str, password: str,
              sender: str, recipients: list[str], subject: str, body: str,
              use_tls: bool = True, timeout: int = 30):
    """发送一封纯文本邮件。调用方负责决定是否配置凭据。"""
    if not host or not recipients:
        return
    if not sender:
        sender = username
    if not sender:
        raise NotifyError("SMTP 通知需要 --smtp-from 或 --smtp-user")
    message = email.message.EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    try:
        with smtplib.SMTP(host, int(port), timeout=timeout) as client:
            client.ehlo()
            if use_tls:
                client.starttls()
                client.ehlo()
            if username:
                client.login(username, password)
            client.send_message(message)
    except Exception as exc:  # noqa: BLE001
        raise NotifyError(f"SMTP 通知失败: {exc}") from exc


def send_notifications(payload: dict, *, webhook_url: str | None = None,
                       smtp: dict | None = None) -> list[str]:
    """发送已配置的通知并返回错误列表。

    Webhook 和邮件互相独立：一个后端失败不会阻止另一个后端尝试，也不会让
    watcher 把已经成功归档的 episode 标为 failed。
    """
    errors = []
    webhook_url = (webhook_url or _env("KNOWPIPE_WEBHOOK_URL") or
                   _env("PODCAST_WEBHOOK_URL"))
    if webhook_url:
        try:
            send_webhook(webhook_url, payload)
        except NotifyError as exc:
            errors.append(str(exc))

    config = dict(smtp or {})
    host = str(config.get("host") or _env("KNOWPIPE_SMTP_HOST"))
    recipients_raw = config.get("to") or _env("KNOWPIPE_SMTP_TO")
    recipients = [x.strip() for x in str(recipients_raw or "").split(",") if x.strip()]
    if host and recipients:
        try:
            port = int(config.get("port") or _env("KNOWPIPE_SMTP_PORT", "587"))
            username = str(config.get("username") or _env("KNOWPIPE_SMTP_USER"))
            password = str(config.get("password") or _env("KNOWPIPE_SMTP_PASSWORD"))
            sender = str(config.get("from") or _env("KNOWPIPE_SMTP_FROM") or username)
            use_tls = config.get("tls")
            if use_tls is None:
                use_tls = _env("KNOWPIPE_SMTP_TLS", "1").lower() not in {"0", "false", "no"}
            subject = (
                f"{payload.get('feed_title', 'Podcast')} 更新：{payload.get('title', '')}"
            ).replace("\r", " ").replace("\n", " ")
            body = (
                f"Podcast 更新\n\n{payload.get('title', '')}\n\n"
                f"摘要：{payload.get('summary', '')}\n\n"
                f"报告：{payload.get('report_path', '')}\n"
            )
            send_smtp(host=host, port=port, username=username, password=password,
                      sender=sender, recipients=recipients, subject=subject,
                      body=body, use_tls=bool(use_tls))
        except (NotifyError, ValueError) as exc:
            errors.append(str(exc))
    return errors
