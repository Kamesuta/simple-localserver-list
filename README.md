# simple-localserver-list

この Mac で立てたサーバーを「起動のしかた」ごと台帳に登録し、スマホの画面から一覧・起動・停止・削除をするための道具。

- **困りごと**
  - セッションごとにサーバーを立てると、`tailscale serve` の公開がたまってリンクが分からなくなる
  - 再起動で全部止まると、どう起動していたかが分からない
- **やり方**
  - 台帳(1 サーバー 1 ファイル)を正本にする
  - 登録し忘れたものは、スキャン(`lsof` と `tailscale serve status`)で拾う

## 使い方

```sh
srv add <名前> --port P [--note メモ] [--env-file F] [--autostart] -- <起動コマンド…>
srv ls / logs / start / stop / restart / rm / attach <名前>
srv autostart   # autostart のものを全部立てる(ログイン時)
srv port        # 空いているポート
```

画面: `https://<この Mac の MagicDNS 名>:7463/`(tailnet の中だけ)

## 仕組み

| ファイル | 役目 |
|---|---|
| `srv` | CLI。`~/.local/bin/srv` からリンクを張る |
| `registry.py` | 台帳 `~/.config/simple-localserver-list/servers/<名前>.json` の読み書き(`SLL_HOME` で場所を変えられる) |
| `scan.py` | `tailscale serve status --json`・`lsof`・`ps`・`tmux` を照らし合わせ、状態(🟢 up / 🟡 partial / ⏳ starting / 🔴 crashed / ⚪ down)を付ける |
| `runner.py` | tmux セッション `srv-<名前>` での起動・停止、`tailscale serve` の張る/外す |
| `server.py` | 画面の HTTP。127.0.0.1:7440 で待ち、自分を https :7463 で公開する |
| `web/` | 画面(素の JS、PWA) |
| `skill/SKILL.md` | セッション向けのスキル。`~/.claude/skills/srv` からリンクを張る |

### 決めごと

- サーバーは tmux の中で動かす。落ちても `(止まった。Enter で閉じる)` で止まるので、ログが残る
- `stop` は tmux を止めるだけ。公開は残すので、また起動すれば同じ URL で開ける。`rm` で公開も外す
- 守る番号(既定 443)の公開には触らない
- 画面からコマンドを書き換える入口は作らない。任意のコマンドを実行させないため。直すのは `srv add` か Claude に頼む
- 画面の登録(adopt)と公開を外す(unpublish)は、サーバー側でスキャンし直した値だけを使う
- POST/DELETE には `X-SLL: 1` ヘッダを要る。ほかのページからの CSRF よけ
- srv の tmux の外で動いているものは止めない(pid を殺すと別物を巻き込みうる)

### ログイン時

`ログイン時のスクリプト` に次の 2 行がある。

```sh
start_tmux srvlist "python3 ~/src/simple-localserver-list/server.py"
srv autostart
```

## テスト

```sh
python3 -m unittest discover -s tests
```
