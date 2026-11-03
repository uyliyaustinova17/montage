"""База data/reels.db – банк идей.
Блогер: active (в разведке) | proposed (нашла нейросеть, ждёт решения) | rejected.
Ролик: new → outlier (залёт) → transcribed → scored (идея в банке) → scripted; или skipped."""
import sqlite3

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS bloggers (
    username TEXT PRIMARY KEY, followers INTEGER, median_views REAL, reels_counted INTEGER, last_scan TEXT,
    status TEXT DEFAULT 'active', score INTEGER, why TEXT, source TEXT, added TEXT);
CREATE TABLE IF NOT EXISTS reels (
    id TEXT PRIMARY KEY, blogger TEXT, views INTEGER, likes INTEGER, comments INTEGER, posted_at TEXT,
    multiplier REAL, state TEXT DEFAULT 'new', transcript TEXT, lang TEXT, caption TEXT, engine TEXT,
    essence TEXT, relevance INTEGER, relevance_why TEXT, content_type TEXT,
    script_file TEXT, verdict TEXT, error TEXT, first_seen TEXT, updated TEXT);
"""


def connect():
    con = sqlite3.connect(config.DATA / "reels.db", timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def active_bloggers(con):
    """bloggers.txt – главный список. Всё, что в нём, – в разведке."""
    from lib.common import now
    names = []
    if config.BLOGGERS.exists():
        for ln in config.BLOGGERS.read_text(encoding="utf-8").splitlines():
            n = ln.strip().split()[0].lstrip("@").rstrip("/").split("/")[-1] if ln.strip() else ""
            if n and not n.startswith("#"):
                names.append(n.lower())
    for n in names:
        con.execute("INSERT INTO bloggers(username, status, source, added) VALUES (?, 'active', 'список', ?) "
                    "ON CONFLICT(username) DO UPDATE SET status='active'", (n, now()))
    con.commit()
    return names


def add_to_list(username):
    names = config.BLOGGERS.read_text(encoding="utf-8").splitlines() if config.BLOGGERS.exists() else []
    if username.lower() not in {n.strip().lstrip("@").lower() for n in names}:
        config.BLOGGERS.write_text("\n".join([*names, username]).strip() + "\n", encoding="utf-8")
