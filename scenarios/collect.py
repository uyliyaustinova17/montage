"""Шаг 1. Разведка: смотрит блогеров из bloggers.txt и находит залёты (просмотры ≥ 2 × медиана блогера).
    python collect.py            – все блогеры (не больше 30 за запуск)
    python collect.py ник1 ник2  – только эти"""
import config  # первым: переключает на окружение .venv
import random
import statistics
import sys
import time
from datetime import datetime, timedelta

from patchright.sync_api import sync_playwright

from lib import db, instagram, tg
from lib.common import launch, log, logged_in, now


def save(con, username, followers, reels):
    cutoff = datetime.now() - timedelta(days=config.MIN_AGE_DAYS)
    aged = [r for r in reels if not r["pinned"] and r["views"] and datetime.fromisoformat(r["posted_at"]) <= cutoff]
    median = statistics.median(r["views"] for r in aged) if len(aged) >= config.MIN_REELS_FOR_MEDIAN else None
    con.execute("INSERT INTO bloggers(username, followers, median_views, reels_counted, last_scan, added) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET followers=COALESCE(excluded.followers, followers), "
                "median_views=excluded.median_views, reels_counted=excluded.reels_counted, last_scan=excluded.last_scan",
                (username, followers, median, len(aged), now(), now()))
    found = []
    for r in reels:
        mult = round(r["views"] / median, 1) if median and r["views"] else None
        is_out = bool(mult and mult >= config.OUTLIER_X and r in aged)
        cur = con.execute("SELECT state FROM reels WHERE id=?", (r["id"],)).fetchone()
        if cur is None:
            con.execute("INSERT INTO reels(id, blogger, views, likes, comments, posted_at, multiplier, state, first_seen,"
                        " updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (r["id"], username, r["views"], r["likes"], r["comments"], r["posted_at"], mult,
                         "outlier" if is_out else "new", now(), now()))
        else:
            con.execute("UPDATE reels SET views=?, likes=?, comments=?, multiplier=?, updated=?,"
                        " state=CASE WHEN state='new' AND ? THEN 'outlier' ELSE state END WHERE id=?",
                        (r["views"], r["likes"], r["comments"], mult, now(), int(is_out), r["id"]))
        if is_out:
            found.append((r, mult))
    con.commit()
    med = f"{int(median):,}".replace(",", " ") if median else "мало роликов"
    log(f"@{username}: {len(reels)} роликов, обычно {med} просмотров, залётов {len(found)}")
    return found


def save_cookies(ctx):
    """Вход Instagram в формате для yt-dlp: им скачиваются только ролики, которые без входа не открываются."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in ctx.cookies("https://www.instagram.com"):
        dom = c["domain"]
        lines.append("\t".join([dom, "TRUE" if dom.startswith(".") else "FALSE", c["path"],
                                "TRUE" if c["secure"] else "FALSE", str(int(max(c.get("expires") or 0, 0))),
                                c["name"], c["value"]]))
    config.COOKIES.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(only=None):
    con = db.connect()
    names = only or db.active_bloggers(con)
    if not names:
        log("bloggers.txt пустой: впиши ники блогеров, по одному в строке")
        return []
    # сначала те, кого давно не смотрели
    seen = {r["username"]: r["last_scan"] or "" for r in con.execute("SELECT username, last_scan FROM bloggers")}
    names = sorted(names, key=lambda n: seen.get(n, ""))[:config.MAX_BLOGGERS_PER_RUN]
    found = []
    with sync_playwright() as pw:
        ctx = launch(pw, headless=True)
        try:
            if not logged_in(ctx):
                msg = "Нет входа в Instagram. Запусти: python login.py"
                log(msg)
                tg.notify(msg)
                return []
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for i, u in enumerate(names):
                if i:
                    time.sleep(random.uniform(*config.PAUSE_BETWEEN_BLOGGERS))
                try:
                    fol, reels = instagram.scan(page, u, want=config.REELS_PER_BLOGGER)
                except instagram.Blocked as e:
                    msg = f"Instagram просит вход или проверку ({e}). Сбор остановлен. Запусти: python login.py"
                    log(msg)
                    tg.notify(msg)
                    break
                except Exception as e:
                    log(f"@{u}: ошибка {type(e).__name__}: {str(e)[:200]}")
                    continue
                if not reels:
                    log(f"@{u}: роликов не видно (закрытый профиль или неверный ник)")
                    continue
                found += [(u, r, m) for r, m in save(con, u, fol, reels)]
            save_cookies(ctx)
        finally:
            ctx.close()
    if found:
        log("\nЗалёты:")
        for u, r, m in sorted(found, key=lambda x: -x[2]):
            views = f"{r['views']:>10,}".replace(",", " ")
            log(f"  ×{m:<5} {views} просм.  @{u:<22} {r['posted_at'][:10]}  https://www.instagram.com/reel/{r['id']}/")
    return found


if __name__ == "__main__":
    main(sys.argv[1:] or None)
