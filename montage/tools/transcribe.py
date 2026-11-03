"""Шаг 2. Расшифровка по кускам и подсказки по дублям.

    python3 tools/transcribe.py <проект>                 – нарезать по паузам, расшифровать каждый кусок отдельно
    python3 tools/transcribe.py <проект> --split 12      – кусок 12 подозрительный: разрезать мельче и перерасшифровать
    python3 tools/transcribe.py <проект> --engine groq   – выбрать расшифровщик (eleven | groq)
    python3 tools/transcribe.py <проект> --second        – вторая расшифровка другим движком, расхождения в takes.md

Выход в папке проекта:
  words.json – куски со словами и временем исходника;
  takes.md   – все куски по порядку с подсказками: ДУБЛЬ, ФАЛЬСТАРТ, ПОВТОР ВНУТРИ, МАЛО СЛОВ…;
  cuts.json  – заготовка решений: по каждому куску null, Claude заполняет каждый (cut.py не примет пустые)."""
import argparse
import json
import sys

import numpy as np

from common import audio, fmt, load, project, save
from speech import ENGINES, HOP, build, chunks, islands, loudness, pick, split_long, threshold, transcribe
from takes import hints, script_match, toks

SPLIT = 0.3     # пауза, на которой кусок обязательно разрывается, с
MAX_LEN = 7.0   # кусок длиннее режется по самой длинной паузе внутри


def write_takes(p, data):
    ch = data["chunks"]
    H = hints(ch)
    second = data.get("second", {})
    L = [f"# Куски речи – {p.name}", "",
         f"Расшифровка: {data['engine']}, кусков: {len(ch)}, длина исходника {fmt(data['duration'])}.", "",
         "Как работать: читать КАЖДЫЙ кусок сверху вниз и записать решение по нему в cuts.json.",
         "Дубль – оставить последнюю ЦЕЛУЮ и чистую версию, все прошлые вырезать. Подсказки ⚠ – повод проверить, а не приговор.",
         "Нужное не резать: кусок, который нигде дальше не повторяется, – это не дубль.", ""]
    for c in ch:
        L.append(f"## #{c['id']}  {fmt(c['start'])}–{fmt(c['end'])}  ({c['end'] - c['start']:.1f} с, пауза до {c['pause']:.2f} с)")
        L.append(c["text"] or "(без слов)")
        if c["id"] in second and second[c["id"]]["diff"]:
            L.append(f"⚠ ВТОРАЯ РАСШИФРОВКА ДРУГАЯ: «{second[c['id']]['text']}» – разрезать мельче: python3 tools/transcribe.py {p.name} --split {c['id']}")
        for h in H[c["id"]]:
            L.append(f"⚠ {h}")
        L.append("слова: " + " · ".join(f"{w['text']} {w['start']:.2f}" for w in c["words"]))
        L.append("")
    script = p / "script.txt"
    if script.exists():
        flat = [dict(w, chunk=c["id"]) for c in ch for w in c["words"] if not w.get("event")]
        L += ["# Сценарий → где сказано", "",
              "Каждое предложение сценария должно остаться в ролике ровно один раз. Если сказано 2+ раз – это дубли, оставить последнее чистое.", ""]
        for s, hits in script_match(script.read_text(encoding="utf-8"), flat):
            where = []
            for a, b, r in hits:
                ids = sorted({w["chunk"] for w in flat if a <= w["start"] <= b}, key=lambda x: [float(t) for t in x.split(".")])
                where.append(f"#{','.join(ids)} ({fmt(a)}, {r:.0%})")
            mark = "❌ НЕ СКАЗАНО" if not hits else ("⚠ ДУБЛИ" if len(hits) > 1 else "✅")
            L.append(f"- {mark} «{s}» → {'; '.join(where) or '—'}")
        L.append("")
    (p / "takes.md").write_text("\n".join(L), encoding="utf-8")
    cuts = p / "cuts.json"
    old = json.loads(cuts.read_text(encoding="utf-8")) if cuts.exists() else {}
    save(p, "cuts.json", {c["id"]: old.get(c["id"]) for c in ch})
    n = sum(1 for c in ch if H[c["id"]])
    print(f"Кусков: {len(ch)}, с подсказками: {n}. Читать {p / 'takes.md'}, решения – в {p / 'cuts.json'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--engine", choices=list(ENGINES))
    ap.add_argument("--split", help="id куска, который разрезать мельче")
    ap.add_argument("--second", action="store_true")
    args = ap.parse_args()
    p = project(args.project)
    src = p / "source.mp4"
    cache = p / "cache"

    if args.split:
        data = load(p, "words.json")
        ch = data["chunks"]
        k = next((i for i, c in enumerate(ch) if c["id"] == args.split), None)
        if k is None:
            sys.exit(f"Нет куска #{args.split}")
        c = ch[k]
        x = audio(src)
        db = loudness(x)
        thr = threshold(db)
        i0, i1 = int(c["start"] / HOP), int(c["end"] / HOP)
        sub = [(round(c["start"] + a, 3), round(c["start"] + b, 3)) for a, b in islands(db[i0:i1], thr, 0.12, 0.05)]
        sub = [s for seg in sub for s in split_long(seg, db, thr, 2.5)]
        if len(sub) < 2:   # пауз нет – режем каждые ~1,5 с в самом тихом месте между словами
            cuts_ = [c["start"]]
            while c["end"] - cuts_[-1] > 2.2:
                t = cuts_[-1] + 1.5
                j0, j1 = int((t - 0.4) / HOP), int((t + 0.4) / HOP)
                cuts_.append(round((j0 + int(np.argmin(db[j0:j1]))) * HOP, 3))
            cuts_.append(c["end"])
            sub = [(a, b) for a, b in zip(cuts_, cuts_[1:])]
        new = build(src, sub, cache, data["engine"], ids=[f"{c['id']}.{i + 1}" for i in range(len(sub))])
        new[0]["pause"] = c["pause"]
        data["chunks"] = ch[:k] + new + ch[k + 1:]
        save(p, "words.json", data)
        print(f"#{c['id']} → {len(new)} кусков:")
        for n_ in new:
            print(f"  #{n_['id']} {n_['start']:.2f}–{n_['end']:.2f}: {n_['text']}")
        write_takes(p, data)
        return

    engine = pick(args.engine)
    x = audio(src)
    segs, db, thr = chunks(x, SPLIT, MAX_LEN)
    print(f"Нарезано по паузам: {len(segs)} кусков. Расшифровываю каждый отдельно ({engine})…")
    data = {"engine": engine, "duration": len(x) / 16000, "chunks": build(src, segs, cache, engine)}
    if args.second:
        other = next((e for e in ENGINES if e != engine and ENGINES[e][0]()), None)
        if not other:
            print("Второго ключа нет – вторая расшифровка пропущена")
        else:
            res = transcribe(src, segs, cache, other)
            data["second"] = {}
            for c, words in zip(data["chunks"], res):
                t2 = [w["text"] for w in (words or []) if not w.get("event")]
                a, b = toks(c), [x_.lower() for x_ in t2]
                data["second"][c["id"]] = {"text": " ".join(t2), "diff": abs(len(a) - len(b)) >= max(2, len(a) * 0.25)}
    save(p, "words.json", data)
    write_takes(p, data)


if __name__ == "__main__":
    main()
