"""Звук чужого рилса → текст. Скачивается только звук, без аккаунта, во временную папку.
Порядок: Groq → ElevenLabs → Gemini → whisper на компьютере (маленькая модель, 2 потока).
Нет ключа – способ пропускается. Временные файлы удаляются сразу."""
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

import config


class SpeechError(Exception):
    pass


def download(reel_id, tmp):
    """→ (путь к звуку, подпись к ролику). Сначала без аккаунта; закрыт – со входом из collect.py."""
    cmd = [sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "-f", "ba/b", "--write-info-json",
           "-o", str(tmp / "a.%(ext)s"), f"https://www.instagram.com/reel/{reel_id}/"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=180)
    err = (r.stderr or "") + (r.stdout or "")
    if r.returncode and config.COOKIES.exists() and ("available" in err or "login" in err.lower()):
        r = subprocess.run(cmd[:-1] + ["--cookies", str(config.COOKIES), cmd[-1]],
                           capture_output=True, text=True, encoding="utf-8", timeout=180)
    info = next(tmp.glob("*.info.json"), None)
    caption = (json.loads(info.read_text(encoding="utf-8")).get("description") or "") if info else ""
    audio = next((p for p in tmp.glob("a.*") if not p.name.endswith(".json")), None)
    if not audio:
        raise SpeechError("звук не скачался: " + (r.stderr or r.stdout).strip()[-200:])
    return audio, caption


def groq(audio):
    with open(audio, "rb") as f:
        r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                          headers={"Authorization": f"Bearer {config.GROQ_KEY}"},
                          files={"file": (audio.name, f)},
                          data={"model": "whisper-large-v3", "response_format": "verbose_json"}, timeout=180)
    if r.status_code != 200:
        raise SpeechError(f"Groq {r.status_code}: {r.text[:200]}")
    j = r.json()
    return j.get("text", "").strip(), j.get("language", "")


def eleven(audio):
    with open(audio, "rb") as f:
        r = requests.post("https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": config.ELEVEN_KEY},
                          files={"file": (audio.name, f)}, data={"model_id": "scribe_v1"}, timeout=300)
    if r.status_code != 200:
        raise SpeechError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    j = r.json()
    return j.get("text", "").strip(), j.get("language_code", "")


def gemini(audio):
    mime = {".m4a": "audio/mp4", ".mp4": "video/mp4", ".mp3": "audio/mpeg", ".webm": "audio/webm"}.get(
        audio.suffix.lower(), "audio/mp4")
    body = {"contents": [{"parts": [
        {"text": "Расшифруй речь в этой записи дословно, на языке оригинала. Верни только текст. "
                 "Если речи нет, верни пустую строку."},
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio.read_bytes()).decode()}}]}]}
    r = None
    for model in ("gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash"):  # перегружена – следующая
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          params={"key": config.GEMINI_KEY}, json=body, timeout=300)
        if r.status_code not in (429, 500, 503, 404):
            break
        time.sleep(3)
    if r.status_code != 200:
        raise SpeechError(f"Gemini {r.status_code}: {r.text[:200]}")
    try:
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        text = ""
    return text.strip(), ""


_local_model = None


def local(audio):
    global _local_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SpeechError("локальный whisper не установлен (pip install faster-whisper)")
    if _local_model is None:
        _local_model = WhisperModel(config.LOCAL_WHISPER_MODEL, device="cpu", compute_type="int8",
                                    cpu_threads=config.LOCAL_WHISPER_THREADS)
    src = str(audio)
    if shutil.which("ffmpeg"):  # звук через ffmpeg надёжнее встроенного декодера на свежих версиях Python
        import numpy as np
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                             capture_output=True, timeout=120).stdout
        src = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    segs, info = _local_model.transcribe(src, vad_filter=True)
    return " ".join(s.text.strip() for s in segs).strip(), info.language


def clean(text):
    """Убрать пометки звуков вроде [music] / (смех)."""
    return re.sub(r"\s+", " ", re.sub(r"\[[^\]]{1,40}\]|\((?:музыка|смех|music|laughs?)[^)]*\)", " ", text or "")).strip()


ENGINES = [("groq", lambda: config.GROQ_KEY, groq), ("elevenlabs", lambda: config.ELEVEN_KEY, eleven),
           ("gemini", lambda: config.GEMINI_KEY, gemini), ("local", lambda: True, local)]


def transcribe(reel_id, only=None):
    """→ (text, lang, caption, engine). only – проверить один конкретный способ."""
    with tempfile.TemporaryDirectory(prefix="reel_") as tmp:
        audio, caption = download(reel_id, Path(tmp))
        errors = []
        for name, has_key, fn in ENGINES:
            if (only and name != only) or not has_key():
                continue
            try:
                text, lang = fn(audio)
                if errors:
                    print(f"  {reel_id}: {'; '.join(errors)[:300]} → взял {name}", flush=True)
                return clean(text), lang, caption, name
            except Exception as e:
                errors.append(f"{name}: {str(e)[:150]}")
        raise SpeechError("; ".join(errors) or "нет ни одного способа расшифровки")
