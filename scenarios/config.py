"""Настройки системы. Менять можно здесь, остальной код трогать не нужно."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

for _s in (sys.stdout, sys.stderr):  # консоль Windows не печатает «✅», «→» и часть русских букв
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")  # и для всех запускаемых из системы программ

# Любая команда сама переключается на окружение .venv (его создаёт install.py): python3 run.py работает как есть
_VENV = ROOT / ".venv"
_VPY = _VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if _VPY.exists() and Path(sys.prefix).resolve() != _VENV.resolve():
    sys.exit(subprocess.run([str(_VPY), *sys.argv]).returncode)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
LOGS = ROOT / "logs"
SCRIPTS = ROOT / "scripts"
KNOWLEDGE = ROOT / "knowledge"
BLOGGERS = ROOT / "bloggers.txt"
IG_PROFILE = Path(os.getenv("IG_PROFILE_DIR") or ROOT / "ig_profile")   # отдельный Chrome-профиль под Instagram
COOKIES = DATA / "ig_cookies.txt"   # вход для роликов, закрытых без аккаунта (пишет collect.py)
for d in (DATA, LOGS, SCRIPTS):
    d.mkdir(exist_ok=True)

# Ключи (.env)
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Разведка
REELS_PER_BLOGGER = 40          # сколько последних роликов смотреть у блогера
MAX_BLOGGERS_PER_RUN = 30       # бережём аккаунт
PAUSE_BETWEEN_BLOGGERS = (20, 60)
OUTLIER_X = 2.0                 # залёт: просмотры ≥ 2 × медиана блогера
MIN_AGE_DAYS = 2                # моложе – ещё набирает
MIN_REELS_FOR_MEDIAN = 5

# Расшифровка
TRANSCRIBE_PER_RUN = 20
LOCAL_WHISPER_MODEL = "base"   # запасной вариант без интернета; small не влезает в память этого компьютера
LOCAL_WHISPER_THREADS = 2

# Сценарии
SCORE_MODEL = os.getenv("SCORE_MODEL", "haiku")     # оценка пачкой – быстрая модель
WRITE_MODEL = os.getenv("WRITE_MODEL", "sonnet")    # сценарист и проверяющий; при подписке Max можно "opus"
SCORE_BATCH = 8
MIN_RELEVANCE = 6               # ниже – тема не переносится на твою аудиторию
SCRIPTS_PER_RUN = 5
PARALLEL = 3                    # сколько сценариев пишется одновременно
ATTEMPTS = 2                    # не прошёл проверку – переписать один раз
MAX_OVERLAP = 20.0              # % совпадения формулировок с оригиналом
FRESH_DAYS = 30                 # свежие залёты идут первыми

AD_MARKERS = ("#ad", "#реклама", "реклама", "paid partnership", "партнерство", "партнёрство", "#sponsored", "erid")
