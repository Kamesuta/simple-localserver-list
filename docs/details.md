# 詳しい話

## ログインしたときにパネルを立てる

LaunchAgent で `srv ui` と `srv autostart` を走らせます。
`srv autostart` は、登録のときに「自動起動」を付けたサーバーを立てるコマンドです。

`~/Library/LaunchAgents/local.srv.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.srv</string>
  <key>ProgramArguments</key><array>
    <string>/bin/zsh</string><string>-lc</string><string>srv ui &amp;&amp; srv autostart</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>
```

```sh
launchctl load ~/Library/LaunchAgents/local.srv.plist
```

ログインした直後で Tailscale がまだつながっていないときは、つながるまでパネルが公開を張り直します。

## 設定

環境変数で変えられます。

| 変数 | 既定 | 意味 |
| --- | --- | --- |
| `SLL_HOME` | `~/.config/simple-localserver-list` | 台帳などの置き場所 |
| `SLL_PORT` / `SLL_HTTPS_PORT` | `7440` / `7463` | パネルが待つポート / tailnet に出す番号 |
| `SLL_PORT_RANGE` | `7400-7599` | `srv port` が番号を出す帯。新しいサーバーはここから選ぶ |
| `SLL_RESERVED_PORTS` | `443` | パネルからも `srv` からも触らない https の番号(カンマ区切り)。ほかの道具が使っている口を守る |
| `SLL_STALE_DAYS` | `3` | お気に入り以外で、これだけ止まっていたら「片付け」に出す |

## コマンド

AI は `srv` コマンドでサーバーを登録します。一覧と書き方は [skill/SKILL.md](../skill/SKILL.md) にあります。
手で打つこともでき、`srv -h` で一覧が出ます。

## 仕組み

- サーバーは tmux セッション `srv-<名前>` の中で動きます。落ちてもログが残ります
- 台帳は 1 サーバーにつき 1 ファイル(`~/.config/simple-localserver-list/servers/<名前>.json`)。複数のセッションが同時に登録してもぶつかりません
- `tailscale serve` の公開は、サーバーが返事をするようになってから張り、止めたら外します。先に張ると、全部のアドレスで待つサーバー(`vite --host` など)が `Address already in use` で落ちるためです
- `srv` の外で動いているもの(登録だけしたもの)は、パネルから止めません
- 外に公開は `cloudflared tunnel --url` を tmux で動かします。時間がたつと tmux の中で止まるので、パネルが落ちていても止まります。URL は、Cloudflare の DNS に名前が載ってから返します(載る前に開くと、しばらく開けなくなるため)
- 再起動では外への公開を止めないので、渡した URL がそのまま使えます(止まっている数秒だけ 502 になります)。停止・削除・起動の失敗では止めます

## 開発

```sh
python3 -m unittest discover tests
python3 tools/demo.py   # 作り物のデータでパネルだけ出す(http://127.0.0.1:7449/)
```

- `srv` は CLI で、tmux と `tailscale serve` は `runner.py` が扱う
- `server.py` はパネルの API、`scan.py` は何が動いていて何が公開されているかの照らし合わせ
- 画面は `web/app.js`

アイコンの線は [Lucide](https://lucide.dev)(ISC)にならっています。
