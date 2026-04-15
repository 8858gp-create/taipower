"""
fetch_daily.py  ─ 每日執行（建議早上 01:00~06:00 台灣時間，確保前一天資料已更新）
抓取：
  loadfueltype_1.csv  前一天能源別發電量
  loadareas_1.csv     前一天區域別用電量

檔名：民國年3碼+月2碼+日2碼  e.g. 1150414（前一天台灣日期）
環境變數：WORKER_URL、WORKER_TOKEN（選填）
"""

import os, sys, time, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

WORKER_URL   = os.environ.get("WORKER_URL", "").rstrip("/")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")

DATA_DIR     = Path(__file__).parent.parent / "data"
DIRS = {
    "fueltype": DATA_DIR / "loadfueltype",
    "areas":    DATA_DIR / "loadareas",
}

TARGETS = [
    {"key": "fueltype", "ext": "csv"},
    {"key": "areas",    "ext": "csv"},
]

# ── 台灣時間前一天 ────────────────────────────────────────────────────────────

def yesterday_roc() -> str:
    """台灣時間（UTC+8）前一天，格式：1150414"""
    tw        = timezone(timedelta(hours=8))
    yesterday = datetime.now(tw) - timedelta(days=1)
    roc_year  = yesterday.year - 1911
    return f"{roc_year:03d}{yesterday.month:02d}{yesterday.day:02d}"

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

    date_str = yesterday_roc()
    log.info(f"前一天台灣日期：{date_str}")

    any_ok = False

    for t in TARGETS:
        raw = fetch(t["key"])
        if raw is None:
            log.error(f"{t['key']} 失敗，跳過"); continue

        out = DIRS[t["key"]] / f"{date_str}.{t['ext']}"
        if out.exists() and out.read_text(encoding="utf-8") == raw:
            log.info(f"已存在，跳過：{out.name}")
        else:
            out.write_text(raw, encoding="utf-8")
            log.info(f"已儲存：{out}")

        any_ok = True
        time.sleep(1)

    return any_ok

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
