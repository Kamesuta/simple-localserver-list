"""台帳の 1 行を tmux で起動・停止し、tailscale serve を張る/外す。

起動は tmux セッション `srv-<名前>` の中。落ちてもログが残るよう、終わったら Enter を待つ
(simple-remote-git/serve.sh と同じ型)。
守る番号(既定 443)の公開には絶対に触らない。
"""
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import registry
import scan

PATH = "/opt/homebrew/bin:{home}/.cargo/bin:{home}/.local/bin:/usr/local/bin:/usr/bin:/bin".format(home=Path.home())


class RunError(RuntimeError):
    pass


def session_name(name: str) -> str:
    return f"srv-{name}"


def _tmux(*args: str, check=False) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True, check=check)


def has_session(name: str) -> bool:
    return _tmux("has-session", "-t", f"={session_name(name)}").returncode == 0


def shell_line(e: dict) -> str:
    """tmux に渡す 1 行。cd → env_file を読む → env を付けて実行 → 止まったら Enter 待ち。"""
    parts = [f"cd {shlex.quote(str(Path(e['cwd']).expanduser()))}"]
    if e.get("env_file"):
        f = shlex.quote(str(Path(e["env_file"]).expanduser()))
        parts.append(f"set -a && . {f} && set +a")
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in e.get("env", {}).items())
    run = f"env {env} {e['command']}" if env else e["command"]
    line = " && ".join(parts) + f" && {{ {run}; }}"
    return f"export PATH={shlex.quote(PATH)}:\"$PATH\"; {line}; echo; echo {shlex.quote(scan.STOPPED_MARK)}; read _"


def port_owner(port: int) -> dict | None:
    return scan.listening().get(port)


def wait_ready(e: dict, timeout: float = 30.0) -> bool:
    url = f"http://127.0.0.1:{e['port']}{e['path']}"
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except urllib.error.HTTPError:
            # 404 や 401 でも、返事をしているなら立っている
            return True
        except (urllib.error.URLError, OSError):
            pass
        # tmux が消えた・コマンドが終わって「止まった」の印が出たら、それ以上待たない
        if not has_session(e["name"]) or scan.pane_stopped(session_name(e["name"])):
            return False
        time.sleep(0.5)
    return False


def ts_serve(*args: str) -> subprocess.CompletedProcess:
    """tailscale serve を呼ぶ。serve の設定は丸ごと 1 つなので、ほかと同時に書くと
    「etag mismatch / Another client is changing the serve config」で断られる。少し待ってやり直す。"""
    for i in range(6):
        r = subprocess.run([scan.tailscale_bin(), "serve", *args], capture_output=True, text=True)
        out = r.stderr + r.stdout
        if r.returncode == 0 or not ("etag mismatch" in out or "Another client" in out):
            return r
        time.sleep(0.3 * (i + 1))
    return r


def publish(e: dict) -> None:
    if e["https_port"] in registry.RESERVED_HTTPS_PORTS:
        raise RunError(f"https ポート {e['https_port']} には触らない")
    r = ts_serve("--bg", f"--https={e['https_port']}", f"http://127.0.0.1:{e['port']}")
    if r.returncode != 0:
        raise RunError(f"tailscale serve に失敗: {r.stderr.strip() or r.stdout.strip()}")


def unpublish(https_port: int) -> None:
    if https_port in registry.RESERVED_HTTPS_PORTS:
        raise RunError(f"https ポート {https_port} には触らない")
    ts_serve(f"--https={https_port}", "off")


def logs(name: str, lines: int = 60) -> str:
    if not has_session(name):
        return ""
    r = _tmux("capture-pane", "-p", "-J", "-S", f"-{lines}", "-t", f"={session_name(name)}:")
    return r.stdout.rstrip()


def start(name: str) -> dict:
    """起動して、返事をするようになってから serve を張る。返り値は {ok, already, message, logs}

    公開を先に張ってはいけない。tailscale が tailnet のアドレスで同じ番号を先に取るので、
    全部のアドレスで待つサーバー(vite --host、python -m http.server など)が Address already in use で落ちる。
    サーバーが先に待ち受けていれば、あとから張った公開とは共存できる(実測)。"""
    e = registry.load(name)
    if not e:
        raise RunError(f"台帳に無い: {name}")
    e = registry.normalize(e)
    owner = port_owner(e["port"])
    if owner:
        # もう誰かが待っている。srv の tmux なら自分、そうでなければ srv の外で立てたもの(画面から登録した下書きなど)。
        # どちらも止めずに、公開だけ張り直す
        publish(e)
        msg = "もう動いている" if has_session(name) else (
            f"srv の外で動いている({owner['name']} pid {owner['pid']})。公開だけ張った")
        return {"ok": True, "already": True, "message": msg}
    if has_session(name):
        # 前に落ちて Enter 待ちのまま残っているもの
        _tmux("kill-session", "-t", f"={session_name(name)}")
    # 宛先が止まったまま残っている同じ番号の公開を外す(残っていると、全部のアドレスで待つサーバーが落ちる)
    if any(h["local_port"] for h in scan.parse_serve(scan.serve_status()).get(e["https_port"], [])):
        unpublish(e["https_port"])
    r = _tmux("new-session", "-d", "-s", session_name(name), "-c", str(Path(e["cwd"]).expanduser()),
              "/bin/zsh", "-c", shell_line(e))
    if r.returncode != 0:
        raise RunError(f"tmux で起動できない: {r.stderr.strip()}")
    if wait_ready(e):
        publish(e)
        return {"ok": True, "already": False, "message": "起動した"}
    msg = "落ちた。ログを見ること" if scan.pane_stopped(session_name(name)) else "返事が無い(30 秒)。ログを見ること"
    return {"ok": False, "already": False, "message": msg, "logs": logs(name, 20)}


def stop(name: str) -> bool:
    """tmux を止めて、公開も外す。https ポートは台帳に残るので、また起動すれば同じ URL で開ける。
    公開を残すと、次の起動で tailscale が番号を先に持っていて落ちる(start の説明)。"""
    import share  # share は runner を使うので、ここで読む

    share.stop(name)  # 外への公開も止める(宛先が無いのに出し続けない)
    if not has_session(name):
        return False
    _tmux("kill-session", "-t", f"={session_name(name)}")
    e = registry.load(name)
    if e and e.get("https_port") not in registry.RESERVED_HTTPS_PORTS:
        unpublish(e["https_port"])
    return True


def remove(name: str) -> dict:
    e = registry.load(name)
    if not e:
        raise RunError(f"台帳に無い: {name}")
    stopped = stop(name)
    hp = e.get("https_port", e.get("port"))
    if hp not in registry.RESERVED_HTTPS_PORTS:
        unpublish(hp)
    registry.remove(name)
    return {"ok": True, "stopped": stopped, "unpublished": hp}
