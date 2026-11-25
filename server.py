"""サーバー台帳のランチャー画面。

127.0.0.1:7440 だけで待ち、外へは tailscale serve(https :7463)で tailnet の中にだけ出す。
起動すると自分の公開も張る。ふだんは `srv ui` で tmux の中に立てる。

画面からできるのは、台帳にある行の 起動・停止・再起動・削除・ログ・お気に入り・外に公開(trycloudflare)と、
スキャンで見つけたものの 登録(下書き)・死んだ公開を外す だけ。
コマンドを書き換える入口は作らない(任意のコマンドを実行させないため。直すのは srv add か Claude に頼む)。
"""
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import meta  # noqa: E402
import registry  # noqa: E402
import runner  # noqa: E402
import scan  # noqa: E402
import share  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("SLL_PORT", "7440"))
HTTPS_PORT = int(os.environ.get("SLL_HTTPS_PORT", "7463"))
WEB = Path(__file__).resolve().parent / "web"

ROUTE = re.compile(r"^/api/servers/([a-z0-9][a-z0-9_-]{0,39})(?:/(start|stop|restart|logs|favorite|unfavorite|share|unshare))?$")
# お気に入り以外で、これだけ止まっていたら「片付け」に出す
STALE_DAYS = float(os.environ.get("SLL_STALE_DAYS", "3"))


def _created(e: dict) -> float:
    try:
        return datetime.fromisoformat(e.get("created_at", "")).timestamp()
    except ValueError:
        return 0.0


def view() -> dict:
    """画面に渡す形。スキャンに、ページのタイトル・アイコンの有無・最後に動いていた時刻を足す。"""
    snap = scan.snapshot()
    now = time.time()
    servers = [s for s in snap["servers"] if not s.get("broken")]
    last = registry.touch_last_up([s["name"] for s in servers if s["state"] in ("up", "partial")], now)
    ports = [s["port"] for s in servers if s.get("running")] + [u["port"] for u in snap["unregistered"]]
    infos = meta.many(ports)
    shares = share.all_status()
    for s in servers:
        seen = max(last.get(s["name"], 0.0), _created(s))
        s["last_up"] = seen
        s["stale"] = (not s["favorite"] and s["state"] == "down" and now - seen > STALE_DAYS * 86400)
        mi = infos.get(s["port"]) if s.get("running") else None
        s["title"] = mi["title"] if mi else ""
        s["icon"] = bool(mi and mi["icon"])
        s["share"] = shares.get(s["name"])
    for u in snap["unregistered"]:
        mi = infos.get(u["port"])
        u["title"] = mi["title"] if mi else ""
        u["icon"] = bool(mi and mi["icon"])
    snap["now"] = now
    snap["stale_days"] = STALE_DAYS
    snap["fav_order"] = registry.load_fav_order()
    return snap


def adopt(port: int) -> dict:
    """スキャンで見つけた未登録のものを、拾ったコマンドのまま下書きで台帳に入れる。
    コマンドは画面から受け取らず、ここでスキャンし直した値だけを使う。"""
    snap = scan.snapshot()
    u = next((x for x in snap["unregistered"] if x["port"] == port), None)
    if not u:
        raise registry.RegistryError(f"ポート {port} の未登録のものが見つからない(もう止まったかも)")
    pubs = [hp for hp in u["https_ports"] if hp not in registry.RESERVED_HTTPS_PORTS]
    if u["https_ports"] and not pubs:
        raise registry.RegistryError("443 の下にあるものはほかの道具が面倒を見ているので登録しない")
    base = re.sub(r"[^a-z0-9_-]+", "-", Path(u["cwd"] or u["process"]).name.lower()).strip("-_") or "server"
    base = base[:30]
    name, i = base, 2
    while registry.load(name):
        name, i = f"{base}-{i}", i + 1
    return registry.save({
        "name": name,
        "cwd": u["cwd"] or str(Path.home()),
        "command": u["command"],
        "port": port,
        "https_port": pubs[0] if pubs else port,
        "note": "画面から登録した下書き。コマンドを確かめること",
        "draft": True,
        "registered_by": "launcher",
    })


def unpublish(https_port: int) -> None:
    """死んだ公開を外す。台帳にあるポート・443・まだ宛先が生きているものは断る。"""
    runner.unpublish_dead(https_port)


def stop_or_restart(name: str, restart: bool) -> dict:
    if not registry.load(name):
        raise runner.RunError(f"台帳に無い: {name}")
    if restart and runner.has_session(name):
        return runner.restart(name)  # 外への公開(trycloudflare)は止めない
    if not runner.stop(name):
        e = registry.normalize(registry.load(name))
        if runner.port_owner(e["port"]):
            # srv の tmux の外で立っているものは、こちらからは止めない(pid を殺すと別物を巻き込みうる)
            return {"ok": False, "message": "srv の外で動いているので、ここからは止められない。立てたところで止めること"}
        if not restart:
            return {"ok": True, "message": "動いていなかった"}
    return runner.start(name) if restart else {"ok": True, "message": "止めた"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        # 5 秒ごとの一覧の読み直しでログが埋まらないようにする
        first = str(args[0]) if args else ""  # log_error からは HTTPStatus が来る
        if "/api/servers HTTP" in first or "/api/icon/" in first:
            return
        super().log_message(fmt, *args)

    def _send(self, code: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _guard(self) -> bool:
        # 別のサイトから POST させない(tailnet の中のページでも、ブラウザ越しの CSRF は防ぐ)
        if self.headers.get("X-SLL") != "1":
            self._send(403, {"error": "X-SLL ヘッダが無い"})
            return False
        return True

    def _handle(self, fn):
        try:
            self._send(200, fn())
        except (registry.RegistryError, runner.RunError) as ex:
            self._send(400, {"error": str(ex)})
        except Exception as ex:  # noqa: BLE001 — 画面には理由を返し、サーバーは落とさない
            self._send(500, {"error": f"{type(ex).__name__}: {ex}"})

    def _icon(self, port: int) -> None:
        # 今待ち受けているポートのものだけ(meta は 127.0.0.1 にしか取りに行かない)
        if port not in scan.listening():
            return self._send(404, {"error": "動いていない"})
        mi = meta.info(port)
        if not mi["icon"]:
            return self._send(404, {"error": "アイコンが無い"})
        self.send_response(200)
        self.send_header("Content-Type", mi["icon_type"])
        self.send_header("Content-Length", str(len(mi["icon"])))
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(mi["icon"])

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/servers":
            return self._handle(view)
        mi = re.match(r"^/api/icon/(\d{1,5})$", path)
        if mi:
            return self._icon(int(mi.group(1)))
        m = ROUTE.match(path)
        if m and m.group(2) == "logs":
            return self._handle(lambda: {"logs": runner.logs(m.group(1), 80)})
        if path.startswith("/api/"):
            return self._send(404, {"error": "無い"})
        return super().do_GET()

    def _json_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 32768:
            raise registry.RegistryError("本文が無いか大きすぎる")
        try:
            data = json.loads(self.rfile.read(n))
        except ValueError:
            raise registry.RegistryError("本文が JSON でない")
        if not isinstance(data, dict):
            raise registry.RegistryError("本文は {\"order\": [...]} の形で")
        return data

    def do_POST(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        m = ROUTE.match(path)
        if m and m.group(2) == "start":
            return self._handle(lambda: runner.start(m.group(1)))
        if m and m.group(2) == "share":
            q = parse_qs(urlparse(self.path).query)
            minutes = int((q.get("minutes") or [share.DEFAULT_MINUTES])[0])
            return self._handle(lambda: share.start(m.group(1), minutes))
        if m and m.group(2) == "unshare":
            return self._handle(lambda: {"ok": True, "stopped": share.stop(m.group(1))})
        if m and m.group(2) in ("favorite", "unfavorite"):
            return self._handle(lambda: {"ok": True, "entry": registry.set_favorite(m.group(1), m.group(2) == "favorite")})
        if m and m.group(2) in ("stop", "restart"):
            return self._handle(lambda: stop_or_restart(m.group(1), m.group(2) == "restart"))
        if path == "/api/fav-order":
            return self._handle(lambda: {"ok": True, "order": registry.save_fav_order(self._json_body().get("order"))})
        m2 = re.match(r"^/api/adopt/(\d{1,5})$", path)
        if m2:
            return self._handle(lambda: {"ok": True, "entry": adopt(int(m2.group(1)))})
        m3 = re.match(r"^/api/unpublish/(\d{1,5})$", path)
        if m3:
            return self._handle(lambda: (unpublish(int(m3.group(1))), {"ok": True})[1])
        self._send(404, {"error": "無い"})

    def do_DELETE(self):
        if not self._guard():
            return
        m = ROUTE.match(urlparse(self.path).path)
        if m and not m.group(2):
            return self._handle(lambda: runner.remove(m.group(1)))
        self._send(404, {"error": "無い"})


def publish_self() -> bool:
    r = runner.ts_serve("--bg", f"--https={HTTPS_PORT}", f"http://{HOST}:{PORT}")
    if r.returncode != 0:
        print(f"tailscale serve に失敗: {r.stderr.strip()}", file=sys.stderr, flush=True)
    return r.returncode == 0


def heal(tries: int = 40, every: float = 15.0) -> None:
    """ログイン直後は tailscale がまだつながっておらず、公開に失敗することがある。
    つながるまで、自分の公開と「autostart で動いているのに公開が無い」ものの公開を張り直す。"""
    for _ in range(tries):
        ok = publish_self()
        if ok:
            for s in scan.snapshot()["servers"]:
                if s.get("autostart") and s.get("state") == "partial" and s.get("session"):
                    try:
                        runner.publish(s)
                    except runner.RunError as ex:
                        print(f"{s['name']}: {ex}", file=sys.stderr, flush=True)
                        ok = False
        if ok:
            return
        time.sleep(every)


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    if os.environ.get("SLL_NO_PUBLISH") != "1":
        threading.Thread(target=heal, daemon=True).start()
    host = scan.dns_name()
    print(f"ランチャー: http://{HOST}:{PORT}/" + (f"  https://{host}:{HTTPS_PORT}/" if host else ""), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
