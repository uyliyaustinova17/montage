"""Паузы и расшифровка по кускам.

Почему по кускам: расшифровщик, которому дали весь ролик целиком, «причёсывает» речь – два одинаковых
дубля подряд склеивает в одну фразу, фальстарт «я сделал… я сделал систему» превращает в «я сделал систему».
Повтор пропадает из текста, а в видео остаётся. Поэтому ролик режется по паузам на короткие куски,
и каждый кусок расшифровывается отдельно: расшифровщику нечего склеивать."""
import hashlib
import json
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

from common import ELEVEN_KEY, GROQ_KEY, LANG, SR, audio, env

LOCAL_MODEL = env("LOCAL_WHISPER_MODEL", "large-v3-turbo")
HOP = 0.01   # шаг анализа громкости, с
WIN = 0.03

# Фразы, которые whisper выдумывает на тишине и обрывках (русские субтитры из его обучающих данных)
HALLUCINATIONS = ("субтитр", "dimatorzok", "продолжение следует", "спасибо за просмотр", "редактор субтитров",
                  "подписывайтесь на канал", "корректор", "amara.org")


# ---------- громкость и паузы ----------

def loudness(x):
    """Громкость по кадрам 10 мс в дБ."""
    n, w = int(HOP * SR), int(WIN * SR)
    if len(x) < w:
        return np.array([-90.0])
    idx = np.arange(0, len(x) - w, n)
    fr = np.stack([x[i:i + w] for i in idx])
    rms = np.sqrt((fr ** 2).mean(1) + 1e-12)
    return 20 * np.log10(rms)


def threshold(db):
    """Порог речи: между шумом комнаты и громкостью голоса."""
    floor, voice = np.percentile(db, 10), np.percentile(db, 90)
    return max(floor + 10, voice - 28)


def islands(db, thr, min_pause, min_len=0.08):
    """Участки речи [(начало, конец)] в секундах: паузы короче min_pause не разрывают речь."""
    on = db > thr
    out, i, n = [], 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            out.append([i * HOP, j * HOP + WIN])
            i = j
        else:
            i += 1
    merged = []
    for a, b in out:
        if merged and a - merged[-1][1] < min_pause:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a >= min_len]


def split_long(seg, db, thr, max_len):
    """Кусок длиннее max_len режется по самой длинной внутренней паузе (пока есть паузы от 0,15 с)."""
    a, b = seg
    if b - a <= max_len:
        return [seg]
    i0, i1 = int(a / HOP), int(b / HOP)
    sub = islands(db[i0:i1], thr, 0.15, 0.0)
    if len(sub) < 2:
        return [seg]
    gaps = [(sub[k + 1][0] - sub[k][1], k) for k in range(len(sub) - 1)]
    _, k = max(gaps)
    cut1, cut2 = a + sub[k][1], a + sub[k + 1][0]
    return split_long((a, cut1), db, thr, max_len) + split_long((cut2, b), db, thr, max_len)


def chunks(x, split=0.45, max_len=9.0):
    """Куски речи для расшифровки: разрыв на каждой паузе от split секунд, куски не длиннее max_len."""
    db = loudness(x)
    thr = threshold(db)
    out = []
    for seg in islands(db, thr, split):
        out += split_long(seg, db, thr, max_len)
    total = len(x) / SR
    return [(round(max(a - 0.0, 0), 3), round(min(b, total), 3)) for a, b in out], db, thr


# ---------- расшифровщики ----------

def _post(url, retries=6, **kw):
    for k in range(retries):
        r = requests.post(url, timeout=180, **kw)
        if r.status_code == 429 or r.status_code >= 500:   # лимит или сбой – ждём и повторяем
            wait = float(r.headers.get("retry-after", 0) or 0) or 4 * (k + 1)
            time.sleep(min(wait, 60))
            continue
        return r
    return r


def groq(wav):
    with open(wav, "rb") as f:
        r = _post("https://api.groq.com/openai/v1/audio/transcriptions",
                  headers={"Authorization": f"Bearer {GROQ_KEY}"}, files={"file": (wav.name, f.read())},
                  data={"model": "whisper-large-v3", "response_format": "verbose_json", "language": LANG,
                        "temperature": "0", "timestamp_granularities[]": "word", "prompt": PROMPT})
    if r.status_code in (401, 403):
        raise SystemExit("Ключ Groq недействителен или истёк: console.groq.com → API Keys → новый ключ в .env (GROQ_API_KEY)")
    if r.status_code != 200:
        raise RuntimeError(f"Groq {r.status_code}: {r.text[:200]}")
    j = r.json()
    return [{"text": w["word"].strip(), "start": w["start"], "end": w["end"]} for w in j.get("words", []) if w["word"].strip()]


def eleven(wav):
    with open(wav, "rb") as f:
        r = _post("https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": ELEVEN_KEY},
                  files={"file": (wav.name, f.read())},
                  data={"model_id": "scribe_v1", "language_code": LANG, "timestamps_granularity": "word",
                        "tag_audio_events": "true"})
    if r.status_code in (401, 403):
        raise SystemExit(f"ElevenLabs отказал ({r.status_code}): ключ недействителен, нет права Speech to Text или кончилась квота. "
                         "Проверь ключ в .env (ELEVENLABS_API_KEY) или запусти с --engine groq")
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    out = []
    for w in r.json().get("words", []):
        if w.get("type") == "word" and w["text"].strip():
            out.append({"text": w["text"].strip(), "start": w["start"], "end": w["end"]})
        elif w.get("type") == "audio_event":   # (смех), (вздох) – тоже повод для выреза
            out.append({"text": w["text"].strip(), "start": w["start"], "end": w["end"], "event": True})
    return out


_model, _lock = None, threading.Lock()


def has_local():
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def local(wav):
    """whisper на компьютере: без ключа и интернета, медленнее. Первый запуск скачивает модель (~1,6 ГБ)."""
    global _model
    from faster_whisper import WhisperModel
    with _lock:
        if _model is None:
            print(f"  загружаю модель {LOCAL_MODEL} (первый раз скачивается ~1,6 ГБ)…", flush=True)
            _model = WhisperModel(LOCAL_MODEL, device="cpu", compute_type="int8")
        # звук numpy-массивом: встроенный декодер faster-whisper (PyAV) ломается на части версий Python
        segs, _ = _model.transcribe(audio(wav), language=LANG, word_timestamps=True, condition_on_previous_text=False,
                                    initial_prompt=PROMPT, beam_size=5, temperature=0, vad_filter=False)
        return [{"text": w.word.strip(), "start": w.start, "end": w.end} for s in segs for w in (s.words or []) if w.word.strip()]


ENGINES = {"eleven": (lambda: ELEVEN_KEY, eleven), "groq": (lambda: GROQ_KEY, groq), "local": (has_local, local)}


def alive(engine):
    """Ключ рабочий? Чтобы не упасть на первом куске с истёкшим ключом."""
    try:
        if engine == "groq":
            return requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {GROQ_KEY}"},
                                timeout=20).status_code == 200
        if engine == "eleven":
            return requests.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": ELEVEN_KEY},
                                timeout=20).status_code == 200
    except requests.RequestException:
        return False
    return True


def pick(engine=None):
    if engine:
        return engine
    for e in ["eleven", "groq", "local"]:   # ElevenLabs точнее по запинкам, Groq бесплатный, local без ключа
        if ENGINES[e][0]():
            if alive(e):
                return e
            print(f"Ключ {e} не работает (истёк или нет прав) – пробую следующий способ")
    raise SystemExit("Нечем расшифровывать: впиши GROQ_API_KEY или ELEVENLABS_API_KEY в .env "
                     "или поставь whisper на компьютер: python3 install.py")


# ---------- расшифровка кусков ----------

# Подсказка whisper: с ней он пишет запинки и повторы как есть, а не «причёсывает» речь
PROMPT = "Э-э, ну, это, повтор, запинка."

PAD = 0.15   # кусок вырезается с запасом, чтобы не съесть первый и последний звук слова


def transcribe(src, segs, cache, engine, threads=4):
    """segs [(a, b)] → для каждого куска список слов с временем исходника. Кэш – по границам и движку."""
    cache.mkdir(parents=True, exist_ok=True)
    fn = ENGINES[engine][1]
    src_id = hashlib.md5(f"{Path(src).resolve()}:{Path(src).stat().st_size}".encode()).hexdigest()[:8]

    # запас по краям – не больше половины паузы до соседа, иначе соседнее слово попадёт в оба куска
    pads = []
    for k, (a, b) in enumerate(segs):
        lo = (a - segs[k - 1][1]) / 2 if k else PAD
        hi = (segs[k + 1][0] - b) / 2 if k + 1 < len(segs) else PAD
        pads.append((max(min(PAD, lo), 0.02), max(min(PAD, hi), 0.02)))

    def one(k):
        a, b = segs[k]
        key = cache / f"{src_id}_{engine}_{a:.2f}_{b:.2f}_{pads[k][0]:.2f}_{pads[k][1]:.2f}.json"
        if key.exists():
            return json.loads(key.read_text(encoding="utf-8"))
        s, e = max(a - pads[k][0], 0), b + pads[k][1]
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "c.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s:.3f}", "-i", str(src), "-t", f"{e - s:.3f}",
                            "-vn", "-ac", "1", "-ar", str(SR), str(wav)], check=True)
            try:
                words = fn(wav)
            except Exception as e:   # не кэшируем: пустой кусок спрятал бы дубль
                print(f"  кусок {a:.2f}–{b:.2f} не расшифровался: {str(e)[:120]}")
                return None
        for w in words:
            w["start"], w["end"] = round(s + w["start"], 3), round(s + w["end"], 3)
        key.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        return words

    with ThreadPoolExecutor(1 if engine == "local" else threads) as ex:
        return list(ex.map(one, range(len(segs))))


def build(src, segs, cache, engine, ids=None):
    """→ список кусков {id, start, end, pause, text, words}."""
    res = transcribe(src, segs, cache, engine)
    failed = [f"{a:.2f}–{b:.2f}" for (a, b), r in zip(segs, res) if r is None]
    if failed:
        raise SystemExit(f"Не расшифровались куски {', '.join(failed)} (причины выше: лимит, сеть или ключ). Запусти ту же команду ещё раз – "
                         "готовые куски возьмутся из кэша. Пропускать нельзя: в пустом куске может быть дубль.")
    out = []
    for k, ((a, b), words) in enumerate(zip(segs, res)):
        words = clean(words, a, b)
        out.append({"id": ids[k] if ids else str(k + 1), "start": a, "end": b,
                    "pause": round(a - segs[k - 1][1], 2) if k else round(a, 2),
                    "text": " ".join(w["text"] for w in words), "words": words})
    return out


def clean(words, a, b):
    """Слова куска без выдумок расшифровщика: «промо -видео» → «промо-видео»; текст подсказки и
    фразы-галлюцинации на тишине → [неразборчиво]."""
    out = []
    for w in words:
        w = dict(w)
        if out and w["text"].startswith("-") and len(w["text"]) > 1 and not out[-1].get("event"):
            out[-1]["text"] += w["text"]
            out[-1]["end"] = w["end"]
            continue
        out.append(w)
    text = " ".join(w["text"] for w in out).lower()
    t, pr = [norm(x) for x in text.split() if norm(x)], [norm(x) for x in PROMPT.lower().split() if norm(x)]
    leak = t and sum(1 for x in t if x in pr) / len(t) >= 0.6
    if any(h in text for h in HALLUCINATIONS) or leak:
        return [{"text": "[неразборчиво]", "start": a, "end": b, "event": True}]
    return out


def norm(s):
    return re.sub(r"[^\w-]", "", s.lower().replace("ё", "е"))
