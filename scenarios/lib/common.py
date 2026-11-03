"""Общее: лог в файл и на экран, Chrome через patchright, русский текст в консоли Windows."""
import platform
import sys
from datetime import datetime

import config

for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    with open(config.LOGS / f"{datetime.now():%Y-%m-%d}.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%H:%M:%S} {line}\n")


def now():
    return datetime.now().isoformat(timespec="seconds")


def launch(pw, headless=False):
    """Постоянный профиль Chrome: вход в Instagram сохраняется между запусками."""
    config.IG_PROFILE.mkdir(parents=True, exist_ok=True)
    ua = None
    if headless:  # без окна Chrome подписывается как HeadlessChrome – подменяем на обычный
        os_part = "Windows NT 10.0; Win64; x64" if IS_WIN else "Macintosh; Intel Mac OS X 10_15_7"
        ua = f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    return pw.chromium.launch_persistent_context(
        str(config.IG_PROFILE), channel="chrome", headless=headless, locale="ru-RU",
        viewport={"width": 1300, "height": 900}, user_agent=ua,
        args=["--disable-blink-features=AutomationControlled"])


def logged_in(ctx):
    return any(c["name"] == "sessionid" and c["value"] for c in ctx.cookies("https://www.instagram.com"))
