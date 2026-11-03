"""Шаг 4. Проверка вырезки: готовый cut.mp4 режется на мелкие кусочки и расшифровывается заново.

    python3 tools/verify.py <проект> [--engine groq]

Ищет то, что пропустили при первом проходе: повторы и дубли, фальстарты, паузы, слова, обрезанные на стыке,
пропавшие или задвоенные предложения сценария (если в проекте есть script.txt). Пишет verify.md.
Пока в verify.md есть ❌ – монтаж дальше не идёт: исправить cuts.json → cut.py → verify.py."""
import argparse
import sys

import numpy as np

from common import audio, duration, fmt, load, project
from speech import HOP, build, chunks, islands, pick
from takes import hints, script_match

FILLER_OK = {"и", "в", "на", "не", "что", "это", "а", "я", "с", "у", "к", "по", "как", "то", "же", "ну"}


def to_src(keep, t):
    c = 0.0
    for a, b in keep:
        if t <= c + (b - a):
            return a + (t - c)
        c += b - a
    return keep[-1][1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--engine")
    args = ap.parse_args()
    p = project(args.project)
    cut, keep = p / "cut.wav", load(p, "keep.json")
    if not cut.exists():
        cut = p / "cut.mp4"
    if not cut.exists():
        sys.exit("Нет cut.wav – сначала python3 tools/cut.py")
    engine = pick(args.engine or load(p, "words.json")["engine"])
    x = audio(cut)
    total = len(x) / 16000
    # мелкие кусочки: разрыв на каждой паузе от 0,2 с, не длиннее 5 с – расшифровщику нечего «склеить»
    segs, db, thr = chunks(x, 0.2, 5.0)
    print(f"Проверка: {len(segs)} кусочков, расшифровываю каждый отдельно ({engine})…")
    ch = build(cut, segs, p / "cache", engine)
    bad, warn = [], []

    def where(t):
        return f"{fmt(t)} в ролике (исходник {fmt(to_src(keep, t))})"

    # 1. дубли и фальстарты между кусочками
    H = hints(ch)
    for c in ch:
        for h in H[c["id"]]:
            if h.startswith(("ДУБЛЬ", "НЕДОГОВОРЁННЫЙ", "ФАЛЬСТАРТ", "КОНЕЦ ПОВТОРЯЕТСЯ", "ПОВТОР ВНУТРИ")):
                bad.append(f"{where(c['start'])}: {h.split(' – ')[0]} – «{c['text']}»")
            elif h.startswith("ПОХОЖЕ"):
                warn.append(f"{where(c['start'])}: {h.split(' – ')[0]} – «{c['text']}» – прочитать: не одна ли мысль дважды")
            elif h.startswith("ЗВУК"):
                warn.append(f"{where(c['start'])}: {h}")
            elif h.startswith("МАЛО СЛОВ"):
                warn.append(f"{where(c['start'])}: мало слов в кусочке «{c['text']}» – возможен проглоченный повтор")

    # 2. повтор фразы через весь текст: одна и та же связка из 3+ слов дважды в пределах 25 слов
    flat = [w for c in ch for w in c["words"] if not w.get("event")]
    t = [w["text"].lower().strip(".,!?…«»\"") for w in flat]
    seen = set()   # слова, уже попавшие в найденный повтор: не дублировать отчёт про ту же фразу
    for n in (4, 3):
        for i in range(len(t) - n):
            g = t[i:i + n]
            if all(w in FILLER_OK for w in g) or any(k in seen for k in range(i, i + n)):
                continue
            for j in range(i + n, min(i + 25, len(t) - n + 1)):
                if t[j:j + n] == g:
                    seen.update(range(i, i + n))
                    bad.append(f"{where(flat[i]['start'])}: фраза «{' '.join(g)}» сказана дважды "
                               f"(второй раз {fmt(flat[j]['start'])}) – дубль?")
                    break

    # 3. паузы в готовом ролике
    voice = islands(db, thr, 0.0, 0.02)
    for (a0, b0), (a1, b1) in zip(voice, voice[1:]):
        gap = a1 - b0
        if gap > 0.45:
            bad.append(f"{where(b0)}: пауза {gap:.2f} с")
        elif gap > 0.3:
            warn.append(f"{where(b0)}: пауза {gap:.2f} с – проверь, смысловая ли")
    if voice and voice[0][0] > 0.25:
        bad.append(f"тишина в самом начале ролика {voice[0][0]:.2f} с – хук должен начинаться сразу")

    # 4. стыки: голос на самом краю выреза = обрезанное слово
    hi = np.percentile(db, 90) - 12
    pos = 0.0
    for a, b in keep[:-1]:
        pos += b - a
        i = int(pos / HOP)
        before, after = db[max(i - 3, 0):i], db[i:i + 3]
        if len(before) and before.mean() > hi:
            warn.append(f"{where(pos)}: на стыке звучит голос до выреза – проверь, не обрезан ли конец слова")
        if len(after) and after.mean() > hi:
            warn.append(f"{where(pos)}: на стыке звучит голос после выреза – проверь, не съедено ли начало слова")

    # 5. сценарий
    script = p / "script.txt"
    if script.exists():
        for s, hits in script_match(script.read_text(encoding="utf-8"), flat):
            if not hits:
                bad.append(f"предложение сценария пропало: «{s}» – вырезано нужное или не было записано")
            elif len(hits) > 1:
                bad.append(f"предложение сценария сказано {len(hits)} раза: «{s}» – {', '.join(fmt(a) for a, _, _ in hits)}")

    # 6. звук и картинка одной длины
    nb = float(duration(cut))
    if abs(nb - total) > 0.1:
        bad.append(f"длина видео {nb:.2f} с, звука {total:.2f} с – рассинхрон")

    L = [f"# Проверка вырезки – {p.name}", "", f"Длина: {fmt(total)}. Кусочков: {len(ch)}, расшифровка {engine}.", ""]
    L += ["## ❌ Исправить"] + ([f"- ❌ {b}" for b in bad] or ["- ✅ ничего"]) + [""]
    L += ["## ⚠ Посмотреть"] + ([f"- ⚠ {w}" for w in warn] or ["- ✅ ничего"]) + [""]
    L += ["## Текст ролика по кусочкам (прочитать целиком: всё ли по смыслу, нет ли повторов)", ""]
    L += [f"{fmt(c['start'])}  {c['text']}" for c in ch]
    (p / "verify.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:L.index('## Текст ролика по кусочкам (прочитать целиком: всё ли по смыслу, нет ли повторов)')]))
    print(f"Полный отчёт: {p / 'verify.md'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
