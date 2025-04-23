import os
import json
import time
import asyncio
import requests
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto

# === ENV VARS ===
API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
LONG_LIVED_USER_TOKEN = os.getenv("LONG_LIVED_USER_TOKEN")

SESSION_FILE = "telegram_session"

# === HELPER: Load posted message IDs from results.json
def get_posted_ids():
    try:
        with open("results.json", "r", encoding="utf-8") as f:
            return [item["telegram_id"] for item in json.load(f)]
    except:
        return []

# === HELPER: Append new log to results.json
def log_result(entry):
    try:
        with open("results.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        data = []

    data.append(entry)
    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# === HELPER: Gemini Translate
def translate_to_malay(text):
    if not text.strip():
        return "Translation failed"

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

# === HELPER: Get fresh FB Page Token
def get_fb_token():
    try:
        res = requests.get(
            f"https://graph.facebook.com/v19.0/me/accounts?access_token={LONG_LIVED_USER_TOKEN}"
        )
        data = res.json()
        return data["data"][0]["access_token"]
    except:
        return None

# === HELPER: Post to Facebook
def post_to_fb(caption, image_url=None):
    token = get_fb_token()
    if not token:
        return False

    endpoint = f"https://graph.facebook.com/{FB_PAGE_ID}/feed"
    data = {"message": caption, "access_token": token}

    if image_url:
        endpoint = f"https://graph.facebook.com/{FB_PAGE_ID}/photos"
        data["url"] = image_url

    try:
        res = requests.post(endpoint, data=data)
        return res.status_code == 200
    except:
        return False

# === MAIN SCRIPT
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
        print(f"[Processing] ID: {msg.id}")
        translated = translate_to_malay(msg.text or "")
        if translated == "Translation failed":
            continue

        success = post_to_fb(translated)
        log_result({
            "telegram_id": msg.id,
            "original_text": msg.text or "",
            "translated_caption": translated,
            "fb_status": "Posted" if success else "Failed",
            "date_posted": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        time.sleep(1)

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
