import os
import json
import time
import asyncio
import re
import requests
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

# --- Gemini (new client) ---
from google import genai
from google.genai import types

# === ENV ===
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "")
LONG_LIVED_USER_TOKEN = os.getenv("LONG_LIVED_USER_TOKEN", "")

SESSION_FILE = "telegram_session"
RESULT_FILE = "results.json"
SOURCE_CHAT = "WatcherGuru"  # change if needed

# HTTP defaults
HTTP_TIMEOUT = 60
SLEEP_BETWEEN_POSTS_SEC = 1

# create Gemini client (reads GEMINI_API_KEY from env)
_gemini_client = genai.Client()

# cache page access token to avoid /me/accounts on every call
_PAGE_TOKEN_CACHE = None


# === Utils ===
def normalize(s: str) -> str:
    """Normalize text for de-duplication."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


# === Load all previously posted texts from results.json ===
def load_posted_texts_from_results() -> set:
    try:
        with open(RESULT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {normalize(entry["original_text"]) for entry in data if entry.get("original_text")}
    except Exception:
        return set()


# === Append new entries to results.json (no overwrite) ===
def log_result(new_entries: list):
    try:
        with open(RESULT_FILE, "r", encoding="utf-8") as f:
            existing_entries = json.load(f)
    except Exception:
        existing_entries = []

    combined = existing_entries + new_entries
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)


# === Translate (Gemini 2.5 via google-genai) ===
def translate_to_malay(text: str, retries: int = 2) -> str:
    cleaned = re.sub(r'@\w+', '', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'https?://\S+', '', cleaned)
    cleaned = re.sub(r'\[.*?\]\(.*?\)', '', cleaned)
    cleaned = re.sub(r'\n+', '\n', cleaned).strip()

    prompt = (
        "Translate the following post into Malay.\n"
        "Do not include usernames, mentions, links, or Telegram source references.\n"
        "If the original starts with 'JUST IN:' or '**JUST IN:**', translate it as 'TERKINI:'.\n"
        "Write it as a casual, friendly FB caption in one paragraph — no heading, no explanation.\n"
        "Avoid slang or shouting; keep it natural, chill, and neutral.\n\n"
        f"'{cleaned}'"
    )

    for attempt in range(1, retries + 1):
        try:
            resp = _gemini_client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                # disable "thinking" to match your docs example
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=0)
                ),
            )
            out = (getattr(resp, "text", "") or "").strip()
            if out:
                return out
            else:
                print(f"[Gemini] Empty response (attempt {attempt})")
        except Exception as e:
            print(f"[Gemini Error] {e} (attempt {attempt})")
        time.sleep(1)

    return "Translation failed"


# === Facebook Posting ===
def get_fb_token() -> str | None:
    global _PAGE_TOKEN_CACHE
    if _PAGE_TOKEN_CACHE:
        return _PAGE_TOKEN_CACHE
    try:
        r = requests.get(
            "https://graph.facebook.com/me/accounts",
            params={"access_token": LONG_LIVED_USER_TOKEN},
            timeout=HTTP_TIMEOUT,
        )
        if r.ok:
            data = r.json().get("data", [])
            if data:
                _PAGE_TOKEN_CACHE = data[0]["access_token"]
                return _PAGE_TOKEN_CACHE
            print("[FB] No pages found for the provided user token.")
        else:
            print(f"[FB Token Error] {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[FB Token Exception] {e}")
    return None


def post_text_only_to_fb(caption: str) -> bool:
    token = get_fb_token()
    if not token:
        return False
    try:
        r = requests.post(
            f"https://graph.facebook.com/{FB_PAGE_ID}/feed",
            data={"message": caption, "access_token": token},
            timeout=HTTP_TIMEOUT,
        )
        print("[FB] Text-only post success." if r.ok else f"[FB Text Error] {r.status_code}: {r.text}")
        return r.ok
    except Exception as e:
        print(f"[FB Text Exception] {e}")
        return False


def post_photos_to_fb(image_paths: list[str], caption: str) -> bool:
    token = get_fb_token()
    if not token:
        return False

    media_ids: list[str] = []
    for path in image_paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                r = requests.post(
                    f"https://graph.facebook.com/{FB_PAGE_ID}/photos",
                    data={"published": "false", "access_token": token},
                    files={"source": f},
                    timeout=HTTP_TIMEOUT,
                )
            if r.ok:
                media_ids.append(r.json()["id"])
            else:
                print(f"[FB Upload Image Error] {r.status_code}: {r.text}")
        except Exception as e:
            print(f"[FB Upload Image Exception] {e}")

    if not media_ids:
        return False

    payload = {"message": caption, "access_token": token}
    for i, mid in enumerate(media_ids):
        payload[f"attached_media[{i}]"] = json.dumps({"media_fbid": mid})

    try:
        r = requests.post(f"https://graph.facebook.com/{FB_PAGE_ID}/feed", data=payload, timeout=HTTP_TIMEOUT)
        print("[FB] Image post success." if r.ok else f"[FB Image Post Error] {r.status_code}: {r.text}")
        return r.ok
    except Exception as e:
        print(f"[FB Image Post Exception] {e}")
        return False


def post_video_to_fb(video_path: str, caption: str) -> bool:
    token = get_fb_token()
    if not token:
        return False
    if not os.path.exists(video_path):
        return False
    try:
        with open(video_path, "rb") as f:
            r = requests.post(
                f"https://graph.facebook.com/{FB_PAGE_ID}/videos",
                data={"description": caption, "access_token": token},
                files={"source": f},
                timeout=HTTP_TIMEOUT,
            )
        print("[FB] Video post success." if r.ok else f"[FB Video Error] {r.status_code}: {r.text}")
        return r.ok
    except Exception as e:
        print(f"[FB Video Exception] {e}")
        return False


# === MAIN ===
async def main():
    if not (API_ID and API_HASH and FB_PAGE_ID and LONG_LIVED_USER_TOKEN):
        raise RuntimeError("Missing required environment variables.")

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()

    posted_texts = load_posted_texts_from_results()
    media_group_ids_done = set()
    results = []

    async for msg in client.iter_messages(SOURCE_CHAT, limit=20):
        original_text = (msg.text or "").strip()
        if not original_text or len(original_text.split()) < 3:
            continue

        # de-dupe by normalized text
        if normalize(original_text) in posted_texts:
            print(f"[SKIP] Already posted content: {original_text[:60]}...")
            continue

        # skip duplicate handling for already-processed media groups
        mgid = getattr(msg, "media_group_id", None)
        if mgid and mgid in media_group_ids_done:
            continue

        translated = translate_to_malay(original_text)
        if translated == "Translation failed":
            continue

        image_paths: list[str] = []
        video_paths: list[str] = []
        success = False

        # --- Handle media groups (photos + videos) ---
        if mgid:
            group_msgs = []
            # collect nearby messages; limit-based scan is simpler/safer
            async for grouped in client.iter_messages(SOURCE_CHAT, limit=40, offset_id=msg.id):
                if getattr(grouped, "media_group_id", None) == mgid:
                    group_msgs.append(grouped)

            # Ensure chronological order
            group_msgs = list(reversed(group_msgs))
            media_group_ids_done.add(mgid)

            for media_msg in group_msgs:
                # Photos
                if isinstance(media_msg.media, MessageMediaPhoto):
                    path = f"temp_{media_msg.id}.jpg"
                    try:
                        await client.download_media(media_msg.media, file=path)
                        image_paths.append(path)
                    except Exception as e:
                        print(f"[DL Photo Error] {e}")

                # Videos in group
                elif isinstance(media_msg.media, MessageMediaDocument):
                    try:
                        mime = getattr(media_msg.file, "mime_type", "") or ""
                        if "video" in mime:
                            vpath = f"temp_{media_msg.id}.mp4"
                            await client.download_media(media_msg.media, file=vpath)
                            video_paths.append(vpath)
                    except Exception as e:
                        print(f"[DL Video Error] {e}")

        else:
            # Single message media handling
            if isinstance(msg.media, MessageMediaPhoto):
                path = f"temp_{msg.id}.jpg"
                try:
                    await client.download_media(msg.media, file=path)
                    image_paths.append(path)
                except Exception as e:
                    print(f"[DL Photo Error] {e}")

            elif isinstance(msg.media, MessageMediaDocument):
                try:
                    mime = getattr(msg.file, "mime_type", "") or ""
                    if "video" in mime:
                        vpath = f"temp_{msg.id}.mp4"
                        await client.download_media(msg.media, file=vpath)
                        video_paths.append(vpath)
                except Exception as e:
                    print(f"[DL Video Error] {e}")

        # --- Post priority: if any video exists, post first video with caption; else photos; else text ---
        try:
            if video_paths:
                success = post_video_to_fb(video_paths[0], translated)
            elif image_paths:
                success = post_photos_to_fb(image_paths, translated)
            else:
                success = post_text_only_to_fb(translated)
        except Exception as e:
            print(f"[FB Post Exception] {e}")
            success = False

        if success:
            results.append({
                "telegram_id": msg.id,
                "original_text": original_text,
                "translated_caption": translated,
                "fb_status": "Posted",
                "date_posted": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            # update in-memory de-dupe set
            posted_texts.add(normalize(original_text))

        # cleanup temp files
        for path in image_paths + video_paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

        time.sleep(SLEEP_BETWEEN_POSTS_SEC)

    if results:
        log_result(results)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
