# AI-агент «Дайджест дня»: раз в сутки собирает новости AI-креаторства,
# выбирает самые интересные и пишет авторский дайджест на русском
# с комментариями, после чего публикует его в Telegram-канал.
#
# Секреты репозитория: BOT_TOKEN, CHANNEL_ID, OPENROUTER_API_KEY.
# Использует ленты и фильтр из news.py.

import email.utils
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
from datetime import datetime, timedelta, timezone

import news  # ленты, ключевые слова и вспомогательные функции

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
# Бесплатный роутер OpenRouter сам выбирает доступную бесплатную модель.
MODEL = os.environ.get("OPENROUTER_MODEL", "").strip() or "openrouter/free"

HOURS = 26          # берём новости за последние ~сутки
MAX_ITEMS = 40      # сколько новостей максимум показываем модели

SYSTEM_PROMPT = """Ты — редактор Telegram-канала про AI-креаторство: нейросети для картинок,
видео, музыки, голоса и 3D. Пиши на русском, живо и по делу, без воды и канцелярита.

Тебе дают список свежих новостей (номер, источник, заголовок, описание, ссылка).
Задача: выбери 3–5 самых полезных и интересных для креаторов и напиши дайджест.

Формат (это Telegram HTML, разрешены ТОЛЬКО теги <b>, <i> и <a href="...">):
<b>🎨 AI-креатив: главное за день</b>

1. <b>Короткий заголовок</b> — 1–2 предложения: что случилось.
💡 <i>Зачем это креатору / что попробовать</i> — 1 предложение.
<a href="ССЫЛКА">Подробнее</a>

(и так для каждой новости, между новостями пустая строка)

В конце одна строка-вывод: общий тренд дня.

Правила: не выдумывай факты, которых нет в новостях; используй только данные ссылки;
не используй Markdown (**, ##), только указанные HTML-теги; весь текст до 3500 символов."""


# ---------- Сбор новостей с датами ----------

def parse_date(text):
    if not text:
        return None
    text = text.strip()
    try:
        d = email.utils.parsedate_to_datetime(text)  # RSS: Sun, 15 Nov 2026 10:00:00 GMT
    except Exception:
        try:
            d = datetime.fromisoformat(text.replace("Z", "+00:00"))  # Atom: 2026-11-15T10:00:00Z
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fetch_with_dates(source, url):
    try:
        req = urllib.request.Request(url, headers=news.UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            root = ET.fromstring(r.read())
    except Exception as e:
        print(f"FAIL {source}: {e}")
        return []
    items = []
    for el in root.iter():
        if news.local(el.tag) not in ("item", "entry"):
            continue
        link = ""
        for c in el:
            if news.local(c.tag) == "link":
                link = (c.text or "").strip() or c.get("href", "")
                if c.get("rel") in (None, "alternate") and link:
                    break
        if not link:
            continue
        items.append({
            "source": source,
            "title": news.clean(news.child_text(el, "title")) or "Без заголовка",
            "link": link,
            "summary": news.clean(news.child_text(el, "description", "summary", "content"))[:400],
            "date": parse_date(news.child_text(el, "pubDate", "published", "updated", "date")),
        })
    print(f"OK   {source}: {len(items)}")
    return items[:30]


def collect():
    since = datetime.now(timezone.utc) - timedelta(hours=HOURS)
    result, titles = [], set()
    for source, url in news.FEEDS:
        for it in fetch_with_dates(source, url):
            if it["date"] and it["date"] < since:
                continue
            key = it["title"].lower()[:80]
            if key in titles or not news.matches(it):
                continue
            titles.add(key)
            result.append(it)
    result.sort(key=lambda i: i["date"] or since, reverse=True)
    return result[:MAX_ITEMS]


# ---------- Модель ----------

def ask_model(items):
    listing = "\n\n".join(
        f"[{n}] {it['source']}\nЗаголовок: {it['title']}\nОписание: {it['summary']}\nСсылка: {it['link']}"
        for n, it in enumerate(items, 1)
    )
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Свежие новости:\n\n" + listing},
        ],
        "temperature": 0.6,
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "X-Title": "AI Creator News Digest",
        },
    )
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode())
            text = (data["choices"][0]["message"].get("content") or "").strip()
            if text:
                print("Модель:", data.get("model"))
                return text
            print("Пустой ответ модели, пробую ещё раз")
        except urllib.error.HTTPError as e:
            print(f"Ошибка OpenRouter ({e.code}):", e.read().decode()[:300])
            if e.code in (401, 402, 403):
                sys.exit(1)
        except Exception as e:
            print("Ошибка запроса к модели:", e)
        time.sleep(10 * attempt)
    sys.exit("Модель не ответила после 3 попыток")


ALLOWED_TAG = re.compile(r"</?(b|i|a)(\s+href=\"[^\"]*\")?\s*>", re.I)


def tidy(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)  # рассуждения моделей
    text = re.sub(r"^```\w*\n?|```$", "", text.strip())
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)  # если модель всё же написала Markdown
    # экранируем всё, кроме разрешённых тегов
    out, pos = [], 0
    for m in ALLOWED_TAG.finditer(text):
        out.append(html.escape(text[pos:m.start()], quote=False))
        tag = m.group(0)
        if m.group(2):  # экранируем & в ссылке, чтобы Telegram принял разметку
            href = html.unescape(m.group(2).split('"')[1])
            tag = f'<a href="{html.escape(href, quote=True)}">'
        out.append(tag)
        pos = m.end()
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out).strip()[:4000]


# ---------- Telegram ----------

def send(text, parse_html=True):
    params = {"chat_id": CHANNEL_ID, "text": text, "disable_web_page_preview": "true"}
    if parse_html:
        params["parse_mode"] = "HTML"
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=urllib.parse.urlencode(params).encode(),
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            return True
    except urllib.error.HTTPError as e:
        print("Ошибка Telegram:", e.read().decode()[:300])
        return False


def main():
    if not (BOT_TOKEN and CHANNEL_ID and OPENROUTER_API_KEY):
        sys.exit("Не заданы секреты BOT_TOKEN, CHANNEL_ID или OPENROUTER_API_KEY")

    items = collect()
    print(f"Новостей за сутки: {len(items)}")
    if len(items) < 2:
        print("Слишком мало новостей для дайджеста — пропускаю.")
        return

    digest = tidy(ask_model(items))
    if not send(digest):
        # если Telegram не принял разметку — отправляем простым текстом
        plain = re.sub(r"<[^>]+>", "", digest)
        if not send(html.unescape(plain), parse_html=False):
            sys.exit(1)
    print("Дайджест опубликован")


if __name__ == "__main__":
    main()
