import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import meta  # noqa: E402
import registry  # noqa: E402
import runner  # noqa: E402
import scan  # noqa: E402
import share  # noqa: E402

SERVE = {
    "TCP": {"443": {"HTTPS": True}, "5190": {"HTTPS": True}, "7443": {"HTTPS": True}, "8443": {"HTTPS": True}},
    "Web": {
        "me.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:5151"},
                                         "/admin": {"Proxy": "http://127.0.0.1:8766"}}},
        "me.ts.net:5190": {"Handlers": {"/": {"Proxy": "http://localhost:5190"}}},
        "me.ts.net:7443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:7420"}}},
        "me.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://localhost:5191"}}},
        "me.ts.net:9000": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9001"}}},
    },
    "AllowFunnel": {"me.ts.net:8443": True},
}

LSOF = """p100
cnginx
n*:5151
p101
cPython
n127.0.0.1:7420
p102
cnode
n*:5274
p103
cadb
n127.0.0.1:5037
p104
cnode
n127.0.0.1:59469
p105
cPython
n127.0.0.1:9001
n[::1]:9001
"""


def fake_info(pid):
    return {"command": f"cmd-{pid}", "cwd": f"/work/{pid}"}


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SLL_HOME"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("SLL_HOME", None)

    def test_save_load_defaults(self):
        registry.save({"name": "web", "command": "npm run dev", "port": 5190})
        e = registry.load("web")
        self.assertEqual(e["https_port"], 5190)
        self.assertEqual(e["path"], "/")
        self.assertFalse(e["autostart"])
        self.assertEqual([x["name"] for x in registry.load_all()], ["web"])

    def test_bad_name_and_reserved_port(self):
        with self.assertRaises(registry.RegistryError):
            registry.save({"name": "../x", "command": "a", "port": 1})
        with self.assertRaises(registry.RegistryError):
            registry.save({"name": "x", "command": "a", "port": 1, "https_port": 443})

    def test_conflicting_ports(self):
        registry.save({"name": "a", "command": "a", "port": 5000})
        with self.assertRaises(registry.RegistryError):
            registry.save({"name": "b", "command": "b", "port": 5001, "https_port": 5000})
        # 同じ名前で上書きはできる
        registry.save({"name": "a", "command": "a2", "port": 5000})
        self.assertEqual(registry.load("a")["command"], "a2")

    def test_broken_file_does_not_break_list(self):
        (registry.servers_dir() / "bad.json").write_text("{")
        registry.save({"name": "ok", "command": "a", "port": 5000})
        names = {e["name"]: e for e in registry.load_all()}
        self.assertIn("broken", names["bad"])
        self.assertNotIn("broken", names["ok"])

    def test_remove(self):
        registry.save({"name": "a", "command": "a", "port": 5000})
        self.assertTrue(registry.remove("a"))
        self.assertFalse(registry.remove("a"))


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.serve = scan.parse_serve(SERVE)
        self.listen = scan.parse_lsof_listen(LSOF)

    def test_parse_serve(self):
        self.assertEqual(self.serve[5190][0]["local_port"], 5190)
        self.assertTrue(self.serve[8443][0]["funnel"])
        self.assertEqual({h["path"] for h in self.serve[443]}, {"/", "/admin"})

    def test_parse_lsof_merges_addrs(self):
        self.assertEqual(self.listen[9001]["addrs"], ["127.0.0.1", "[::1]"])
        self.assertEqual(self.listen[5151]["name"], "nginx")

    def test_merge_states(self):
        entries = [
            registry.normalize({"name": "graph", "command": "x", "port": 7420, "https_port": 7443}),
            registry.normalize({"name": "web", "command": "x", "port": 5190}),
            registry.normalize({"name": "off", "command": "x", "port": 6000}),
        ]
        m = scan.merge(entries, self.serve, self.listen, {"srv-web"}, "me.ts.net", info=fake_info,
                       stopped=lambda s: True)
        st = {s["name"]: s for s in m["servers"]}
        self.assertEqual(st["graph"]["state"], "up")
        self.assertEqual(st["graph"]["url"], "https://me.ts.net:7443/")
        self.assertEqual(st["web"]["state"], "crashed")  # tmux はあるのにポートが閉じている
        m4 = scan.merge(entries, self.serve, self.listen, {"srv-web"}, "me.ts.net", info=fake_info,
                        stopped=lambda s: False)
        self.assertEqual({s["name"]: s["state"] for s in m4["servers"]}["web"], "starting")
        self.assertEqual(st["off"]["state"], "down")
        m2 = scan.merge(entries, self.serve, self.listen, set(), "me.ts.net", info=fake_info)
        self.assertEqual({s["name"]: s["state"] for s in m2["servers"]}["web"], "down")  # 公開だけ残っていても down
        e9001 = [registry.normalize({"name": "p", "command": "x", "port": 9001, "https_port": 9100})]
        m3 = scan.merge(e9001, self.serve, self.listen, set(), "me.ts.net", info=fake_info)
        self.assertEqual(m3["servers"][0]["state"], "partial")  # 動いているのに公開が無い

        unreg = {u["port"]: u for u in m["unregistered"]}
        self.assertIn(5274, unreg)          # 動いているのに未登録
        self.assertIn(5151, unreg)          # 443 で公開されている nginx は出す
        self.assertEqual(unreg[5151]["https_ports"], [443])
        self.assertIn(9001, unreg)
        self.assertEqual(unreg[9001]["https_ports"], [9000])
        self.assertNotIn(5037, unreg)       # adb は外す
        self.assertNotIn(59469, unreg)      # 一時ポートは外す
        self.assertNotIn(7420, unreg)       # 台帳にある

        orphans = {(o["https_port"], o["path"]) for o in m["orphans"]}
        self.assertIn((8443, "/"), orphans)   # 宛先 5191 が止まっている
        self.assertIn((443, "/admin"), orphans)
        self.assertNotIn((443, "/"), orphans)  # nginx は動いている
        self.assertNotIn((5190, "/"), orphans)  # 台帳にある
        admin = [o for o in m["orphans"] if o["path"] == "/admin"][0]
        self.assertTrue(admin["protected"])


class PickPortsTest(unittest.TestCase):
    def test_skips_used_and_reserved(self):
        self.assertEqual(registry.pick_ports({7400, 7401}, 1, (7400, 7410)), [7402])
        self.assertEqual(registry.pick_ports(set(), 2, (442, 445)), [442, 444])  # 443 は守る番号

    def test_count_and_ok(self):
        self.assertEqual(registry.pick_ports(set(), 3, (7400, 7410), ok=lambda p: p % 2 == 0), [7400, 7402, 7404])

    def test_range_edges_and_full(self):
        self.assertEqual(registry.pick_ports({7400}, 1, (7400, 7401)), [7401])
        self.assertEqual(registry.pick_ports({7400, 7401}, 1, (7400, 7401)), [])
        self.assertEqual(registry.pick_ports(set(), 3, (7400, 7401)), [])

    def test_parse_range(self):
        self.assertEqual(registry._parse_range("8000-8099"), (8000, 8099))
        self.assertEqual(registry._parse_range("8099-8000"), (8000, 8099))
        self.assertEqual(registry._parse_range("x"), (7400, 7599))


class FavoriteAndLastUpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SLL_HOME"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("SLL_HOME", None)

    def test_favorite(self):
        registry.save({"name": "a", "command": "a", "port": 5000})
        self.assertFalse(registry.load("a")["favorite"])
        registry.set_favorite("a", True)
        self.assertTrue(registry.load("a")["favorite"])
        with self.assertRaises(registry.RegistryError):
            registry.set_favorite("nope", True)

    def test_fav_order(self):
        self.assertEqual(registry.load_fav_order(), [])
        self.assertEqual(registry.save_fav_order(["b", "a", "b"]), ["b", "a"])
        self.assertEqual(registry.load_fav_order(), ["b", "a"])
        with self.assertRaises(registry.RegistryError):
            registry.save_fav_order(["../x"])
        with self.assertRaises(registry.RegistryError):
            registry.save_fav_order("a")

    def test_last_up_writes_at_most_every_minute(self):
        d = registry.touch_last_up(["a"], now=1000)
        self.assertEqual(d["a"], 1000)
        d = registry.touch_last_up(["a"], now=1030)
        self.assertEqual(d["a"], 1000)  # 1 分たっていない
        d = registry.touch_last_up(["a", "b"], now=1070)
        self.assertEqual((d["a"], d["b"]), (1070, 1070))
        self.assertEqual(registry.load_last_up()["b"], 1070)


class MetaTest(unittest.TestCase):
    def test_parse_html(self):
        html = """<html><head><title>
          Git &amp; Graph </title>
          <link rel="icon" href="/favicon.png">
          <link rel='apple-touch-icon' href=icons/icon-180.png>
        </head></html>"""
        title, icon = meta.parse_html(html)
        self.assertEqual(title, "Git & Graph")
        self.assertEqual(icon, "icons/icon-180.png")  # 大きい apple-touch-icon を選ぶ

    def test_parse_html_none(self):
        self.assertEqual(meta.parse_html("<p>hi</p>"), ("", ""))


class ShareTest(unittest.TestCase):
    def test_session_name_does_not_clash(self):
        # 「foo-share」という名前のサーバーがあっても、foo の共有と取り違えない
        self.assertEqual(share.session("foo"), "srvshare-foo")
        self.assertNotEqual(share.session("foo"), runner.session_name("foo-share"))

    def test_url_pattern(self):
        log = '{"message":"|  https://kidney-genre-given-knit.trycloudflare.com  |"}'
        self.assertEqual(share.URL_RE.search(log).group(0), "https://kidney-genre-given-knit.trycloudflare.com")


class RunnerTest(unittest.TestCase):
    def test_shell_line_quotes(self):
        e = registry.normalize({"name": "a", "command": "npm run dev", "port": 5000,
                                "cwd": "/tmp/a b", "env": {"X": "1 2"}, "env_file": "/tmp/.env"})
        line = runner.shell_line(e)
        self.assertIn("cd '/tmp/a b'", line)
        self.assertIn("set -a && . /tmp/.env && set +a", line)
        self.assertIn("env X='1 2' npm run dev", line)
        self.assertIn("read _", line)
        self.assertIn(scan.STOPPED_MARK, line)


if __name__ == "__main__":
    unittest.main()
