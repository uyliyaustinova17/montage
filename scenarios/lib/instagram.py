"""Лента Reels блогера. Цифры берутся из ответов, которые страница Instagram получает сама
(clips_connection: просмотры, лайки, комментарии, закреп) – своих запросов к API нет."""
import json
import random
import re
import time
from datetime import datetime

IG_EPOCH_MS = 1314220021721  # pk >> 23 = мс от этой точки → дата публикации


class Blocked(Exception):
    """Instagram просит вход или проверку – сбор останавливаем, аккаунт бережём."""


def posted_at(pk):
    return datetime.fromtimestamp(((int(pk) >> 23) + IG_EPOCH_MS) / 1000)


def followers(page):
    try:
        desc = page.locator('meta[property="og:description"]').get_attribute("content", timeout=5000) or ""
    except Exception:
        return None
    m = (re.search(r"подписчик\w*:\s*(.+?),\s*подписк", desc, re.I)
         or re.search(r"([\d.,]+\s*[KM]?)\s+Followers", desc, re.I))
    if not m:
        return None
    v = re.fullmatch(r"([\d.,\s\xa0]+?)\s*(K|M|тыс\.?|млн)?", m[1].strip(), re.I)
    if not v:
        return None
    raw = re.sub(r"[\s\xa0]", "", v[1])
    num = float(raw.replace(",", ".")) if v[2] else float(raw.replace(",", "").replace(".", ""))
    mult = {"k": 1e3, "тыс": 1e3, "тыс.": 1e3, "m": 1e6, "млн": 1e6}.get((v[2] or "").lower(), 1)
    return int(num * mult)


def scan(page, username, want=40, max_scrolls=14):
    """→ (подписчики, [ролики]). Медленно, как человек: прокрутка колесом с паузами."""
    got = {}

    def on_resp(r):
        if "graphql" not in r.url:
            return
        try:
            body = r.text()
        except Exception:
            return
        if "clips_connection" not in body:
            return
        try:
            conn = json.loads(body)["data"]["fetch__XDTUserDict"]["clips_connection"]
        except (ValueError, KeyError, TypeError):
            return
        for e in conn.get("edges", []):
            m = e["node"]["media"]
            if m.get("user", {}).get("username", username) != username:
                continue
            got[m["code"]] = {
                "id": m["code"], "views": m.get("play_count") or m.get("view_count"),
                "likes": m.get("like_count"), "comments": m.get("comment_count"),
                "posted_at": posted_at(m["pk"]).isoformat(timespec="seconds"),
                "pinned": bool(m.get("clips_tab_pinned_user_ids")),
            }

    page.on("response", on_resp)
    try:
        page.goto(f"https://www.instagram.com/{username}/reels/", wait_until="domcontentloaded")
        time.sleep(random.uniform(6, 9))
        if any(x in page.url for x in ("/accounts/login", "/challenge", "/suspended")):
            raise Blocked(page.url)
        fol = followers(page)
        last, idle = -1, 0
        for _ in range(max_scrolls):
            if len(got) >= want or idle >= 3:  # три прокрутки подряд без новых роликов – конец ленты
                break
            idle = idle + 1 if len(got) == last else 0
            last = len(got)
            for _ in range(4):
                page.mouse.move(random.randint(500, 800), random.randint(350, 550))
                page.mouse.wheel(0, random.randint(550, 800))
                time.sleep(random.uniform(0.6, 1.2))
            time.sleep(random.uniform(2, 3.5))
        return fol, list(got.values())
    finally:
        page.remove_listener("response", on_resp)
