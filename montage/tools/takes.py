"""Поиск дублей, фальстартов и повторов по кускам расшифровки. Используется в transcribe.py и verify.py.

Подсказки – не решение. Решение по каждому куску принимает Claude в cuts.json, читая кусок за куском."""
import re
from difflib import SequenceMatcher

from speech import norm

FILLER = re.compile(r"^(э+|э-э[-э]*|эм+|м+|м-м[-м]*|а-а[-а]*|аа+|хм+|ы+|у-у[-у]*)$")
LOOKAHEAD = 6   # дубль обычно перезаписывают в ближайших 6 кусках


def toks(ch):
    return [norm(w["text"]) for w in ch["words"] if not w.get("event") and norm(w["text"])]


def overlap(a, b):
    """Доля слов куска a, которые по порядку нашлись в куске b."""
    if not a or not b:
        return 0.0
    m = SequenceMatcher(None, a, b, autojunk=False)
    return sum(x.size for x in m.get_matching_blocks()) / len(a)


def stems(t):
    """Смысловые слова по первым 5 буквам: «применил» и «применить», «монтажа» и «монтаж» совпадут."""
    return {w[:5] for w in t if len(w) >= 4}


def tail_repeat(a, b):
    """Конец куска a повторяется в начале куска b: «…показать, готовит анимации» → «Готовит музыку, анимации…».
    Сравнение по первым 5 буквам и по порядку, между словами повтора могут быть вставки.
    → (номер слова в a, с которого начинается повтор, фраза) или None."""
    sa, sb = [w[:5] for w in a], [w[:5] for w in b]
    for k in (5, 4, 3, 2):
        if len(a) <= k:
            continue
        t, h = sa[-k:], sb[:k + 2]
        blocks = [x for x in SequenceMatcher(None, t, h, autojunk=False).get_matching_blocks() if x.size]
        m = sum(x.size for x in blocks)
        content = sum(1 for x in blocks for w in t[x.a:x.a + x.size] if len(w) >= 4)
        if m >= 2 and m >= k - 1 and content >= 2 and blocks[0].b <= 1:
            pos = len(a) - k + blocks[0].a
            return pos, " ".join(a[pos:])
    return None


def same_start(a, b, n=2):
    return len(a) >= n and len(b) >= n and [w[:5] for w in a[:n]] == [w[:5] for w in b[:n]]


def inner_repeats(ch):
    """Повтор подряд внутри куска без паузы: «я сделал я сделал», «это это». → [(слово-начало, время, фраза)]."""
    ws = [w for w in ch["words"] if not w.get("event")]
    t = [norm(w["text"]) for w in ws]
    out, i = [], 0
    while i < len(t):
        hit = False
        for n in (5, 4, 3, 2, 1):
            if i + 2 * n <= len(t) and t[i:i + n] == t[i + n:i + 2 * n] and all(t[i:i + n]):
                if n == 1 and len(t[i]) <= 1:   # «и и», «а а» – тоже оговорка, но отмечаем
                    pass
                out.append((ws[i]["start"], ws[i + n]["start"], " ".join(w["text"] for w in ws[i:i + 2 * n])))
                i += n
                hit = True
                break
        if not hit:
            i += 1
    return out


def hints(chunks):
    """id куска → список подсказок."""
    H = {c["id"]: [] for c in chunks}
    T = [toks(c) for c in chunks]
    spans = []   # (дубль i, его повтор j): всё между ними – скорее всего, остаток первой попытки
    for i, c in enumerate(chunks):
        a = T[i]
        found = []   # (приоритет, j, подсказка) по всем следующим кускам – берём самую сильную
        for j in range(i + 1, min(i + 1 + LOOKAHEAD, len(chunks))):
            b = T[j]
            if not a or not b:
                continue
            ov = overlap(a, b)
            r = SequenceMatcher(None, a, b, autojunk=False).ratio()
            nid = chunks[j]["id"]
            tr = tail_repeat(a, b)
            sa, sb = stems(a), stems(b)
            common = sa & sb
            if r >= 0.6 and len(a) >= 3:
                found.append((0, j, f"ДУБЛЬ: почти то же, что #{nid} ({r:.0%}) – оставить одну, обычно последнюю целую"))
            elif ov >= 0.7 and len(a) < len(b):
                found.append((1, j, f"НЕДОГОВОРЁННЫЙ ДУБЛЬ: {ov:.0%} слов повторяются в #{nid} целиком"))
            elif tr:
                k, phrase = tr
                ws = [w for w in c["words"] if not w.get("event") and norm(w["text"])]
                cut_from = ws[k]["start"] if k < len(ws) else c["end"]
                found.append((2, j, f"КОНЕЦ ПОВТОРЯЕТСЯ в #{nid}: «{phrase}» – оставить этот кусок только до {cut_from:.2f}, "
                                    f"дальше версия из #{nid}"))
            elif same_start(a, b):
                found.append((3, j, f"ФАЛЬСТАРТ: начинается как #{nid} – проверь, не перезапись ли"))
            elif len(common) >= 3 and len(common) / max(len(sa), 1) >= 0.5:
                found.append((4, j, f"ПОХОЖЕ ПО СМЫСЛУ на #{nid} (общие слова: {', '.join(sorted(common))}) – "
                                    "та же мысль другими словами? Тогда это дубль"))
        if found:
            prio, j, text = min(found)
            H[c["id"]].append(text)
            if prio in (0, 1) and j > i + 1:
                spans.append((i, j))
        for s, e, txt in inner_repeats(c):
            H[c["id"]].append(f"ПОВТОР ВНУТРИ на {s:.2f}: «{txt}» – вырезать с {s:.2f} до {e:.2f}")
        fill = [w for w in c["words"] if FILLER.match(norm(w["text"])) or w.get("event")]
        for w in fill:
            H[c["id"]].append(f"ЗВУК «{w['text']}» {w['start']:.2f}–{w['end']:.2f}")
        voiced = c["end"] - c["start"]
        if voiced >= 1.5 and len(a) / voiced < 1.4:
            H[c["id"]].append(f"МАЛО СЛОВ ({len(a)} за {voiced:.1f} с): расшифровка могла проглотить повтор – "
                              f"python3 tools/transcribe.py <проект> --split {c['id']}")
        if len(a) <= 2 and voiced < 1.2 and a:
            H[c["id"]].append("ОБРЫВОК: 1–2 слова отдельно – часто брошенное начало фразы")
        if not a:
            H[c["id"]].append("БЕЗ СЛОВ: вдох, шум или неразборчиво – обычно вырезать")
        txt = c["text"].strip()
        if a and (txt.endswith("…") or not re.search(r"[.!?]$", txt)) and i + 1 < len(chunks) and chunks[i + 1]["pause"] >= 0.9:
            H[c["id"]].append("ОБОРВАНО: фраза без конца и длинная пауза после – похоже на брошенный дубль")
    for i, j in spans:
        for k in range(i + 1, j):
            ck = chunks[k]
            if T[k] and not any(h.startswith(("ДУБЛЬ", "НЕДОГОВОРЁННЫЙ", "МЕЖДУ")) for h in H[ck["id"]]):
                H[ck["id"]].append(f"МЕЖДУ ДУБЛЕМ #{chunks[i]['id']} и его повтором #{chunks[j]['id']}: скорее всего, часть первой "
                                   f"попытки – есть ли эта мысль после #{chunks[j]['id']}? Есть – вырезать")
    return H


# ---------- сценарий ----------

def sentences(text):
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if len(norm_words(s)) >= 3]


def norm_words(s):
    return [norm(w) for w in s.split() if norm(w)]


def script_match(script, words, min_ratio=0.6):
    """Для каждого предложения сценария – где и сколько раз оно сказано. words – плоский список слов.
    → [(предложение, [(начало, конец, похожесть)])]"""
    t = [norm(w["text"]) for w in words]
    out = []
    for s in sentences(script):
        q = norm_words(s)
        n, hits, i = len(q), [], 0
        while i < len(t):
            best, bj = 0, None
            for L in (n - 2, n - 1, n, n + 1, n + 2):
                if L < 2 or i + L > len(t):
                    continue
                r = SequenceMatcher(None, q, t[i:i + L], autojunk=False).ratio()
                if r > best:
                    best, bj = r, i + L
            if best >= min_ratio and bj:
                hits.append((words[i]["start"], words[bj - 1]["end"], best))
                i = bj
            else:
                i += 1
        out.append((s, hits))
    return out
