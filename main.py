import os
import requests
import json
import datetime
from pcloud import PyCloud

email = os.environ.get("PCLOUD_EMAIL")
password = os.environ.get("PCLOUD_PASSWORD")

# --- 設定：任務一 (舊資料 - 今日系統供需狀況) ---
url_1 = "https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/loadpara.json"
folder_1 = "/爬蟲資料/台灣電力公司今日系統供需狀況_web"

# --- 設定：任務二 (新資料 - 各機組發電量) ---
url_2 = "https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/genary.json"
folder_2 = "/爬蟲資料/台灣電力公司各機組發電量即時資訊(含外購電力)_web"

def job():
    print(f"[{datetime.datetime.now()}] 開始執行整合任務...")

    # 1. 共用登入 pCloud
    pc = None
    try:
        pc = PyCloud(email, password, endpoint='eapi')
        pc.userinfo()
        print("✅ pCloud 登入成功")
    except Exception as e:
        print(f"❌ pCloud 登入失敗: {e}")
        return

    # ==========================================
    # 任務一：抓取「今日系統供需狀況」
    # ==========================================
    print("--- 正在執行任務一：今日系統供需狀況 ---")
    try:
        response = requests.get(url_1, timeout=30)
        if response.status_code == 200:
            data = response.json()
            publish_time = "unknown"
            
            if 'records' in data:
                for record in data['records']:
                    if 'publish_time' in record:
                        publish_time = record['publish_time']
                        break 

            print(f"  (1) 原始時間: {publish_time}")
            safe_time_str = ''.join(filter(str.isdigit, publish_time))
            
            if not safe_time_str:
                safe_time_str = datetime.datetime.now().strftime("%Y%m%d%H%M")
            
            filename_1 = f"{safe_time_str}.json"

            with open(filename_1, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

            try:
                pc.createfolderifnotexists(path=folder_1)
            except:
                pass
            pc.uploadfile(files=[filename_1], path=folder_1)
            print(f"  🎉 (1) 成功上傳: {filename_1}")
            
            if os.path.exists(filename_1):
                os.remove(filename_1)
        else:
            print(f"  ⚠️ (1) 下載失敗: {response.status_code}")
    except Exception as e:
        print(f"  ❌ (1) 發生錯誤: {e}")


    # ==========================================
    # 任務二：抓取「各機組發電量即時資訊」
    # ==========================================
    print("--- 正在執行任務二：各機組發電量即時資訊 ---")
    try:
        response = requests.get(url_2, timeout=30)
        if response.status_code == 200:

            try:
                raw_text = response.content.decode('utf-8-sig')
                data = json.loads(raw_text)
            except Exception as decode_error:
                print(f"  ⚠️ 解碼異常，嘗試備用方式: {decode_error}")
                data = response.json()

            # ★ 從資料內的時間欄位取得，空字串 key 格式為 "2026-02-23 09:10"
            raw_time = data.get("DateTime") or data.get("")
            print(f"  (2) 原始時間: {raw_time}")

            if not raw_time:
                print(f"  ❌ (2) 無法取得時間欄位，本次略過不儲存")
            else:
                clean_digits = ''.join(filter(str.isdigit, raw_time))
                file_time_str = clean_digits[:12]
                filename_2 = f"{file_time_str}.json"

                with open(filename_2, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)

                try:
                    pc.createfolderifnotexists(path=folder_2)
                except:
                    pass
                pc.uploadfile(files=[filename_2], path=folder_2)
                print(f"  🎉 (2) 成功上傳: {filename_2}")

                if os.path.exists(filename_2):
                    os.remove(filename_2)
        else:
            print(f"  ⚠️ (2) 下載失敗: {response.status_code}")
    except Exception as e:
        print(f"  ❌ (2) 發生錯誤: {e}")

    print(f"[{datetime.datetime.now()}] 所有任務執行完畢。")

if __name__ == "__main__":
    job()
