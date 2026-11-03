"""Поиск новых блогеров в твоей нише.
Разбирает профили твоих блогеров (описание, подписи, суть залётов) → нейросеть по ним и паспорту
находит общее и придумывает поисковые запросы → поиск Instagram →
у самых заметных авторов смотрим подписчиков → нейросеть оценивает каждого 0–10 по паспорту.
Найденные ждут твоего решения на сайте (вкладка «Блогеры») или командой --add.
    python discover.py           – найти (5–10 минут)
    python discover.py --add 7   – добавить в разведку всех с оценкой 7 и выше"""
import config  # первым: переключает на окружение .venv
import json
import random
import sys
import time
from urllib.parse import quote

from patchright.sync_api import sync_playwright

from lib import db, instagram, llm
from lib.common import launch, log, logged_in, now

MAX_QUERIES = 8
MAX_SEEDS = 6           # сколько профилей-образцов разбирать
MAX_PROFILES = 25
MIN_FOLLOWERS, MAX_FOLLOWERS = 5_000, 3_000_000

QUERIES_PROMPT = """Паспорт блога автора:
<passport>
{passport}
</passport>

Блогеры, которых автор дал как образец, – их профили (описание, подписи к роликам, суть залетевших роликов):
<профили>
{profiles}
</профили>

1. Разбери профили: что у этих блогеров общего – тема, аудитория, формат роликов, подача. 2–4 предложения.
2. Придумай {n} коротких поисковых запросов для поиска Instagram (2–4 слова), по которым найдутся ещё такие же
   авторы под паспорт: половина по-русски, половина по-английски. Запросы – темы роликов, которые снимают такие
   авторы, а не названия профессий.
Ответь только JSON-объектом: {{"common": "что общего", "queries": ["...", "..."]}}"""

RATE_PROMPT = """Паспорт блога:
<passport>
{passport}
</passport>
Блогеры, которых автор дал как образец: {seeds}
Что у них общего: {common}

Ниже авторы, найденные поиском Instagram, с подписями их публикаций. Оцени каждого 0–10: насколько его ролики
годятся как источник идей для этого блога.
10–8: та же ниша или та же аудитория, регулярно снимает рилсы. 7–5: соседняя тема, приёмы переносятся.
4–0: не про это, магазин, развлечения, новости.
Ответь только JSON-массивом: [{{"username": "...", "score": число, "why": "одна фраза"}}]

<авторы>
{items}
</авторы>"""


def walk(x, found):
    """В любом ответе Instagram ищем публикации: есть code и автор."""
    if isinstance(x, dict):
        u = x.get("user") or x.get("owner")
        if x.get("code") and isinstance(u, dict) and u.get("username"):
            cap = x.get("caption")
            found.append({"username": u["username"].lower(), "plays": x.get("play_count") or x.get("view_count") or 0,
                          "likes": x.get("like_count") or 0,
                          "caption": ((cap or {}).get("text") or "")[:300] if isinstance(cap, dict) else ""})
        for v in x.values():
            walk(v, found)
    elif isinstance(x, list):
        for v in x:
            walk(v, found)


def seed_profile(page, username):
    """Профиль блогера-образца: описание, подписчики, подписи к последним публикациям."""
    info = {"bio": "", "captions": []}

    def on_resp(r):
        if "instagram.com" not in r.url or r.request.resource_type not in ("xhr", "fetch"):
            return
        try:
            data = json.loads(r.text())
        except Exception:
            return
        posts = []
        walk(data, posts)
        info["captions"] += [p["caption"].replace("\n", " ") for p in posts if p["username"] == username and p["caption"]]
        stack = [data]
        while stack:  # описание профиля лежит в поле biography у объекта пользователя
            x = stack.pop()
            if isinstance(x, dict):
                if x.get("username", "").lower() == username and x.get("biography"):
                    info["bio"] = x["biography"]
                stack += list(x.values())
            elif isinstance(x, list):
                stack += x

    page.on("response", on_resp)
    try:
        page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")
        time.sleep(random.uniform(5, 8))
        if any(x in page.url for x in ("/accounts/login", "/challenge")):
            raise instagram.Blocked(page.url)
        info["followers"] = instagram.followers(page)
        page.mouse.wheel(0, 1500)
        time.sleep(random.uniform(2, 4))
    finally:
        page.remove_listener("response", on_resp)
    info["captions"] = list(dict.fromkeys(info["captions"]))[:8]
    return info


def search(page, query):
    found = []

    def on_resp(r):
        if "instagram.com" not in r.url or r.request.resource_type not in ("xhr", "fetch"):
            return
        try:
            walk(json.loads(r.text()), found)
        except Exception:
            pass

    page.on("response", on_resp)
    try:
        page.goto("https://www.instagram.com/explore/search/keyword/?q=" + quote(query))
        time.sleep(random.uniform(5, 8))
        if any(x in page.url for x in ("/accounts/login", "/challenge")):
            raise instagram.Blocked(page.url)
        for _ in range(3):
            page.mouse.wheel(0, 2500)
            time.sleep(random.uniform(2.5, 4.5))
    finally:
        page.remove_listener("response", on_resp)
    return found


def add(min_score):
    con = db.connect()
    rows = con.execute("SELECT username FROM bloggers WHERE status='proposed' AND score>=?", (min_score,)).fetchall()
    for r in rows:
        db.add_to_list(r["username"])
        con.execute("UPDATE bloggers SET status='active' WHERE username=?", (r["username"],))
    con.commit()
    log(f"Добавлено в разведку: {len(rows)}")


def main():
    passport = (config.KNOWLEDGE / "passport.md")
    if not passport.exists():
        sys.exit("Нет паспорта блога: сначала python passport.py")
    passport = passport.read_text(encoding="utf-8")
    focus = config.KNOWLEDGE / "focus.md"
    if focus.exists():
        passport += "\n\nУТОЧНЕНИЕ ДЛЯ ПОИСКА (важнее остального):\n" + focus.read_text(encoding="utf-8")
    con = db.connect()
    seeds = db.active_bloggers(con)
    known = {r["username"] for r in con.execute("SELECT username FROM bloggers")}
    cands = {}
    with sync_playwright() as pw:
        ctx = launch(pw, headless=True)
        try:
            if not logged_in(ctx):
                sys.exit("Нет входа в Instagram: python login.py")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            # 1. разбираем профили блогеров-образцов
            profiles = []
            for u in seeds[:MAX_SEEDS]:
                try:
                    pi = seed_profile(page, u)
                except instagram.Blocked as e:
                    sys.exit(f"Instagram просит проверку: {e}. Запусти python login.py")
                except Exception as e:
                    log(f"@{u}: профиль не открылся – {str(e)[:120]}")
                    continue
                ess = [r["essence"] for r in con.execute(
                    "SELECT essence FROM reels WHERE blogger=? AND essence IS NOT NULL ORDER BY multiplier DESC LIMIT 3", (u,))]
                profiles.append(f"@{u}, подписчиков {pi.get('followers') or '?'}\nописание: {pi['bio'] or '–'}\n"
                                + "".join(f"– подпись: {c[:200]}\n" for c in pi["captions"])
                                + "".join(f"– суть залёта: {e}\n" for e in ess))
                log(f"Профиль @{u}: описание {'есть' if pi['bio'] else 'нет'}, подписей {len(pi['captions'])}, залётов в банке {len(ess)}")
                time.sleep(random.uniform(4, 8))
            # 2. нейросеть: что у них общего и как искать таких же
            plan = llm.ask_json(QUERIES_PROMPT.format(passport=passport, profiles="\n".join(profiles) or "нет – ищи по паспорту",
                                                      n=MAX_QUERIES), model=config.SCORE_MODEL, timeout=300)
            common = plan.get("common", "") if isinstance(plan, dict) else ""
            queries = [q for q in (plan.get("queries", []) if isinstance(plan, dict) else plan) if isinstance(q, str)][:MAX_QUERIES]
            log("Что общего у твоих блогеров: " + common)
            log("Запросы: " + " · ".join(queries))
            # 3. поиск похожих авторов
            for q in queries:
                try:
                    posts = search(page, q)
                except instagram.Blocked as e:
                    log(f"Instagram просит проверку, поиск остановлен: {e}")
                    break
                for p in posts:
                    if p["username"] in known:
                        continue
                    c = cands.setdefault(p["username"], {"best": 0, "hits": 0, "queries": set(), "captions": []})
                    c["best"] = max(c["best"], p["plays"], p["likes"] * 30)
                    c["hits"] += 1
                    c["queries"].add(q)
                    if p["caption"] and len(c["captions"]) < 3:
                        c["captions"].append(p["caption"].replace("\n", " "))
                log(f"«{q}»: {len(posts)} публикаций, новых авторов всего {len(cands)}")
                time.sleep(random.uniform(5, 10))
            top = sorted(cands.items(), key=lambda kv: (kv[1]["hits"], kv[1]["best"]), reverse=True)[:MAX_PROFILES]
            for u, c in top:
                try:
                    page.goto(f"https://www.instagram.com/{u}/", wait_until="domcontentloaded")
                    time.sleep(random.uniform(5, 9))
                    c["followers"] = instagram.followers(page)
                except Exception:
                    c["followers"] = None
        finally:
            try:
                ctx.close()
            except Exception as e:
                log(f"браузер закрылся с ошибкой (не страшно): {str(e)[:100]}")
    top = [(u, c) for u, c in top if MIN_FOLLOWERS <= (c.get("followers") or 0) <= MAX_FOLLOWERS]
    if not top:
        log("Подходящих авторов не нашлось. Попробуй ещё раз или добавь блогеров руками.")
        return
    items = "\n\n".join(f"@{u}, {c['followers']} подписчиков, запросы: {', '.join(sorted(c['queries']))}\n"
                        + "\n".join("– " + x for x in c["captions"]) for u, c in top)
    res = llm.ask_json(RATE_PROMPT.format(passport=passport, seeds=", ".join("@" + s for s in seeds) or "нет",
                                          common=common or "–", items=items), model=config.SCORE_MODEL, timeout=300)
    by = {x["username"].lstrip("@").lower(): x for x in res if isinstance(x, dict) and x.get("username")}
    log("\nНайдены (оценка · подписчики · почему):")
    for u, c in sorted(top, key=lambda t: -int((by.get(t[0]) or {}).get("score") or 0)):
        x = by.get(u, {})
        sc = int(x.get("score") or 0)
        con.execute("INSERT OR IGNORE INTO bloggers(username, followers, status, score, why, source, added) "
                    "VALUES (?,?,?,?,?,?,?)", (u, c["followers"], "proposed", sc, x.get("why"),
                                               "поиск: " + sorted(c["queries"])[0], now()))
        fol = f"{c['followers']:>9,}".replace(",", " ")
        log(f"  {sc:>2} · {fol} · @{u} – {x.get('why', '')}")
    con.commit()
    log("\nДобавить лучших: python discover.py --add 7  (или кнопками на сайте)")


if __name__ == "__main__":
    if "--add" in sys.argv:
        i = sys.argv.index("--add")
        add(int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 7)
    else:
        main()
