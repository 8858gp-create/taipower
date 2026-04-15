"""
fetch_realtime.py  ─ 每 10 分鐘執行
抓取：
  loadpara.json  電力供需參數
  loadpara.txt   電力供需參數（txt版）
  genary.txt     各機組即時發電量

檔名：民國年3碼+月2碼+日2碼+時2碼+分2碼  e.g. 11504151420
環境變數：WORKER_URL、WORKER_TOKEN（選填）
"""

import os, re, sys, time, logging
from datetime import datetime
from pathlib import Path
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

WORKER_URL   = os.environ.get("WORKER_URL", "").rstrip("/")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")

DATA_DIR   = Path(__file__).parent.parent / "data"
DIRS = {
    "json":   DATA_DIR / "loadpara_json",
    "txt":    DATA_DIR / "loadpara_txt",
    "genary": DATA_DIR / "genary",
}

TARGETS = [
    {"key": "json",   "ext": "json"},
    {"key": "txt",    "ext": "txt"},
    {"key": "genary", "ext": "txt"},
]

# ── 時間解析 ──────────────────────────────────────────────────────────────────

def parse_publish_time(text: str) -> str | None:
    # loadpara 格式：115.04.15(三)16:40
    m = re.search(r'(\d{3})\.(\d{2})\.(\d{2})[^\d]*(\d{2}):(\d{2})', text)
    if m:
        year, month, day, hour, minute = m.groups()
        ts = f"{int(year):03d}{month}{day}{hour}{minute}"
        log.info(f"publish_time：{ts}")
        return ts

    # genary 格式：{"":"2026-04-15 17:30"
    m = re.search(r'""\s*:\s*"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})"', text)
    if m:
        year, month, day, hour, minute = m.groups()
        ts = f"{int(year) - 1911:03d}{month}{day}{hour}{minute}"
        log.info(f"publish_time：{ts}")
        return ts

    log.warning("找不到 publish_time，用系統時間代替")
    now = datetime.now()
    return f"{now.year - 1911:03d}{now.month:02d}{now.day:02d}{now.hour:02d}{now.minute:02d}"

# ── 下載 ──────────────────────────────────────────────────────────────────────

def fetch(file_key: str) -> str | None:
    if not WORKER_URL:
        log.error("未設定 WORKER_URL"); sys.exit(1)

    params = {"file": file_key}
    if WORKER_TOKEN:
        params["token"] = WORKER_TOKEN

    for attempt in range(1, 4):
        try:
            log.info(f"[{attempt}/3] 取得 {file_key}")
            resp = requests.get(WORKER_URL, params=params, timeout=20)
            resp.raise_for_status()
            log.info(f"成功 {len(resp.content)} bytes")
            return resp.text
        except requests.HTTPError as e:
            log.warning(f"HTTP {e.response.status_code}")
        except requests.RequestException as e:
            log.warning(f"連線錯誤: {e}")
        if attempt < 3:
            time.sleep(5 * attempt)
    return None

# ── 主流程 ────────────────────────────────────────────────────────────────────

def run() -> bool:
    for d in DIRS.values():
        d.mkdir(parents=True, exist_ok=True)

    any_ok = False

    for t in TARGETS:
        raw = fetch(t["key"])
        if raw is None:
            log.error(f"{t['key']} 失敗，跳過"); continue

        ts = parse_publish_time(raw)  # 每個檔用自己的時間戳

        out = DIRS[t["key"]] / f"{ts}.{t['ext']}"
        if out.exists() and out.read_text(encoding="utf-8") == raw:
            log.info(f"內容未變，跳過：{out.name}")
        else:
            out.write_text(raw, encoding="utf-8")
            log.info(f"已儲存：{out}")

        any_ok = True
        time.sleep(1)

    return any_ok

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
