"""Проверка, что всё для монтажа на месте. Печатает таблицу ✅/❌ и что делать.
    python3 doctor.py"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
from common import ELEVEN_KEY, GROQ_KEY, ROOT  # noqa: E402  (переключает на .venv)

rows = []


def check(name, ok, fix=""):
    rows.append((name, ok, fix))


def main():
    check("Python 3.10+", sys.version_info >= (3, 10), "поставить Python 3.12")
    check("Окружение .venv", (ROOT / ".venv").exists(), "python3 install.py")
    for tool in ("ffmpeg", "ffprobe"):
        check(tool, bool(shutil.which(tool)), "Mac: brew install ffmpeg · Windows: winget install Gyan.FFmpeg")
    for mod in ("patchright", "requests", "dotenv", "PIL", "numpy", "yt_dlp"):
        try:
            __import__(mod)
            check(f"пакет {mod}", True)
        except ImportError:
            check(f"пакет {mod}", False, "python3 install.py")

    import requests
    ways = []
    if GROQ_KEY:
        try:
            ok = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {GROQ_KEY}"},
                              timeout=20).status_code == 200
        except Exception:
            ok = False
        check("Ключ Groq", ok, "ключ недействителен: console.groq.com → API Keys → новый ключ в .env")
        ways.append(ok)
    else:
        check("Ключ Groq", None, "нет – не обязательно, если есть другой способ")
    if ELEVEN_KEY:
        try:
            ok = requests.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": ELEVEN_KEY}, timeout=20).status_code == 200
        except Exception:
            ok = False
        check("Ключ ElevenLabs", ok, "ключ недействителен или нет права Speech to Text / user_read")
        ways.append(ok)
    else:
        check("Ключ ElevenLabs", None, "нет – не обязательно")
    try:
        import faster_whisper  # noqa: F401
        check("Whisper на компьютере", True, "")
        ways.append(True)
    except ImportError:
        check("Whisper на компьютере", None, "не установлен – не обязательно, если есть ключ")
    check("Есть чем расшифровывать", any(ways), "нужен ключ Groq или ElevenLabs в .env либо whisper (python3 install.py)")

    try:
        from patchright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel="chrome", headless=True)
            b.close()
        check("Google Chrome", True)
    except Exception as e:
        check("Google Chrome", False, f"установить Google Chrome ({str(e)[:60]})")
    try:
        ok = requests.get("https://fonts.googleapis.com/css2?family=Inter", timeout=15).status_code == 200
    except Exception:
        ok = False
    check("Шрифты Google Fonts", ok, "нет интернета или сайт закрыт – включить VPN")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True) if shutil.which("ffmpeg") else None
    check("ffmpeg lut3d (HDR с iPhone)", bool(r and " lut3d " in r.stdout), "обновить ffmpeg")

    w = max(len(n) for n, _, _ in rows)
    for n, ok, fix in rows:
        mark = "✅" if ok else ("➖" if ok is None else "❌")
        print(f"{mark} {n.ljust(w)}  {fix if not ok else ''}")
    bad = [n for n, ok, _ in rows if ok is False]
    print("\nВсё готово к монтажу." if not bad else f"\nИсправить: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
