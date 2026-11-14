# AI Creator News - публикация новостей в Telegram-канал через GitHub Actions.
# Скрипт запускается по расписанию, отправляет новые статьи и завершается.
# Токен и канал берутся из секретов репозитория (BOT_TOKEN, CHANNEL_ID).

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()

MAX_PER_RUN = 8        # максимум постов за один запуск
FIRST_RUN_POST = 3     # сколько новостей опубликовать при самом первом запуске
SUMMARY_LEN = 280
SEEN_FILE = "seen.json"
SEEN_LIMIT = 3000      # сколько ссылок помнить

FEEDS = [
    ("The Verge · AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("TechCrunch · AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("VentureBeat · AI", "https://venturebeat.com/category/ai/feed/"),
    ("MIT Tech Review · AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("r/StableDiffusion", "https://www.reddit.com/r/StableDiffusion/top/.rss?t=day"),
    ("r/aivideo", "https://www.reddit.com/r/aivideo/top/.rss?t=day"),
    ("r/midjourney", "https://www.reddit.com/r/midjourney/top/.rss?t=day"),
    ("Хабр · ИИ", "https://habr.com/ru/rss/hubs/artificial_intelligence/articles/?fl=ru"),
]

UNFILTERED_SOURCES = {"r/StableDiffusion", "r/aivideo", "r/midjourney"}

KEYWORDS = [
    "generative", "genai", "text-to-image", "text-to-video", "image-to-video",
    "text-to-music", "ai art", "ai video", "ai music", "ai image", "creator",
    "creative", "diffusion", "lora", "upscal",
    "midjourney", "stable diffusion", "flux", "dall-e", "dall·e", "imagen",
    "ideogram", "firefly", "leonardo", "krea", "recraft", "comfyui", "gpt-image",
    "nano banana",
    "sora", "runway", "kling", "veo", "pika", "luma", "dream machine",
    "hailuo", "minimax", "seedance", "wan 2", "hunyuan", "higgsfield",
    "suno", "udio", "elevenlabs", "lyria", "musicgen", "voice clon",
    "3d model", "gaussian splat", "avatar", "heygen", "synthesia", "capcut",
    "генерац", "нейросет", "нейроарт", "креатор", "дипфейк", "озвучк",
]
KW = [k.lower() for k in KEYWORDS]
UA = {"User-Agent": "Mozilla/5.0 (compatible; AI-Creator-News/1.0)"}

TAG_RE = re.compile(r"<[^>]+>")
SP_RE = re.compile(r"\s+")


def clean(t):
    return SP_RE.sub(" ", html.unescape(TAG_RE.sub(" ", t or ""))).strip()


def local(tag):
    return tag.rsplit("}", 1)[-1]


def child_text(el, *names):
    for c in el:
        if local(c.tag) in names and (c.text or "").strip():
            return c.text
    return ""


def parse_feed(raw, source):
    items = []
    for el in ET.fromstring(raw).iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        link = ""
        for c in el:
            if local(c.tag) == "link":
                link = (c.text or "").strip() or c.get("href", "")
                if c.get("rel") in (None, "alternate") and link:
                    break
        if link:
            items.append({
                "source": source,
                "title": clean(child_text(el, "title")) or "Без заголовка",
                "link": link,
                "summary": clean(child_text(el, "description", "summary", "content")),
            })
    return items[:30]


def fetch(source, url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            items = parse_feed(r.read(), source)
            print(f"OK   {source}: {len(items)}")
            return items
    except Exception as e:
        print(f"FAIL {source}: {e}")
        return []


def matches(it):
    if it["source"] in UNFILTERED_SOURCES:
        return True
    hay = (it["title"] + " " + it["summary"]).lower()
    return any(k in hay for k in KW)


def fmt(it):
    s = it["summary"]
    if len(s) > SUMMARY_LEN:
        s = s[:SUMMARY_LEN].rsplit(" ", 1)[0] + "…"
    parts = [f"<b>{html.escape(it['title'])}</b>", f"<i>{html.escape(it['source'])}</i>"]
    if s:
        parts.append(html.escape(s))
    parts.append(f'<a href="{html.escape(it["link"], quote=True)}">Читать →</a>')
    return "\n\n".join(parts)


def send(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHANNEL_ID, "text": text, "parse_mode": "HTML",
    }).encode()
    for _ in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30):
                return True
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode() or "{}")
            if e.code == 429:
                time.sleep(body.get("parameters", {}).get("retry_after", 5) + 1)
                continue
            print("Ошибка Telegram:", body.get("description", e))
            if e.code in (400, 401, 403):
                sys.exit(1)  # неверный токен/канал - пусть запуск покажется красным
            return False
        except Exception as e:
            print("Сеть:", e)
            time.sleep(3)
    return False


def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        sys.exit("Не заданы секреты BOT_TOKEN и/или CHANNEL_ID")

    first_run = not os.path.exists(SEEN_FILE)
    seen = [] if first_run else json.load(open(SEEN_FILE, encoding="utf-8"))
    seen_set = set(seen)

    fresh, titles = [], set()
    for source, url in FEEDS:
        for it in fetch(source, url):
            key = it["title"].lower()[:80]
            if it["link"] in seen_set or key in titles or not matches(it):
                continue
            titles.add(key)
            fresh.append(it)

    if first_run:
        to_post = fresh[:FIRST_RUN_POST]
        for it in fresh[FIRST_RUN_POST:]:  # остальное запоминаем без публикации
            seen.append(it["link"])
    else:
        to_post = fresh[:MAX_PER_RUN]

    posted = 0
    for it in to_post:
        if send(fmt(it)):
            posted += 1
        seen.append(it["link"])
        time.sleep(1.5)

    json.dump(seen[-SEEN_LIMIT:], open(SEEN_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    print(f"Найдено новых: {len(fresh)}, опубликовано: {posted}")


if __name__ == "__main__":
    main()
