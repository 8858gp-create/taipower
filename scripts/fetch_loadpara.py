"""
fetch_loadpara.py
爬取台電 loadpara.json / loadpara.txt，以資料內的 publish_time 命名存檔。
檔名格式：民國年(3碼)+月(2碼)+日(2碼)+時分(4碼)  e.g. 11504151420
"""

import json
import os
import re
import sys
import time
import random
import logging
from datetime import datetime
from pathlib import Path

import requests

# ── 設定 ────────────────────────────────────────────────────────────────────

BASE_URL = "https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/"
REFERER  = "https://www.taipower.com.tw/d006/loadGraph/loadGraph/load_graph.html"

TARGETS = [
    {"url": BASE_URL + "loadpara.json", "fmt": "json"},
    {"url": BASE_URL + "loadpara.txt",  "fmt": "txt"},
]

DATA_DIR   = Path(__file__).parent.parent / "data"
JSON_DIR   = DATA_DIR / "json"
TXT_DIR    = DATA_DIR / "txt"
LATEST_DIR = DATA_DIR / "latest"          # 永遠保留最新一筆，方便外部直接讀取

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── 偽裝成瀏覽器的 headers ───────────────────────────────────────────────────

def make_headers() -> dict:
    """模擬 Chrome 瀏覽器直接從台電負載圖頁面發出請求。"""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer":         REFERER,
        "Origin":          "https://www.taipower.com.tw",
        "Cache-Control":   "no-cache",
        "Pragma":          "no-cache",
        "Connection":      "keep-alive",
        "Sec-Fetch-Dest":  "empty",
        "Sec-Fetch-Mode":  "cors",
        "Sec-Fetch-Site":  "same-origin",
        "Sec-Ch-Ua":       '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile":"?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "DNT":             "1",
    }

# ── 解析 publish_time ────────────────────────────────────────────────────────

def parse_publish_time(text: str) -> str | None:
    """
    從原始內容嘗試抽出發布時間，支援多種可能格式：
      - "aaDataTime":"115/04/15 14:20"
      - "publish_time":"115/04/15 14:20"
      - "aaDataTime":"2026/04/15 14:20"   (西元年)
      - aaDataTime: '115/04/15 14:20'
    回傳格式：11504151420（民國年3碼）
    """
    patterns = [
        # JSON 雙引號
        r'"(?:aaDataTime|publish_time|ptime|data_time)"\s*:\s*"(\d{2,3}/\d{2}/\d{2}\s+\d{2}:\d{2})"',
        # JSON 單引號（某些 txt 格式）
        r"'(?:aaDataTime|publish_time|ptime|data_time)'\s*:\s*'(\d{2,3}/\d{2}/\d{2}\s+\d{2}:\d{2})'",
        # 單純日期字串 e.g. 115/04/15 14:20
        r'(\d{3}/\d{2}/\d{2}\s+\d{2}:\d{2})',
        # 西元年 e.g. 2026/04/15 14:20
        r'(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2})',
    ]

    for pat in patterns:
        m = re.search(pat, text)
        if m:
            raw = m.group(1).strip()
            return normalize_time(raw)

    log.warning("找不到 publish_time，使用當前時間代替")
    return None


def normalize_time(raw: str) -> str:
    """
    統一轉成民國年格式字串：11504151420
    接受：
      '115/04/15 14:20'  → 11504151420
      '2026/04/15 14:20' → 11504151420   (2026 - 1911 = 115)
    """
    raw = raw.strip()
    m = re.match(r'(\d+)/(\d+)/(\d+)\s+(\d+):(\d+)', raw)
    if not m:
        return None
    year, month, day, hour, minute = (int(x) for x in m.groups())
    if year > 1900:               # 西元年
        year = year - 1911
    return f"{year:03d}{month:02d}{day:02d}{hour:02d}{minute:02d}"


def fallback_filename() -> str:
    """當無法解析發布時間時，以現在系統時間（民國年）命名。"""
    now = datetime.now()
    roc_year = now.year - 1911
    return f"{roc_year:03d}{now.month:02d}{now.day:02d}{now.hour:02d}{now.minute:02d}"

# ── 下載 ─────────────────────────────────────────────────────────────────────

def fetch_with_retry(url: str, retries: int = 3, backoff: float = 5.0) -> requests.Response | None:
    session = requests.Session()
    # 先訪問 referer 頁取得 cookie（模擬真實瀏覽行為）
    try:
        session.get(REFERER, headers=make_headers(), timeout=10)
        time.sleep(random.uniform(1.0, 2.5))
    except Exception:
        pass  # 拿不到 cookie 也繼續

    for attempt in range(1, retries + 1):
        try:
            log.info(f"[{attempt}/{retries}] 嘗試下載 {url}")
            resp = session.get(url, headers=make_headers(), timeout=15)
            resp.raise_for_status()
            log.info(f"下載成功，狀態碼 {resp.status_code}，內容長度 {len(resp.content)} bytes")
            return resp
        except requests.HTTPError as e:
            log.warning(f"HTTP 錯誤: {e}")
            if e.response is not None and e.response.status_code == 403:
                log.error("403 Forbidden：台電擋住了，稍後再試")
        except requests.RequestException as e:
            log.warning(f"連線錯誤: {e}")

        if attempt < retries:
            wait = backoff * attempt + random.uniform(0, 3)
            log.info(f"等待 {wait:.1f} 秒後重試…")
            time.sleep(wait)

    return None

# ── 主流程 ────────────────────────────────────────────────────────────────────

def run() -> bool:
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    TXT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_DIR.mkdir(parents=True, exist_ok=True)

    any_success = False
    shared_filename = None   # json 和 txt 用同一個時間戳命名

    for target in TARGETS:
        url = target["url"]
        fmt = target["fmt"]
        dest_dir = JSON_DIR if fmt == "json" else TXT_DIR

        resp = fetch_with_retry(url)
        if resp is None:
            log.error(f"無法取得 {url}，跳過")
            continue

        raw_text = resp.text

        # 解析時間戳（優先用 json 檔，若先跑 txt 再跑 json 則覆蓋）
        ts = parse_publish_time(raw_text)
        if ts:
            shared_filename = ts
        else:
            if shared_filename is None:
                shared_filename = fallback_filename()
            ts = shared_filename

        out_path = dest_dir / f"{ts}.{fmt}"
        latest_path = LATEST_DIR / f"loadpara.{fmt}"

        # 若檔案已存在且內容相同，不重複寫入
        if out_path.exists() and out_path.read_text(encoding="utf-8") == raw_text:
            log.info(f"內容未變，跳過寫入：{out_path.name}")
        else:
            out_path.write_text(raw_text, encoding="utf-8")
            log.info(f"已儲存：{out_path}")

        # 更新 latest
        latest_path.write_text(raw_text, encoding="utf-8")

        # 如果是 json，另存一份格式化版本方便閱讀
        if fmt == "json":
            try:
                pretty_path = dest_dir / f"{ts}_pretty.json"
                if not pretty_path.exists():
                    parsed = json.loads(raw_text)
                    pretty_path.write_text(
                        json.dumps(parsed, ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )
                    log.info(f"已儲存格式化版：{pretty_path}")
            except json.JSONDecodeError:
                log.warning("JSON 解析失敗，略過格式化版本")

        any_success = True
        # 兩個請求之間稍微等一下，避免被判定為爬蟲
        time.sleep(random.uniform(1.5, 3.0))

    return any_success


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
