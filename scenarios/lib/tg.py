"""Отправка в Telegram-бот ученика. Длинный текст режется на части."""
import requests

import config


def send(text):
    if not (config.TG_TOKEN and config.TG_CHAT):
        print("Telegram не настроен (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID в .env)")
        return False
    lines = [ln[i:i + 3900] for ln in text.split("\n") for i in range(0, max(len(ln), 1), 3900)]
    parts, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > 3900:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    for p in parts:
        r = requests.post(f"https://api.telegram.org/bot{config.TG_TOKEN}/sendMessage",
                          data={"chat_id": config.TG_CHAT, "text": p, "disable_web_page_preview": "true"}, timeout=30)
        if not r.json().get("ok"):
            print("Telegram:", r.text[:200])
            return False
    return True


def notify(text):
    """Сообщение об ошибке – не роняет работу, если Telegram недоступен."""
    try:
        send("⚠️ Система сценариев\n" + text)
    except Exception:
        pass
