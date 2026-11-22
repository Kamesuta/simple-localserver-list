// サーバー台帳の画面。/api/servers を 5 秒ごとに読み直して並べる。
"use strict";

const $ = (s) => document.querySelector(s);
const STATE = {
  up: "動いている",
  partial: "動いているが公開なし",
  starting: "起動中",
  crashed: "落ちた",
  down: "止まっている",
  broken: "台帳が壊れている",
};
let busy = 0; // 操作中は読み直さない(押したボタンが消えないように)
let errors = {}; // 名前 → 最後の失敗のメッセージ

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) el.append(k);
  return el;
}

async function api(method, path) {
  const r = await fetch(path, { method, headers: { "X-SLL": "1" } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3500);
}

async function act(btn, fn, okMsg) {
  busy++;
  const label = btn.textContent;
  btn.disabled = true;
  btn.classList.add("busy");
  btn.textContent = label + "…";
  try {
    const r = await fn();
    if (r && r.ok === false) throw new Error(r.message + (r.logs ? "\n" + r.logs : ""));
    if (okMsg) toast(okMsg);
    return r;
  } catch (e) {
    toast(e.message.split("\n")[0]);
    throw e;
  } finally {
    busy--;
    btn.disabled = false;
    btn.classList.remove("busy");
    btn.textContent = label;
    load();
  }
}

function shortPath(p) {
  return (p || "").replace(/^\/Users\/[^/]+/, "~");
}

async function showLogs(name) {
  $("#logs-title").textContent = `${name} のログ`;
  $("#logs-body").textContent = "読み込み中…";
  $("#logs").showModal();
  try {
    const r = await api("GET", `/api/servers/${name}/logs`);
    $("#logs-body").textContent = r.logs || "(tmux のセッションが無い。止まっているときはログも無い)";
    const pre = $("#logs-body");
    pre.scrollTop = pre.scrollHeight;
  } catch (e) {
    $("#logs-body").textContent = e.message;
  }
}

function serverRow(s) {
  if (s.state === "broken") {
    return h("li", {}, h("div", { class: "row-main" }, h("span", { class: "dot crashed" }),
      h("div", { class: "body" }, h("span", { class: "name" }, s.name),
        h("div", { class: "err" }, `台帳のファイルが壊れている: ${s.broken}`))));
  }
  const live = s.state === "up";
  const title = live
    ? h("a", { class: "name", href: s.url, target: "_blank", rel: "noopener" }, s.name)
    : h("span", { class: "name" }, s.name);
  const running = s.state === "up" || s.state === "partial" || s.state === "starting";

  // 起動・再起動の失敗(ログの最後つき)は、その行の下に残しておく
  const run = (btn, op, msg) => act(btn, () => api("POST", `/api/servers/${s.name}/${op}`), msg)
    .then(() => delete errors[s.name])
    .catch((e) => (errors[s.name] = e.message));
  const buttons = [];
  if (s.state === "partial") {
    buttons.push(h("button", { class: "primary", onclick: (ev) => run(ev.target, "start", `${s.name} を公開した`) }, "公開"));
  }
  if (running) {
    buttons.push(h("button", { onclick: (ev) => run(ev.target, "stop", `${s.name} を止めた`) }, "停止"));
    buttons.push(h("button", { onclick: (ev) => run(ev.target, "restart", `${s.name} を再起動した`) }, "再起動"));
  } else {
    buttons.push(h("button", { class: "primary", onclick: (ev) => run(ev.target, "start", `${s.name} を起動した`) }, "起動"));
  }
  buttons.push(h("button", { onclick: () => showLogs(s.name) }, "ログ"));
  buttons.push(h("button", {
    class: "danger ghost",
    onclick: (ev) => {
      if (!confirm(`${s.name} を台帳から消す?\n止めて、tailscale の公開 :${s.https_port} も外す。`)) return;
      act(ev.target, () => api("DELETE", `/api/servers/${s.name}`), `${s.name} を消した`);
    },
  }, "削除"));

  return h("li", {},
    h("div", { class: "row-main" },
      h("span", { class: `dot ${s.state}`, title: STATE[s.state] }),
      h("div", { class: "body" },
        h("div", { class: "title" },
          title,
          h("span", { class: "state" }, STATE[s.state]),
          s.draft && h("span", { class: "tag draft" }, "下書き"),
          s.autostart && h("span", { class: "tag" }, "自動起動")),
        s.note && h("p", { class: "note" }, s.note),
        s.url && h("a", { class: live ? "url" : "url off", href: s.url, target: "_blank", rel: "noopener" }, s.url.replace(/^https:\/\//, "")),
        h("details", {},
          h("summary", {}, "起動のしかた"),
          h("div", { class: "meta" }, `cd ${shortPath(s.cwd)}`),
          s.env_file && h("div", { class: "meta" }, `env file: ${shortPath(s.env_file)}`),
          Object.keys(s.env || {}).length > 0 && h("div", { class: "meta" }, Object.entries(s.env).map(([k, v]) => `${k}=${v}`).join(" ")),
          h("div", { class: "meta" }, s.command),
          h("div", { class: "meta" }, `:${s.port} → https :${s.https_port}` + (s.registered_by ? ` ・ 登録: ${s.registered_by}` : ""))),
      )),
    h("div", { class: "actions" }, buttons),
    errors[s.name] && h("div", { class: "err" }, errors[s.name]));
}

function unregRow(u) {
  const only443 = u.https_ports.length > 0 && u.https_ports.every((p) => p === 443);
  return h("li", {},
    h("div", { class: "row-main" },
      h("span", { class: "dot up" }),
      h("div", { class: "body" },
        h("div", { class: "title" },
          h("span", { class: "name" }, shortPath(u.cwd).split("/").pop() || u.process),
          h("span", { class: "state" }, `:${u.port} ・ ${u.process}`)),
        ...u.urls.map((url) => h("a", { class: "url", href: url, target: "_blank", rel: "noopener" }, url.replace(/^https:\/\//, ""))),
        u.urls.length === 0 && h("div", { class: "url off" }, "tailnet には出ていない"),
        h("details", {},
          h("summary", {}, "拾ったコマンド"),
          h("div", { class: "meta" }, `cd ${shortPath(u.cwd)}`),
          h("div", { class: "meta" }, u.command)))),
    h("div", { class: "actions" },
      only443
        ? h("span", { class: "hint" }, "443 の下にある(ほかの道具が面倒を見ている)")
        : h("button", {
          onclick: (ev) => act(ev.target, () => api("POST", `/api/adopt/${u.port}`), "下書きで登録した"),
        }, "登録")));
}

function orphanRow(o) {
  // 数が多くなりがちなので 1 段に詰める(ボタンは右端)
  return h("li", { class: "compact" },
    h("div", { class: "row-main" },
      h("span", { class: "dot" }),
      h("div", { class: "body" },
        h("div", { class: "title" },
          h("span", { class: "name" }, `:${o.https_port}${o.path === "/" ? "" : o.path}`),
          h("span", { class: "state" }, `→ ${o.target.replace(/^https?:\/\//, "")}`),
          o.funnel && h("span", { class: "tag" }, "funnel(外に公開)"))),
      o.protected
        ? h("span", { class: "hint" }, "443 は触らない")
        : h("button", {
          class: "danger ghost",
          onclick: (ev) => {
            if (!confirm(`tailscale の公開 :${o.https_port} を外す?`)) return;
            act(ev.target, () => api("POST", `/api/unpublish/${o.https_port}`), `:${o.https_port} を外した`);
          },
        }, "外す")));
}

function render(snap) {
  const order = { crashed: 0, starting: 1, up: 2, partial: 3, down: 4, broken: 5 };
  const servers = [...snap.servers].sort((a, b) => (order[a.state] - order[b.state]) || a.name.localeCompare(b.name));
  $("#servers").replaceChildren(...servers.map(serverRow));
  $("#empty").hidden = servers.length > 0;
  const up = servers.filter((s) => s.state === "up").length;
  $("#summary").textContent = `${up} / ${servers.length} 動いている`;

  $("#unreg-sec").hidden = snap.unregistered.length === 0;
  $("#unreg-count").textContent = snap.unregistered.length;
  $("#unreg").replaceChildren(...snap.unregistered.map(unregRow));

  // 同じポートに複数のパスがあるもの(443 の下など)は 1 行ずつ出す
  $("#orphan-sec").hidden = snap.orphans.length === 0;
  $("#orphan-count").textContent = snap.orphans.length;
  $("#orphans").replaceChildren(...snap.orphans.map(orphanRow));
}

let openDetails = new Set();
async function load() {
  if (busy || $("#logs").open) return;
  try {
    // 読み直しで開いていた「起動のしかた」が閉じないよう覚えておく
    openDetails = new Set([...document.querySelectorAll("details[open]")].map((d) => d.closest("li")?.querySelector(".name")?.textContent));
    render(await api("GET", "/api/servers"));
    document.querySelectorAll("details").forEach((d) => {
      if (openDetails.has(d.closest("li")?.querySelector(".name")?.textContent)) d.open = true;
    });
  } catch (e) {
    $("#summary").textContent = `読めない: ${e.message}`;
  }
}

$("#logs-close").addEventListener("click", () => $("#logs").close());
load();
setInterval(load, 5000);
document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && load());
