"""台帳のサーバーを、trycloudflare(Cloudflare の Quick Tunnel)でインターネットに一時的に出す。

- tmux セッション `srvshare-<名前>` の中で `cloudflared tunnel --url http://127.0.0.1:<ポート>` を動かす
- 決めた時間がたつと、tmux の中で自分から止まる(ランチャーが落ちていても止まる)
- URL は起動のたびに新しいランダムなもの。止めれば URL ごと使えなくなる
- Host ヘッダは localhost:<ポート> に書き換えて渡す(vite などが知らないホストを断るため)
"""
import json
import re
import shlex
import subprocess
import time
from pathlib import Path

import registry
import runner
import scan

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
DEFAULT_MINUTES = 60
MAX_MINUTES = 24 * 60


def session(name: str) -> str:
    # srv-<名前> とぶつからない名前にする(「foo-share」という名前のサーバーがあっても困らない)
    return f"srvshare-{registry.check_name(name)}"


def _dir() -> Path:
    d = registry.home() / "share"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_file(name: str) -> Path:
    return _dir() / f"{registry.check_name(name)}.json"


def _log_file(name: str) -> Path:
    return _dir() / f"{registry.check_name(name)}.log"


def _alive(name: str) -> bool:
    return runner._tmux("has-session", "-t", f"={session(name)}").returncode == 0


def status(name: str) -> dict | None:
    """公開中なら {url, started, expires}。止まっていれば None(残っていた記録は消す)。"""
    f = _state_file(name)
    if not _alive(name):
        f.unlink(missing_ok=True)
        return None
    try:
        st = json.loads(f.read_text())
    except (OSError, ValueError):
        st = {}
    if not st.get("url"):
        st["url"] = _find_url(name)
    return st if st.get("url") else {"url": "", "started": st.get("started", 0), "expires": st.get("expires", 0)}


def all_status() -> dict[str, dict]:
    out = {}
    for f in _dir().glob("*.json"):
        st = status(f.stem)
        if st:
            out[f.stem] = st
    return out


def _find_url(name: str) -> str:
    try:
        m = URL_RE.search(_log_file(name).read_text(errors="replace"))
        return m.group(0) if m else ""
    except OSError:
        return ""


def _published(url: str) -> bool:
    """Cloudflare の DNS に名前が載ったか。載る前に手元(や URL を渡した相手のスマホ)で引くと、
    「無い名前」として覚えられて、しばらく開けなくなる。手元の DNS は使わず 1.1.1.1 に聞く。"""
    host = url.split("//", 1)[-1]
    try:
        out = subprocess.run(["dig", "+short", "+time=2", "+tries=1", "@1.1.1.1", host],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return True  # 確かめられないときは待たない
    return any(re.match(r"^\d+\.\d+\.\d+\.\d+$", x.strip()) for x in out.splitlines())


def start(name: str, minutes: int = DEFAULT_MINUTES, wait: float = 45.0) -> dict:
    e = registry.load(name)
    if not e:
        raise runner.RunError(f"台帳に無い: {name}")
    e = registry.normalize(e)
    if e["port"] not in scan.listening():
        raise runner.RunError(f"{name} が動いていない。先に起動すること")
    minutes = max(1, min(int(minutes), MAX_MINUTES))
    stop(name)
    log = _log_file(name)
    log.unlink(missing_ok=True)
    cf = " ".join(shlex.quote(x) for x in [
        "cloudflared", "tunnel", "--no-autoupdate",
        "--logfile", str(log),
        "--http-host-header", f"localhost:{e['port']}",
        "--url", f"http://127.0.0.1:{e['port']}",
    ])
    secs = minutes * 60
    # cloudflared を裏で動かし、時間がたったら止める。止まれば tmux のセッションも消える
    line = (f"export PATH={shlex.quote(runner.PATH)}:\"$PATH\"; "
            f"{cf} & CF=$!; ( sleep {secs}; kill $CF 2>/dev/null ) & T=$!; wait $CF; kill $T 2>/dev/null")
    r = runner._tmux("new-session", "-d", "-s", session(name), "/bin/zsh", "-c", line)
    if r.returncode != 0:
        raise runner.RunError(f"tmux で起動できない: {r.stderr.strip()}")
    now = time.time()
    st = {"url": "", "started": now, "expires": now + secs}
    end = now + wait
    while time.time() < end:
        st["url"] = st["url"] or _find_url(name)
        if not _alive(name):
            break
        if st["url"] and _published(st["url"]):
            break
        time.sleep(1)
    if not st["url"]:
        tail = ""
        try:
            tail = "\n".join(log.read_text(errors="replace").splitlines()[-8:])
        except OSError:
            pass
        stop(name)
        raise runner.RunError("trycloudflare の URL が出なかった\n" + tail)
    _state_file(name).write_text(json.dumps(st))
    return {"ok": True, **st}


def stop(name: str) -> bool:
    was = _alive(name)
    if was:
        runner._tmux("kill-session", "-t", f"={session(name)}")
    _state_file(name).unlink(missing_ok=True)
    return was


def cloudflared_ok() -> bool:
    try:
        return subprocess.run(["cloudflared", "--version"], capture_output=True, env={"PATH": runner.PATH}).returncode == 0
    except OSError:
        return False
