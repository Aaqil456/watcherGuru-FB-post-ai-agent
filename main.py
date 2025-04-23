import os
import json
import asyncio
import time
from datetime import datetime
from utils.telegram_reader import extract_channel_username, fetch_latest_messages
from utils.ai_translator import translate_text_gemini
import requests

# === ENV VARS ===
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
LONG_LIVED_USER_TOKEN = os.getenv("LONG_LIVED_USER_TOKEN")


# === FACEBOOK POSTING ===
def get_fresh_page_token():
    try:
        response = requests.get(
            f"https://graph.facebook.com/v19.0/me/accounts?access_token={LONG_LIVED_USER_TOKEN}"
        )
        data = response.json()
        if "data" in data and data["data"]:
            return data["data"][0]["access_token"]
        else:
            print("[FB] No pages found or token issue.")
    except Exception as e:
        print(f"[FB Error] {e}")
    return None


def post_to_facebook(image_url, caption):
    page_token = get_fresh_page_token()
    if not page_token:
        print("[FB] Skipping — no token.")
        return False

    try:
        if image_url:
            data = {
                "url": image_url,
                "message": caption,
                "access_token": page_token
            }
            endpoint = f"https://graph.facebook.com/{FB_PAGE_ID}/photos"
        else:
            data = {
                "message": caption,
                "access_token": page_token
            }
            endpoint = f"https://graph.facebook.com/{FB_PAGE_ID}/feed"

        response = requests.post(endpoint, data=data)
        if response.status_code == 200:
            print(f"[FB] Post success.")
            return True
        else:
            print(f"[FB Error] {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[FB Exception] {e}")
    return False


# === RESULTS.JSON HANDLING ===
def get_posted_ids_from_results():
    try:
        with open("results.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return [item.get("telegram_id") for item in data if "telegram_id" in item]
    except FileNotFoundError:
        return []


def append_to_results_log(entry):
    filename = "results.json"
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                data = []
    except FileNotFoundError:
        data = []

    data.append(entry)

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


# === TRANSLATION PROMPT ===
def generate_prompt(text):
    return f"""
Translate the following post into Malay.
Write it as a casual, friendly FB caption in one paragraph — no heading, no explanation, just the final result.
Do not use slang or shouting. Keep it natural, chill, and neutral.
No need to say “Terjemahan:” or any extra labels.

'{text}'
"""


# === MAIN PROCESS ===
async def main():
    channel_url = "https://t.me/WatcherGuru"
    channel_username = extract_channel_username(channel_url)
    posted_ids = get_posted_ids_from_results()

    print("[INFO] Fetching messages...")
    messages = await fetch_latest_messages(API_ID, API_HASH, channel_username, limit=10)

    for msg in reversed(messages):
        msg_id = msg["id"]
        if msg_id in posted_ids:
            print(f"[SKIP] Already posted: {msg_id}")
            continue

        raw_text = msg["text"]
        if not raw_text.strip():
            print(f"[SKIP] Empty text in message {msg_id}")
            continue

        print(f"[PROCESSING] Message ID {msg_id}")
        translated = translate_text_gemini(generate_prompt(raw_text))

        if translated.lower() == "translation failed":
            print(f"[FAIL] Translation failed for msg {msg_id}")
            continue

        image_url = None
        if msg["has_photo"] and msg["photo"]:
            print("[INFO] Skipping image upload for now.")
            image_url = None

        success = post_to_facebook(image_url, translated)

        append_to_results_log({
            "telegram_id": msg_id,
            "original_text": raw_text,
            "translated_caption": translated,
            "fb_status": "Posted" if success else "Failed",
            "date_posted": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        time.sleep(1)

    print("[DONE] Script completed.")


# === ENTRY POINT ===
if __name__ == "__main__":
    asyncio.run(main())
