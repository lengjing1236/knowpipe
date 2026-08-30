"""bilibili.py — B站输入通道：BV号 → 逐字稿文本。

获取逐字稿的路径（按优先级）：
  1. B站官方 AI 字幕（语音识别结果）。走 WBI 签名 + 可选 cookie，大多数公开视频免登录可用。
  2. 本地 whisper 兜底（可选安装 faster-whisper）：字幕不可用时在本地做语音识别。
  3. 都没有 → 明确报错并给出建议，不静默降级。

环境变量：
  BILIBILI_SESSDATA   登录 cookie 的 SESSDATA（仅当视频字幕要求登录时提供）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# WBI 签名用的固定 mixin key 置换表
_MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
          27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
          37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
          22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]

_API = "https://api.bilibili.com"
_wbi_key_cache = None
_bvid_info_cache = {}


class BiliError(Exception):
    pass


def _http_get(url, headers=None, timeout=25):
    h = {"User-Agent": UA, "Referer": "https://www.bilibili.com"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _api(url, params, headers=None):
    q = urllib.parse.urlencode(params)
    try:
        body = _http_get(f"{url}?{q}", headers=headers)
    except Exception as e:  # noqa: BLE001
        raise BiliError(f"接口请求失败: {e}")
    d = json.loads(body)
    if d.get("code") != 0:
        raise BiliError(f"B站接口返回错误 code={d.get('code')} {d.get('message')}")
    return d["data"]


def cookie_header():
    sess = os.getenv("BILIBILI_SESSDATA", "")
    return {"Cookie": f"SESSDATA={sess}"} if sess else {}


# --------------------------------------------------------------------------
# WBI 签名
# --------------------------------------------------------------------------

def _wbi_keys():
    global _wbi_key_cache
    if _wbi_key_cache:
        return _wbi_key_cache
    nav = _api(f"{_API}/x/web-interface/nav", {})
    img = nav["wbi_img"]["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub = nav["wbi_img"]["sub_url"].rsplit("/", 1)[-1].split(".")[0]
    _wbi_key_cache = "".join((img + sub)[i] for i in _MIXIN)[:32]
    return _wbi_key_cache


def _wbi_sign(params: dict) -> dict:
    key = _wbi_keys()
    params = dict(params)
    params["wts"] = int(time.time())
    q = urllib.parse.urlencode(sorted(params.items()))
    params["w_rid"] = hashlib.md5((q + key).encode()).hexdigest()
    return params


# --------------------------------------------------------------------------
# BV 解析
# --------------------------------------------------------------------------

def resolve_bvid(bvid: str) -> dict:
    """解析 BV → 标题 / 首P cid / 分P列表。"""
    bvid = bvid.strip()
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
        raise BiliError(f"无效 BV 号: {bvid}")
    # 同一条命令的合集处理会反复查询元数据；进程内缓存可避免每个分P一次网络往返。
    cached = _bvid_info_cache.get(bvid)
    if cached is not None:
        return cached
    d = _api(f"{_API}/x/web-interface/view", {"bvid": bvid})
    pages = [{"page": p["page"], "cid": p["cid"], "part": p.get("part", "")}
             for p in d.get("pages", [])]
    if not pages:
        pages = [{"page": 1, "cid": d["cid"], "part": ""}]
    info = {
        "bvid": bvid,
        "aid": d.get("aid"),
        "title": d.get("title", ""),
        "pages": pages,
        "duration": d.get("duration", 0),
    }
    _bvid_info_cache[bvid] = info
    return info


# --------------------------------------------------------------------------
# 官方字幕
# --------------------------------------------------------------------------

def fetch_subtitles(bvid: str, cid: int) -> list[dict]:
    """返回 [{lan, lan_doc, subtitle_url, ...}, ...]，可能为空。"""
    try:
        d = _api(f"{_API}/x/player/wbi/v2",
                 _wbi_sign({"bvid": bvid, "cid": cid}), headers=cookie_header())
    except BiliError:
        # 某些环境 wbi 失败时退回无签名接口
        d = _api(f"{_API}/x/player/v2", {"bvid": bvid, "cid": cid},
                 headers=cookie_header())
    return d.get("subtitle", {}).get("subtitles", []) or []


def _pick_subtitle(subs: list[dict]) -> dict:
    # 优先中文，其次第一个
    for s in subs:
        if s.get("lan", "").startswith("zh") or s.get("lan_doc", "").find("中文") >= 0:
            return s
    return subs[0]


def subtitle_to_text(subs_body: list[dict]) -> str:
    """把字幕 JSON body 合并成带换行的连续文本。"""
    lines = []
    for seg in subs_body:
        content = re.sub(r"\{\{.*?\}\}", "", str(seg.get("content", "")))
        content = re.sub(r"<[^>]+>", "", content)
        content = content.strip()
        if content:
            lines.append(content)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 本地 whisper 兜底（可选依赖 faster-whisper）
# --------------------------------------------------------------------------

def transcribe_local(audio_path: str) -> str:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise BiliError(
            "无官方字幕且本地未装 faster-whisper。"
            "可用 `pip install faster-whisper` 后在本地做语音识别；"
            "或用系统转写链路（飞书妙记）产出逐字稿文件后走 --text-file 输入。"
        ) from e
    model = WhisperModel(os.getenv("WHISPER_MODEL", "small"), device="cpu",
                         compute_type="int8")
    segments, _info = model.transcribe(audio_path, language="zh")
    return "\n".join(seg.text.strip() for seg in segments if seg.text.strip())



# --------------------------------------------------------------------------
# 本地 whisper 完整后端（yt-dlp 下载音频 + faster-whisper 转写，完全离线）
# --------------------------------------------------------------------------
def _check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

def transcribe_via_whisper(bvid: str, page: int = 1, workdir: str = ".",
                           model_size: str | None = None) -> dict:
    """用 yt-dlp 下载 B站音频 + faster-whisper 本地语音识别。完全离线，不依赖云服务。
    返回 {"bvid","title","page","part","text","method":"whisper"}
    依赖：pip install yt-dlp faster-whisper；系统需装 ffmpeg。
    模型大小由 WHISPER_MODEL 环境变量或 model_size 参数控制，默认 small。
    """
    # 检查依赖
    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        raise BiliError(
            "未安装 yt-dlp，无法下载B站音频。\n"
            "  pip install yt-dlp faster-whisper\n"
            "  sudo apt install ffmpeg   # WSL/Ubuntu"
        ) from e
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise BiliError(
            "未安装 faster-whisper，无法本地语音识别。\n"
            "  pip install faster-whisper"
        ) from e
    if not _check_ffmpeg():
        raise BiliError(
            "系统未安装 ffmpeg，yt-dlp 无法提取音频。\n"
            "  sudo apt install ffmpeg   # WSL/Ubuntu\n"
            "  brew install ffmpeg       # macOS"
        )

    info = resolve_bvid(bvid)
    out_dir = os.path.join(workdir, "whisper_out")
    os.makedirs(out_dir, exist_ok=True)
    url = f"https://www.bilibili.com/video/{bvid}?p={page}"
    # yt-dlp outtmpl 不带扩展名，postprocessor 会自动加 .mp3
    base_path = os.path.join(out_dir, f"{bvid}_p{page}")
    audio_path = base_path + ".mp3"

    # 已下载过音频则跳过
    if not os.path.exists(audio_path):
        print(f"[whisper] 下载音频：{bvid} P{page}（yt-dlp）")
        ydl_opts = {
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "outtmpl": base_path,
            "quiet": True,
            "no_warnings": True,
            "retries": 3,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:  # noqa: BLE001
            raise BiliError(f"yt-dlp 下载失败: {e}")
    else:
        print(f"[whisper] 音频已缓存，跳过下载：{audio_path}")

    if not os.path.exists(audio_path):
        raise BiliError(f"音频下载后未找到文件：{audio_path}")

    # faster-whisper 转写
    model_size = model_size or os.getenv("WHISPER_MODEL", "small")
    device = os.getenv("WHISPER_DEVICE", "cpu")
    compute_type = os.getenv("WHISPER_COMPUTE", "int8")
    print(f"[whisper] 本地语音识别（模型={model_size}, device={device}）…首次会下载模型")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(audio_path, language="zh", vad_filter=True)
    text = "\n".join(seg.text.strip() for seg in segments if seg.text.strip())
    if not text.strip():
        raise BiliError("whisper 转写结果为空（视频可能无语音或音频损坏）")

    part = info["pages"][page - 1]["part"] if page <= len(info["pages"]) else ""
    return {"bvid": bvid, "title": info["title"], "page": page,
            "part": part, "text": text, "method": "whisper"}

# --------------------------------------------------------------------------
# 飞书妙记转写后端（可选：无官方字幕时的语音识别兜底）
# --------------------------------------------------------------------------

_LARK_ROOTS = [
    "/home/user/.super_doubao/super-doubao-runtime/skills",
    "/home/user/.super_doubao/super-doubao-runtime/workspace/skills",
    "/home/user/.super_doubao/super-doubao-runtime/workspace/.skills",
]
_LARK_REL = "doubao-video-extract/scripts/minutes/social_video_to_minutes.py"


def find_lark_script() -> str | None:
    env = os.getenv("KNOWPIPE_LARK_SCRIPT")
    if env and os.path.exists(env):
        return env
    for root in _LARK_ROOTS:
        p = os.path.join(root, _LARK_REL)
        if os.path.exists(p):
            return p
    return None


def _extract_json_object(text: str):
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise BiliError("无法从转写脚本输出中解析 JSON")


def clean_lark_transcript(text: str) -> str:
    """去掉飞书妙记逐字稿的元数据行（时间戳头 / Keywords 块 / Speaker 前缀），只留发言文本。"""
    out, in_keywords = [], False
    for raw in text.splitlines():
        s = raw.strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", s):  # 头部时间戳
            continue
        if s == "Keywords:":
            in_keywords = True
            continue
        if in_keywords:
            if not re.match(r"^Speaker\b", s):  # 关键词列表行
                continue
            in_keywords = False
        s = re.sub(r"^Speaker\s+\d+\s+[\d:.]+\s*", "", s).strip()
        if s:
            out.append(s)
    return "\n".join(out)


def transcribe_via_lark(bvid: str, page: int = 1, workdir: str = ".",
                        notes_retries: int = 20, notes_wait: int = 25) -> dict:
    """无官方字幕时，用飞书妙记 ASR 转写（依赖 doubao-video-extract 技能与 lark-cli）。

    返回 {"bvid","title","page","part","text","method":"lark","minute_url":...}
    """
    script = find_lark_script()
    if not script:
        raise BiliError(
            "未找到飞书妙记转写脚本（doubao-video-extract），无法自动转写。"
            "可先手动把视频转成逐字稿文件，再走 --text-file 输入。"
        )
    info = resolve_bvid(bvid)
    out_dir = os.path.join(workdir, "lark_out")
    os.makedirs(out_dir, exist_ok=True)
    url = f"https://www.bilibili.com/video/{bvid}?p={page}"
    cmd = [sys.executable, script, url, "--run-lark",
           "--notes-output-dir", out_dir,
           "--notes-retries", str(notes_retries),
           "--notes-wait", str(notes_wait),
           "--json"]
    print(f"[lark] 开始语音识别：{bvid} P{page}（下载→妙记→ASR，可能需数分钟）")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=notes_retries * (notes_wait + 5) + 900)
    except subprocess.TimeoutExpired:
        raise BiliError("飞书妙记转写超时；可稍后重试，或直接看妙记链接")
    result = _extract_json_object(proc.stdout)
    # 脚本把转写结果嵌在 result["lark_result"] 里；兼容直接返回的情况
    lk = result.get("lark_result") if isinstance(result, dict) else None
    if not isinstance(lk, dict):
        lk = result
    tf = lk.get("transcript_file")
    status = lk.get("status")
    minute_url = lk.get("minute_url") or (result.get("minute_url") if isinstance(result, dict) else "")
    if not tf or not os.path.exists(tf):
        raise BiliError(
            f"转写尚未就绪（status={status}）。妙记链接: {minute_url or '?'}；"
            "可稍后重跑，或直接读取妙记逐字稿后走 --text-file。"
        )
    with open(tf, encoding="utf-8", errors="replace") as f:
        text = f.read()
    text = clean_lark_transcript(text)
    if not text.strip():
        raise BiliError(f"转写产物为空（{tf}）")
    return {"bvid": bvid, "title": info["title"], "page": page,
            "part": info["pages"][page - 1]["part"] if page <= len(info["pages"]) else "",
            "text": text, "method": "lark", "minute_url": minute_url}


# --------------------------------------------------------------------------
# 对外统一入口
# --------------------------------------------------------------------------

def fetch_transcript(bvid: str, page: int = 1, allow_local_asr: bool = False,
                     audio_path: str | None = None) -> dict:
    """BV号 → 逐字稿文本。

    返回 {"bvid","title","page","part","text","method"}：
      method: subtitle(官方字幕) | local_asr(本地whisper) | none
    默认只走官方字幕；无字幕时若 allow_local_asr 且给了 audio_path，则本地识别。
    """
    info = resolve_bvid(bvid)
    target = None
    for p in info["pages"]:
        if p["page"] == page:
            target = p
            break
    if not target:
        raise BiliError(f"BV 只有 {len(info['pages'])} 个分P，请求 page={page} 不存在")

    subs = fetch_subtitles(bvid, target["cid"])
    if subs:
        s = _pick_subtitle(subs)
        body = json.loads(_http_get(s["subtitle_url"]))
        text = subtitle_to_text(body.get("body", []))
        if text.strip():
            return {"bvid": bvid, "title": info["title"], "page": page,
                    "part": target["part"], "text": text, "method": "subtitle"}

    if allow_local_asr and audio_path:
        text = transcribe_local(audio_path)
        return {"bvid": bvid, "title": info["title"], "page": page,
                "part": target["part"], "text": text, "method": "local_asr"}

    raise BiliError(
        f"[{bvid}] 第{page}P 无官方字幕（{info['title'][:30]}）。"
        "可用系统转写链路（飞书妙记）产出逐字稿后走 --text-file 输入；"
        "或安装 faster-whisper 用 --local-asr --audio <本地音频>。"
    )


# --------------------------------------------------------------------------
# 带缓存的统一入口（合集批量 / 单P 都走这里）
# --------------------------------------------------------------------------

def _save_cache(cache_dir, bvid, page, text):
    if not cache_dir:
        return
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{bvid}_p{page}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def fetch_transcript_cached(bvid, page=1, cache_dir=None, transcriber="auto",
                             workdir=".", notes_retries=20, notes_wait=25,
                             whisper_model=None):
    """带磁盘缓存的逐字稿获取。
    降级链（auto 模式）：缓存 → 官方字幕 → 飞书妙记（若有脚本）→ 本地whisper（若装了依赖）。
    transcriber: auto=自动降级；subtitle=仅官方字幕；lark=强制妙记；whisper=强制本地whisper。
    返回 dict 含 bvid/title/page/part/text/method（method: cache|subtitle|lark|whisper）。
    合集批量时缓存至关重要：重跑不会重复下载+ASR。
    """
    # 1. 缓存命中
    if cache_dir:
        cache_path = os.path.join(cache_dir, f"{bvid}_p{page}.txt")
        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                text = f.read()
            if text.strip():
                info = resolve_bvid(bvid)
                part = info["pages"][page - 1]["part"] if page <= len(info["pages"]) else ""
                return {"bvid": bvid, "title": info["title"], "page": page,
                        "part": part, "text": text, "method": "cache"}
    # 2. 官方字幕
    if transcriber in ("auto", "subtitle"):
        try:
            src = fetch_transcript(bvid, page=page)
            _save_cache(cache_dir, bvid, page, src["text"])
            return src
        except BiliError:
            if transcriber == "subtitle":
                raise
            # 继续往下走 ASR 兜底
    # 3. ASR 兜底
    if transcriber == "auto":
        # 先试飞书妙记（开发环境有脚本），失败/不存在则降级本地 whisper
        if find_lark_script():
            try:
                print(f"[ingest] 无官方字幕，改用飞书妙记语音识别…（{bvid} P{page}）")
                src = transcribe_via_lark(bvid, page=page, workdir=workdir,
                                           notes_retries=notes_retries, notes_wait=notes_wait)
                _save_cache(cache_dir, bvid, page, src["text"])
                return src
            except BiliError as e:
                print(f"[ingest] 飞书妙记不可用（{e}），降级本地 whisper…")
        print(f"[ingest] 无官方字幕，使用本地 whisper 语音识别…（{bvid} P{page}）")
        src = transcribe_via_whisper(bvid, page=page, workdir=workdir,
                                     model_size=whisper_model)
        _save_cache(cache_dir, bvid, page, src["text"])
        return src
    if transcriber == "lark":
        print(f"[ingest] 强制飞书妙记语音识别…（{bvid} P{page}）")
        src = transcribe_via_lark(bvid, page=page, workdir=workdir,
                                   notes_retries=notes_retries, notes_wait=notes_wait)
        _save_cache(cache_dir, bvid, page, src["text"])
        return src
    if transcriber == "whisper":
        print(f"[ingest] 强制本地 whisper 语音识别…（{bvid} P{page}）")
        src = transcribe_via_whisper(bvid, page=page, workdir=workdir,
                                     model_size=whisper_model)
        _save_cache(cache_dir, bvid, page, src["text"])
        return src
    raise BiliError(f"未配置可用的逐字稿来源（transcriber={transcriber}）")
