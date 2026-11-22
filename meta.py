"""動いているサーバーのページから、タイトルとアイコン(favicon)を拾う。

タイルに出す名前の下の一言(台帳にメモが無いとき)と、頭文字の代わりのアイコンに使う。
127.0.0.1 にしか取りに行かない。1 ポートにつき 1 分は覚えておく。
"""
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import urljoin, urlsplit

TTL = 60.0
MAX_HTML = 256 * 1024
MAX_ICON = 512 * 1024
_cache: dict[int, tuple[float, dict]] = {}
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=8)

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
ATTR_RE = re.compile(r'([a-zA-Z-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))')


def _get(url: str, limit: int, timeout: float = 1.5) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "simple-localserver-list"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit), r.headers.get("Content-Type", "")


def parse_html(html: str) -> tuple[str, str]:
    """(タイトル, アイコンの href)。どちらも無ければ空。"""
    m = TITLE_RE.search(html)
    title = re.sub(r"\s+", " ", unescape(m.group(1))).strip() if m else ""
    icon = ""
    best = -1
    for tag in LINK_RE.findall(html):
        attrs = {a.lower(): (b or c or d or "") for a, _, b, c, d in ATTR_RE.findall(tag)}
        rel = attrs.get("rel", "").lower().split()
        if "icon" not in rel and "apple-touch-icon" not in rel:
            continue
        # apple-touch-icon(大きい)> svg > ほか の順に選ぶ
        score = 2 if "apple-touch-icon" in rel else 1 if attrs.get("href", "").endswith(".svg") else 0
        if score > best and attrs.get("href"):
            best, icon = score, attrs["href"]
    return title[:120], icon


def _fetch(port: int) -> dict:
    base = f"http://127.0.0.1:{port}/"
    out = {"title": "", "icon": None, "icon_type": ""}
    try:
        body, ctype = _get(base, MAX_HTML)
        title, href = ("", "")
        if "html" in ctype or body.lstrip()[:1] == b"<":
            title, href = parse_html(body.decode("utf-8", "replace"))
        out["title"] = title
    except (urllib.error.URLError, OSError, ValueError):
        href = ""
    for cand in ([urljoin(base, href)] if href else []) + [urljoin(base, "/favicon.ico")]:
        # よそのホストへは取りに行かない
        if urlsplit(cand).hostname not in ("127.0.0.1", "localhost") or urlsplit(cand).port != port:
            continue
        try:
            data, ctype = _get(cand, MAX_ICON)
        except (urllib.error.URLError, OSError, ValueError):
            continue
        ctype = ctype.split(";")[0].strip()
        if data and (ctype.startswith("image/") or cand.endswith((".ico", ".png", ".svg"))):
            out["icon"] = data
            out["icon_type"] = ctype if ctype.startswith("image/") else "image/x-icon"
            break
    return out


EMPTY = {"title": "", "icon": None, "icon_type": ""}
_pending: set[int] = set()


def info(port: int) -> dict:
    """待って取る(アイコンの配信用)。"""
    now = time.time()
    with _lock:
        hit = _cache.get(port)
        if hit and now - hit[0] < TTL:
            return hit[1]
    data = _fetch(port)
    with _lock:
        _cache[port] = (now, data)
    return data


def _refresh(port: int) -> None:
    try:
        data = _fetch(port)
        with _lock:
            _cache[port] = (time.time(), data)
    finally:
        with _lock:
            _pending.discard(port)


def many(ports: list[int]) -> dict[int, dict]:
    """待たずに返す。覚えていないもの・古いものは後ろで取りに行き、今は手元の値(無ければ空)を返す。
    返事の遅いサーバーがあっても、一覧の読み直しを遅らせないため。"""
    now = time.time()
    out = {}
    with _lock:
        for port in set(ports):
            hit = _cache.get(port)
            out[port] = hit[1] if hit else EMPTY
            if (not hit or now - hit[0] >= TTL) and port not in _pending:
                _pending.add(port)
                _pool.submit(_refresh, port)
    return out
