import os
import json
import time
import asyncio
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
Write it as a casual, friendly FB caption in one paragraph — no heading, no explanation, just the final result.
Do not use slang or shouting. Keep it natural, chill, and neutral.
No need to say “Terjemahan:” or any extra labels.

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

def post_to_fb(caption, image_path=None):
    token = get_fb_token()
    if not token:
        print("[FB] Failed to get access token.")
        return False

    try:
        if image_path:
            with open(image_path, 'rb') as f:
                files = {'source': f}
                data = {
                    "message": caption,
                    "access_token": token
                }
                response = requests.post(
                    f"https://graph.facebook.com/{FB_PAGE_ID}/photos",
                    data=data,
                    files=files
                )
        else:
            data = {
                "message": caption,
                "access_token": token
            }
            response = requests.post(
                f"https://graph.facebook.com/{FB_PAGE_ID}/feed",
                data=data
            )
        return response.status_code == 200
    except Exception as e:
        print(f"[FB Error] {e}")
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
        translated = translate_to_malay(msg.text or "")
        if translated == "Translation failed":
            continue

        image_path = None
        if isinstance(msg.media, MessageMediaPhoto):
            try:
                image_path = f"temp_{msg.id}.jpg"
                await client.download_media(msg.media, file=image_path)
            except Exception as e:
                print(f"[Image Download Error] {e}")
                image_path = None

        success = post_to_fb(translated, image_path)
        log_result({
            "telegram_id": msg.id,
            "original_text": msg.text or "",
            "translated_caption": translated,
            "fb_status": "Posted" if success else "Failed",
            "date_posted": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        if image_path and os.path.exists(image_path):
            os.remove(image_path)

        time.sleep(1)

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
