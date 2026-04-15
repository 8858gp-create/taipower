"""
fetch_loadpara.py
透過 Cloudflare Worker 中繼爬取台電 loadpara 資料。
環境變數：
  WORKER_URL    必填，例如 https://taipower-relay.yourname.workers.dev
  WORKER_TOKEN  選填，與 Worker 內 SECRET_TOKEN 一致
"""

import json
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

# ── 設定 ──────────────────────────────────────────────────────────────────────

WORKER_URL   = os.environ.get("WORKER_URL", "").rstrip("/")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")

DATA_DIR   = Path(__file__).parent.parent / "data"
JSON_DIR   = DATA_DIR / "json"
TXT_DIR    = DATA_DIR / "txt"
LATEST_DIR = DATA_DIR / "latest"

TARGETS = [
    {"file": "json", "dest": JSON_DIR},
    {"file": "txt",  "dest": TXT_DIR},
]

# ── 時間解析 ──────────────────────────────────────────────────────────────────

def parse_publish_time(text: str) -> str | None:
    patterns = [
        r'"(?:aaDataTime|publish_time|ptime|data_time)"\s*:\s*"(\d{2,3}/\d{2}/\d{2}\s+\d{2}:\d{2})"',
        r"'(?:aaDataTime|publish_time|ptime|data_time)'\s*:\s*'(\d{2,3}/\d{2}/\d{2}\s+\d{2}:\d{2})'",
        r'(\d{3}/\d{2}/\d{2}\s+\d{2}:\d{2})',
        r'(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2})',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return normalize_time(m.group(1).strip())
    log.warning("找不到 publish_time，使用當前時間")
    return None

def normalize_time(raw: str) -> str:
    m = re.match(r'(\d+)/(\d+)/(\d+)\s+(\d+):(\d+)', raw)
    if not m:
        return None
    year, month, day, hour, minute = (int(x) for x in m.groups())
    if year > 1900:
        year -= 1911
    return f"{year:03d}{month:02d}{day:02d}{hour:02d}{minute:02d}"

def fallback_filename() -> str:
    now = datetime.now()
    return f"{now.year - 1911:03d}{now.month:02d}{now.day:02d}{now.hour:02d}{now.minute:02d}"

# ── 下載 ──────────────────────────────────────────────────────────────────────

def fetch(file: str) -> str | None:
    if not WORKER_URL:
        log.error("未設定 WORKER_URL 環境變數，請在 GitHub Secrets 中設定")
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
    for d in (JSON_DIR, TXT_DIR, LATEST_DIR):
        d.mkdir(parents=True, exist_ok=True)

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
        if ts:
            shared_ts = ts
        else:
            if shared_ts is None:
                shared_ts = fallback_filename()
            ts = shared_ts

        out_path    = dest_dir  / f"{ts}.{fmt}"
        latest_path = LATEST_DIR / f"loadpara.{fmt}"

        if out_path.exists() and out_path.read_text(encoding="utf-8") == raw:
            log.info(f"內容未變，跳過：{out_path.name}")
        else:
            out_path.write_text(raw, encoding="utf-8")
            log.info(f"已儲存：{out_path}")

        latest_path.write_text(raw, encoding="utf-8")

        if fmt == "json":
            try:
                pretty = dest_dir / f"{ts}_pretty.json"
                if not pretty.exists():
                    pretty.write_text(
                        json.dumps(json.loads(raw), ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )
            except json.JSONDecodeError:
                pass

        any_success = True
        time.sleep(1)

    return any_success

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
