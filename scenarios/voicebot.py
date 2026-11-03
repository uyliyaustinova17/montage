"""Голосовое в Telegram → видео с аватаром HeyGen → обратно в Telegram.
    python voicebot.py         – слушать бота в этом окне (для проверки; остановить Ctrl + C)
    python voicebot.py on      – включить: бот работает сам, пока компьютер включён, и стартует после перезагрузки
    python voicebot.py off     – выключить
    python voicebot.py status  – проверить

Отправь боту голосовое (можно ответом на сценарий) – через 5–15 минут придёт видео.
Голосовые обрабатываются по очереди, по одному."""
import config  # первым: переключает на окружение .venv
import base64
import plistlib
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

from lib.common import IS_MAC, IS_WIN, log

NAME = "scenarios-voicebot"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"com.{NAME}.plist"
API = f"https://api.telegram.org/bot{config.TG_TOKEN}"
VOICE_DIR = config.DATA / "voice"
OFFSET = config.DATA / "voicebot_offset"
TG_LIMIT_MB = 45   # бот может отправить видео до 50 МБ – больше сжимаем
HELP = ("Пришли голосовое – сделаю из него видео с твоим аватаром в HeyGen. Это 5–15 минут.\n"
        "Записывай в тихом месте, телефон держи у рта.")

jobs = queue.Queue()


def tg(method, files=None, timeout=60, **data):
    r = requests.post(f"{API}/{method}", data=data, files=files, timeout=timeout).json()
    if not r.get("ok"):
        raise RuntimeError(f"Telegram {method}: {r.get('description')}")
    return r["result"]


def say(text, reply_to=None):
    try:
        tg("sendMessage", chat_id=config.TG_CHAT, text=text, reply_to_message_id=reply_to or "",
           allow_sending_without_reply="true")
    except Exception as e:
        log("Telegram не ответил:", e)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "csv=p=0:s=,", str(path)],
                         capture_output=True, text=True).stdout.split()
    w, h = out[0].split(",")[:2]
    return int(w), int(h), int(float(out[-1]))


def send_video(path, reply_to):
    """Видео с плеером. Ширина, высота и длина передаются явно, иначе Telegram сплющивает вертикаль."""
    path = Path(path)
    if path.stat().st_size > TG_LIMIT_MB * 1024 * 1024:
        small = path.with_name(path.stem + "_tg.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-c:v", "libx264", "-crf", "26", "-preset", "fast",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(small)], check=True)
        path = small
    w, h, d = probe(path)
    with open(path, "rb") as f:
        tg("sendVideo", files={"video": (path.name, f, "video/mp4")}, timeout=600, chat_id=config.TG_CHAT,
           width=w, height=h, duration=d, supports_streaming="true", reply_to_message_id=reply_to,
           allow_sending_without_reply="true",
           caption="Готово. Полное качество – в папке scenarios/video на компьютере.")


def work():
    import heygen  # тяжёлый импорт – только в рабочем потоке
    while True:
        m = jobs.get()
        mid = m["message_id"]
        try:
            v = m.get("voice") or m.get("audio")
            f = tg("getFile", file_id=v["file_id"])
            VOICE_DIR.mkdir(parents=True, exist_ok=True)
            raw = VOICE_DIR / f"{mid}{Path(f['file_path']).suffix or '.ogg'}"
            raw.write_bytes(requests.get(f"https://api.telegram.org/file/bot{config.TG_TOKEN}/{f['file_path']}",
                                         timeout=120).content)
            mp3 = VOICE_DIR / f"voice_{mid}.mp3"   # HeyGen принимает mp3, голосовые Telegram – ogg
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-ar", "44100", "-b:a", "192k", str(mp3)],
                           check=True)
            log("Голосовое", mid, "→ HeyGen")
            video = heygen.make(mp3)
            log("Видео готово:", video)
            send_video(video, mid)
        except heygen.HeyGenError as e:
            say(f"Не получилось сделать видео: {e}\nОткрой систему и скажи Claude: «почини видео с аватаром».", mid)
        except Exception as e:
            log("Ошибка:", repr(e))
            say(f"Не получилось сделать видео: {e}\nОткрой систему и скажи Claude: «почини видео с аватаром».", mid)
        finally:
            jobs.task_done()


def listen():
    if not (config.TG_TOKEN and config.TG_CHAT):
        sys.exit("Telegram не настроен (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID в .env)")
    threading.Thread(target=work, daemon=True).start()
    offset = int(OFFSET.read_text()) if OFFSET.exists() else 0
    log("Бот слушает голосовые")
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 50,
                                                         "allowed_updates": '["message"]'}, timeout=70).json()
            if r.get("error_code") == 409:   # этот же бот уже слушает в другом окне или в фоне
                sys.exit("Бот уже запущен в другом месте. Проверь: python voicebot.py status")
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                OFFSET.write_text(str(offset))
                m = u.get("message") or {}
                if str((m.get("chat") or {}).get("id")) != config.TG_CHAT:
                    continue   # чужие сообщения не обрабатываем
                if m.get("voice") or m.get("audio"):
                    ahead = jobs.unfinished_tasks
                    jobs.put(m)
                    say("Принял, делаю видео в HeyGen. Пришлю через 5–15 минут." +
                        (f" Передо мной в очереди: {ahead}." if ahead else ""), m["message_id"])
                elif m.get("text"):
                    say(HELP)
        except requests.RequestException as e:
            log("Нет связи с Telegram:", e)
            time.sleep(10)


def on():
    if IS_MAC:
        # у фонового запуска нет твоего терминала: явно говорим, где лежат ffmpeg и python
        dirs = [str(Path(p).parent) for p in (shutil.which("ffmpeg"), sys.executable) if p]
        path = ":".join(dict.fromkeys(dirs + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]))
        log_file = str(config.LOGS / "voicebot.log")
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_bytes(plistlib.dumps({
            "Label": f"com.{NAME}", "ProgramArguments": [sys.executable, "-u", str(Path(__file__).resolve())],
            "WorkingDirectory": str(config.ROOT), "RunAtLoad": True, "KeepAlive": True,
            "StandardOutPath": log_file, "StandardErrorPath": log_file, "EnvironmentVariables": {"PATH": path}}))
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        subprocess.run(["launchctl", "load", str(PLIST)], check=True)
    elif IS_WIN:
        pyw = Path(sys.executable).with_name("pythonw.exe")   # без чёрного окна
        exe = pyw if pyw.exists() else Path(sys.executable)
        q = lambda v: str(v).replace("'", "''")  # апостроф в пути для PowerShell удваивается
        ps = ("$ProgressPreference = 'SilentlyContinue';"
              f"$a = New-ScheduledTaskAction -Execute '{q(exe)}' -Argument '\"{q(Path(__file__).resolve())}\"' "
              f"-WorkingDirectory '{q(config.ROOT)}';"
              "$t = New-ScheduledTaskTrigger -AtLogOn;"
              "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 "
              "-RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 0);"
              f"Register-ScheduledTask -TaskName '{NAME}' -Action $a -Trigger $t -Settings $s -Force | Out-Null;"
              f"Start-ScheduledTask -TaskName '{NAME}'")
        # закодированная команда: кавычки и кириллица в пути не ломают PowerShell
        enc = base64.b64encode(ps.encode("utf-16-le")).decode()
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-EncodedCommand", enc], capture_output=True)
        if r.returncode != 0:
            sys.exit("Не получилось включить: " + r.stderr.decode("utf-8", "replace")[-500:])
    else:
        sys.exit("Поддерживаются macOS и Windows")
    print("Включено: бот принимает голосовые, пока компьютер включён, и сам стартует после перезагрузки.")


def off():
    if IS_MAC:
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        PLIST.unlink(missing_ok=True)
    elif IS_WIN:
        subprocess.run(["schtasks", "/End", "/TN", NAME], capture_output=True)
        subprocess.run(["schtasks", "/Delete", "/TN", NAME, "/F"], capture_output=True)
    print("Выключено.")


def status():
    if IS_MAC:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        print("Включено" if f"com.{NAME}" in r.stdout else "Выключено")
    elif IS_WIN:
        r = subprocess.run(["schtasks", "/Query", "/TN", NAME], capture_output=True)  # без text: вывод в кодировке консоли
        print("Включено" if r.returncode == 0 else "Выключено")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"on": on, "off": off, "status": status}.get(cmd, listen)()
