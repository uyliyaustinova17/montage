"""Шаг 3. Вырезка по решениям из cuts.json → keep.json + cut.mp4 + cuts.md.

    python3 tools/cut.py <проект>

cuts.json – решение по КАЖДОМУ куску из takes.md (пустых быть не должно):
  "7": "keep"                                     – оставить кусок целиком
  "8": {"cut": "дубль, чистая версия в #10"}      – вырезать, причина обязательна
  "9": {"keep": [12.40, 15.10], "why": "…"}       – оставить часть куска (секунды исходника)
  "9": {"keep": [[12.40, 13.00], [13.85, 15.10]], "why": "вырезан повтор «я сделал»"}

Границы режутся не по времени слов из расшифровки (оно плывёт), а по громкости: начало – там, где реально
начинается голос (вдох перед фразой не попадает), конец – где голос затих. Паузы между кусками сжимаются до ~0,16 с.
Куски выравниваются по кадрам, звук собирается одним графом с фейдами 20 мс – без щелчков и рассинхрона."""
import math
import sys

import numpy as np

from common import audio, fmt, load, project, run, save
from speech import HOP, WIN, loudness, threshold

PRE, POST = 0.06, 0.10   # запас до начала голоса и после его конца, с
FPS = 30
MAX_REEL = 60.0


def parse(cid, d, chunk):
    """→ (список диапазонов, причина) или ошибка."""
    if d is None:
        raise ValueError("нет решения")
    if d == "keep":
        return [(chunk["start"], chunk["end"])], ""
    if isinstance(d, str) and d.startswith("cut"):
        why = d[3:].strip(" :–-")
        if not why:
            raise ValueError("у выреза нет причины")
        return [], why
    if isinstance(d, dict) and "cut" in d:
        if not str(d["cut"]).strip():
            raise ValueError("у выреза нет причины")
        return [], str(d["cut"])
    if isinstance(d, dict) and "keep" in d:
        k = d["keep"]
        rng = [k] if isinstance(k[0], (int, float)) else k
        if not d.get("why"):
            raise ValueError("частичный кусок без причины (why)")
        for a, b in rng:
            if not (chunk["start"] - 0.5 <= a < b <= chunk["end"] + 0.5):
                raise ValueError(f"диапазон {a}–{b} вне куска {chunk['start']:.2f}–{chunk['end']:.2f}")
        return [(float(a), float(b)) for a, b in rng], d["why"]
    raise ValueError(f"не понимаю решение {d!r}")


def refine(a, b, db, thr, hi, whole):
    """Границы по громкости.
    Целый кусок: начало – первый кадр громче голоса (вдох и шорох тише голоса и отрезаются), конец – где голос затих.
    Часть куска (время из слов): граница сдвигается в ближайшую «яму» громкости между словами, чтобы не резать слово."""
    def at(t):
        return min(max(int(t / HOP), 0), len(db) - 1)

    if whole:
        i0, i1 = at(a), at(b) + 1
        seg = db[i0:i1]
        loud, voiced = np.where(seg > hi)[0], np.where(seg > thr)[0]
        if len(loud):
            a = (i0 + loud[0]) * HOP
        if len(voiced):
            b = (i0 + voiced[-1]) * HOP + WIN
        return a - PRE, b + POST
    j0, j1 = at(a - 0.12), at(a + 0.05) + 1
    a = (j0 + int(np.argmin(db[j0:j1]))) * HOP
    k0, k1 = at(b - 0.05), at(b + 0.12) + 1
    b = (k0 + int(np.argmin(db[k0:k1]))) * HOP + WIN
    return a, b


def main():
    p = project(sys.argv[1])
    data, cuts = load(p, "words.json"), load(p, "cuts.json")
    ch = data["chunks"]
    errors, keep, rows = [], [], []
    for c in ch:
        try:
            rng, why = parse(c["id"], cuts.get(c["id"]), c)
        except ValueError as e:
            errors.append(f"#{c['id']}: {e} – «{c['text'][:60]}»")
            continue
        rows.append((c, rng, why))
    extra = set(cuts) - {c["id"] for c in ch}
    if extra:
        errors.append(f"в cuts.json лишние id {sorted(extra)} – их нет в words.json (перезапусти transcribe.py, если резал --split)")
    if errors:
        sys.exit("cuts.json не готов – решение нужно по каждому куску:\n" + "\n".join(errors))

    src = p / "source.mp4"
    x = audio(src)
    db = loudness(x)
    thr = threshold(db)
    hi = max(thr + 4, np.percentile(db, 90) - 20)
    for c, rng, why in rows:
        for a, b in rng:
            whole = (a, b) == (c["start"], c["end"])
            a2, b2 = refine(a, b, db, thr, hi, whole)
            keep.append([max(a2, 0), min(b2, len(x) / 16000)])
    keep.sort()
    out = []
    for a, b in keep:   # по кадрам: иначе звук и картинка расходятся на каждом стыке
        a, b = math.floor(a * FPS) / FPS, math.ceil(b * FPS) / FPS
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([round(a, 4), round(b, 4)])
    if not out:
        sys.exit("Всё вырезано – проверь cuts.json")
    save(p, "keep.json", out)

    # отчёт: что вырезано и почему
    L = [f"# Что вырезано – {p.name}", "", "| # | время | решение | текст |", "|---|---|---|---|"]
    for c, rng, why in rows:
        dec = "оставлен" if rng and not why else (f"часть: {why}" if rng else f"✂ {why}")
        L.append(f"| {c['id']} | {fmt(c['start'])} | {dec} | {c['text'][:90]} |")
    total = sum(b - a for a, b in out)
    L += ["", f"Было {fmt(len(x) / 16000)}, стало {fmt(total)}, кусков в ролике: {len(out)}."]
    (p / "cuts.md").write_text("\n".join(L), encoding="utf-8")

    if "--video" not in sys.argv:
        # только звук (cut.wav): для verify.py хватает его, а картинку один раз собирает движок стиля прямо из исходника
        fc = "".join(f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,afade=t=in:d=0.02,afade=t=out:st={b - a - 0.02:.4f}:d=0.02[a{i}];"
                     for i, (a, b) in enumerate(out)) + "".join(f"[a{i}]" for i in range(len(out))) + f"concat=n={len(out)}:v=0:a=1[a]"
        (p / "cut_graph.txt").write_text(fc, encoding="utf-8")   # граф в файле: на 800+ кусках командная строка Windows не вмещается
        run(["ffmpeg", "-v", "error", "-y", "-i", src, "-/filter_complex", "cut_graph.txt", "-map", "[a]", "-ac", "1", "-ar", "48000", "cut.wav"], cwd=p)
        print(f"Было {fmt(len(x) / 16000)} → стало {fmt(total)} ({len(out)} кусков). Отчёт: {p / 'cuts.md'}, звук: {p / 'cut.wav'}")
        print(f"Дальше обязательно: python3 tools/verify.py {p.name}")
        return
    # cut.mp4 (--video) – одним графом: trim/atrim по кадрам, фейды 20 мс на каждом стыке
    parts ="".join(f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS[v{i}];[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:d=0.02,afade=t=out:st={b - a - 0.02:.4f}:d=0.02[a{i}];" for i, (a, b) in enumerate(out))
    fc = parts + "".join(f"[v{i}][a{i}]" for i in range(len(out))) + f"concat=n={len(out)}:v=1:a=1[v][a]"
    run(["ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
         "-ar", "48000", "-movflags", "+faststart", "cut.mp4"], cwd=p)
    print(f"Было {fmt(len(x) / 16000)} → стало {fmt(total)} ({len(out)} кусков). Отчёт: {p / 'cuts.md'}, видео: {p / 'cut.mp4'}")
    if total > MAX_REEL:
        print(f"⚠ Ролик длиннее минуты ({total:.0f} с). Рилс должен быть короче 60 с – скажи автору, что можно сократить.")
    print(f"Дальше обязательно: python3 tools/verify.py {p.name}")


if __name__ == "__main__":
    main()
