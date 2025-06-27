"""
Auto‑poster: скачивает видео с TikTok и публикует их
в Instagram Reels и (позже) YouTube Shorts.

❗️ Секреты (токены, OAuth‑JSON) НЕ хранятся в репозитории.
   Все ключи берутся из переменных окружения Railway.

ТРЕБУЕМЫЕ ENV‑переменные
-----------------------
TIKTOK_USERNAME   – имя пользователя TikTok без «@»
INSTAGRAM_TOKEN   – long‑lived Instagram Graph API token
GOOGLE_CREDENTIALS – полный JSON‑текст файла client_secret … .json
DRIVE_FOLDER_ID   – id папки Google Drive, куда складываем ролики
#  (получить можно из URL: https://drive.google.com/drive/folders/<ID>)

Сторонние библиотеки (см. requirements.txt):
  TikTokApi, requests, schedule, moviepy, google‑api‑python‑client,
  google‑auth‑oauthlib, google‑auth‑httplib2, facebook‑sdk
"""

import os
import time
import tempfile
from datetime import datetime
from typing import List

import requests
import schedule
from TikTokApi import TikTokApi
from moviepy.editor import VideoFileClip  # для конвертации/проверки
from facebook import GraphAPI

# --- Google imports ---
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ------------------------
# 1. Читаем переменные окружения
# ------------------------
TIKTOK_USERNAME   = os.environ["TIKTOK_USERNAME"]
INSTAGRAM_TOKEN   = os.environ["INSTAGRAM_TOKEN"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDENTIALS"]
DRIVE_FOLDER_ID   = os.environ.get("DRIVE_FOLDER_ID")  # опционально

# ------------------------
# 2. Подготавливаем временный credentials.json из env
# ------------------------
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
_tmp.write(GOOGLE_CREDS_JSON.encode())
_tmp.flush()
GOOGLE_CREDENTIALS_FILE = _tmp.name

# ------------------------
# 3. Функции работы с Google Drive
# ------------------------
SCOPES_DRIVE  = ["https://www.googleapis.com/auth/drive.file"]
SCOPES_YT     = ["https://www.googleapis.com/auth/youtube.upload"]

def _build_service(api_name: str, api_version: str, scopes: List[str]):
    creds = Credentials.from_authorized_user_file(GOOGLE_CREDENTIALS_FILE, scopes)
    return build(api_name, api_version, credentials=creds, cache_discovery=False)

def upload_to_drive(path: str) -> str:
    """Загружает файл в указанный DRIVE_FOLDER_ID, возвращает fileId."""
    drive_service = _build_service("drive", "v3", SCOPES_DRIVE)
    metadata = {"name": os.path.basename(path)}
    if DRIVE_FOLDER_ID:
        metadata["parents"] = [DRIVE_FOLDER_ID]
    media = MediaFileUpload(path, mimetype="video/mp4", resumable=True)
    file = drive_service.files().create(body=metadata, media_body=media, fields="id").execute()
    return file["id"]

# ------------------------
# 4. TikTok helpers
# ------------------------

def download_latest_tiktoks(username: str, max_count: int = 10) -> List[str]:
    """Скачивает последние ролики, возвращает список локальных путей."""
    paths = []
    with TikTokApi() as api:
        user = api.user(username=username)
        for video in user.videos(count=max_count):
            vid_bytes = video.bytes()
            fname = f"{video.id}.mp4"
            with open(fname, "wb") as f:
                f.write(vid_bytes)
            paths.append(fname)
    return paths

# ------------------------
# 5. Instagram upload
# ------------------------
GRAPH_URL = "https://graph.facebook.com/v19.0"
ig = GraphAPI(access_token=INSTAGRAM_TOKEN, version="19.0")

def upload_instagram_reel(path: str, caption: str = "") -> None:
    """Загружает видео и публикует Reel."""
    # Шага 1: загружаем видео как контейнер
    with open(path, "rb") as f:
        video_data = f.read()

    upload_resp = requests.post(
        f"{GRAPH_URL}/me/media",
        params={
            "media_type": "REELS",
            "caption": caption,
            "access_token": INSTAGRAM_TOKEN,
        },
        files={"video": (os.path.basename(path), video_data, "video/mp4")},
    ).json()

    creation_id = upload_resp.get("id")
    if not creation_id:
        print("[IG] Ошибка загрузки", upload_resp)
        return

    # Шаг 2: публикуем
    publish_resp = requests.post(
        f"{GRAPH_URL}/me/media_publish",
        params={"creation_id": creation_id, "access_token": INSTAGRAM_TOKEN},
    ).json()
    if "id" in publish_resp:
        print(f"[IG] Reel опубликован: {publish_resp['id']}")
    else:
        print("[IG] Ошибка публикации", publish_resp)

# ------------------------
# 6. Основная рабочая функция
# ------------------------

def job():
    print(f"\n=== Запуск задачи {datetime.now()} ===")
    videos = download_latest_tiktoks(TIKTOK_USERNAME)
    if not videos:
        print("Нет новых видео для загрузки.")
        return

    # Берём два первых видео
    for vid_path in videos[:2]:
        try:
            # мини‑проверка файла через moviepy (валидность)
            VideoFileClip(vid_path).close()

            # Загрузка в Drive (необязательно, но полезно)
            if DRIVE_FOLDER_ID:
                file_id = upload_to_drive(vid_path)
                print(f"Загружено в Drive → {file_id}")

            # Публикуем в Instagram
            caption = "Автор: @_from_tiktok"  # можно изменить
            upload_instagram_reel(vid_path, caption)

        except Exception as exc:
            print("Ошибка обработки", vid_path, exc)

# ------------------------
# 7. Планировщик
# ------------------------
print("Сервис автопостинга запущен…")
schedule.every().day.at("17:00").do(job)
schedule.every().day.at("19:00").do(job)

while True:
    schedule.run_pending()
    time.sleep(30)
