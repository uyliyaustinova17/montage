"""Шаг 5. Заготовка плана монтажа после того, как verify.py чистый.

    python3 tools/plan.py <проект> [--style ui]

Пишет в проект:
  transcript_src.json – слова оставленных кусков (время исходника) – для субтитров и привязки сцен к словам;
  phrases.md          – текст ролика фразами со временем В РОЛИКЕ и словами-якорями – по нему пишутся сцены;
  plan.json           – заготовка плана стиля (если ещё нет): source, keep, кропы лица, пустой список сцен."""
import argparse

from common import ROOT, fmt, load, project, save

CARD_RATIO = 920 / 620     # карточка лица в стиле ui
HERO_RATIO = 1080 / 640


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--style", default="ui")
    a = ap.parse_args()
    p = project(a.project)
    keep = load(p, "keep.json")
    # слова оставленных кусков по решениям cuts.json, прижатые к границам keep: расшифровщик ставит слова
    # на 0,1–0,2 с раньше реального звука, и по одному времени первое слово куска выпадает из субтитров
    cuts = load(p, "cuts.json")
    words = []
    for c in load(p, "words.json")["chunks"]:
        d = cuts.get(c["id"])
        if d is None or (isinstance(d, dict) and "cut" in d) or (isinstance(d, str) and d.startswith("cut")):
            continue
        rng = [(c["start"], c["end"])] if d == "keep" else ([d["keep"]] if isinstance(d["keep"][0], (int, float)) else d["keep"])
        for w in c["words"]:
            if w.get("event"):
                continue
            if d != "keep" and not any(a_ - 0.2 <= w["start"] < b - 0.05 for a_, b in rng):   # часть куска
                continue
            ka, kb = min(keep, key=lambda k: max(k[0] - w["end"], w["start"] - k[1], 0))   # ближайший кусок keep
            s_, e_ = max(w["start"], ka + 0.002), min(w["end"], kb)   # +0,002: после округления не раньше начала куска
            words.append({"text": w["text"], "start": round(s_, 3), "end": round(max(e_, s_ + 0.05), 3), "k": (ka, kb)})
    # склейка разных дублей: «показать, Готовит музыку» → «показать, готовит музыку»
    words.sort(key=lambda w: w["start"])
    for prev, w in zip(words, words[1:]):
        t = w["text"]
        if prev["k"] == w["k"]:   # внутри одного куска регистр не трогаем
            continue
        if not prev["text"].rstrip().endswith((".", "!", "?", "…")) and len(t) > 1 and "А" <= t[0] <= "Я" and not t[1].isupper():
            w["text"] = t[0].lower() + t[1:]
        if prev["text"].endswith(("...", "…")):   # обрыв дубля «сервиса...» → «сервиса,»
            prev["text"] = prev["text"].rstrip(".…") + ","
    for w in words:
        w.pop("k")
    save(p, "transcript_src.json", {"words": words})

    # слова в ролике: время после склейки
    res, off = [], 0.0
    for a_, b in keep:
        for w in words:
            if a_ <= w["start"] < b:
                res.append((round(w["start"] - a_ + off, 2), w["text"]))
        off += b - a_
    L = [f"# Фразы ролика – {p.name}", "", f"Длина ролика {fmt(off)}. Время – в готовом ролике. Якорь сцены – слово, "
         "на котором она начинается (\"слово\" или \"слово#2\" для второго вхождения от начала сцены).", ""]
    cur, t0 = [], None
    for t, w in res:
        if t0 is None:
            t0 = t
        cur.append(w)
        if w.rstrip().endswith((".", "!", "?", "…")) or len(cur) >= 12:
            L.append(f"- {fmt(t0)}  {' '.join(cur)}")
            cur, t0 = [], None
    if cur:
        L.append(f"- {fmt(t0)}  {' '.join(cur)}")
    (p / "phrases.md").write_text("\n".join(L), encoding="utf-8")

    plan = p / "plan.json"
    if not plan.exists():
        pr = load(p, "project.json")
        W, H = pr["width"], pr["height"]
        ch = round(W / CARD_RATIO)
        hh = round(W / HERO_RATIO)
        save(p, "plan.json", {
            "title": p.name, "style": a.style, "source": "source.mp4", "transcript": "transcript_src.json",
            "keep_file": "keep.json",
            "card_crop": [0, max(0, round(H * 0.28)), W, ch],
            "hero_crop": [0, max(0, round(H * 0.30)), W, hh],
            "_crop_note": "ЗАГОТОВКА. Подобрать по frames.py --grid и проверить frames.py --crop: лицо крупно, глаза на 35–40% высоты кропа",
            "fix": {}, "out": "final.mp4", "jobs": 6, "scenes": []})
    print(f"Готово: {p / 'phrases.md'}, {p / 'plan.json'}. Длина ролика {fmt(off)}.")
    print(f"Правила стиля: {ROOT / 'styles' / a.style / 'STYLE.md'}")


if __name__ == "__main__":
    main()
