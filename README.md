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
srv add <名前> --port P [--note メモ] [--env-file F] [--fav] [--autostart] -- <起動コマンド…>
srv ls / logs / start / stop / restart / rm / attach <名前>
srv fav <名前> [--off]   お気に入り(画面の最初のタブ)
srv autostart   # autostart のものを全部立てる(ログイン時)
srv port        # 空いているポート
```

画面: `https://<この Mac の MagicDNS 名>:7463/`(tailnet の中だけ)。見た目は simple-remote-git に揃えてある。

- 下の 3 タブ
  - **お気に入り**: ★ を付けたものをタイルで並べる
  - **新規**: 台帳の全部を登録の新しい順に並べる。前に見たあとで増えたものに色が付き、タブに件数が出る
  - **片付け**: 3 つに分けて並べる
    - お気に入り以外で長く止まっているもの(`SLL_STALE_DAYS`、既定 3 日)
    - 台帳に無いのに動いているもの
    - 宛先の止まった tailscale の公開
- タイルや行を押すと、そのページへ 1 回で飛ぶ。止まっているものは、起動してから飛ぶ
- ⋯ か長押しでシートが出る。停止・再起動・ログ・起動のしかた・削除はここ
- アイコンとタイトルは、各ページの favicon と `<title>` から拾う(`meta.py`)。取れなければ頭文字

## 仕組み

| ファイル | 役目 |
|---|---|
| `srv` | CLI。`~/.local/bin/srv` からリンクを張る |
| `registry.py` | 台帳 `~/.config/simple-localserver-list/servers/<名前>.json` の読み書き(`SLL_HOME` で場所を変えられる)。お気に入りと、最後に動いていた時刻(`last_up.json`)も |
| `meta.py` | 動いているページのタイトルと favicon を拾う(127.0.0.1 だけ。1 分覚える。一覧を待たせないよう後ろで取る) |
| `scan.py` | `tailscale serve status --json`・`lsof`・`ps`・`tmux` を照らし合わせ、状態(🟢 up / 🟡 partial / ⏳ starting / 🔴 crashed / ⚪ down)を付ける |
| `runner.py` | tmux セッション `srv-<名前>` での起動・停止、`tailscale serve` の張る/外す |
| `server.py` | 画面の HTTP。127.0.0.1:7440 で待ち、自分を https :7463 で公開する |
| `web/` | 画面(素の JS、PWA) |
| `skill/SKILL.md` | セッション向けのスキル。`~/.claude/skills/srv` からリンクを張る |

### 決めごと

- サーバーは tmux の中で動かす。落ちても `(止まった。Enter で閉じる)` で止まるので、ログが残る。この印が出たら「落ちた」と判定する
- 公開は、サーバーが返事をするようになってから張る。先に張ると、tailscale が tailnet のアドレスで同じ番号を取ってしまい、全部のアドレスで待つサーバー(`vite --host`、`python -m http.server`)が Address already in use で落ちる(実測)
- `stop` は公開も外す。https の番号は台帳に残るので、また起動すれば同じ URL で開ける。`rm` は台帳からも消す
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
