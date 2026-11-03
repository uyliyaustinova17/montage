"""Сайт банка идей – http://localhost:8777 (открывается только на твоём компьютере).
    python bank.py"""
import config  # первым: переключает на окружение .venv
import json
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lib import db
from lib.common import now

PORT = 8777
PAGE = config.ROOT / "bank.html"
RUNNING = {}  # что сейчас запущено с сайта: имя → процесс


def data():
    con = db.connect()
    try:
        db.active_bloggers(con)
        reels = [dict(r) for r in con.execute(
            "SELECT r.*, b.followers, b.median_views FROM reels r LEFT JOIN bloggers b ON b.username=r.blogger "
            "WHERE r.state!='new' ORDER BY r.relevance IS NULL, r.relevance DESC, r.multiplier DESC")]
        bloggers = [dict(r) for r in con.execute(
            "SELECT b.*, (SELECT COUNT(*) FROM reels WHERE blogger=b.username AND state!='new') AS outliers,"
            " (SELECT COUNT(*) FROM reels WHERE blogger=b.username) AS total FROM bloggers b"
            " ORDER BY b.status, b.score DESC, b.username")]
        states = {r[0]: r[1] for r in con.execute("SELECT state, COUNT(*) FROM reels GROUP BY state")}
    finally:
        con.close()
    scripts = []
    for f in sorted(config.SCRIPTS.glob("*.md"), reverse=True)[:100]:
        text = f.read_text(encoding="utf-8").split("\n\n<!--")[0]
        scripts.append({"file": f.name, "text": text})
    logs = sorted(config.LOGS.glob("20*.log"))
    log = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()[-60:] if logs else []
    running = [k for k, p in RUNNING.items() if p.poll() is None]
    return {"reels": reels, "bloggers": bloggers, "states": states, "scripts": scripts, "log": log,
            "running": running}


def start(name, args):
    if name in RUNNING and RUNNING[name].poll() is None:
        raise ValueError("Уже запущено, подожди")
    RUNNING[name] = subprocess.Popen([sys.executable, *args], cwd=config.ROOT,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def action(b):
    con = db.connect()
    try:
        kind, act = b.get("kind"), b.get("action")
        if kind == "reel":
            rid = str(b.get("id") or "")
            if act == "skip":
                con.execute("UPDATE reels SET state='skipped', updated=? WHERE id=?", (now(), rid))
            elif act == "restore":
                con.execute("UPDATE reels SET updated=?, state=CASE WHEN script_file IS NOT NULL THEN 'scripted' "
                            "WHEN relevance IS NOT NULL THEN 'scored' "
                            "WHEN transcript IS NOT NULL THEN 'transcribed' ELSE 'outlier' END WHERE id=?", (now(), rid))
            elif act == "write":
                start("write:" + rid, ["write.py", "--id", rid])
                return "Пишу сценарий, через 3–5 минут он появится во вкладке «Сценарии» и в Telegram"
            else:
                raise ValueError("Неизвестное действие")
        elif kind == "blogger":
            u = str(b.get("username") or "").lstrip("@").lower()
            if act == "add":
                db.add_to_list(u)
                con.execute("UPDATE bloggers SET status='active' WHERE username=?", (u,))
            elif act == "reject":
                con.execute("UPDATE bloggers SET status='rejected' WHERE username=?", (u,))
            else:
                raise ValueError("Неизвестное действие")
        elif kind == "run":
            steps = {"collect": ["collect.py"], "transcribe": ["transcribe.py"], "write": ["write.py"],
                     "discover": ["discover.py"], "all": ["run.py"]}
            if act not in steps:
                raise ValueError("Неизвестный шаг")
            start(act, steps[act])
            return "Запущено. Ход работы – во вкладке «Журнал»"
        con.commit()
        return "Готово"
    finally:
        con.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _local(self):
        return self.headers.get("Host") in (f"localhost:{PORT}", f"127.0.0.1:{PORT}")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._local():
            return self._send(403, {"error": "forbidden"})
        try:
            if self.path == "/":
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/data":
                return self._send(200, data())
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        # свой заголовок: чужой сайт в браузере не сможет нажимать кнопки
        if not self._local() or self.headers.get("X-Local") != "1":
            return self._send(403, {"error": "forbidden"})
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or "{}")
            self._send(200, {"ok": True, "message": action(b)})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Сайт банка идей: {url}  (закрыть – Ctrl+C)")
    if "--no-open" not in sys.argv:
        threading.Timer(1, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
