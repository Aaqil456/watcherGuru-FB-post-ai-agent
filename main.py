import os
import json
import time
import asyncio
import re
import requests
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
LONG_LIVED_USER_TOKEN = os.getenv("LONG_LIVED_USER_TOKEN")

SESSION_FILE = "telegram_session"

def get_posted_ids():
    try:
        with open("results.json", "r", encoding="utf-8") as f:
            return [item["telegram_id"] for item in json.load(f)]
    except:
        return []

def log_result(entry):
    try:
        with open("results.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        data = []
    data.append(entry)
    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def translate_to_malay(text):
    prompt = f"""
Translate the following post into Malay.
Do not include any usernames, mentions, or Telegram handles (e.g., @WatcherGuru).
Write it as a casual, friendly FB caption in one paragraph — no heading, no explanation.
Do not use slang or shouting. Keep it natural, chill, and neutral.

'{text}'
"""
    try:
        res = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]}
        )
        return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"[Gemini Error] {e}")
        return "Translation failed"

def get_fb_token():
    try:
        res = requests.get(f"https://graph.facebook.com/v19.0/me/accounts?access_token={LONG_LIVED_USER_TOKEN}")
        return res.json()["data"][0]["access_token"]
    except:
        return None

def post_text_only_to_fb(caption):
    token = get_fb_token()
    if not token:
        return False
    try:
        response = requests.post(
            f"https://graph.facebook.com/{FB_PAGE_ID}/feed",
            data={
                "message": caption,
                "access_token": token
            }
        )
        if response.status_code == 200:
            print("[FB] Text-only post success.")
        else:
            print(f"[FB Text Post Error] {response.status_code}: {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"[FB Text Post Exception] {e}")
        return False

def post_multiple_photos_to_fb(image_paths, caption):
    token = get_fb_token()
    if not token:
        print("[FB] Missing page token.")
        return False

    media_ids = []
    for image_path in image_paths:
        if not os.path.exists(image_path):
            print(f"[Skip Upload] File not found: {image_path}")
            continue
        try:
            with open(image_path, 'rb') as f:
                files = {'source': f}
                data = {
                    "published": "false",
                    "access_token": token
                }
                r = requests.post(
                    f"https://graph.facebook.com/{FB_PAGE_ID}/photos",
                    data=data,
                    files=files
                )
                if r.status_code == 200:
                    media_id = r.json()["id"]
                    media_ids.append({"media_fbid": media_id})
                    print(f"[FB Uploaded] {image_path} → media_id={media_id}")
                else:
                    print(f"[FB Upload Failed] {r.status_code}: {r.text}")
        except Exception as e:
            print(f"[FB Upload Exception] {e}")

    if not media_ids:
        return False

    try:
        post_data = {
            "message": caption,
            "access_token": token,
            "attached_media": json.dumps(media_ids)
        }
        r = requests.post(f"https://graph.facebook.com/{FB_PAGE_ID}/feed", data=post_data)
        if r.status_code == 200:
            print("[FB] Multi-image post success.")
        else:
            print(f"[FB Post Error] {r.status_code}: {r.text}")
        return r.status_code == 200
    except Exception as e:
        print(f"[FB Final Post Exception] {e}")
        return False

async def main():
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()

    posted_ids = get_posted_ids()
    messages = []
    async for message in client.iter_messages("WatcherGuru", limit=10):
        if message.id in posted_ids:
            continue
        if not message.text and not isinstance(message.media, MessageMediaPhoto):
            continue
        messages.append(message)

    for msg in reversed(messages):
        print(f"[Processing] Message ID: {msg.id}")
        cleaned_text = re.sub(r'@\w+', '', (msg.text or "")).strip()
        if not cleaned_text:
            print(f"[SKIP] Empty message after cleaning: {msg.id}")
            continue

        translated = translate_to_malay(cleaned_text)
        if translated == "Translation failed":
            continue

        image_paths = []
        if hasattr(msg, "media_group_id") and msg.media_group_id:
            media_group = []
            async for grouped in client.iter_messages("WatcherGuru", min_id=msg.id - 10, max_id=msg.id + 10):
                if (
                    hasattr(grouped, "media_group_id") and
                    grouped.media_group_id == msg.media_group_id and
                    isinstance(grouped.media, MessageMediaPhoto)
                ):
                    media_group.append(grouped)
            for media_msg in reversed(media_group):
                try:
                    path = f"temp_{media_msg.id}.jpg"
                    await client.download_media(media_msg.media, file=path)
                    image_paths.append(path)
                    print(f"[Downloaded] {path}")
                except Exception as e:
                    print(f"[Download Error] {e}")
        elif isinstance(msg.media, MessageMediaPhoto):
            try:
                path = f"temp_{msg.id}.jpg"
                await client.download_media(msg.media, file=path)
                image_paths.append(path)
                print(f"[Downloaded] {path}")
            except Exception as e:
                print(f"[Download Error] {e}")

        if image_paths:
            success = post_multiple_photos_to_fb(image_paths, translated)
        else:
            success = post_text_only_to_fb(translated)

        if success:
            log_result({
                "telegram_id": msg.id,
                "original_text": msg.text or "",
                "translated_caption": translated,
                "fb_status": "Posted",
                "date_posted": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })

        for path in image_paths:
            if os.path.exists(path):
                os.remove(path)

        time.sleep(1)

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
