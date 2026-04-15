"""
fetch_loadpara.py
透過 Cloudflare Worker 中繼爬取台電 loadpara 資料。
環境變數：
  WORKER_URL    必填，例如 https://taipower-relay.yourname.workers.dev
  WORKER_TOKEN  選填，與 Worker 內 SECRET_TOKEN 一致
"""

import os
import re
import sys
import time
import logging
from datetime import datetime
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

WORKER_URL   = os.environ.get("WORKER_URL", "").rstrip("/")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")

DATA_DIR = Path(__file__).parent.parent / "data"
JSON_DIR = DATA_DIR / "json"
TXT_DIR  = DATA_DIR / "txt"

TARGETS = [
    {"file": "json", "dest": JSON_DIR},
    {"file": "txt",  "dest": TXT_DIR},
]

# ── 時間解析 ──────────────────────────────────────────────────────────────────

def parse_publish_time(text: str) -> str | None:
    """
    支援台電實際格式：
      "publish_time" : "115.04.15(三)16:40"
      "115.04.15(三)16:40更新"
    回傳：11504151640
    """
    m = re.search(r'(\d{3})\.(\d{2})\.(\d{2})[^\d]*(\d{2}):(\d{2})', text)
    if m:
        year, month, day, hour, minute = m.groups()
        ts = f"{int(year):03d}{month}{day}{hour}{minute}"
        log.info(f"解析到 publish_time：{ts}")
        return ts

    log.warning("找不到 publish_time，使用系統當前時間代替")
    now = datetime.now()
    return f"{now.year - 1911:03d}{now.month:02d}{now.day:02d}{now.hour:02d}{now.minute:02d}"

# ── 下載 ──────────────────────────────────────────────────────────────────────

def fetch(file: str) -> str | None:
    if not WORKER_URL:
        log.error("未設定 WORKER_URL 環境變數")
        sys.exit(1)

    params = {"file": file}
    if WORKER_TOKEN:
        params["token"] = WORKER_TOKEN

    for attempt in range(1, 4):
        try:
            log.info(f"[{attempt}/3] 透過 Worker 取得 loadpara.{file}")
            resp = requests.get(WORKER_URL, params=params, timeout=20)
            resp.raise_for_status()
            log.info(f"成功，{len(resp.content)} bytes")
            return resp.text
        except requests.HTTPError as e:
            log.warning(f"HTTP {e.response.status_code}: {e}")
        except requests.RequestException as e:
            log.warning(f"連線錯誤: {e}")
        if attempt < 3:
            wait = 5 * attempt
            log.info(f"等待 {wait}s 後重試…")
            time.sleep(wait)
    return None

# ── 主流程 ────────────────────────────────────────────────────────────────────

def run() -> bool:
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    TXT_DIR.mkdir(parents=True, exist_ok=True)

    any_success = False
    shared_ts = None

    for target in TARGETS:
        fmt      = target["file"]
        dest_dir = target["dest"]

        raw = fetch(fmt)
        if raw is None:
            log.error(f"loadpara.{fmt} 取得失敗，跳過")
            continue

        ts = parse_publish_time(raw)
        if shared_ts is None:
            shared_ts = ts
        else:
            ts = shared_ts   # json 和 txt 用同一個時間戳

        out_path = dest_dir / f"{ts}.{fmt}"

        if out_path.exists() and out_path.read_text(encoding="utf-8") == raw:
            log.info(f"內容未變，跳過：{out_path.name}")
        else:
            out_path.write_text(raw, encoding="utf-8")
            log.info(f"已儲存：{out_path}")

        any_success = True
        time.sleep(1)

    return any_success


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
