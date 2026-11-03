"""Проверка, что всё готово к работе. Печатает таблицу ✅ / ❌ и что сделать.
    python doctor.py           – быстрая проверка
    python doctor.py --full    – плюс настоящая расшифровка тестового ролика каждым способом"""
import config  # первым: переключает на окружение .venv
import subprocess
import sys

import requests


TEST_REEL = "DUqmYhgjCRT"  # публичный рилс с речью для проверки расшифровки
rows = []


def check(name, fn):
    try:
        ok, note = fn()
    except Exception as e:
        ok, note = False, str(e)[:160]
    rows.append((ok, name, note))


def py():
    return sys.version_info >= (3, 10), sys.version.split()[0]


def module(m):
    def f():
        __import__(m)
        return True, "есть"
    return f


def claude():
    from lib import llm
    try:
        cmd = llm.command(["--version"])
    except llm.LLMError as e:
        return False, str(e)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    return r.returncode == 0, (r.stdout or r.stderr).strip()[:60]


def chrome():
    from patchright.sync_api import sync_playwright
    from lib.common import launch, logged_in
    with sync_playwright() as pw:
        ctx = launch(pw, headless=True)
        try:
            return logged_in(ctx), "вход в Instagram есть" if logged_in(ctx) else "Chrome есть, входа нет – python login.py"
        finally:
            ctx.close()


def key(name, val):
    return lambda: (bool(val), "есть" if val else f"нет {name} в .env")


def telegram():
    if not (config.TG_TOKEN and config.TG_CHAT):
        return False, "нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID в .env"
    r = requests.get(f"https://api.telegram.org/bot{config.TG_TOKEN}/getChat",
                     params={"chat_id": config.TG_CHAT}, timeout=15).json()
    return r.get("ok", False), "бот видит чат" if r.get("ok") else str(r.get("description"))[:100]


def files():
    miss = [n for n in ("rules.md", "check.md", "passport.md", "style.md") if not (config.KNOWLEDGE / n).exists()]
    if not config.BLOGGERS.exists() or not config.BLOGGERS.read_text(encoding="utf-8").strip():
        miss.append("bloggers.txt")
    return not miss, "всё на месте" if not miss else "нет: " + ", ".join(miss)


def engine(name):
    def f():
        from lib import speech
        text, lang, _, used = speech.transcribe(TEST_REEL, only=name)
        return bool(text), f"{len(text)} знаков, язык {lang or '?'}"
    return f


if __name__ == "__main__":
    check("Python 3.10+", py)
    for m in ("patchright", "requests", "dotenv", "yt_dlp"):
        check(f"Пакет {m}", module(m))
    check("Локальный whisper (запасной)", module("faster_whisper"))
    check("Claude Code", claude)
    check("Chrome и вход в Instagram", chrome)
    check("Ключ Groq", key("GROQ_API_KEY", config.GROQ_KEY))
    check("Ключ ElevenLabs (запасной)", key("ELEVENLABS_API_KEY", config.ELEVEN_KEY))
    check("Ключ Gemini (запасной)", key("GEMINI_API_KEY", config.GEMINI_KEY))
    check("Telegram", telegram)
    check("Паспорт, стиль, правила, блогеры", files)
    if "--full" in sys.argv:
        for name, has in (("groq", config.GROQ_KEY), ("elevenlabs", config.ELEVEN_KEY), ("gemini", config.GEMINI_KEY),
                          ("local", True)):
            if has:
                check(f"Расшифровка: {name}", engine(name))
    w = max(len(r[1]) for r in rows)
    for ok, name, note in rows:
        print(f"{'✅' if ok else '❌'} {name:<{w}}  {note}")
    must = {"Python 3.10+", "Пакет patchright", "Пакет requests", "Пакет dotenv", "Пакет yt_dlp", "Claude Code",
            "Chrome и вход в Instagram", "Telegram"}
    bad = [n for ok, n, _ in rows if not ok and n in must]
    print("\nВсё готово к работе." if not bad else "\nЧинить обязательно: " + ", ".join(bad))
