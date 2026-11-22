"""作り物のデータで画面を出すデモ(スクショ・見た目の確かめ用)。

    python3 tools/demo.py        # http://127.0.0.1:7449/

本物の台帳・tmux・tailscale には触らない。一覧は下の SERVERS などを返すだけで、
起動・停止などのボタンは何もせず「ok」を返す。
"""
import os
import sys
import time
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SLL_HOST", "my-mac.tail1234.ts.net")

import server  # noqa: E402

HOST = os.environ["SLL_HOST"]
PORT = int(os.environ.get("SLL_DEMO_PORT", "7449"))


def ago(**kw) -> str:
    return (datetime.now() - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%S")


def srv(name, port, note, state, created, by, fav=False, draft=False, cmd="npm run dev", cwd=None, https=None, share=None, last=None):
    hp = https or port
    running = state in ("up", "partial", "starting")
    return {
        "name": name, "cwd": cwd or f"/Users/you/src/{by}", "command": cmd, "env": {}, "env_file": "",
        "port": port, "https_port": hp, "path": "/", "note": note, "autostart": False, "draft": draft,
        "favorite": fav, "registered_by": by, "created_at": created,
        "state": state, "running": running, "published": state == "up", "session": running,
        "url": f"https://{HOST}:{hp}/", "title": "", "icon": False,
        "last_up": time.time() - (last or 0), "stale": last is not None and last > 3 * 86400 and not fav,
        "share": share,
    }


def snapshot() -> dict:
    now = time.time()
    return {
        "host": HOST,
        "now": now,
        "stale_days": 3,
        "servers": [
            srv("my-blog", 4321, "ブログの下書きプレビュー", "up", ago(days=12), "blog", fav=True, cmd="npx astro dev --port 4321"),
            srv("api-docs", 8000, "社内 API のドキュメント", "up", ago(days=30), "docs-site", fav=True, cmd="mkdocs serve -a 127.0.0.1:8000"),
            srv("storybook", 6006, "UI 部品のカタログ", "down", ago(days=9), "design-system", fav=True, cmd="npm run storybook"),
            srv("git-graph", 7420, "スマホで見る Git Graph", "up", ago(days=20), "simple-remote-git", fav=True, https=7443, cmd="python3 server.py"),
            srv("game-proto", 5174, "ブラウザゲームの試作", "up", ago(minutes=12), "game", cmd="npx vite --port 5174 --strictPort",
                share={"url": "https://quiet-river-sample-demo.trycloudflare.com", "started": now - 600, "expires": now + 2940}),
            srv("admin-ui", 3001, "管理画面のモック", "up", ago(minutes=40), "admin", draft=True, cmd="node …/next dev -p 3001"),
            srv("ml-dashboard", 8501, "学習の進み具合", "crashed", ago(hours=7), "ml", cmd="streamlit run app.py --server.port 8501"),
            srv("old-experiment", 5180, "前に試した案", "down", ago(days=6), "playground", last=5 * 86400, cmd="npm run dev -- --port 5180"),
        ],
        "unregistered": [
            {"port": 5173, "pid": 101, "process": "node", "command": "node …/vite --port 5173", "cwd": "/Users/you/src/vite-playground",
             "https_ports": [], "urls": [], "title": "Vite Playground", "icon": False},
            {"port": 8888, "pid": 102, "process": "python3", "command": "python3 -m jupyter lab --port 8888", "cwd": "/Users/you/notebooks",
             "https_ports": [], "urls": [], "title": "JupyterLab", "icon": False},
        ],
        "orphans": [
            {"https_port": 5190, "path": "/", "target": "http://localhost:5190", "funnel": False, "protected": False},
            {"https_port": 8090, "path": "/", "target": "http://127.0.0.1:18090", "funnel": False, "protected": False},
        ],
    }


LOG = """  VITE v6.2.0  ready in 412 ms

  ➜  Local:   http://localhost:5174/
  ➜  Network: use --host to expose
12:41:02 [vite] page reload src/main.ts
12:43:18 [vite] hmr update /src/scene.ts"""


CRASH_LOG = """  You can now view your Streamlit app in your browser.
  URL: http://localhost:8501
Traceback (most recent call last):
  File "app.py", line 14, in <module>
    df = load_metrics("runs/latest")
FileNotFoundError: [Errno 2] No such file or directory: 'runs/latest/metrics.csv'

(止まった。Enter で閉じる)"""


class DemoHandler(server.Handler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/servers":
            return self._send(200, snapshot())
        if path.endswith("/logs"):
            return self._send(200, {"logs": CRASH_LOG if "/ml-dashboard/" in path else LOG})
        if path.startswith("/api/icon/"):
            return self._send(404, {"error": "デモなのでアイコンは無い"})
        return super().do_GET()

    def do_POST(self):
        return self._send(200, {"ok": True, "message": "デモなので何もしない"})

    def do_DELETE(self):
        return self._send(200, {"ok": True})


if __name__ == "__main__":
    print(f"デモ: http://127.0.0.1:{PORT}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), DemoHandler).serve_forever()
