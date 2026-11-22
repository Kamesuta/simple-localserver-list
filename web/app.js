// サーバー台帳の画面。下の 3 タブ(お気に入り / 新規 / 片付け)と、⋯ か長押しで出るシート。
// 押せば 1 回でそのページへ飛ぶ。止まっているものは起動してから飛ぶ。
import { setupTheme } from "./theme.js";

const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 覚えられなくても動く */ } },
};

const STATE = { up: "動いている", partial: "公開なし", starting: "起動中", crashed: "落ちた", down: "止まっている" };
const TABS = {
  fav: { h1: "サーバー", sub: () => snap?.host?.split(".")[0] || "この Mac" },
  new: { h1: "新規", sub: () => "押すと開く ・ ★ でお気に入りへ ・ ⋯ で操作" },
  tidy: { h1: "片付け", sub: () => "消すか登録するかを選ぶ" },
};
const ICONS = {
  more: '<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>',
  star: '<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.8l-5.2 2.8 1-5.8-4.3-4.1 5.9-.9z"/></svg>',
  ext: '<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17L17 7"/><path d="M8 7h9v9"/></svg>',
  globe: '<svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18"/><path d="M12 3a14 14 0 0 0 0 18"/></svg>',
  lock: '<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
};

let snap = null;
let loadedAt = 0;
let tab = TABS[store.get("tab")] ? store.get("tab") : "fav";
let busy = 0;
let seenNew = Number(store.get("seenNew", "0")); // 新規のタブを最後に見た時刻(秒)
let seenShown = seenNew; // 今開いている新規のタブで「新しい」と色を付ける境目
const errors = {}; // 名前 → 最後の失敗(ログの最後つき)
let sheet = null; // { name, key }
let reorder = false; // お気に入りを並べ替えている間(タイルが揺れている間)は true。描き直さない
let holding = null; // 長押し中のタイル(このまま動かせば並べ替えに入る)
let drag = null; // つかんで動かしているタイル { t, dx, dy, before }

// ---- 小さな道具 ----
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c);
  return el;
}
function svg(name) {
  const t = document.createElement("template");
  t.innerHTML = ICONS[name];
  return t.content.firstChild;
}
async function api(method, path) {
  const r = await fetch(path, { method, headers: { "X-SLL": "1" } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3200);
}
async function act(fn, okMsg) {
  busy++;
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
    load(true);
  }
}
const shortPath = (p) => (p || "").replace(/^\/Users\/[^/]+/, "~");
const running = (s) => ["up", "partial", "starting"].includes(s.state);
const created = (s) => (Date.parse(s.created_at) || 0) / 1000;
function hue(name) {
  let n = 0;
  for (const ch of name) n = (n * 31 + ch.codePointAt(0)) >>> 0;
  return n % 8;
}
const dayStart = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
function daysAgo(sec) {
  return Math.round((dayStart(new Date()) - dayStart(new Date(sec * 1000))) / 86400000);
}
function when(sec) {
  if (!sec) return "不明";
  const d = new Date(sec * 1000);
  const mins = Math.floor((Date.now() - d) / 60000);
  if (mins < 1) return "たった今";
  if (mins < 60) return `${mins} 分前`;
  if (mins < 60 * 6) return `${Math.floor(mins / 60)} 時間前`;
  const hm = `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  const diff = daysAgo(sec);
  if (diff === 0) return `今日 ${hm}`;
  if (diff === 1) return `昨日 ${hm}`;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
const dayGroup = (sec) => { const d = daysAgo(sec); return d <= 0 ? "今日" : d === 1 ? "昨日" : "もっと前"; };

// ページのアイコン。取れなければ(読み込みに失敗したら)頭文字
function ico(item, size = "", live = running(item)) {
  const el = h("div", { class: `ico c${hue(item.name || "?")} ${size} ${live ? "" : "dim"}`, "aria-hidden": "true" });
  const letter = (item.name || "").match(/[\p{L}\p{N}]/u)?.[0].toUpperCase() || "?";
  if (item.icon && live) {
    const img = h("img", { src: `/api/icon/${item.port}`, alt: "", loading: "lazy" });
    img.addEventListener("error", () => img.replaceWith(letter));
    el.append(img);
  } else el.append(letter);
  return el;
}

// ---- 押したら飛ぶ ----
// 動いていればリンクのまま開く。止まっていれば、先に空のタブを開いて(後から開くとポップアップ扱いで止められる)、
// 起動が済んだらそのタブを URL へ進める
async function launch(s) {
  const w = window.open("", "_blank");
  if (w) {
    w.document.title = `${s.name} を起動中`;
    w.document.body.innerHTML = `<p style="font:15px system-ui;padding:40px;color:#596068">${s.name} を起動中…</p>`;
  }
  try {
    await act(() => api("POST", `/api/servers/${s.name}/start`), `${s.name} を起動した`);
    delete errors[s.name];
    if (w) {
      w.opener = null;
      w.location.href = s.url;
    } else location.href = s.url;
  } catch (e) {
    if (w) w.close();
    errors[s.name] = e.message;
    openSheet(s.name);
  }
}
function wireOpen(a, s, onHoldMove = null) {
  // 長押しはシート(iOS のリンクのプレビューは CSS の -webkit-touch-callout で止めてある)。
  // onHoldMove があるとき(お気に入りのタイル)は iPhone のホーム画面と同じで、
  // 長押しして離せばシート、長押ししたまま動かせば並べ替えに入る。
  let timer = 0;
  let long = false;
  let start = null;
  const cancel = () => clearTimeout(timer);
  a.addEventListener("pointerdown", (ev) => {
    if (reorder) return;
    long = false;
    start = { x: ev.clientX, y: ev.clientY };
    timer = setTimeout(() => {
      long = true;
      if (!onHoldMove) return openSheet(s.name);
      holding = a.closest(".tile");
      holding?.classList.add("held");
    }, 500);
  });
  a.addEventListener("pointermove", (ev) => {
    if (!holding || holding !== a.closest(".tile") || !start) return;
    if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 8) return;
    const t = holding;
    t.classList.remove("held");
    holding = null;
    onHoldMove(t, ev);
  });
  a.addEventListener("pointerup", () => {
    cancel();
    if (holding && holding === a.closest(".tile")) {
      holding.classList.remove("held");
      holding = null;
      openSheet(s.name);
    }
  });
  a.addEventListener("pointercancel", () => { cancel(); holding?.classList.remove("held"); holding = null; });
  a.addEventListener("pointerleave", () => { if (!holding) cancel(); });
  a.addEventListener("contextmenu", (ev) => ev.preventDefault());
  // リンクや画像をブラウザが自分で引きずり始めると pointercancel になり、並べ替えに入れない
  a.draggable = false;
  a.addEventListener("dragstart", (ev) => ev.preventDefault());
  a.addEventListener("click", (ev) => {
    if (reorder) { ev.preventDefault(); return; }
    if (long) { ev.preventDefault(); long = false; return; }
    if (s.state !== "up") {
      ev.preventDefault();
      a.querySelector(".st, .meta span:nth-child(2)")?.replaceChildren("起動中…");
      a.querySelector(".dot")?.setAttribute("class", "dot starting");
      launch(s);
    }
  });
  return a;
}
function moreBtn(s, cls) {
  return h("button", { type: "button", class: cls, "aria-label": `${s.name} の操作`, onclick: () => openSheet(s.name) }, svg("more"));
}
function toggleFav(s) {
  return act(() => api("POST", `/api/servers/${s.name}/${s.favorite ? "unfavorite" : "favorite"}`),
    s.favorite ? `${s.name} をお気に入りから外した` : `${s.name} をお気に入りに入れた`).catch(() => {});
}
function starBtn(s, cls = "side") {
  return h("button", {
    type: "button", class: `${cls} star ${s.favorite ? "on" : ""}`,
    "aria-label": s.favorite ? `${s.name} をお気に入りから外す` : `${s.name} をお気に入りに入れる`,
    "aria-pressed": s.favorite ? "true" : "false",
    onclick: () => toggleFav(s),
  }, svg("star"));
}
const subText = (s) => s.note || s.title || "";
const shareChip = (s) => s.share && h("span", { class: "chip share", title: s.share.url }, svg("globe"), "外に公開中");
const minsLeft = (st) => Math.max(0, Math.round((st.expires - Date.now() / 1000) / 60));
function confirmDelete(s) {
  if (!confirm(`${s.name} を台帳から消す?\n止めて、tailscale の公開 :${s.https_port} も外す。`)) return;
  if (sheet) closeSheet();
  act(() => api("DELETE", `/api/servers/${s.name}`), `${s.name} を消した`).catch(() => {});
}

// ---- お気に入り ----
function favList(servers) {
  const order = snap?.fav_order || [];
  const pos = (s) => { const i = order.indexOf(s.name); return i < 0 ? Infinity : i; };
  return servers.filter((s) => s.favorite).sort((a, b) => (pos(a) - pos(b)) || a.name.localeCompare(b.name));
}
function renderFav(servers) {
  const favs = favList(servers);
  if (!favs.length) {
    return [h("div", { class: "empty" },
      h("b", {}, "お気に入りはまだ無い"), h("br"),
      "新規のタブで ★ を押すと、ここにタイルで並ぶ。")];
  }
  const grid = h("div", { class: "grid", id: "fav-grid" }, favs.map((s) => {
    const st = STATE[s.state] + (running(s) ? "" : " ・ 押すと起動");
    return h("div", { class: `tile ${running(s) ? "" : "off"}`, "data-name": s.name },
      moreBtn(s, "more"),
      wireOpen(h("a", { href: s.url, target: "_blank", rel: "noopener" },
        ico(s),
        h("div", {}, h("div", { class: "nm" }, s.name), h("div", { class: "sub" }, subText(s) || " ")),
        s.share && h("div", {}, shareChip(s)),
        h("div", { class: "foot" }, h("span", { class: `dot ${s.state}` }), h("span", { class: "st" }, st),
          h("span", { class: "port" }, `:${s.https_port}`))), s,
      (t, ev) => { setReorder(true); startDrag(t, ev); }));
  }));
  wireDrag(grid);
  return [grid];
}
// 並べ替えの出入りは DOM を作り直さず、印(class)だけ付け替える(つかんでいる指を離さないため)
function setReorder(on) {
  if (reorder === on) return;
  reorder = on;
  if (on && sheet) closeSheet();
  $("fav-grid")?.classList.toggle("editing", on);
  $("done").hidden = !on;
  $("refresh").hidden = on;
  $("sub").textContent = on ? "つかんで動かす ・ 何もない所を押すと終わる" : TABS[tab].sub();
  if (!on) render();
}
function gridOrder() {
  return [...document.querySelectorAll("#fav-grid .tile")].map((t) => t.dataset.name);
}
function saveOrder(names) {
  snap.fav_order = names; // 画面の並びはもう変わっているので、保存は裏で
  fetch("/api/fav-order", {
    method: "POST", headers: { "X-SLL": "1", "Content-Type": "application/json" },
    body: JSON.stringify({ order: names }),
  }).then((r) => r.ok || r.json().then((b) => toast(`並び順を保存できなかった: ${b.error || r.status}`)))
    .catch((e) => toast(`並び順を保存できなかった: ${e.message}`));
}
function startDrag(t, ev) {
  const grid = $("fav-grid");
  const r = t.getBoundingClientRect();
  drag = { t, dx: ev.clientX - r.left, dy: ev.clientY - r.top, before: gridOrder().join() };
  t.classList.add("dragging");
  try { grid.setPointerCapture(ev.pointerId); } catch { /* 指がもう離れている */ }
  follow(ev);
}
// つかんだタイルを指に付いてこさせ、指の下のタイルと入れ替える
function follow(ev) {
  const t = drag.t;
  const over = document.elementFromPoint(ev.clientX, ev.clientY)?.closest("#fav-grid .tile");
  if (over && over !== t) {
    const tiles = [...t.parentNode.children];
    if (tiles.indexOf(t) < tiles.indexOf(over)) over.after(t);
    else over.before(t);
  }
  t.style.transform = "none";
  const r = t.getBoundingClientRect();
  t.style.transform = `translate(${ev.clientX - drag.dx - r.left}px, ${ev.clientY - drag.dy - r.top}px) scale(1.06)`;
}
function wireDrag(grid) {
  grid.addEventListener("pointerdown", (ev) => {
    const t = ev.target.closest(".tile");
    if (!reorder) return;
    if (!t) return setReorder(false); // 揺れている間に何もない所を押したら終わる
    if (ev.button > 0) return;
    ev.preventDefault();
    startDrag(t, ev);
  });
  grid.addEventListener("pointermove", (ev) => drag && follow(ev));
  const end = () => {
    if (!drag) return;
    drag.t.classList.remove("dragging");
    drag.t.style.transform = "";
    const names = gridOrder();
    if (names.join() !== drag.before) saveOrder(names);
    drag = null;
  };
  grid.addEventListener("pointerup", end);
  grid.addEventListener("pointercancel", end);
  // 長押しのあと・つかんでいる間は、指を動かしてもページをスクロールさせない(iOS は touchmove で止める)
  grid.addEventListener("touchmove", (ev) => { if (holding || drag) ev.preventDefault(); }, { passive: false });
}

// ---- 新規 ----
function renderNew(servers) {
  if (!servers.length) {
    return [h("div", { class: "empty" },
      h("b", {}, "台帳はまだ空"), h("br"),
      "セッションが srv add でサーバーを立てると、ここに新しい順で並ぶ。")];
  }
  const groups = new Map();
  for (const s of [...servers].sort((a, b) => created(b) - created(a))) {
    const g = dayGroup(created(s));
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(s);
  }
  const out = [];
  for (const [label, rows] of groups) {
    out.push(h("div", { class: "part" }, label, h("span", {}, rows.length)));
    for (const s of rows) {
      const extra = subText(s) || s.registered_by;
      out.push(h("div", { class: `row ${created(s) > seenShown ? "fresh" : ""}` },
        ico(s, "sm"),
        wireOpen(h("a", { href: s.url, target: "_blank", rel: "noopener" },
          h("div", { class: "l1" }, h("span", { class: "nm" }, s.name), s.draft && h("span", { class: "chip" }, "下書き"), shareChip(s)),
          h("div", { class: "meta" },
            h("span", { class: `dot ${s.state}` }), h("span", {}, STATE[s.state]),
            h("span", {}, `・ ${when(created(s))}`),
            extra && h("span", { class: "by" }, `・ ${extra}`))), s),
        starBtn(s),
        moreBtn(s, "side")));
    }
  }
  return out;
}

// ---- 片付け ----
const lockedUnreg = (u) => u.https_ports.length > 0 && u.https_ports.every((p) => p === 443);
function renderTidy() {
  const stale = snap.servers.filter((s) => s.stale || s.broken);
  const unreg = snap.unregistered.filter((u) => !lockedUnreg(u));
  const orphans = snap.orphans.filter((o) => !o.protected);
  const locked = [...new Set([
    ...snap.unregistered.filter(lockedUnreg).map((u) => u.title || u.process),
    ...snap.orphans.filter((o) => o.protected).map((o) => o.path),
  ])];
  const none = () => h("div", { class: "allclear" }, "なし");
  return [h("div", { class: "tidy" },
    h("div", { class: "part" }, `長く止まっている(お気に入り以外・${snap.stale_days} 日)`, h("span", {}, stale.length)),
    stale.length ? stale.map((s) => h("div", { class: "trow" },
      h("span", { class: "dot" }),
      h("div", { class: "txt" },
        h("span", { class: "nm" }, s.name),
        h("small", {}, s.broken ? `台帳が壊れている: ${s.broken}` : `最後に動いていた: ${when(s.last_up)}${s.registered_by ? ` ・ ${s.registered_by}` : ""}`)),
      h("button", { type: "button", class: "pill bad", onclick: () => confirmDelete(s) }, "削除"))) : none(),

    h("div", { class: "part" }, "台帳に無いのに動いている", h("span", {}, unreg.length)),
    unreg.length ? unreg.map((u) => {
      const name = u.title || shortPath(u.cwd).split("/").pop() || u.process;
      return h("div", { class: "trow" },
        ico({ name, icon: u.icon, port: u.port }, "sm", true),
        h("div", { class: "txt" },
          u.urls[0] ? h("a", { class: "nm", href: u.urls[0], target: "_blank", rel: "noopener" }, name) : h("span", { class: "nm" }, name),
          h("small", { class: "cmd" }, `:${u.port}  ${u.command}`)),
        h("button", {
          type: "button", class: "pill go",
          onclick: () => act(() => api("POST", `/api/adopt/${u.port}`), "下書きで登録した。新規のタブに出る").catch(() => {}),
        }, "登録"));
    }) : none(),

    h("div", { class: "part" }, "宛先の止まった tailscale の公開", h("span", {}, orphans.length)),
    orphans.length ? orphans.map((o) => h("div", { class: "trow slim" },
      h("span", { class: "pnum" }, `:${o.https_port}`),
      h("span", { class: "tgt" }, `→ ${o.target.replace(/^https?:\/\//, "")}${o.funnel ? "  (funnel)" : ""}`),
      h("button", {
        type: "button", class: "pill bad ghost",
        onclick: () => {
          if (!confirm(`tailscale の公開 :${o.https_port} を外す?`)) return;
          act(() => api("POST", `/api/unpublish/${o.https_port}`), `:${o.https_port} を外した`).catch(() => {});
        },
      }, "外す"))) : none(),

    locked.length > 0 && h("div", { class: "lock" }, svg("lock"),
      h("span", {}, `ほかの道具が面倒を見ているもの(:443 の下): ${locked.join(" ・ ")}`)),
  )];
}
function tidyCount() {
  if (!snap) return 0;
  return snap.servers.filter((s) => s.stale || s.broken).length
    + snap.unregistered.filter((u) => !lockedUnreg(u)).length
    + snap.orphans.filter((o) => !o.protected).length;
}

// ---- シート ----
function closeSheet() {
  sheet = null;
  $("layer").replaceChildren();
  document.body.classList.remove("locked");
}
const sheetKey = (s) => [s.state, s.favorite, s.share?.url || "", errors[s.name] || ""].join("|");
function openSheet(name) {
  const s = snap?.servers.find((x) => x.name === name);
  if (!s) return;
  sheet = { name, key: sheetKey(s) };
  const log = h("pre", { class: "log" }, "読み込み中…");
  const run = running(s);
  // 押したボタンは、終わるまで「…中」にして押せなくする(起動は返事を待つので数秒かかる)
  const op = (ev, path, msg, doing) => {
    const b = ev.currentTarget;
    b.disabled = true;
    b.textContent = doing;
    return act(() => api("POST", `/api/servers/${s.name}/${path}`), msg)
      .then(() => delete errors[s.name])
      .catch((e) => (errors[s.name] = e.message))
      .finally(() => { if (sheet?.name === s.name) openSheet(s.name); });
  };
  const body = h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": `${s.name} の操作` },
    h("div", { class: "grab" }),
    h("div", { class: "sh-head" },
      ico(s, "lg"),
      h("div", { class: "names2" },
        h("div", { class: "l1" }, h("h2", {}, s.name), s.draft && h("span", { class: "chip" }, "下書き")),
        subText(s) && h("span", { class: "sub" }, subText(s))),
      starBtn(s, "icon-btn")),
    h("div", { class: "meta-row" },
      h("span", {}, h("span", { class: `dot ${s.state}` }), `${STATE[s.state]}${s.registered_by ? ` ・ 登録: ${s.registered_by}` : ""}`),
      h("span", { class: "port" }, `:${s.port} → :${s.https_port}`)),
    s.state === "up"
      ? h("a", { class: "open", href: s.url, target: "_blank", rel: "noopener" }, "開く", svg("ext"))
      : h("button", {
        type: "button", class: "open",
        onclick: (ev) => { ev.currentTarget.disabled = true; ev.currentTarget.textContent = "起動中…"; launch(s); },
      }, run ? "公開して開く" : "起動して開く", svg("ext")),
    h("p", { class: "url" }, s.url.replace(/^https:\/\//, "")),
    h("div", { class: "btns" },
      run
        ? [h("button", { type: "button", class: "btn", onclick: (ev) => op(ev, "restart", `${s.name} を再起動した`, "再起動中…") }, "再起動"),
          h("button", { type: "button", class: "btn", onclick: (ev) => op(ev, "stop", `${s.name} を止めた`, "停止中…") }, "停止")]
        : h("button", { type: "button", class: "btn", onclick: (ev) => op(ev, "start", `${s.name} を起動した`, "起動中…") }, "起動だけ"),
      h("button", {
        type: "button", class: "btn",
        onclick: () => navigator.clipboard?.writeText(s.url).then(() => toast("URL をコピーした"), () => toast("コピーできなかった")),
      }, "URL をコピー")),
    errors[s.name] && h("div", { class: "err-box" }, errors[s.name]),
    h("div", { class: "sec" }, h("b", {}, "外に公開"), h("span", {}, "trycloudflare ・ 誰でも開ける")),
    shareBox(s, run),
    h("div", { class: "sec" }, h("b", {}, "ログ"), h("span", {}, "tmux の最後の行")),
    log,
    h("div", { class: "sec" }, h("b", {}, "起動のしかた"), h("span", {}, "直すときは Claude に頼む")),
    h("div", { class: "how" },
      h("div", { class: "dir" }, shortPath(s.cwd)),
      s.env_file && h("div", { class: "dir" }, `env file: ${shortPath(s.env_file)}`),
      Object.keys(s.env || {}).length > 0 && h("div", {}, Object.entries(s.env).map(([k, v]) => `${k}=${v}`).join(" ")),
      h("div", {}, s.command),
      s.autostart && h("div", { class: "dir" }, "ログイン時に自動で起動する")),
    h("button", { type: "button", class: "del", onclick: () => confirmDelete(s) }, "台帳から削除"));
  $("layer").replaceChildren(h("div", { class: "sheet-wrap" }, h("div", { class: "backdrop", onclick: closeSheet }), body));
  document.body.classList.add("locked");
  api("GET", `/api/servers/${s.name}/logs`).then((r) => {
    const lines = (r.logs || "").split("\n").filter((x) => x.trim()).slice(-30);
    const none = running(s) && !s.session
      ? "(srv の外で動いているので、ログはここに無い。立てたところで見る)"
      : "(ログは無い。止まっているときは残らない)";
    log.textContent = lines.length ? lines.join("\n") : none;
    log.scrollTop = log.scrollHeight;
  }).catch((e) => (log.textContent = e.message));
}

// 外に公開(trycloudflare)。URL は毎回新しく、時間がたつと自動で止まる
function shareBox(s, run) {
  if (s.share) {
    const left = minsLeft(s.share);
    return h("div", { class: "share-box on" },
      h("a", { class: "share-url", href: s.share.url, target: "_blank", rel: "noopener" }, s.share.url.replace(/^https:\/\//, "")),
      h("p", { class: "why" }, `URL を知っていれば誰でも開ける ・ あと ${left} 分で自動で止まる`),
      h("div", { class: "btns" },
        h("button", {
          type: "button", class: "btn",
          onclick: () => navigator.clipboard?.writeText(s.share.url).then(() => toast("外向きの URL をコピーした"), () => toast("コピーできなかった")),
        }, "URL をコピー"),
        h("button", {
          type: "button", class: "btn bad",
          onclick: (ev) => {
            ev.currentTarget.disabled = true;
            act(() => api("POST", `/api/servers/${s.name}/unshare`), "外への公開を止めた").catch(() => {});
          },
        }, "止める")));
  }
  if (!run) return h("p", { class: "why" }, "動いているときだけ外に出せる");
  return h("div", { class: "share-box" },
    h("button", {
      type: "button", class: "btn wide",
      onclick: (ev) => {
        if (!confirm(`${s.name} を、誰でも開ける URL で外に出す?\n1 時間で自動で止まる。`)) return;
        const b = ev.currentTarget;
        b.disabled = true;
        b.textContent = "URL を作っています…(30 秒ほど)";
        act(() => api("POST", `/api/servers/${s.name}/share?minutes=60`), "外に公開した")
          .then((r) => r?.url && navigator.clipboard?.writeText(r.url).then(() => toast("外に公開した。URL をコピーした"), () => {}))
          .catch((e) => (errors[s.name] = e.message));
      },
    }, svg("globe"), "外に公開する(1 時間)"));
}

// ---- 全体 ----
function markNewSeen() {
  if (!snap) return;
  seenShown = seenNew;
  seenNew = Math.max(seenNew, snap.now);
  store.set("seenNew", String(seenNew));
}
function setTab(t) {
  if (tab === "new" && t !== "new") seenShown = seenNew; // 新規を離れたら色付けを消す
  setReorder(false);
  tab = t;
  store.set("tab", t);
  if (t === "new") markNewSeen();
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-pressed", String(b.dataset.tab === t));
  for (const k of Object.keys(TABS)) $(k).hidden = k !== t;
  window.scrollTo(0, 0);
  render();
}

function render() {
  if (!snap) return;
  const servers = snap.servers.filter((s) => !s.broken);
  $("h1").textContent = TABS[tab].h1;
  if (!reorder) $("sub").textContent = TABS[tab].sub();
  // 並べ替えの最中と長押しの最中は描き直さない(つかんでいるタイルが消えるため)。完了で描き直す
  if (tab === "fav" && !reorder && !holding) $("fav").replaceChildren(...renderFav(servers));
  if (tab === "new") $("new").replaceChildren(...renderNew(servers));
  if (tab === "tidy") $("tidy").replaceChildren(...renderTidy());

  const fresh = servers.filter((s) => created(s) > seenNew).length;
  $("new-n").hidden = fresh === 0 || tab === "new";
  $("new-n").textContent = fresh;
  const tc = tidyCount();
  $("tidy-n").hidden = tc === 0;
  $("tidy-n").textContent = tc;

  const up = servers.filter((s) => s.state === "up").length;
  $("status").dataset.state = "done";
  $("status-text").textContent = servers.length ? `${up} / ${servers.length} 動いている` : "台帳は空";

  if (sheet) {
    const s = snap.servers.find((x) => x.name === sheet.name);
    if (!s) closeSheet();
    else if (sheetKey(s) !== sheet.key) openSheet(s.name); // 状態が変わったときだけ描き直す(ログのスクロールを保つ)
  }
}

let loading = false;
async function load(force = false) {
  if (loading || (busy && !force)) return;
  loading = true;
  if (force) $("status").dataset.state = "busy";
  try {
    const first = !snap;
    snap = await api("GET", "/api/servers");
    loadedAt = Date.now();
    if (first && !seenNew) {
      // 初めて開いたときは、今あるものを「新しい」扱いにしない
      seenNew = seenShown = snap.now;
      store.set("seenNew", String(seenNew));
    } else if (first && tab === "new") markNewSeen();
    render();
  } catch (e) {
    $("status").dataset.state = "err";
    $("status-text").textContent = `読めない: ${e.message}`;
  } finally {
    loading = false;
  }
}

setupTheme();
for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => setTab(b.dataset.tab));
$("refresh").addEventListener("click", () => load(true));
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  if (sheet) closeSheet();
  else setReorder(false);
});
$("done").addEventListener("click", () => setReorder(false));
// 揺れている間に、タイルの外(余白や上の帯)を押しても終わる
document.addEventListener("pointerdown", (ev) => { if (reorder && !ev.target.closest("#fav-grid .tile, #done")) setReorder(false); });
document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && load());
setInterval(() => {
  if (loadedAt) $("ago").textContent = `${Math.round((Date.now() - loadedAt) / 1000)} 秒前`;
}, 1000);
setInterval(load, 5000);
setTab(tab);
load(true);
