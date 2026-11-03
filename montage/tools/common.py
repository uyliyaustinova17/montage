"""Общее для всех инструментов монтажа: окружение, ключи, ffmpeg, папка проекта."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for _s in (sys.stdout, sys.stderr):  # консоль Windows не печатает «✅», «→» и часть русских букв
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Любой инструмент сам переключается на окружение .venv (его создаёт install.py)
_VENV = ROOT / ".venv"
_VPY = _VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if _VPY.exists() and Path(sys.prefix).resolve() != _VENV.resolve():
    sys.exit(subprocess.run([str(_VPY), *sys.argv]).returncode)

from dotenv import dotenv_values  # noqa: E402

# Ключи: свой .env, а пустые – из системы сценариев (~/scenarios/.env)
_ENV = {k: (v or "").strip() for k, v in dotenv_values(Path.home() / "scenarios" / ".env").items()}
_ENV.update({k: v.strip() for k, v in dotenv_values(ROOT / ".env").items() if v and v.strip()})


def env(name, default=""):
    return (os.getenv(name) or _ENV.get(name) or default).strip()


GROQ_KEY = env("GROQ_API_KEY")
ELEVEN_KEY = env("ELEVENLABS_API_KEY")
LANG = env("MONTAGE_LANG", "ru")

PROJECTS = ROOT / "projects"
SR = 16000  # звук для расшифровки и анализа пауз


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,r_frame_rate,pix_fmt,color_transfer,color_primaries:"
                        "stream_tags=rotate:stream_side_data=rotation:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def duration(path):
    return float(probe(path)["format"]["duration"])


def project(arg):
    """Папка проекта: путь к ней или имя внутри projects/."""
    p = Path(arg).expanduser()
    if not p.is_absolute() and not p.exists():
        p = PROJECTS / arg
    if not (p / "source.mp4").exists():
        sys.exit(f"В {p} нет source.mp4 – сначала python3 tools/prep.py <видео>")
    return p.resolve()


def load(p, name):
    return json.loads((p / name).read_text(encoding="utf-8"))


def save(p, name, data):
    (p / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def audio(src, start=None, end=None):
    """Звук моно 16 кГц как numpy float32 (весь файл или кусок)."""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if end is not None:
        cmd += ["-t", f"{end - (start or 0):.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def fmt(t):
    m, s = divmod(max(t, 0), 60)
    return f"{int(m)}:{s:05.2f}"
