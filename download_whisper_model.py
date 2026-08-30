#!/usr/bin/env python3
"""download_whisper_model.py — 从 hf-mirror.com 下载 faster-whisper 模型到本地目录。
完全绕过 huggingface_hub 的 XET 协议，普通 HTTP 下载。
用法：
  python3 download_whisper_model.py              # 默认 small
  python3 download_whisper_model.py medium       # 指定模型大小
  python3 download_whisper_model.py small /path/to/dir  # 指定保存目录
下载完成后：
  python3 -m knowpipe process --bilibili BVxxx --p 1 --whisper-model ~/models/faster-whisper-small --out report.md
"""
import os
import sys
import urllib.request

# faster-whisper 模型仓库（Systran 组织下的 CTranslate2 格式）
REPO_BASE = "https://hf-mirror.com/Systran/faster-whisper-{size}/resolve/main"
# 模型仓库包含的文件（不存在的会自动跳过）
FILES = [
    "config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.txt",
    "preprocessor_config.json",
]

def download_file(url, dest_path):
    """带进度条下载单个文件。"""
    print(f"  下载 {os.path.basename(dest_path)} ...", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        mb = downloaded / (1024 * 1024)
                        sys.stdout.write(f"\r    {pct:3d}%  {mb:.1f}MB / {total/(1024*1024):.1f}MB")
                        sys.stdout.flush()
            print()  # 换行
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"    跳过（404 不存在）")
            return False
        print(f"    失败: HTTP {e.code}")
        return False
    except Exception as e:
        print(f"    失败: {e}")
        return False

def main():
    size = sys.argv[1] if len(sys.argv) > 1 else "small"
    save_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser(f"~/models/faster-whisper-{size}")
    save_dir = os.path.abspath(save_dir)
    os.makedirs(save_dir, exist_ok=True)

    repo = REPO_BASE.format(size=size)
    print(f"模型: faster-whisper-{size}")
    print(f"仓库: {repo}")
    print(f"目录: {save_dir}")
    print()

    success = 0
    for fname in FILES:
        dest = os.path.join(save_dir, fname)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            print(f"  已存在，跳过: {fname} ({os.path.getsize(dest)//(1024*1024)}MB)")
            success += 1
            continue
        url = f"{repo}/{fname}"
        if download_file(url, dest):
            success += 1

    print()
    # 验证关键文件
    model_bin = os.path.join(save_dir, "model.bin")
    config_json = os.path.join(save_dir, "config.json")
    if os.path.exists(model_bin) and os.path.exists(config_json):
        print(f"下载完成！模型文件在: {save_dir}")
        print(f"model.bin 大小: {os.path.getsize(model_bin)//(1024*1024)}MB")
        print()
        print("使用方法（--whisper-model 指向本地目录，完全离线，不再访问网络）：")
        print(f"  python3 -m knowpipe process --bilibili BV1ST4y1m7No --p 1 --whisper-model {save_dir} --out report.md --brain auto")
        print()
        print("合集批量也一样：")
        print(f"  python3 -m knowpipe process --bilibili BVxxx --bili-pages 1-3 --whisper-model {save_dir} --out batch.md")
    else:
        print("下载不完整，缺少 model.bin 或 config.json，请检查网络后重试。")
        print("可以重复运行本脚本，已下载的文件会自动跳过。")
        sys.exit(1)

if __name__ == "__main__":
    main()
