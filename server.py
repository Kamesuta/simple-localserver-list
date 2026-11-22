"""サーバー台帳のランチャー画面。

127.0.0.1:7440 だけで待ち、外へは tailscale serve(https :7463)で tailnet の中にだけ出す。
起動すると自分の公開も張る。ログイン時に ログイン時のスクリプト の start_tmux から立つ。

画面からできるのは、台帳にある行の 起動・停止・再起動・削除・ログ と、
スキャンで見つけたものの 登録(下書き)・死んだ公開を外す だけ。
コマンドを書き換える入口は作らない(任意のコマンドを実行させないため。直すのは srv add か Claude に頼む)。
"""
import json
import os
import re
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import registry  # noqa: E402
import runner  # noqa: E402
import scan  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("SLL_PORT", "7440"))
HTTPS_PORT = int(os.environ.get("SLL_HTTPS_PORT", "7463"))
WEB = Path(__file__).resolve().parent / "web"

ROUTE = re.compile(r"^/api/servers/([a-z0-9][a-z0-9_-]{0,39})(?:/(start|stop|restart|logs))?$")


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
    if https_port in registry.RESERVED_HTTPS_PORTS:
        raise registry.RegistryError(f"https ポート {https_port} には触らない")
    snap = scan.snapshot()
    if any(s.get("https_port") == https_port for s in snap["servers"]):
        raise registry.RegistryError("台帳にある行の公開は、その行の削除で外す")
    handlers = scan.parse_serve(scan.serve_status()).get(https_port, [])
    if not handlers:
        raise registry.RegistryError(f"https ポート {https_port} は公開されていない")
    dead = {(o["https_port"], o["path"]) for o in snap["orphans"]}
    if not all((https_port, h["path"]) in dead for h in handlers):
        raise registry.RegistryError("宛先がまだ動いているので外さない")
    runner.unpublish(https_port)


def stop_or_restart(name: str, restart: bool) -> dict:
    if not registry.load(name):
        raise runner.RunError(f"台帳に無い: {name}")
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
        if "/api/servers HTTP" in (args[0] if args else ""):
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

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/servers":
            return self._handle(scan.snapshot)
        m = ROUTE.match(path)
        if m and m.group(2) == "logs":
            return self._handle(lambda: {"logs": runner.logs(m.group(1), 80)})
        if path.startswith("/api/"):
            return self._send(404, {"error": "無い"})
        return super().do_GET()

    def do_POST(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        m = ROUTE.match(path)
        if m and m.group(2) == "start":
            return self._handle(lambda: runner.start(m.group(1)))
        if m and m.group(2) in ("stop", "restart"):
            return self._handle(lambda: stop_or_restart(m.group(1), m.group(2) == "restart"))
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
