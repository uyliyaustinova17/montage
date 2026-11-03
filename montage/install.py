"""Установка: отдельное окружение .venv в этой папке и все пакеты. Запускать один раз.
    python3 install.py      (на Windows: python install.py)"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # консоль Windows не печатает «→» и часть русских букв
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(*cmd, must=True):
    print("→", " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run([str(c) for c in cmd])
    if must and r.returncode != 0:
        sys.exit(f"Не получилось: {' '.join(str(c) for c in cmd)}")
    return r.returncode == 0


if __name__ == "__main__":
    if sys.version_info < (3, 10):
        sys.exit("Нужен Python 3.10 или новее")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("Нет ffmpeg. Mac: brew install ffmpeg. Windows: winget install Gyan.FFmpeg, потом закрыть и открыть окно заново")
    if not PY.exists():
        run(sys.executable, "-m", "venv", VENV)
    run(PY, "-m", "pip", "install", "-q", "--upgrade", "pip")
    run(PY, "-m", "pip", "install", "-q", "patchright", "requests", "python-dotenv", "yt-dlp", "pillow", "numpy")
    # расшифровка на компьютере без ключа: на части версий Python может не встать – тогда нужен ключ Groq или ElevenLabs
    whisper = run(PY, "-m", "pip", "install", "-q", "faster-whisper", must=False)
    if not (ROOT / ".env").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")
    for d in ("projects", "refs", "готовые"):
        (ROOT / d).mkdir(exist_ok=True)
    print("\nГотово." + ("" if whisper else " Whisper на компьютере не встал – расшифровка будет через Groq или ElevenLabs."))
    print("Дальше: python3 doctor.py")
