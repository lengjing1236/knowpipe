#!/usr/bin/env python3
"""Export only observable user prompts from project-local root sessions.

Never export credentials, system/developer instructions, model analysis or tool output.
This is a bounded historical archive, not proof that all clients/history were retained.
"""
import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def clean(text):
    text = re.sub(r'<environment_context>.*?</environment_context>', '', text, flags=re.S)
    if text.lstrip().startswith(('<codex_internal_context', '# AGENTS.md instructions',
                                 '<permissions instructions>', 'Another language model',
                                 '<turn_aborted>', '<collaboration_mode>')):
        return None, 0
    count = 0
    patterns = [
        (r'(?i)\b(?:sk|ghp|github_pat|hf)[_-][A-Za-z0-9_-]{15,}', '[凭据已脱敏]'),
        (r'(?i)(Bearer\s+)[A-Za-z0-9._~-]{12,}', r'\1[凭据已脱敏]'),
        (r'(?i)((?:api[_ -]?key|access[_ -]?token|secret[_ -]?key|password)\s*[=:]\s*)[^\s,;`"\']{8,}', r'\1[凭据已脱敏]'),
        (r'https?://[^\s/@]+:[^\s/@]+@', 'https://[凭据已脱敏]@'),
    ]
    for pattern, replacement in patterns:
        text, n = re.subn(pattern, replacement, text)
        count += n
    text = re.sub(r'/home/[^/\s]+/knowpipe/?', '[项目目录]/', text)
    text = re.sub(r'/home/[^/\s]+/', '[用户目录]/', text)
    text = re.sub(r'[\w.-]+\\?@LAPTOP-[\w-]+', '[本机终端]', text)
    return text.strip(), count


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sessions', type=Path, default=Path.home()/'.codex/sessions')
    p.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[4])
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
    args = p.parse_args()
    records, agent_records, source_files, excluded = [], [], [], Counter()
    for file in sorted(args.sessions.rglob('*.jsonl')):
        data = file.read_bytes()
        rows = []
        for line in data.splitlines():
            try:
                rows.append(json.loads(line))
            except (ValueError, UnicodeDecodeError):
                excluded['invalid_or_in_progress_lines'] += 1
        if not rows:
            continue
        meta = rows[0].get('payload', {})
        if meta.get('cwd') != str(args.project) or not isinstance(meta.get('source'), str):
            continue
        for row in rows:
            q = row.get('payload', {})
            if row.get('type') != 'response_item' or q.get('type') != 'function_call' or q.get('name') not in {'spawn_agent', 'followup_task', 'send_message'}:
                continue
            try:
                arguments = json.loads(q.get('arguments', '{}'))
            except ValueError:
                continue
            if not isinstance(arguments.get('message'), str):
                continue
            text, redactions = clean(arguments['message'])
            if text:
                agent_records.append({'timestamp_utc': row.get('timestamp'), 'source_file': file.name,
                    'operation': q['name'], 'target': arguments.get('target') or arguments.get('task_name'),
                    'text': text, 'credential_redactions': redactions})
        events = [r for r in rows if r.get('type') == 'event_msg' and r.get('payload', {}).get('type') == 'user_message']
        mode = 'event_msg.user_message'
        if not events:
            mode = 'response_item.user (older log format)'
            events = [r for r in rows if r.get('type') == 'response_item' and r.get('payload', {}).get('role') == 'user']
        kept = 0
        for row in events:
            payload = row['payload']
            message = payload.get('message') or '\n'.join(c.get('text', '') for c in payload.get('content', []) if isinstance(c, dict))
            text, redactions = clean(message)
            if not text:
                excluded['environment_or_generated_context'] += 1
                continue
            records.append({'timestamp_utc': row.get('timestamp'), 'source_file': file.name,
                            'record_type': mode, 'text': text, 'credential_redactions': redactions})
            kept += 1
        source_files.append({'name': file.name, 'bytes_at_export': len(data),
                             'snapshot_sha256': hashlib.sha256(data).hexdigest(),
                             'record_type': mode, 'exported_prompts': kept})
    records.sort(key=lambda r: (r['timestamp_utc'] or '', r['source_file']))
    args.output.mkdir(parents=True, exist_ok=True)
    exported = datetime.now(timezone.utc).isoformat()
    manifest = {'exported_at_utc': exported, 'scope': 'Local Codex root sessions whose recorded cwd equals this project; child-agent forks excluded.',
                'not_guaranteed_complete_history': True, 'prompt_count': len(records),
                'root_agent_task_prompt_count': len(agent_records),
                'credential_redactions': sum(r['credential_redactions'] for r in records),
                'exclusions': dict(excluded), 'source_files': source_files}
    (args.output/'来源清单.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    lines = ['# 本项目可访问会话中的真实用户提示词', '',
             f'导出时间（UTC）：{exported}。共 {len(records)} 条，来自 {len(source_files)} 个项目目录根会话日志。', '',
             '范围：只导出本机可访问、工作目录为本项目的根会话用户原文；旧格式使用 user 记录，新格式使用 user_message 事件，避免重复抄录。子代理任务、AI推理、工具输出、系统/开发者指令、环境块不列为用户提示词。个人目录替换为占位符；可识别凭据脱敏。日期是日志UTC时间。', '',
             '**完整性限制：不声称覆盖所有历史、其他设备/客户端、已删除记录、未保存附件或所有自动代理内部提示。部分日志可能持续追加；来源清单记录导出时快照哈希。** 原消息所引用文件仍需结合Git中的对应版本阅读，不能把当前文档误作当时原文。', '',
             '历史提示中的意见并非当前需求；以最新用户决策、当前Spec及验收状态为准。保留重复发送的真实消息，不为形成理想过程而改写历史。', '']
    for i, r in enumerate(records, 1):
        lines.extend([f'## P{i:03d} · {r["timestamp_utc"]}', '', f'来源：`{r["source_file"]}`；类型：`{r["record_type"]}`；凭据脱敏 {r["credential_redactions"]} 处。', ''])
        fence = '`' * max(4, max((len(s) for s in re.findall(r'`+', r['text'])), default=0)+1)
        lines.extend([fence+'text', r['text'], fence, ''])
    (args.output/'真实用户提示词.md').write_text('\n'.join(lines))
    agent_records.sort(key=lambda r: (r['timestamp_utc'] or '', r['source_file']))
    lines = ['# AI主代理发出的可核实协作任务提示词', '',
             f'导出时间（UTC）：{exported}。共 {len(agent_records)} 条。', '',
             '这些是同一项目根会话中实际记录的spawn_agent/followup_task/send_message工具输入，属于AI生成的协作指令，不是人类用户原话；不包含模型隐藏推理、系统/开发者指令或工具输出。按事件逐条保留任务、修正与反馈；个人目录及可识别凭据脱敏。', '',
             '范围不包含其他设备/客户端及子代理内部全部嵌套协作；仅有归档所列来源，不声称所有自动指令齐全。', '']
    for i, r in enumerate(agent_records, 1):
        lines.extend([f'## A{i:03d} · {r["timestamp_utc"]}', '',
                      f'来源：`{r["source_file"]}`；操作：`{r["operation"]}`；目标：`{r["target"]}`。', ''])
        fence = '`' * max(4, max((len(s) for s in re.findall(r'`+', r['text'])), default=0)+1)
        lines.extend([fence+'text', r['text'], fence, ''])
    (args.output/'AI协作任务提示词.md').write_text('\n'.join(lines))
    print(json.dumps({'prompts':len(records),'agent_task_prompts':len(agent_records),'sessions':len(source_files),'redactions':manifest['credential_redactions'],'exported_at_utc':exported},ensure_ascii=False))


if __name__ == '__main__':
    main()
