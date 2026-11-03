"""Шаг 3. Сценарии: оценка залётов пачкой → сценарии параллельно → проверка → Telegram.
    python write.py        – до 5 сценариев
    python write.py 3      – до 3 сценариев
    python write.py --id ССЫЛКА – по конкретному ролику: из банка или любому по ссылке"""
import config  # первым: переключает на окружение .venv
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from lib import db, llm, tg
from lib.common import log, now


def kb(name):
    p = config.KNOWLEDGE / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


SCORE_PROMPT = """Ты отбираешь чужие залетевшие рилсы как идеи для блога автора. Профиль автора:
<profile>
{profile}
</profile>

Для каждого ролика верни объект:
- "id": как во входе
- "essence": 2–3 строки по-русски – на чём держится ролик (приём: история, метафора, контраст, список, ограничение, вопрос), а не пересказ
- "relevance": 0–10 – насколько этот приём можно перенести на тему и зрителя автора. 8–10: прямо про боли его зрителя или легко переносится; 5–7: соседняя тема, переносится с усилием; 0–4: не переносится, развлечение, реклама
- "relevance_why": одна фраза
- "content_type": "экспертный" | "личный" | "продающий" | "развлекательный"

Ответь только JSON-массивом.

<ролики>
{items}
</ролики>"""

WRITE_PROMPT = """Ты пишешь сценарий рилса для автора по сути чужого залетевшего ролика.

Правила сценария:
<rules>
{rules}
</rules>

Кто автор и его зритель:
<profile>
{profile}
</profile>

Как автор говорит (пиши его словами):
<style>
{style}
</style>

Проверка, которую сценарий должен пройти:
<check>
{check}
</check>

Оригинал (@{blogger}, {views} просмотров, ×{mult} к обычным у этого автора):
<original>
{transcript}
</original>
Подпись к оригиналу: {caption}
Суть по оценке: {essence}

Порядок:
1. Разбери оригинал: какую боль задел, чем зацепил в первые 3 секунды, на чём держится.
2. Перенеси этот приём на тему и зрителя автора. Текст, примеры и хук – свои.
3. Ни одной выдуманной цифры, кейса или цитаты. Нужен факт, которого нет в профиле, – пиши [уточнить: что].
{feedback}
Ответь только JSON-объектом:
{{
  "analysis": {{"боль": "...", "хук оригинала": "...", "на чём держится": "...", "как переносим": "..."}},
  "thought": "мысль ролика одним предложением",
  "content_type": "экспертный|личный|продающий",
  "rubric": "рубрика из правил",
  "duration_sec": число,
  "rows": [{{"say": "что говорит автор", "show": "что на экране"}}],
  "tools": [номера приёмов удержания из правил],
  "caption": "подпись к рилсу"
}}"""

CHECK_PROMPT = """Ты строгий редактор рилсов. Проверь сценарий по чек-листу и правилам. Ищешь, почему ролик НЕ залетит. Похвала не нужна.

<check>
{check}
</check>
<rules>
{rules}
</rules>
<profile>
{profile}
</profile>
<style>
{style}
</style>

Оригинал: <original>{transcript}</original>
Сценарий (JSON): <script>{script}</script>
Совпадение формулировок с оригиналом (посчитано программой): {overlap}%

FAIL ставь только за критичное – то, из-за чего ролик точно не сработает:
- хук про автора, а не про зрителя, или узкий и непонятный;
- выдуманная цифра, кейс, цитата или история автора, которых нет в паспорте ([уточнить] – это не выдумка);
- нет структуры (хук → причина досмотреть → основная часть → вывод → призыв) или основная часть пустая;
- копия оригинала или совпадение формулировок больше 20%;
- противоречит паспорту (боли зрителя, «не говорим никогда»).
Всё остальное – замечания, они не валят сценарий.

Ответь только JSON-объектом:
{{"verdict": "PASS|FAIL", "tools_used": [номера], "fails": ["только критичное: цитата → что не так → как исправить"],
  "notes": ["замечания: цитата → как улучшить"]}}"""


def overlap_pct(a, b):
    """Доля 4-словных сочетаний сценария, которые есть в оригинале."""
    words = lambda s: re.findall(r"\w+", (s or "").lower())
    grams = lambda ws: {tuple(ws[i:i + 4]) for i in range(len(ws) - 3)}
    x, y = grams(words(a)), grams(words(b))
    return round(100 * len(x & y) / len(x), 1) if x else 0.0


def score(con, profile):
    done = 0
    while True:
        rows = con.execute("SELECT id, blogger, views, multiplier, caption, transcript FROM reels "
                           "WHERE state='transcribed' LIMIT ?", (config.SCORE_BATCH,)).fetchall()
        if not rows:
            return done
        items = "\n\n".join(f'id: {r["id"]}\nблогер: @{r["blogger"]}, {r["views"]} просмотров, ×{r["multiplier"]}\n'
                            f'подпись: {(r["caption"] or "")[:400]}\nрасшифровка: {(r["transcript"] or "")[:2500]}'
                            for r in rows)
        res = llm.ask_json(SCORE_PROMPT.format(profile=profile, items=items), model=config.SCORE_MODEL, timeout=300)
        by_id = {x.get("id"): x for x in res if isinstance(x, dict)} if isinstance(res, list) else {}
        for r in rows:
            x = by_id.get(r["id"])
            if not x:
                con.execute("UPDATE reels SET state='skipped', error='нет оценки' WHERE id=?", (r["id"],))
                continue
            rel = int(x.get("relevance") or 0)
            state = "scored" if rel >= config.MIN_RELEVANCE and x.get("content_type") != "развлекательный" else "skipped"
            con.execute("UPDATE reels SET essence=?, relevance=?, relevance_why=?, content_type=?, state=?, updated=? "
                        "WHERE id=?", (x.get("essence"), rel, x.get("relevance_why"), x.get("content_type"), state,
                                       now(), r["id"]))
            done += 1
        con.commit()
        ids = [r["id"] for r in rows]
        good = con.execute(f"SELECT COUNT(*) FROM reels WHERE state='scored' AND id IN ({','.join('?' * len(ids))})",
                           ids).fetchone()[0]
        log(f"Оценено {len(rows)}: в банк идей {good}")


def write_one(reel, ctx):
    feedback, result = "", None
    for n in range(1, config.ATTEMPTS + 1):
        body = llm.ask_json(WRITE_PROMPT.format(
            rules=ctx["rules"], profile=ctx["profile"], style=ctx["style"], check=ctx["check"], blogger=reel["blogger"],
            views=reel["views"], mult=reel["multiplier"], transcript=reel["transcript"],
            caption=(reel["caption"] or "")[:500], essence=reel["essence"], feedback=feedback), model=config.WRITE_MODEL)
        say = " ".join(r.get("say", "") for r in body.get("rows", []))
        ov = overlap_pct(say, reel["transcript"])
        chk = llm.ask_json(CHECK_PROMPT.format(
            check=ctx["check"], rules=ctx["rules"], profile=ctx["profile"], style=ctx["style"],
            transcript=reel["transcript"], script=json.dumps(body, ensure_ascii=False), overlap=ov),
            model=config.WRITE_MODEL)
        if ov > config.MAX_OVERLAP:
            chk["verdict"] = "FAIL"
            chk.setdefault("fails", []).append(f"Совпадение с оригиналом {ov}% – переписать своими словами")
        result = {"body": body, "check": chk, "overlap": ov, "attempt": n}
        if chk.get("verdict") == "PASS":
            break
        feedback = ("\nПредыдущая версия НЕ прошла проверку. Исправь всё из списка:\n- " + "\n- ".join(chk.get("fails") or [])
                    + "\nПредыдущая версия:\n" + json.dumps(body, ensure_ascii=False))
    return result


def render(reel, res):
    b, chk = res["body"], res["check"]
    url = f"https://www.instagram.com/reel/{reel['id']}/"
    lines = [f"🎬 {b.get('thought', '')}", "",
             f"Тип: {b.get('content_type', '')} · {b.get('rubric', '')} · ~{b.get('duration_sec', '?')} с", ""]
    for i, r in enumerate(b.get("rows", []), 1):
        lines += [f"{i}. 🎙 {r.get('say', '')}", f"   📺 {r.get('show', '')}"]
    lines += ["", "Подпись:", b.get("caption", ""), "",
              f"Оригинал: {url}"]
    if reel.get("views"):
        lines.append(f"@{reel['blogger']} · {reel['views']:,} просм. · ×{reel['multiplier']} к обычному".replace(",", " "))
    if chk.get("verdict") != "PASS":
        lines += ["", "⚠️ Не прошёл проверку:"] + [f"- {f}" for f in chk.get("fails", [])]
    if chk.get("notes"):
        lines += ["", "Что можно улучшить:"] + [f"- {f}" for f in chk["notes"][:4]]
    return "\n".join(lines)


def main(limit=config.SCRIPTS_PER_RUN, reel_id=None):
    con = db.connect()
    ctx = {"rules": kb("rules.md"), "check": kb("check.md"), "profile": kb("passport.md"), "style": kb("style.md")}
    if not ctx["profile"]:
        log("Нет knowledge/passport.md – сначала сделай паспорт: python passport.py")
        return 0
    if reel_id:  # сценарий по конкретному ролику (кнопка на сайте или любая ссылка): расшифровать, если ещё нет
        m = re.search(r"/(?:reel|reels|p)/([A-Za-z0-9_-]+)", reel_id)
        reel_id = m[1] if m else reel_id.strip()
        r = con.execute("SELECT state FROM reels WHERE id=?", (reel_id,)).fetchone()
        if not r:  # чужой ролик не из разведки – добавляем в банк
            con.execute("INSERT INTO reels(id, blogger, state, first_seen, updated) VALUES (?, 'ссылка', 'outlier', ?, ?)",
                        (reel_id, now(), now()))
            con.commit()
            r = {"state": "outlier"}
        if r["state"] in ("new", "outlier", "skipped"):
            import transcribe
            con.execute("UPDATE reels SET state='outlier' WHERE id=?", (reel_id,))
            con.commit()
            transcribe.main(ids=[reel_id])
    score(con, ctx["profile"])
    if reel_id:
        reels = [dict(r) for r in con.execute("SELECT * FROM reels WHERE id=? AND transcript IS NOT NULL", (reel_id,))]
    else:
        reels = [dict(r) for r in con.execute(
            "SELECT * FROM reels WHERE state='scored' ORDER BY posted_at >= date('now', ?) DESC, relevance DESC, "
            "multiplier DESC LIMIT ?", (f"-{config.FRESH_DAYS} days", limit))]
    if not reels:
        msg = ("Сегодня новых сценариев нет: среди свежих залётов не нашлось подходящих твоему блогу. "
               "Добавь блогеров (сайт → «Найти блогеров») или напиши сценарий по любой идее из банка.")
        log(msg)
        if not reel_id:
            tg.send(msg)
        return 0
    log(f"Пишу сценарии: {len(reels)}, одновременно {config.PARALLEL}")
    sent = done = 0
    with ThreadPoolExecutor(config.PARALLEL) as ex:
        futs = {ex.submit(write_one, r, ctx): r for r in reels}
        for f in as_completed(futs):
            reel = futs[f]
            try:
                res = f.result()
            except Exception as e:
                log(f"{reel['id']}: сценарий не написан – {str(e)[:200]}")
                con.execute("UPDATE reels SET error=? WHERE id=?", (str(e)[:300], reel["id"]))
                con.commit()
                continue
            text = render(reel, res)
            path = config.SCRIPTS / f"{datetime.now():%Y-%m-%d}_{reel['id']}.md"
            path.write_text(text + "\n\n<!--\n" + json.dumps(res, ensure_ascii=False, indent=1) + "\n-->\n",
                            encoding="utf-8")
            verdict = res["check"].get("verdict")
            con.execute("UPDATE reels SET state='scripted', script_file=?, verdict=?, updated=? WHERE id=?",
                        (path.name, verdict, now(), reel["id"]))
            con.commit()
            ok = tg.send(text)
            sent += ok
            done += 1
            log(f"{reel['id']}: {verdict}, попытка {res['attempt']}, совпадение {res['overlap']}%"
                f"{', отправлен в Telegram' if ok else ''}")
    log(f"Готово сценариев: {done}, отправлено в Telegram: {sent}")
    return done


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--id":
        main(reel_id=sys.argv[2])
    else:
        main(int(sys.argv[1]) if len(sys.argv) > 1 else config.SCRIPTS_PER_RUN)
