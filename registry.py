"""台帳(どのサーバーをどう起動するか)の読み書き。

1 サーバーにつき 1 ファイル `<台帳の場所>/<名前>.json`。セッションが同時に書いても競合しない。
台帳の場所は SLL_HOME(既定 ~/.config/simple-localserver-list)の下の servers/。
秘密は書かない。要るときは env_file にファイルの場所だけ書く。
"""
import json
import os
import re
import time
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

# 書けるのはこの項目だけ(知らない項目は落とす)
FIELDS = {
    "name": str,
    "cwd": str,
    "command": str,
    "env": dict,
    "env_file": str,
    "port": int,
    "https_port": int,
    "path": str,
    "note": str,
    "autostart": bool,
    "draft": bool,
    "registered_by": str,
    "created_at": str,
}

# 443 はほかの道具が使っている。台帳からは触らせない
RESERVED_HTTPS_PORTS = {443}


def home() -> Path:
    return Path(os.environ.get("SLL_HOME", Path.home() / ".config" / "simple-localserver-list"))


def servers_dir() -> Path:
    d = home() / "servers"
    d.mkdir(parents=True, exist_ok=True)
    return d


class RegistryError(ValueError):
    pass


def check_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise RegistryError(f"名前は英小文字・数字・- _ だけ(40 文字まで): {name!r}")
    return name


def normalize(entry: dict) -> dict:
    """足りない項目を既定で埋め、型を確かめる。"""
    e = {k: v for k, v in entry.items() if k in FIELDS}
    check_name(e.get("name", ""))
    if not e.get("command"):
        raise RegistryError("command が無い")
    if not isinstance(e.get("port"), int) or not 1 <= e["port"] <= 65535:
        raise RegistryError(f"port がおかしい: {e.get('port')!r}")
    e.setdefault("https_port", e["port"])
    if e["https_port"] in RESERVED_HTTPS_PORTS:
        raise RegistryError(f"https ポート {e['https_port']} はほかの道具が使っているので選べない")
    e.setdefault("cwd", str(Path.home()))
    e.setdefault("env", {})
    e.setdefault("env_file", "")
    e.setdefault("path", "/")
    if not e["path"].startswith("/"):
        e["path"] = "/" + e["path"]
    e.setdefault("note", "")
    e.setdefault("autostart", False)
    e.setdefault("draft", False)
    e.setdefault("registered_by", "")
    e.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    for k, t in FIELDS.items():
        if not isinstance(e[k], t) or (t is int and isinstance(e[k], bool)):
            raise RegistryError(f"{k} の型がおかしい: {e[k]!r}")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in e["env"].items()):
        raise RegistryError("env は 文字列→文字列 だけ")
    return e


def _file(name: str) -> Path:
    return servers_dir() / f"{check_name(name)}.json"


def load(name: str) -> dict | None:
    f = _file(name)
    if not f.exists():
        return None
    return json.loads(f.read_text())


def load_all() -> list[dict]:
    out = []
    for f in sorted(servers_dir().glob("*.json")):
        try:
            out.append(normalize(json.loads(f.read_text())))
        except (ValueError, OSError) as ex:
            # 壊れた 1 枚で一覧全体を落とさない
            out.append({"name": f.stem, "broken": str(ex)})
    return out


def save(entry: dict) -> dict:
    e = normalize(entry)
    # 同じ https ポートを別の名前が持っていたら断る(公開がぶつかる)
    for other in load_all():
        if other.get("name") != e["name"] and not other.get("broken"):
            if other["https_port"] == e["https_port"]:
                raise RegistryError(f"https ポート {e['https_port']} は {other['name']} が登録済み")
            if other["port"] == e["port"]:
                raise RegistryError(f"ポート {e['port']} は {other['name']} が登録済み")
    f = _file(e["name"])
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(e, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(f)
    return e


def remove(name: str) -> bool:
    f = _file(name)
    if f.exists():
        f.unlink()
        return True
    return False
