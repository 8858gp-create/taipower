"""
fetch_realtime_direct.py ─ 高頻執行（每 2 分鐘，直連台電官網，不經 Cloudflare Worker）

背景：2026-11-27 17:20 起，經 Cloudflare Worker 抓資料的流程全部 HTTP 403 失敗；
2026-11-28 21:18 直連在 GitHub Actions 上也一樣 403，log 的回應內容證實是台電站前的
Amazon CloudFront 在擋 Actions 機房 IP（不是 Cloudflare，也不是 Worker 的問題）。
但 CloudFront 是按 IP 段擋的：有的雲端出口通、有的不通，所以 Actions 每次執行都是一次
抽獎——跑得夠密、帳號夠多，總會有跑到沒被擋的 IP 的時候。這支程式就是為這種打法寫的：
每 2 分鐘跑一次、十個帳號一起跑，一個十分鐘的資料就有五十次機會抓到。

抓取（台電官網直連）：
  loadpara.json        電力供需參數
  loadpara.txt         電力供需參數（txt 版）
  genary.txt           各機組即時發電量（上游是 genary.json，內容原樣存成 .txt）
  genloadareaperc.csv  區域用電占比

檔名：民國年 3 碼 + 月 2 碼 + 日 2 碼 + 時 2 碼 + 分 2 碼  e.g. 11504151420
存檔位置與命名和 scripts/fetch_realtime.py 完全相同，可直接替代它。

去重：抓到先算 SHA-256，跟磁碟上同名檔案的 SHA 比對，一樣就跳過不存，
不一樣才寫檔——五十次機會裡重複抓到的同一份資料不會重複存、也不會重複 commit。

不需環境變數、不需 token。失敗時會把回應內容前 500 字記進 log，
方便判斷 403 是台電擋、還是其他原因（舊版只記狀態碼，看不出原因）。
"""

import hashlib, re, sys, time, logging

from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

log = logging.getLogger(__name__)

BASE = "https://www.taipower.com.tw/d006/loadGraph/loadGraph/data"

DATA_DIR = Path(__file__).parent.parent / "data"

DIRS = {
    "json":     DATA_DIR / "loadpara_json",
    "txt":      DATA_DIR / "loadpara_txt",
    "genary":   DATA_DIR / "genary",
    "areaperc": DATA_DIR / "genloadareaperc",
}

TARGETS = [
    {"key": "json",     "ext": "json", "url": f"{BASE}/loadpara.json"},
    {"key": "txt",      "ext": "txt",  "url": f"{BASE}/loadpara.txt"},
    {"key": "genary",   "ext": "txt",  "url": f"{BASE}/genary.json"},
    {"key": "areaperc", "ext": "csv",  "url": f"{BASE}/genloadareaperc.csv"},
]


# ── 時間解析（與 scripts/fetch_realtime.py 相同）─────────────────────────────────

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

    # areaperc 格式：第一欄 2026-04-16 10:50,...
    m = re.search(r'^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})', text)
    if m:
        year, month, day, hour, minute = m.groups()
        ts = f"{int(year) - 1911:03d}{month}{day}{hour}{minute}"
        log.info(f"publish_time：{ts}")
        return ts

    log.error("找不到 publish_time，略過此檔案")
    return None


# ── 下載（直連台電官網）─────────────────────────────────────────────────────────

def fetch(file_key: str, url: str) -> str | None:
    for attempt in range(1, 4):
        try:
            log.info(f"[{attempt}/3] 取得 {file_key}：{url}")
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            log.info(f"成功 {len(resp.content)} bytes")
            return resp.text
        except requests.HTTPError as e:
            body = ""
            try:
                body = e.response.text[:500]
            except Exception:
                pass
            log.warning(f"HTTP {e.response.status_code}：{body!r}")
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
        raw = fetch(t["key"], t["url"])
        if raw is None:
            log.error(f"{t['key']} 失敗，跳過"); continue

        ts = parse_publish_time(raw)  # 每個檔用自己的時間戳
        if ts is None:
            log.error(f"{t['key']} 找不到 publish_time，略過，不存檔"); continue

        out = DIRS[t["key"]] / f"{ts}.{t['ext']}"
        new_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if out.exists():
            old_sha = hashlib.sha256(out.read_bytes()).hexdigest()
            if old_sha == new_sha:
                log.info(f"SHA 相同（{new_sha[:12]}…），跳過：{out.name}")
                any_ok = True
                time.sleep(1)
                continue
        out.write_text(raw, encoding="utf-8")
        log.info(f"已儲存：{out}（SHA {new_sha[:12]}…）")

        any_ok = True
        time.sleep(1)

    return any_ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
