"""今この Mac で何が動き、何が tailscale で公開されているかを調べ、台帳と照らし合わせる。

- tailscale serve status --json → 「https ポート → ローカルの宛先」
- lsof -iTCP -sTCP:LISTEN → 「ポート → PID」、PID ごとに起動フォルダとコマンド
台帳の行には state(up / partial / starting / crashed / down)を付け、台帳に無いものは
「動いているのに未登録」「公開だけ残っている」に分ける。
"""
import json
import os
import re
import shutil
import subprocess
from urllib.parse import urlsplit

import registry

# Mac の公式版の tailscale は PATH に入らず、対話シェルの alias でしか呼べないので本体の場所も試す
TS_CANDIDATES = ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale")

# 未登録の一覧から外すもの(サーバーとして見せる意味がない道具)
IGNORE_COMMANDS = re.compile(
    r"^(adb|ssh|sshd|claude|code-[0-9a-f]+|cloudflar|cloudflared|rapportd|ControlCe|"
    r"Spotify|Dropbox|Google|Discord|Slack|figma_age|Electron|Code|Cursor|"
    r"launchd|mDNSRespo|tailscale|IPNExtens|io\.tailsc)",
)
# ランチャー自身のポート(server.py と合わせる)
SELF_PORTS = {int(os.environ.get("SLL_PORT", "7440"))}


def tailscale_bin() -> str:
    for c in TS_CANDIDATES:
        p = shutil.which(c) or (c if os.path.exists(c) else None)
        if p:
            return p
    return TS_CANDIDATES[-1]


def _run(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ---- tailscale ----

def serve_status() -> dict:
    out = _run([tailscale_bin(), "serve", "status", "--json"])
    try:
        return json.loads(out or "{}")
    except ValueError:
        return {}


def dns_name() -> str:
    out = _run([tailscale_bin(), "status", "--json"])
    try:
        return json.loads(out)["Self"]["DNSName"].rstrip(".")
    except (ValueError, KeyError):
        return ""


def parse_serve(status: dict) -> dict[int, list[dict]]:
    """https ポート → [{path, target, local_port, funnel}]"""
    funnel = {hp for hp, on in (status.get("AllowFunnel") or {}).items() if on}
    out: dict[int, list[dict]] = {}
    for hostport, web in (status.get("Web") or {}).items():
        try:
            hp = int(hostport.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            continue
        for path, h in (web.get("Handlers") or {}).items():
            target = h.get("Proxy") or h.get("Path") or h.get("Text") or ""
            lp = None
            if h.get("Proxy"):
                u = urlsplit(target if "://" in target else "http://" + target)
                lp = u.port or (443 if u.scheme == "https" else 80)
            out.setdefault(hp, []).append(
                {"path": path, "target": target, "local_port": lp, "funnel": hostport in funnel}
            )
    return out


# ---- 待ち受けているプロセス ----

def parse_lsof_listen(text: str) -> dict[int, dict]:
    """`lsof -nP -iTCP -sTCP:LISTEN -F pcn` の出力 → ポート → {pid, name, addr}"""
    out: dict[int, dict] = {}
    pid = name = None
    for line in text.splitlines():
        if not line:
            continue
        tag, val = line[0], line[1:]
        if tag == "p":
            pid = int(val)
        elif tag == "c":
            name = val
        elif tag == "n":
            addr, _, port = val.rpartition(":")
            if port.isdigit() and pid is not None:
                p = int(port)
                # 同じポートを 127.0.0.1 と [::1] で待つものは 1 つにまとめる
                out.setdefault(p, {"pid": pid, "name": name or "", "addrs": []})["addrs"].append(addr)
    return out


def listening() -> dict[int, dict]:
    return parse_lsof_listen(_run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pcn"]))


def proc_info(pid: int) -> dict:
    command = _run(["ps", "-o", "command=", "-p", str(pid)]).strip()
    cwd = ""
    for line in _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-F", "n"]).splitlines():
        if line.startswith("n"):
            cwd = line[1:]
    return {"command": command, "cwd": cwd}


def tmux_sessions() -> set[str]:
    out = _run(["tmux", "list-sessions", "-F", "#{session_name}"])
    return set(out.split())


STOPPED_MARK = "(止まった。Enter で閉じる)"  # runner.shell_line がコマンドの終わりに出す


def pane_stopped(session: str) -> bool:
    """tmux の画面の最後に「止まった」の印が出ていれば、コマンドはもう終わっている。"""
    out = _run(["tmux", "capture-pane", "-p", "-t", f"={session}:"])
    lines = [x for x in out.splitlines() if x.strip()]
    return bool(lines) and STOPPED_MARK in lines[-1]


# ---- 照らし合わせ ----

def merge(entries: list[dict], serve: dict[int, list[dict]], listen: dict[int, dict],
          sessions: set[str], host: str, info=proc_info, stopped=pane_stopped) -> dict:
    """台帳・serve・待ち受け・tmux を合わせて画面に出す形にする。"""
    servers = []
    claimed_https: set[int] = set()
    claimed_ports: set[int] = set()
    for e in entries:
        if e.get("broken"):
            servers.append({**e, "state": "broken"})
            continue
        handlers = serve.get(e["https_port"], [])
        published = any(h["local_port"] == e["port"] for h in handlers)
        running = e["port"] in listen
        sname = f"srv-{e['name']}"
        session = sname in sessions
        # 公開は止めても残すので、止まっている行は公開があっても down
        if running:
            state = "up" if published else "partial"
        elif session and stopped(sname):
            state = "crashed"  # コマンドが終わって Enter 待ち
        elif session:
            state = "starting"  # tmux の中でまだコマンドが動いていて、ポートを開く前
        else:
            state = "down"
        claimed_https.add(e["https_port"])
        claimed_ports.add(e["port"])
        servers.append({
            **e,
            "state": state,
            "running": running,
            "published": published,
            "session": session,
            "url": f"https://{host}:{e['https_port']}{e['path']}" if host else "",
        })

    # 公開されているが台帳に無いもの
    published_ports = {}
    for hp, handlers in serve.items():
        for h in handlers:
            if h["local_port"]:
                published_ports.setdefault(h["local_port"], []).append((hp, h))

    unregistered = []  # 動いているのに台帳に無い
    for port, l in sorted(listen.items()):
        if port in claimed_ports or port in SELF_PORTS:
            continue
        pubs = [p for p in published_ports.get(port, []) if p[0] not in claimed_https]
        local_only = all(a in ("127.0.0.1", "[::1]", "localhost") for a in l["addrs"])
        if not pubs and IGNORE_COMMANDS.match(l["name"]):
            continue
        if not pubs and local_only and port > 49151:
            # 一時ポート(外に見せるつもりのない内部の口)は外す
            continue
        pi = info(l["pid"])
        unregistered.append({
            "port": port,
            "pid": l["pid"],
            "process": l["name"],
            "command": pi["command"],
            "cwd": pi["cwd"],
            "https_ports": [hp for hp, _ in pubs],
            "urls": [f"https://{host}:{hp}{h['path']}" for hp, h in pubs] if host else [],
        })

    orphans = []  # 公開だけ残っている(宛先が止まっている)
    for hp, handlers in sorted(serve.items()):
        if hp in claimed_https:
            continue
        for h in handlers:
            if h["local_port"] and h["local_port"] in listen:
                continue
            orphans.append({
                "https_port": hp,
                "path": h["path"],
                "target": h["target"],
                "funnel": h["funnel"],
                "protected": hp in registry.RESERVED_HTTPS_PORTS,
            })

    return {"host": host, "servers": servers, "unregistered": unregistered, "orphans": orphans}


def snapshot() -> dict:
    return merge(
        registry.load_all(),
        parse_serve(serve_status()),
        listening(),
        tmux_sessions(),
        dns_name(),
    )
