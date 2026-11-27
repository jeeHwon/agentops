// aops console: every dbx command of the CLI as a screen, on the same /api/v1 the CLI calls.
// No build step and no external libraries. All text goes through textContent (no HTML injection).

const API = "/api/v1";
const state = { me: null, version: null, toApprove: 0, timers: new Set() };

// ── API ────────────────────────────────────────────────────────────────────

class ApiError extends Error {
  constructor(status, problem) {
    super(problem.title || `HTTP ${status}`);
    Object.assign(this, { status, ...problem });
  }
}

async function api(method, path, { json, form, blob } = {}) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (json !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(json); }
  if (form) opts.body = form;
  const resp = await fetch(API + path, opts);
  if (!resp.ok) {
    const raw = await resp.text();  // read once: a body that is not JSON is shown as text
    let problem;
    try { problem = JSON.parse(raw); } catch { problem = { title: raw.slice(0, 200) || `HTTP ${resp.status}` }; }
    if (resp.status === 401 && problem.code === "login_required" && problem.data && problem.data.login) {
      location.href = `${problem.data.login}?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`;
      return new Promise(() => {});  // the page is leaving for the login
    }
    throw new ApiError(resp.status, problem);
  }
  if (blob) return resp;
  const text = await resp.text();
  return text ? JSON.parse(text) : null;
}

const get = (p) => api("GET", p);
const post = (p, json) => api("POST", p, { json: json ?? {} });

// ── DOM helpers ────────────────────────────────────────────────────────────

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

// replaceChildren without the "null" text a skipped part would leave
const fill = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
const enc = encodeURIComponent;
const short = (sha) => (sha ? sha.slice(0, 12) : "");

// ── icons: small line drawings in one style (24×24, stroke), drawn here so the console needs nothing from outside ──
// A part is a path ("M…"), a circle ("c cx,cy,r") or a rectangle ("r x,y,w,h,rx"); parts are split by "|".
const SVGNS = "http://www.w3.org/2000/svg";
const ICONS = {
  grid: "r3,3,7,7,1.5|r14,3,7,7,1.5|r3,14,7,7,1.5|r14,14,7,7,1.5",
  inbox: "M3 13h5l1.5 3h5l1.5-3h5|M5.5 5h13l2.5 8v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5z",
  upload: "M12 15V4|M7.5 8.5 12 4l4.5 4.5|M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3",
  download: "M12 4v11|M7.5 10.5 12 15l4.5-4.5|M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3",
  sparkle: "M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z|M18.5 16v4|M16.5 18h4",
  sliders: "M4 7h9|M17 7h3|c15,7,2|M4 17h3|M11 17h9|c9,17,2",
  help: "c12,12,9|M9.6 9.4a2.5 2.5 0 1 1 3.4 2.4c-.6.3-1 .8-1 1.5v.4|M12 17h.01",
  compass: "c12,12,9|M15.5 8.5l-2 5-5 2 2-5z",
  terminal: "r3,4,18,16,2|M7 9.5l3 2.5-3 2.5|M13 15h4",
  plus: "M12 5v14|M5 12h14",
  edit: "M4 20h4L19 9l-4-4L4 16z|M13.5 6.5l4 4",
  check: "M5 12.5l4.5 4.5L19 7.5",
  x: "M6 6l12 12|M18 6 6 18",
  alert: "M12 4 21 20H3z|M12 10v4|M12 17h.01",
  info: "c12,12,9|M12 11v5|M12 8h.01",
  checklist: "r5,4,14,17,2|r9,2.5,6,3,1|M9 13l2 2 4-4",
  package: "M3.5 7.5 12 3.5l8.5 4v9L12 20.5l-8.5-4z|M3.5 7.5 12 11.5l8.5-4|M12 11.5v9",
  flask: "M9.5 3h5|M10.5 3v6L5 18.6A1.6 1.6 0 0 0 6.4 21h11.2a1.6 1.6 0 0 0 1.4-2.4L13.5 9V3|M7.5 15h9",
  send: "M21 3 3 10.5l7.5 3L13.5 21z|M21 3 10.5 13.5",
  shield: "M12 3l8 3v6c0 4.4-3.3 7.9-8 9-4.7-1.1-8-4.6-8-9V6z|M8.5 12l2.5 2.5 4.5-5",
  key: "c8,15,4|M11 12l8.5-8.5|M16.5 6.5l2.5 2.5|M14 9l2 2",
  live: "c12,12,2|M8 8a5.7 5.7 0 0 0 0 8|M16 8a5.7 5.7 0 0 1 0 8|M5.2 5.2a9.6 9.6 0 0 0 0 13.6|M18.8 5.2a9.6 9.6 0 0 1 0 13.6",
  external: "M14 4h6v6|M20 4l-9 9|M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  search: "c11,11,7|M20 20l-4-4",
  logout: "M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3|M10 16l-4-4 4-4|M6 12h10",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  doc: "M6 3h8l4 4v14H6z|M14 3v4h4|M9 12h6|M9 16h6",
  code: "M8 7l-5 5 5 5|M16 7l5 5-5 5|M13.5 5l-3 14",
  chat: "M4 5h16v11H9l-5 4z|M8 9.5h8|M8 12.5h5",
  arrow: "M5 12h14|M13 6l6 6-6 6",
  back: "M19 12H5|M11 6l-6 6 6 6",
  undo: "M9 14 4 9l5-5|M4 9h10a6 6 0 0 1 0 12h-3",
  clock: "c12,12,9|M12 7v5l3 2",
  layers: "M12 3 3 8l9 5 9-5z|M3 13l9 5 9-5",
  pointer: "M6 3l13 7-5.5 1.8L11 17.5z",
  flag: "M5 21V4|M5 4h11l-2 4 2 4H5",
};
function icon(name, size = 16, cls = "") {
  const make = (tag, attrs) => {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };
  const svg = make("svg", { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor", "stroke-width": 1.9,
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: `ico-svg ${cls}`.trim() });
  for (const part of (ICONS[name] || ICONS.info).split("|")) {
    const n = part.slice(1).split(",").map(Number);
    svg.append(part[0] === "c" ? make("circle", { cx: n[0], cy: n[1], r: n[2] })
      : part[0] === "r" ? make("rect", { x: n[0], y: n[1], width: n[2], height: n[3], rx: n[4] || 0 }) : make("path", { d: part }));
  }
  return svg;
}
// An empty list: an icon, what is missing, a hint, and the one thing to do about it
const empty = (ico, title, text = "", action = null) => h("div", { class: "empty" }, h("div", { class: "empty-ico" }, icon(ico, 22)), h("b", {}, title),
  text ? h("div", {}, text) : null, action);

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const two = (n) => String(n).padStart(2, "0");
  const today = new Date().toDateString() === d.toDateString();
  return `${today ? "오늘" : `${two(d.getMonth() + 1)}-${two(d.getDate())}`} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

function size(n) {
  if (n === null || n === undefined) return "";
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

const LABEL = {
  lifecycle: { registered: ["등록됨", ""], production: ["운영 중", "ok"], archived: ["보관", ""] },
  version: { registered: ["등록됨", ""], production: ["운영", "ok"], retired: ["이전 운영", "plain"] },
  validation: { queued: ["대기", "info run"], running: ["평가 중", "info run"], passed: ["통과", "ok"], failed: ["실패", "bad"], stale: ["무효", "warn"], error: ["오류", "bad"] },
  verdict: { pass: ["적합", "ok"], warn: ["주의", "warn"], fail: ["부적합", "bad"] },
  item: { pass: ["적합", "ok"], warn: ["주의", "warn"], fail: ["부적합", "bad"], na: ["해당 없음", "plain"], unchecked: ["답 없음", "bad"] },
  decision: { validating: ["평가 중", "info run"], pending_approval: ["승인 대기", "warn"], approved: ["승인됨", "ok"], rejected: ["반려", "bad"], canceled: ["취소", "plain"] },
  deployment: { running: ["배포 중", "info run"], succeeded: ["완료", "ok"], failed: ["실패", "bad"] },
  git: { synced: ["Git", "ok"], failed: ["Git 실패", "bad"], skipped: ["Git 건너뜀", "warn"] },
};
const BUILD = { skill: "스킬", code: "코드", no_code: "노코드" };
// One name and one icon per action, everywhere (screens, buttons, steps): the same words as the CLI commands
const ACT = {
  edit: ["edit", "개발"], selfcheck: ["checklist", "셀프 체크"], register: ["package", "등록"], evaluate: ["flask", "평가"],
  deploy: ["send", "배포 요청"], approve: ["shield", "승인"], promote: ["key", "권한"], load: ["download", "가져오기"],
};
const act = (key, extra = "") => [icon(ACT[key][0]), `${ACT[key][1]}${extra}`];
const actText = (key) => ACT[key][1];
const STEP_ICON = { edit: "edit", selfcheck: "checklist", register: "package", evaluate: "flask", approve: "shield", deploy: "live" };
const KIND_ICON = { skill: "doc", code: "code", no_code: "chat" };
const kindTag = (kind) => h("span", { class: `kind k-${kind}` }, icon(KIND_ICON[kind] || "doc", 13), BUILD[kind] || kind);
const cli = (command) => h("span", { class: "cli", title: "같은 일을 하는 CLI 명령" }, command);
// One word for the last evaluation: a run that did not pass says so; otherwise its content review decides
function evalBadge(status, verdict) {
  if (!status) return h("span", { class: "muted" }, "–");
  if (["failed", "error", "stale"].includes(status) || !verdict) return badge("validation", status);
  return h("span", { class: `badge ${LABEL.verdict[verdict][1]}` }, { pass: "통과", warn: "주의 있음", fail: "부적합 있음" }[verdict]);
}
// More than one workspace (policy workspaces): names, roles, the default production one
const spaces = () => {
  const pol = state.policy || {};
  const listed = Object.entries(pol.workspaces || {}).map(([name, w]) => ({ name, ...w }));
  return { many: listed.length > 0, listed, targets: listed.filter((w) => (w.roles || []).includes("target")), fallback: pol.default_target || null };
};
const targetOf = (spec) => (spec && spec.target_workspace) || spaces().fallback || (spaces().targets[0] || {}).name || "서버 워크스페이스";
const UPGRADE = { initial: "첫 배포", major: "major", minor: "minor", patch: "patch", rollback: "되돌리기" };
const EVENT = {
  create: "만들기", register: "등록", validate: "검사(옛)", evaluate: "평가", gate_requested: "배포 요청", gate_approved: "승인", gate_rejected: "반려",
  gate_canceled: "취소", deploy: "운영 반영", rollback: "되돌리기", promote: "권한", load: "가져오기", git: "Git",
};
const MODE = { mandatory: "필수", optional: "선택", exempt: "면제", required: "사람 승인", auto: "자동 승인" };
const RANK = { fail: 0, unchecked: 1, warn: 2, pass: 3, na: 4 };  // the report lists problems first
const RECOMMEND = { approve: "승인해도 됨", changes: "고친 뒤 다시 요청", reject: "내보내지 말 것" };

function segment(options, value, set, tour = null) {  // a row of buttons; one is on (`tour` marks each for the tutorial)
  const box = h("div", { class: "seg" });
  options.forEach(([v, label]) => box.append(h("button", { type: "button", class: v === value ? "on" : "", "data-tour": tour ? `${tour}-${v}` : null, onclick: (e) => {
    box.querySelectorAll("button").forEach((b) => b.classList.remove("on")); e.currentTarget.classList.add("on"); set(v);
  } }, label)));
  return box;
}

function badge(kind, value) {
  if (!value) return h("span", { class: "muted" }, "–");
  const [text, cls] = (LABEL[kind] || {})[value] || [value, ""];
  return h("span", { class: `badge ${cls}` }, text);
}

function toast(message, kind = "", detail = "") {
  const el = h("div", { class: `toast ${kind}` }, message, detail ? h("small", {}, detail) : null);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), kind === "bad" ? 9000 : 5000);
}

function fail(error) {
  if (error instanceof ApiError) {
    toast(error.title, "bad", [error.detail, error.command && `CLI: ${error.command}`, `HTTP ${error.status}`].filter(Boolean).join(" · "));
  } else {
    toast(String(error.message || error), "bad");
  }
}

function every(ms, fn) {
  let stopped = false;
  const timer = { stop: () => { stopped = true; state.timers.delete(timer); } };
  state.timers.add(timer);
  const loop = async () => {
    if (stopped) return;
    let again = true;
    try { again = (await fn()) !== false; } catch (e) { fail(e); again = false; }
    if (again && !stopped) setTimeout(loop, ms);
    else timer.stop();
  };
  loop();
  return timer;
}

function modal(title, body, buttons = [], { wide = false } = {}) {
  const close = () => { back.remove(); onClose(); };
  let onClose = () => {};
  const foot = h("footer", {}, buttons.map((b) => b instanceof Node ? b : h("button", { class: `btn ${b.cls || ""}`, onclick: () => b.run(close) }, b.label)));
  const bodyEl = h("div", { class: "body" }, body);
  const back = h("div", { class: "backdrop", onclick: (e) => { if (e.target === back) close(); } },
    h("div", { class: `modal ${wide ? "wide" : ""}` },
      h("header", {}, h("h2", {}, title), h("button", { class: "btn ghost", onclick: close, "aria-label": "닫기" }, icon("x", 18))),
      bodyEl,
      buttons.length ? foot : null));
  document.body.append(back);
  return { close, onClose: (fn) => { onClose = fn; }, footer: foot, body: bodyEl };
}

async function download(path, filename) {
  const resp = await api("GET", path, { blob: true });
  const url = URL.createObjectURL(await resp.blob());
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ── layout ─────────────────────────────────────────────────────────────────

const NAV = [
  ["작업", [["agents", "grid", "에이전트"], ["gates", "inbox", "배포 요청함"]]],
  ["도구", [["register", "upload", "폴더·zip 등록"], ["genie", "sparkle", "Genie 점검"]]],
  ["관리", [["policy", "sliders", "정책·평가 프롬프트"]]],
  ["도움말", [["help", "compass", "튜토리얼·가이드"], ["guide", "terminal", "CLI·Genie Code"]]],
];
const ROLE = { developer: "개발자", approver: "승인자", admin: "관리자" };

function layout(active, crumbs, content) {
  const nav = h("nav", { class: "nav" },
    h("a", { href: "#/new", class: `new-btn ${active === "new" ? "active" : ""}`, "data-tour": "nav-new" }, icon("plus", 16), "새 에이전트"),
    NAV.map(([group, items]) => [h("div", { class: "nav-group" }, group), items.map(([key, ico, label]) =>
      h("a", { href: `#/${key}`, class: active === key ? "active" : "", "data-tour": `nav-${key}` }, h("span", { class: "ico" }, icon(ico, 18)), label,
        key === "gates" && state.toApprove ? h("span", { class: "count", title: "내가 승인할 요청" }, state.toApprove) : null))]));
  const me = state.me;
  const side = h("aside", { class: "side" },
    h("a", { class: "brand", href: "#/agents" }, h("div", { class: "logo" }, "ao"), h("div", {}, "aops", h("small", {}, "AgentOps console"))),
    nav,
    h("div", { class: "side-foot" },
      me ? h("div", { class: "who" }, h("span", { class: "avatar" }, (me.user || "?").slice(0, 1).toUpperCase()),
        h("div", { class: "who-t" }, h("div", { class: "user", title: me.user }, me.user), h("div", { class: "muted" }, me.roles.map((r) => ROLE[r] || r).join(" · ")),
          spaces().many && me.workspace ? h("div", { class: "muted", title: me.workspace.host }, icon("layers", 12), ` ${me.workspace.name}`) : null)) : null,
      me && me.login === "oauth" ? h("a", { href: "#", class: "logout", onclick: async (e) => {
        e.preventDefault();
        await fetch("/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => null);
        location.href = "/auth/signed-out";
      } }, icon("logout", 14), "로그아웃") : null,
      state.version ? h("div", { class: "ver" }, `aops ${state.version.aops} · ${state.version.edition}`) : null));
  const top = h("div", { class: "topbar" }, h("div", { class: "crumbs" }, crumbs), h("div", { class: "spacer" }),
    h("button", { class: "btn small ghost help-btn", "data-tour": "help-btn", "aria-haspopup": "menu", onclick: (e) => openHelpMenu(e.currentTarget) }, icon("help", 16), "도움말"),
    h("a", { class: "btn small ghost subtle", href: `${API}/docs`, target: "_blank" }, icon("doc", 15), "API 문서"));
  return h("div", { class: "shell" }, side, h("div", { class: "main" }, top, h("div", { class: "page" }, content)));
}

// Title (with the screen's icon), one line under it, the CLI command for the same thing, and the page's actions on the right
function pageHead(title, sub, actions = [], command = null, ico = null) {
  return h("div", { class: "page-head" },
    ico ? h("div", { class: "head-ico" }, icon(ico, 20)) : null,
    h("div", { class: "title" }, h("h1", {}, title), sub || command ? h("div", { class: "sub" }, sub || "", command ? cli(command) : null) : null),
    h("div", { class: "actions" }, actions));
}

function route() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [path, query] = hash.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(query || "");
  return { parts, params };
}

async function render() {
  for (const t of [...state.timers]) t.stop();
  document.querySelectorAll(".backdrop, .menu-pop").forEach((b) => b.remove());
  const { parts, params } = route();
  const app = document.getElementById("app");
  const show = (active, crumbs, content) => app.replaceChildren(layout(active, crumbs, content));
  const body = h("div", {}, h("div", { class: "loading" }, h("span", { class: "spinner" }), " 불러오는 중…"));
  try {
    switch (parts[0] || "agents") {
      case "agents":
        if (parts[1]) { show("agents", [h("a", { href: "#/agents" }, "에이전트"), "/", h("b", {}, parts[1])], body); await agentPage(body, parts[1], params); }
        else { show("agents", [h("b", {}, "에이전트")], body); await agentsPage(body); }
        break;
      case "gates":
        if (parts[1]) { show("gates", [h("a", { href: "#/gates" }, "배포 요청함"), "/", h("b", { class: "mono" }, parts[1].slice(0, 12))], body); await gatePage(body, parts[1]); }
        else { show("gates", [h("b", {}, "배포 요청함")], body); await gatesPage(body, params); }
        break;
      case "register": show("register", [h("b", {}, "폴더·zip 등록")], body); registerPage(body, params); break;
      case "new": show("new", [h("b", {}, "새 에이전트")], body); newPage(body, params); break;
      case "genie": show("genie", [h("b", {}, "Genie 점검")], body); geniePage(body, params); break;
      case "policy": show("policy", [h("b", {}, "정책·평가 프롬프트")], body); await policyPage(body); break;
      case "help": show("help", [h("b", {}, "튜토리얼·가이드")], body); helpPage(body); break;
      case "guide": show("guide", [h("b", {}, "CLI·Genie Code")], body); await guidePage(body); break;
      default: location.hash = "#/agents";
    }
  } catch (error) {
    body.replaceChildren(h("div", { class: "card pad" }, h("div", { class: "empty" }, h("b", {}, error.title || "불러오지 못했습니다"),
      error.detail || error.message || "", error.command ? h("div", { class: "mt" }, h("code", {}, error.command)) : null)));
  }
}

async function refreshCounts() {
  try {
    const page = await get("/release-gates?to_approve=true");
    state.toApprove = page.items.filter((g) => g.decision === "pending_approval").length;
  } catch { state.toApprove = 0; }
}

// ── 에이전트 목록 (aops list) ────────────────────────────────────────────────

async function agentsPage(root) {
  const [page, mine] = await Promise.all([get("/agents"), get("/release-gates?mine=true").catch(() => ({ items: [] }))]);
  const items = page.items;
  let q = "", type = "", life = "";
  const table = h("div", {});
  const draw = () => {
    const rows = items.filter((a) => (!type || a.build_type === type) && (!life || (life === "fix" ? needsFix(a) : a.lifecycle === life)) &&
      (!q || `${a.agent_id} ${a.name} ${a.description || ""} ${a.owner}`.toLowerCase().includes(q)));
    table.replaceChildren(rows.length ? h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["에이전트", "종류", "운영", "최신", "마지막 평가", "진행 중", "담당"].map((t) => h("th", {}, t)))),
      h("tbody", {}, rows.map((a, i) => h("tr", { class: "click", "data-tour": i === 0 ? "agent-row" : null, onclick: () => { location.hash = `#/agents/${enc(a.agent_id)}`; } },
        h("td", {}, h("div", { class: "cell-main" }, a.name || a.agent_id),
          h("div", { class: "cell-sub" }, [a.name && a.name !== a.agent_id ? a.agent_id : null, a.description].filter(Boolean).join(" · "))),
        h("td", {}, kindTag(a.build_type)),
        h("td", {}, a.production_version ? h("span", { class: "badge ok" }, a.production_version) : h("span", { class: "muted" }, "–")),
        h("td", { class: "mono" }, a.latest_version || "–"),
        h("td", {}, evalBadge(a.last_validation, a.last_review)),
        h("td", {}, a.pending_gates ? h("span", { class: "badge warn" }, `${a.pending_gates}건`) : h("span", { class: "muted" }, "–")),
        h("td", {}, h("div", {}, a.owner), h("div", { class: "cell-sub" }, when(a.updated_at)))))))
      : empty("grid", items.length ? "조건에 맞는 에이전트가 없습니다" : "아직 에이전트가 없습니다",
        items.length ? "찾는 말이나 종류·상태를 바꿔 보세요" : "새 에이전트로 첫 에이전트를 만들어 보세요",
        items.length ? null : h("a", { class: "btn primary mt", href: "#/new" }, icon("plus"), "새 에이전트")));
  };
  const mineOpen = mine.items.filter((g) => ["validating", "pending_approval"].includes(g.decision)).length;
  const toFix = items.filter(needsFix).length;
  const todo = (n, label, hint, href, cls, ico) => h("a", { class: `todo ${n ? cls : "zero"}`, href },
    h("span", { class: "todo-ico" }, icon(ico, 18)), h("div", { class: "grow" }, h("div", { class: "label" }, label), h("div", { class: "n" }, n), h("div", { class: "hint" }, hint)));
  root.replaceChildren(
    pageHead("에이전트", "레지스트리에 있는 에이전트", [h("a", { class: "btn primary", href: "#/new" }, icon("plus"), "새 에이전트")], "aops list", "grid"),
    welcomeCard(),
    h("div", { class: "todos", "data-tour": "todos" },
      todo(state.toApprove, "승인할 요청", "내가 리포트를 보고 결정할 것", "#/gates?tab=approve", "warn", "shield"),
      todo(mineOpen, "진행 중인 내 요청", "평가 중이거나 승인을 기다림", "#/gates?tab=mine", "info", "clock"),
      todo(toFix, "고칠 에이전트", "마지막 평가가 실패했거나 부적합", "#/agents?filter=fix", "bad", "alert"),
      todo(items.filter((a) => a.production_version).length, "운영 중", `전체 ${items.length}개 중`, "#/agents?filter=production", "ok", "live")),
    h("div", { class: "card mt" },
      h("div", { class: "toolbar", style: "padding:12px 14px 0" },
        h("label", { class: "search" }, icon("search", 15), h("input", { type: "search", placeholder: "이름·ID·설명·담당으로 찾기", oninput: (e) => { q = e.target.value.toLowerCase(); draw(); } })),
        segment([["", "모든 종류"], ["skill", "스킬"], ["code", "코드"], ["no_code", "노코드"]], type, (v) => { type = v; draw(); }),
        segment([["", "모든 상태"], ["production", "운영 중"], ["registered", "등록됨"], ["fix", "고칠 것"]], life, (v) => { life = v; draw(); })),
      table));
  const filter = route().params.get("filter");
  if (filter) {
    life = filter;
    root.querySelectorAll(".toolbar .seg")[1].querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.textContent === { production: "운영 중", fix: "고칠 것", registered: "등록됨" }[filter]));
  }
  draw();
}

const needsFix = (a) => ["failed", "error", "stale"].includes(a.last_validation) || a.last_review === "fail";

// ── 에이전트 상세 ────────────────────────────────────────────────────────────

async function agentPage(root, id, params) {
  const [agent, versions, gates] = await Promise.all([get(`/agents/${enc(id)}`), get(`/agents/${enc(id)}/versions`),
    get(`/release-gates?agent_id=${enc(id)}`).catch(() => ({ items: [] }))]);
  const latest = versions.items[0];
  const kind = agent.build_type;
  const noCode = kind === "no_code";
  const isOwner = state.me && (state.me.user === agent.owner || (agent.owner_group && state.me.groups.includes(agent.owner_group)));
  const [nc, ws, deploys] = await Promise.all([
    noCode ? get(`/no-code/agents/${enc(id)}`).catch(() => null) : null,
    noCode ? null : get(`/agents/${enc(id)}/workspace`).catch(() => null),
    kind === "code" && agent.production_version ? get(`/agents/${enc(id)}/deployments`).catch(() => ({ items: [] })) : null]);
  const appUrl = deploys ? (deploys.items.find((d) => d.status === "succeeded" && d.app_url) || {}).app_url || null : null;
  const openGate = gates.items.find((g) => ["validating", "pending_approval"].includes(g.decision));
  const lastGate = gates.items[0];
  const link = (url, text) => url ? h("a", { class: "ext", href: url, target: "_blank" }, text, icon("external", 12)) : h("span", { class: "muted" }, "–");
  const only = isOwner ? "" : "담당자·담당 팀만";

  // the lifecycle at a glance: where this agent is, and the one action of each step
  const stepBtn = (key, onclick, disabled, title) => h("button", { class: "btn small", disabled: !!disabled, title: title || "", onclick }, act(key));
  const evalState = !agent.last_validation ? "wait" : agent.last_review === "fail" || ["failed", "error", "stale"].includes(agent.last_validation) ? "bad"
    : agent.last_review === "warn" ? "warn" : agent.last_validation === "passed" ? "done" : "run";
  const steps = [
    { key: "edit", state: "done", title: "개발", text: noCode ? "개발용 Genie" : ws ? "워크스페이스 폴더 (Genie Code)" : "내 PC 폴더",
      act: noCode ? link(nc && nc.dev_url, "Genie에서 열기") : ws ? [link(ws.folder_url, "폴더"), " ", link(ws.open_url, "편집기")] : null },
    { key: "selfcheck", state: "info", title: "셀프 체크", text: "언제든 · 기록 안 함",
      act: stepBtn("selfcheck", () => openSelfCheck(id, noCode ? {} : ws ? { workspace_path: ws.workspace_path } : { version: latest && latest.version }),
        noCode ? !nc || !nc.dev_space_id || !isOwner : !ws && !latest) },
    { key: "register", state: noCode ? "na" : latest ? "done" : "wait", title: "등록",
      text: noCode ? "배포가 성공할 때 버전이 생김" : latest ? `최신 ${latest.version} · ${when(latest.registered_at || latest.created_at)}` : "아직 버전 없음",
      act: noCode ? null : h("a", { class: "btn small", href: `#/register?agent=${enc(id)}${ws ? `&path=${enc(ws.workspace_path)}` : ""}` }, act("register")) },
    { key: "evaluate", state: evalState, title: "평가",
      text: agent.last_validation ? evalBadge(agent.last_validation, agent.last_review) : "배포 요청 때 돎 (미리 돌려도 됨)",
      act: stepBtn("evaluate", () => openEvaluate(id, noCode ? null : latest && latest.version), noCode ? !nc || !nc.dev_space_id || !isOwner : !latest || !isOwner, only) },
    { key: "approve", state: openGate ? (openGate.decision === "pending_approval" ? "warn" : "run") : lastGate && lastGate.decision === "rejected" ? "bad" : lastGate ? "done" : "wait",
      title: "승인", text: openGate ? [badge("decision", openGate.decision), ` ${openGate.version}`] : lastGate ? [badge("decision", lastGate.decision), ` ${lastGate.version}`]
        : agent.approvers.length ? `승인자 ${agent.approvers.join(", ")}` : "승인자: 정책의 승인 그룹",
      act: openGate || lastGate ? h("a", { class: "btn small", href: `#/gates/${enc((openGate || lastGate).gate_id)}` }, "리포트·승인") : null },
    { key: "deploy", state: agent.production_version ? "done" : "wait", title: "운영",
      text: agent.production_version ? `운영 ${agent.production_version}` : "아직 운영 버전 없음",
      act: kind === "code" ? link(appUrl, "운영 앱") : noCode ? link(nc && nc.prod_url, "운영용 Genie") : agent.production_version ? h("span", { class: "muted" }, "UC Skill") : null },
  ];
  const stepper = h("ol", { class: "lifecycle", "data-tour": "lifecycle" }, steps.map((st) => h("li", { class: `st ${st.state}`, "data-tour": `step-${st.key}` },
    h("div", { class: "dot" }, icon(st.state === "done" ? "check" : st.state === "bad" ? "alert" : STEP_ICON[st.key], 15)),
    h("div", { class: "body" }, h("div", { class: "t" }, st.title), h("div", { class: "s" }, st.text), st.act ? h("div", { class: "a" }, st.act) : null))));

  const info = h("div", { class: "card pad", "data-tour": "agent-info" },
    h("dl", { class: "kv two-col" },
      h("dt", {}, "담당"), h("dd", {}, agent.owner, agent.owner_group ? h("span", { class: "muted" }, ` · ${agent.owner_group}`) : null),
      h("dt", {}, "지정 승인자"), h("dd", {}, agent.approvers.length ? agent.approvers.join(", ") : h("span", { class: "muted" }, "없음 (정책의 승인 그룹)")),
      h("dt", {}, "중요도 · 민감도"), h("dd", {}, `${agent.service_criticality} · ${agent.data_sensitivity}`),
      h("dt", {}, "보안 등급"), h("dd", {}, agent.security_level),
      noCode ? [
        h("dt", {}, "에이전트 폴더"), h("dd", {}, nc && nc.folder_url ? link(nc.folder_url, nc.folder_path) : (nc && nc.folder_path) || "–"),
        h("dt", {}, "공개 그룹"), h("dd", {}, (nc && nc.consumer_group) || h("span", { class: "muted" }, "없음 (SPEC.md consumer_group)"))]
        : [h("dt", {}, "작업 폴더"), h("dd", {}, ws ? (ws.folder_url ? link(ws.folder_url, ws.workspace_path) : h("span", { class: "mono" }, ws.workspace_path)) : h("span", { class: "muted" }, "PC (폴더·zip으로 등록)"))],
      spaces().many ? [
        h("dt", {}, "개발 워크스페이스"), h("dd", {}, (noCode ? nc && nc.dev_workspace : ws && ws.workspace) || h("span", { class: "muted" }, "서버 워크스페이스")),
        h("dt", {}, "운영 워크스페이스"), h("dd", {}, kind === "skill" ? h("span", { class: "muted" }, "UC Skill (워크스페이스에 매이지 않음)")
          : (noCode && nc && nc.prod_workspace) || agent.target_workspace || targetOf({}))] : null,
      h("dt", {}, "내 권한"), h("dd", {}, isOwner ? `담당자 (개발·${noCode ? "" : "등록·"}평가·배포 요청·권한)` : "보기 · 운영 버전 가져오기")));

  const tabs = [["versions", "버전", versions.items.length, "layers"], ["checks", "평가", undefined, "flask"], ["gates", "배포 요청", gates.items.length, "send"],
    ["deploys", "배포", undefined, "live"], ["events", "이력", undefined, "clock"]];
  let tab = params.get("tab") || "versions";
  const pane = h("div", {});
  const tabBar = h("div", { class: "tabs", "data-tour": "agent-tabs" });
  const drawTabs = () => tabBar.replaceChildren(...tabs.map(([key, label, n, ico]) => h("button", { class: key === tab ? "on" : "", onclick: () => { tab = key; drawTabs(); loadTab(); } },
    icon(ico, 15), label, n !== undefined ? h("span", { class: "n" }, n) : null)));
  const loadTab = async () => {
    pane.replaceChildren(h("div", { class: "loading" }, h("span", { class: "spinner" })));
    try {
      if (tab === "versions") pane.replaceChildren(versionsTab(agent, versions.items));
      if (tab === "checks") pane.replaceChildren(checksTab(await get(`/agents/${enc(id)}/evaluations`), id));
      if (tab === "gates") pane.replaceChildren(gatesTable(await get(`/release-gates?agent_id=${enc(id)}`), { agent: false }));
      if (tab === "deploys") pane.replaceChildren(deploysTab(await get(`/agents/${enc(id)}/deployments`)));
      if (tab === "events") pane.replaceChildren(eventsTab(await get(`/agents/${enc(id)}/events`)));
    } catch (e) { pane.replaceChildren(h("div", { class: "note bad" }, e.title || String(e))); }
  };
  root.replaceChildren(
    h("div", { class: "page-head" },
      h("div", { class: `head-ico kind-ico k-${kind}` }, icon(KIND_ICON[kind] || "doc", 20)),
      h("div", { class: "title" }, h("div", { class: "row" }, h("h1", {}, agent.name || agent.agent_id), kindTag(kind), badge("lifecycle", agent.lifecycle)),
        h("div", { class: "sub" }, [agent.name && agent.name !== agent.agent_id ? agent.agent_id : null, agent.description].filter(Boolean).join(" · "))),
      h("div", { class: "actions" },
        h("button", { class: "btn", "data-tour": "act-load", disabled: noCode ? !agent.production_version : !latest, onclick: () => openLoad(agent) }, act("load")),
        h("button", { class: "btn", "data-tour": "act-promote", disabled: !agent.production_version || !isOwner, title: only, onclick: () => openPromote(agent) }, act("promote")),
        h("button", { class: "btn primary", "data-tour": "act-deploy", disabled: !isOwner || (!noCode && !latest), title: only || (!noCode && !latest ? "먼저 등록하세요" : ""),
          onclick: () => openDeploy(id, versions.items, agent.production_version, null, kind) }, act("deploy")))),
    h("div", { class: "card pad" }, stepper),
    h("div", { class: "mt" }, info),
    h("div", { class: "card pad mt" }, tabBar, pane));
  drawTabs();
  loadTab();
}

function versionsTab(agent, versions) {
  const noCode = agent.build_type === "no_code";
  if (!versions.length) return empty("layers", "버전이 없습니다",
    noCode ? "노코드는 배포가 성공할 때 버전이 생깁니다 (처음 1.0.0, 데이터 소스가 바뀌면 major, 그 밖은 minor)." : "");
  const prodIndex = versions.findIndex((v) => v.status === "production");
  return h("table", { class: "data" },
    h("thead", {}, h("tr", {}, ["버전", "상태", "사유", "등록", "크기·해시", "Git", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, versions.map((v, i) => h("tr", {},
      h("td", {}, h("div", { class: "cell-main mono" }, v.version), h("div", { class: "cell-sub" }, v.harness ? `기본 하네스 ${v.harness}` : "")),
      h("td", {}, badge("version", v.status)),
      h("td", { style: "max-width:280px" }, v.note || h("span", { class: "muted" }, "–")),
      h("td", {}, h("div", {}, v.registered_by || ""), h("div", { class: "cell-sub" }, when(v.registered_at || v.created_at))),
      h("td", {}, h("div", {}, size(v.size_bytes)), h("div", { class: "cell-sub mono" }, short(v.source_sha256))),
      h("td", {}, v.git_url ? h("a", { class: "ext", href: v.git_url, target: "_blank" }, `v${v.version}`, icon("external", 12)) : badge("git", v.git_status)),
      h("td", { class: "num" }, h("div", { class: "row", style: "justify-content:flex-end" },
        h("button", { class: "btn ghost small", onclick: () => openVersion(agent.agent_id, v.version) }, "자세히"),
        noCode ? null : h("button", { class: "btn ghost small", onclick: () => openEvaluate(agent.agent_id, v.version) }, act("evaluate")),
        v.status !== "production" ? h("button", { class: "btn small", onclick: () => openDeploy(agent.agent_id, versions, agent.production_version, v.version, agent.build_type) },
          prodIndex >= 0 && i > prodIndex ? [icon("undo"), "되돌리기"] : act("deploy")) : null))))));
}

async function openVersion(id, version) {
  const m = modal(`${id} ${version}`, h("div", { class: "loading" }, h("span", { class: "spinner" })), [], { wide: true });
  try {
    const v = await get(`/agents/${enc(id)}/versions/${enc(version)}`);
    m.body.replaceChildren(
      h("dl", { class: "kv" },
        h("dt", {}, "상태"), h("dd", {}, badge("version", v.status)),
        h("dt", {}, "등록"), h("dd", {}, `${v.registered_by || ""} · ${when(v.registered_at || v.created_at)}`),
        h("dt", {}, "버전 zip"), h("dd", { class: "mono" }, v.artifact_path || ""),
        h("dt", {}, "파일 해시"), h("dd", { class: "mono" }, v.source_sha256 || ""),
        h("dt", {}, "스킬 해시"), h("dd", { class: "mono" }, v.skills_sha256 || ""),
        h("dt", {}, "Git"), h("dd", {}, v.git && v.git.url ? h("a", { href: v.git.url, target: "_blank" }, v.git.url) : v.git ? `${v.git.status}: ${v.git.error || ""}` : "–")),
      h("h2", { class: "mt" }, "스킬"),
      v.skills.length ? h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["스킬", "UC Skill", "해시"].map((t) => h("th", {}, t)))),
        h("tbody", {}, v.skills.map((s) => h("tr", {}, h("td", { class: "cell-main" }, s.skill_alias), h("td", { class: "mono" }, s.uc_name || "–"),
          h("td", { class: "mono muted" }, short(s.skill_sha256)))))) : h("p", { class: "muted" }, "스킬이 없습니다"),
      h("h2", { class: "mt" }, "SPEC.md"),
      h("dl", { class: "kv" }, Object.entries(v.spec || {}).flatMap(([k, val]) => [h("dt", {}, k), h("dd", {}, typeof val === "object" ? JSON.stringify(val) : String(val))])),
      h("div", { class: "row mt" },
        h("button", { class: "btn", onclick: () => download(`/agents/${enc(id)}/versions/${enc(version)}/archive`, `${id}-${version}.zip`).catch(fail) }, "버전 zip 받기")));
  } catch (e) { m.close(); fail(e); }
}

function checksTab(page, id) {
  if (!page.items.length) return empty("flask", "평가 기록이 없습니다", "배포를 요청하면 평가(Gate)가 먼저 돕니다. [평가]로 따로 돌릴 수도 있습니다. 셀프 체크는 기록하지 않습니다.");
  return h("div", {}, page.items.map((r) => h("div", { class: "card pad", style: "box-shadow:none" },
    h("div", { class: "row" }, h("b", { class: "mono" }, r.version), badge("validation", r.status), r.review ? badge("verdict", r.review.verdict) : null,
      h("span", { class: "muted" }, `정책 v${r.policy_version}`), h("span", { class: "muted right" }, when(r.started_at)),
      h("button", { class: "btn ghost small", onclick: () => openRun(id, r.validation_run_id) }, "리포트")),
    r.steps.length ? h("ul", { class: "steps mt" }, r.steps.map(stepItem)) : null,
    r.failure_reason ? h("div", { class: "note bad mt" }, r.failure_reason) : null)));
}

function stepItem(step) {
  const ok = step.status === "passed";
  const cls = ok ? "ok" : step.status === "failed" ? "bad" : step.status === "warned" ? "warnm" : step.status === "running" ? "run" : "wait";
  const missed = (step.rules || []).filter((r) => !r.passed);
  return h("li", {}, h("span", { class: `mark ${cls}` }, ok ? icon("check", 12) : step.status === "failed" ? icon("x", 12) : step.status === "warned" ? icon("alert", 12) : ""),
    h("div", {}, h("div", { class: "name" }, STEP_TITLE[step.name] || step.name, " ", h("span", { class: "mono muted" }, step.name),
      step.advisory ? h("span", { class: "tag" }, "승인자 판단") : null), step.detail ? h("div", { class: "detail" }, step.detail) : null,
      missed.length ? h("ul", { class: "rules" }, missed.map((r) => h("li", {}, h("span", { class: `badge ${r.level === "ERROR" ? "bad" : "warn"} plain` }, r.level), " ",
        h("b", { class: "mono" }, r.id), ` ${r.message}`, h("div", { class: "muted" }, `고치는 곳: ${r.fix}`)))) : null));
}

const STEP_TITLE = { format: "SPEC.md", config: "aops.toml", instructions: "지시", skills: "스킬", evals: "평가셋", project: "Agent Bricks 프로젝트",
  definition: "Genie 정의 규칙", target: "운영 위치", secrets: "비밀값·금지 파일", review: "내용 검토", evaluate: "품질 평가", integrity: "무결성", record: "기록" };

// The self-check as a checklist: every rule of every step, the ones to fix first with where to fix them.
function checklist(steps) {
  const rules = steps.flatMap((s) => s.rules || []);
  const errors = rules.filter((r) => !r.passed && r.level === "ERROR").length;
  const warns = rules.filter((r) => !r.passed && r.level !== "ERROR").length;
  const mark = (r) => r.passed ? h("span", { class: "rmark ok" }, icon("check", 14)) : h("span", { class: `rmark ${r.level === "ERROR" ? "bad" : "warn"}` }, icon(r.level === "ERROR" ? "x" : "alert", 14));
  return h("div", {},
    h("div", { class: "row" }, h("span", { class: "badge ok plain" }, `통과 ${rules.length - errors - warns}`),
      h("span", { class: `badge ${errors ? "bad" : "plain"} plain` }, `ERROR ${errors}`), h("span", { class: `badge ${warns ? "warn" : "plain"} plain` }, `WARN ${warns}`),
      h("span", { class: "muted" }, "ERROR는 배포 전 평가가 반려합니다. WARN은 막지 않습니다.")),
    steps.map((s) => h("div", { class: "check-step" },
      h("div", { class: "row" }, h("span", { class: `mark-s ${s.status === "failed" ? "bad" : "ok"}` }, icon(s.status === "failed" ? "x" : "check", 11)),
        h("b", {}, STEP_TITLE[s.name] || s.name), h("span", { class: "mono muted" }, s.name), s.score != null ? h("span", { class: "muted" }, `준수율 ${Math.round(s.score * 100)}%`) : null),
      (s.rules || []).length ? h("ul", { class: "check-rules" }, [...s.rules].sort((a, b) => a.passed - b.passed).map((r) => h("li", { class: r.passed ? "passed" : "" },
        mark(r), h("div", {}, h("span", { class: "mono" }, r.id), " ", r.message, r.passed ? null : h("div", { class: "muted" }, `고치는 곳: ${r.fix}`)))))
        : h("div", { class: "detail muted" }, s.detail || ""))));
}

function deploysTab(page) {
  if (!page.items.length) return empty("live", "배포 기록이 없습니다");
  return h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["버전", "결과", "대상", "시작", "끝"].map((t) => h("th", {}, t)))),
    h("tbody", {}, page.items.map((d) => h("tr", {},
      h("td", { class: "mono cell-main" }, d.version), h("td", {}, badge("deployment", d.status), d.error ? h("div", { class: "cell-sub" }, d.error) : null),
      h("td", { class: "mono", style: "max-width:380px" }, d.target || "–"), h("td", { class: "muted" }, when(d.started_at)), h("td", { class: "muted" }, when(d.ended_at))))));
}

function eventsTab(page) {
  if (!page.items.length) return empty("clock", "기록이 없습니다");
  const describe = (e) => {
    const d = e.detail || {};
    if (e.event_type === "git") return `${d.action === "production" ? "production 브랜치" : "커밋·태그"} ${d.status || ""}${d.error ? ` · ${d.error}` : ""}`;
    if (e.event_type === "promote") return `${d.revoke ? "회수" : "부여"} ${(d.principals || []).join(", ")}`;
    if (e.event_type === "gate_rejected") return d.reason || "";
    if (e.event_type === "deploy" || e.event_type === "rollback") return d.previous ? `이전 ${d.previous}` : "첫 운영";
    if (e.event_type === "validate") return d.status || "";
    if (e.event_type === "gate_approved" && d.exception) return "개발 예외 승인 (정책 dev_approvers)";
    return "";
  };
  return h("ul", { class: "timeline" }, page.items.map((e) => h("li", {},
    h("span", { class: "when" }, when(e.at)), h("span", {}, h("span", { class: "tag" }, EVENT[e.event_type] || e.event_type), h("span", { class: "mono" }, e.version || "")),
    h("span", {}, h("span", {}, e.actor), h("span", { class: "muted" }, describe(e) ? ` · ${describe(e)}` : "")))));
}

// ── 셀프 체크 (aops validate) · 평가 (aops evaluate) ─────────────────────────

function resultsTable(results) {
  if (!results || !results.length) return null;
  return h("table", { class: "data mt" }, h("thead", {}, h("tr", {}, ["판정", "문항", "이유", "정답 · 답"].map((t) => h("th", {}, t)))),
    h("tbody", {}, results.map((r) => h("tr", {},
      h("td", {}, h("span", { class: `badge ${r.assessment === "GOOD" ? "ok" : r.assessment === "BAD" ? "bad" : "warn"}` }, r.assessment || "–")),
      h("td", { style: "max-width:260px" }, h("div", { class: "cell-main" }, r.question || r.question_id), h("div", { class: "cell-sub mono" }, r.question_id)),
      h("td", { class: "muted", style: "max-width:260px" }, (r.reasons || []).join(", ") || "–"),
      h("td", { style: "max-width:380px" }, h("div", { class: "cell-sub" }, `정답: ${(r.expected || "–").slice(0, 160)}`),
        h("div", { class: "cell-sub mono" }, `답: ${(r.generated || "–").slice(0, 220)}`))))));
}

async function openSelfCheck(id, fields) {
  const m = modal([act("selfcheck"), `: ${id}`], h("div", { class: "loading" }, h("span", { class: "spinner" }), " 점검 중…"), [], { wide: true });
  const form = new FormData();
  Object.entries(fields || {}).forEach(([k, v]) => v && form.append(k, v));
  try {
    const r = await api("POST", `/projects/${enc(id)}/validations`, { form });
    m.body.replaceChildren(
      h("div", { class: "row" }, h("b", {}, r.source), badge("validation", r.status)),
      h("div", { class: "mt" }, checklist(r.steps)));
  } catch (e) { m.close(); fail(e); }
}

// ── 평가 진행 (GET /evaluations/{id}/progress: 평가 Job이 1초 남짓마다 씀) ─────────

const PSTEP = { wait: ["", "wait"], running: ["", "run"], done: ["check", "ok"], warned: ["alert", "warn"], failed: ["x", "bad"], skipped: ["", "skip"] };
const pmark = (st) => (PSTEP[st] && PSTEP[st][0] ? icon(PSTEP[st][0], 12) : st === "skipped" ? "–" : "");
const RUN_DONE = ["passed", "failed", "stale", "error"];
function elapsed(iso) {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  return s < 60 ? `${s}초` : `${Math.floor(s / 60)}분 ${s % 60}초`;
}
const clockOf = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
};

// What the evaluate Job is doing now. Built once and then updated in place, so the window keeps its size: the head
// (spinner · % · what now · how long), the bar, the step chips (always one row, same places), the counts and the files
// being read (one line each), and below them the activity, which only grows: new lines at the bottom, scrolled to
// unless the person scrolled up to read older ones.
const STEP_PLACEHOLDER = [["load", "버전 읽기"], ["format", "형식 검사"], ["review", "내용 검토"], ["evaluate", "품질 평가"], ["record", "기록"]]
  .map(([name, title]) => ({ name, title, state: "wait", fraction: 0 }));

function progressPanel({ compact = false } = {}) {
  const spin = h("span", { class: "spinner" });
  const pct = h("b", { class: "ppct" }, "0%");
  const msg = h("span", { class: "pmsg" }, "평가 Job을 시작하는 중");
  const ago = h("span", { class: "muted pago" });
  const fill = h("div", { class: "pfill", style: "width:2%" });
  const bar = h("div", { class: "pbar live" }, fill);
  const head = h("div", { class: "phead" }, spin, pct, msg, ago);
  if (compact) {
    return { el: h("div", { class: "progress compact" }, head, bar), update(p) { paintHead(p); } };
  }
  const steps = h("ol", { class: "psteps" });
  const counts = h("div", { class: "pcounts muted" }, " ");
  const files = h("div", { class: "pfiles mono muted" }, " ");
  const list = h("ul", { class: "plog-list" });
  const job = h("div", { class: "pjob" });
  const seen = new Set();
  let follow = true;  // keep the newest line in view, until the person scrolls up
  list.addEventListener("scroll", () => { follow = list.scrollTop + list.clientHeight >= list.scrollHeight - 12; });

  function paintHead(p) {
    const running = p.status !== "done";
    spin.style.visibility = running ? "visible" : "hidden";
    pct.textContent = `${p.percent || 0}%`;
    msg.textContent = p.message || "";
    ago.textContent = running && p.since && Date.now() - Date.parse(p.since) >= 3000 ? elapsed(p.since) : "";
    fill.style.width = `${Math.max(2, p.percent || 0)}%`;
    bar.classList.toggle("live", running);
  }

  function update(p) {
    paintHead(p);
    steps.replaceChildren(...((p.steps || []).length ? p.steps : STEP_PLACEHOLDER).map((s) => h("li", {
      class: `ps ${PSTEP[s.state] ? PSTEP[s.state][1] : "wait"}`, title: s.detail || "" },
      h("span", { class: "pdot" }, s.state === "running" ? h("span", { class: "spinner" }) : pmark(s.state)),
      h("span", { class: "ptitle" }, s.title),
      h("small", { class: "pfrac" }, s.state === "running" && s.fraction ? `${Math.round(s.fraction * 100)}%` : ""))));
    const r = p.review;
    counts.textContent = [r && r.files_total != null ? `파일 ${r.files_done}/${r.files_total}` : null,
      r && r.lines_total ? `줄 ${(r.lines_read || 0).toLocaleString()}/${r.lines_total.toLocaleString()}` : null,
      r && r.turn ? `모델 호출 ${r.turn}/${r.max_turns}` : null,
      p.quality ? `${p.quality.done}/${p.quality.total}문항` : null].filter(Boolean).join(" · ") || " ";
    files.textContent = r && (r.current || []).length ? `읽는 파일  ${r.current.join("  ·  ")}` : " ";
    files.title = r && (r.current || []).length ? r.current.join("\n") : "";
    const fresh = [...(p.events || [])].reverse().filter((e) => !seen.has(`${e.at}|${e.text}`));  // the document is newest first
    for (const e of fresh) {
      seen.add(`${e.at}|${e.text}`);
      list.append(h("li", { title: e.detail || "" }, h("span", { class: "muted mono" }, clockOf(e.at)), h("span", {}, e.text)));
    }
    list.classList.toggle("empty", !list.children.length);
    if (fresh.length && follow) list.scrollTop = list.scrollHeight;
    job.replaceChildren(p.job && p.job.url ? h("a", { class: "muted ext", href: p.job.url, target: "_blank" }, "평가 Job 실행 화면", icon("external", 12)) : "");
  }

  const el = h("div", { class: "progress" }, head, bar, steps, counts, files,
    h("div", { class: "plog" }, h("div", { class: "plog-title muted" }, "활동 기록"), list), job);
  update({ status: "running", percent: 0, message: "평가 Job을 시작하는 중", steps: [] });
  return { el, update };
}

// Keep `box` on the run's progress (every 2 s) until it is done; then `onDone(progress)`. A server without the
// progress route falls back to the run's status.
function watchProgress(runId, box, { compact = false, onDone } = {}) {
  const panel = progressPanel({ compact });
  box.replaceChildren(panel.el);
  return every(2000, async () => {
    let p = await get(`/evaluations/${enc(runId)}/progress`).catch(() => null);
    if (!p) {
      const r = await get(`/evaluations/${enc(runId)}`).catch(() => null);
      if (!r) return true;
      p = { status: RUN_DONE.includes(r.status) ? "done" : "running", percent: RUN_DONE.includes(r.status) ? 100 : 0, message: `평가 ${r.status}` };
    }
    panel.update(p);
    if (p.status === "done") { if (onDone) onDone(p); return false; }
    return true;
  });
}
function openEvaluate(id, version) {
  const status = h("div", { class: "row" }, h("b", { class: "mono" }, `${id} ${version || "(지금 개발용 정의)"}`));
  const live = h("div", { class: "mt" }, h("div", { class: "muted" }, h("span", { class: "spinner" }), " 평가를 요청하는 중…"));
  const results = h("div", {});
  const m = modal([act("evaluate"), `: ${id}`], h("div", {}, status, live, results), [{ label: "닫기", run: (close) => close() }], { wide: true });
  let finished = false;
  post(`/projects/${enc(id)}/evaluations`, { version }).then((run) => {
    const timer = watchProgress(run.validation_run_id, live, { onDone: async () => {
      const r = await get(`/evaluations/${enc(run.validation_run_id)}`).catch(() => null);
      if (!r) return;
      const verdict = r.review ? r.review.verdict : null;
      fill(status, h("b", { class: "mono" }, `${id} ${r.version}`), evalBadge(r.status, verdict), h("span", { class: "muted" }, `정책 v${r.policy_version}`));
      fill(live, h("ul", { class: "steps" }, r.steps.map(stepItem)));
      fill(results, r.failure_reason && r.status === "error" ? h("div", { class: "note bad mt" }, r.failure_reason) : null, h("div", { class: "mt" }, reportView(r)));
      finished = true;
      const word = ["failed", "error", "stale"].includes(r.status) || !verdict ? (LABEL.validation[r.status] || [r.status])[0] : { pass: "통과", warn: "주의 있음", fail: "부적합 있음" }[verdict];
      toast(`평가 끝: ${id} ${r.version} · ${word}`, verdict === "pass" && r.status === "passed" ? "ok" : "bad", "아래 리포트 · 닫으면 에이전트 화면이 바뀝니다");
    } });
    m.onClose(() => { timer.stop(); if (finished && location.hash.startsWith(`#/agents/${enc(id)}`)) render(); });
  }).catch((e) => { m.close(); fail(e); });
}

async function openRun(id, runId) {
  try {
    const r = await get(`/evaluations/${enc(runId)}`);
    modal(`평가 리포트 ${id} ${r.version}`, h("div", {}, h("div", { class: "row" }, badge("validation", r.status), h("span", { class: "muted" }, `정책 v${r.policy_version} · ${when(r.started_at)}`)),
      r.failure_reason ? h("div", { class: "note bad mt" }, r.failure_reason) : null, reportView(r)), [], { wide: true });
  } catch (e) { fail(e); }
}

// The approver's report: the content review checklist with reasons and evidence, the quality evaluation, the form checks.
function reportView(run) {
  const rv = run.review;
  const form = run.steps.filter((s) => !["review", "evaluate", "integrity", "record"].includes(s.name));
  const quality = run.steps.find((s) => s.name === "evaluate");
  const rest = run.steps.filter((s) => ["integrity", "record"].includes(s.name));
  const cov = rv ? rv.coverage : null;
  return h("div", { class: "report" },
    rv ? h("div", { class: `verdict v-${rv.verdict}` },
      h("div", { class: "row" }, h("span", { class: "big" }, icon({ pass: "check", warn: "alert", fail: "x" }[rv.verdict], 22)),
        h("div", {}, h("div", { class: "vt" }, `내용 검토 ${LABEL.verdict[rv.verdict][0]}`, h("span", { class: "muted" }, ` · 검토 모델의 권고: ${RECOMMEND[rv.recommendation] || rv.recommendation}`)),
          h("div", { class: "muted" }, `항목 ${rv.items.length}개 · 부적합 ${rv.items.filter((i) => i.status === "fail").length} · 주의 ${rv.items.filter((i) => i.status === "warn").length}`
            + ` · 파일 ${cov.files_read}/${cov.text_files} 읽음`))),
      h("p", { class: "mt-s" }, rv.summary), h("div", { class: "opinion" }, h("b", {}, "의견 "), rv.opinion))
      : h("div", { class: "note warn" }, run.status === "error" ? "평가가 끝나지 못해 리포트가 없습니다" : "내용 검토 리포트가 없습니다 (형식 검사가 실패했거나 이전 버전의 평가)"),
    rv ? [h("h2", { class: "mt" }, "체크리스트 (내용 검토)"),
      h("table", { class: "data report-items" }, h("thead", {}, h("tr", {}, ["판정", "항목", "사유", "근거"].map((t) => h("th", {}, t)))),
        h("tbody", {}, [...rv.items].sort((a, b) => RANK[a.status] - RANK[b.status]).map((i) => h("tr", { class: `st-${i.status}` },
          h("td", {}, badge("item", i.status)),
          h("td", { style: "max-width:240px" }, h("div", { class: "cell-main" }, i.title), h("div", { class: "cell-sub mono" }, i.id)),
          h("td", { style: "max-width:360px" }, i.reason || "–"),
          h("td", { style: "max-width:360px" }, i.findings.length ? i.findings.map((f) => h("div", { class: "finding" },
            h("span", { class: "mono" }, f.line ? `${f.file}:${f.line}` : f.file), f.issue ? ` ${f.issue}` : "",
            f.excerpt ? h("div", { class: "excerpt mono" }, f.excerpt) : null)) : h("span", { class: "muted" }, "–")))))),
      h("div", { class: "muted mt-s" }, `검토 범위: 텍스트 파일 ${cov.files_read}/${cov.text_files}, 줄 ${cov.lines_read}/${cov.lines_total}`
        + (cov.unread.length ? ` · 안 읽음 ${cov.unread.join(", ")}` : "") + (cov.partial.length ? ` · 일부만 ${cov.partial.join(", ")}` : "")
        + (cov.binary.length ? ` · 텍스트 아님 ${cov.binary.length}개` : "")
        + ` · 프롬프트 ${Object.entries(rv.prompts).map(([k, v]) => `${{ common: "공통", skill: "스킬", code: "코드", no_code: "노코드" }[k] || k} v${v}`).join(", ")}`
        + ` · 모델 ${rv.model} · ${rv.seconds}초`)] : null,
    h("h2", { class: "mt" }, "품질 평가"),
    quality ? h("ul", { class: "steps" }, stepItem(quality)) : h("p", { class: "muted" }, "없음"),
    resultsTable(run.results),
    h("details", { class: "mt" }, h("summary", {}, `형식 검사 (셀프 체크 규칙) · ${form.every((s) => s.status !== "failed") ? "통과" : "실패"}`),
      h("div", { class: "mt-s" }, checklist(form))),
    rest.length ? h("ul", { class: "steps mt" }, rest.map(stepItem)) : null);
}

// ── 배포 (aops deploy) ──────────────────────────────────────────────────────

function openDeploy(id, versions, production, preset, kind) {
  if (kind === "no_code" && !preset) return openNoCodeDeploy(id, production);
  const select = h("select", {}, versions.map((v) => h("option", { value: v.version, selected: v.version === (preset || versions[0].version) },
    `${v.version}${v.status === "production" ? " (운영)" : ""}`)));
  const reason = h("input", { type: "text", placeholder: "선택: 요청 사유 (예: 할인 규정 보강)" });
  const noteOf = (v) => (versions.find((x) => x.version === v) || {}).note || "";
  let typed = false;
  reason.value = noteOf(select.value);
  reason.addEventListener("input", () => { typed = true; });
  select.addEventListener("change", () => { if (!typed) reason.value = noteOf(select.value); });
  const body = h("div", {},
    h("label", { class: "field" }, h("span", {}, "배포할 버전"), select, h("small", {}, production ? `지금 운영 버전 ${production}. 더 낮은 버전을 고르면 되돌리기입니다.` : "운영 버전이 없습니다 (첫 배포)")),
    h("label", { class: "field" }, h("span", {}, "사유"), reason));
  modal(act("deploy"), body, [
    { label: "닫기", run: (close) => close() },
    { label: act("deploy"), cls: "primary", run: (close) => { close(); followDeploy(id, select.value, reason.value || null, kind); } }]);
}

function openNoCodeDeploy(id, production) {
  const reason = h("input", { type: "text", placeholder: "선택: 요청 사유 (예: 벤치마크 문항 보강)" });
  modal([act("deploy"), " (노코드)"], h("div", {},
    h("p", {}, "지금 개발용 Genie 정의를 스냅샷으로 평가합니다"),
    h("label", { class: "field" }, h("span", {}, "사유"), reason),
    h("div", { class: "note warn" }, "평가가 끝날 때까지 개발용 Genie를 고치지 마세요"),
    h("div", { class: "note mt" }, production ? `지금 운영 ${production}` : "첫 배포 (1.0.0)")), [
    { label: "닫기", run: (close) => close() },
    { label: act("deploy"), cls: "primary", run: (close) => { close(); followDeploy(id, null, reason.value || null, "no_code"); } }]);
}

function followDeploy(id, version, reason, kind) {
  const head = h("div", { class: "row" }, h("b", { class: "mono" }, `${id} ${version || "(스냅샷)"}`), h("span", { class: "spinner" }));
  const list = h("ul", { class: "steps mt" });
  const outcome = h("div", {});
  let gateId = null;
  const cancelBtn = h("button", { class: "btn danger", disabled: true, onclick: async () => {
    try { await post(`/release-gates/${enc(gateId)}/cancel`); toast("배포 요청을 취소했습니다", "ok"); } catch (e) { fail(e); }
  } }, "요청 취소");
  const m = modal("배포 진행", h("div", {}, head, list, outcome), [cancelBtn, h("button", { class: "btn", onclick: () => m.close() }, "닫기")], { wide: true });
  const liveBox = h("div", { class: "mt-s" });
  let watching = null;
  const item = (cls, name, detail) => h("li", {}, h("span", { class: `mark ${cls}` }, cls === "ok" ? icon("check", 12) : cls === "bad" ? icon("x", 12) : ""),
    h("div", {}, h("div", { class: "name" }, name), detail ? h("div", { class: "detail" }, detail) : null));
  post(`/projects/${enc(id)}/deploy-requests`, { version, reason }).then((gate) => {
    gateId = gate.gate_id;
    const timer = every(3000, async () => {
      const g = await get(`/release-gates/${enc(gateId)}`);
      const steps = g.evaluation ? g.evaluation.steps : [];
      const open = ["validating", "pending_approval"].includes(g.decision);
      const personDecides = g.decision === "pending_approval" && (g.approval_mode !== "auto" || !!g.decision_reason);
      cancelBtn.disabled = !open;
      fill(head, h("b", { class: "mono" }, `${id} ${g.version}`), badge("decision", g.decision), g.deployment_status ? badge("deployment", g.deployment_status) : null,
        h("span", { class: "muted" }, `${UPGRADE[g.upgrade_type] || g.upgrade_type} · 평가 ${MODE[g.validation_policy_mode]} · 승인 ${MODE[g.approval_mode] || g.approval_mode}`),
        (open && !personDecides) || g.deployment_status === "running" ? h("span", { class: "spinner" }) : null);
      const rows = [item("ok", "배포 요청", `gate ${g.gate_id} · 요청 ${g.requester}${kind === "no_code" && !version ? ` · 제안 버전 ${g.version} (배포가 성공하면 매김)` : ""}`)];
      const runId = g.validation_run_id || (g.evaluation && g.evaluation.validation_run_id);
      if (g.decision === "validating") {
        if (runId && !watching) watching = watchProgress(runId, liveBox, { compact: true });
        rows.push(item("run", "평가 중", runId ? liveBox : "평가 Job을 시작하는 중"));
      } else if (watching) { watching.stop(); watching = null; }
      else if (g.evaluation) {
        const verdict = g.evaluation.review ? g.evaluation.review.verdict : null;
        const word = g.evaluation.status !== "passed" || !verdict ? LABEL.validation[g.evaluation.status]?.[0] || g.evaluation.status : { pass: "통과", warn: "주의 있음", fail: "부적합 있음" }[verdict];
        rows.push(item(g.evaluation.status === "passed" && verdict !== "fail" ? "ok" : "bad", `평가 ${word}`,
          steps.map((s) => `${s.status === "passed" ? "✓" : s.status === "skipped" ? "–" : s.status === "warned" ? "!" : "✗"} ${s.name}`).join("  ")));
      }
      else if (g.validation_policy_mode === "exempt") rows.push(item("ok", "평가 면제", "정책"));
      if (g.decision === "pending_approval") rows.push(item(personDecides ? "wait" : "run", personDecides ? "승인자 필요" : "정책 자동 승인 중", g.decision_reason || "승인자가 배포 요청함에서 승인합니다"));
      if (["approved"].includes(g.decision)) rows.push(item("ok", "승인됨", g.approved_by === "policy:auto" ? "정책 자동 승인" : `승인자 ${g.approved_by || ""}`));
      if (g.deployment_status === "running") rows.push(item("run", "운영 반영 중", { no_code: "배포 Job이 운영용 Genie를 만들거나 고치고 공개 그룹에 CAN RUN (1~2분)",
        code: "aops 서버가 운영 앱을 배포 (4~5분)", skill: "배포 Job이 UC Skill 내용을 바꿈 (1~2분)" }[kind] || "배포 Job (1~5분)"));
      if (g.deployment_status === "succeeded") rows.push(item("ok", "운영 반영 완료", `운영 버전 ${g.version}`));
      if (g.deployment_status === "failed") rows.push(item("bad", "배포 실패", "배포 탭의 기록을 확인하세요"));
      if (g.decision === "rejected") rows.push(item("bad", "반려", g.decision_reason || ""));
      if (g.decision === "canceled") rows.push(item("bad", "취소됨", g.decision_reason || ""));
      list.replaceChildren(...rows);
      if (g.decision !== "validating" && steps.some((s) => s.status === "failed")) outcome.replaceChildren(h("h3", { class: "mt" }, "평가 결과"), h("ul", { class: "steps" }, steps.map(stepItem)), resultsTable(g.evaluation.results));
      const done = ["rejected", "canceled"].includes(g.decision) || ["succeeded", "failed"].includes(g.deployment_status);
      if (done) {
        refreshCounts();
        if (g.deployment_status === "succeeded") {
          toast(`운영 반영 완료: ${id} ${g.version}`, "ok");
          if (kind === "no_code") get(`/no-code/agents/${enc(id)}`).then((nc) => nc.prod_url && outcome.replaceChildren(h("div", { class: "note mt" },
            "운영용 Genie: ", h("a", { class: "ext", href: nc.prod_url, target: "_blank" }, nc.prod_url, icon("external", 12)), nc.consumer_group ? ` · ${nc.consumer_group} CAN RUN` : ""))).catch(() => {});
        }
      }
      if (personDecides) {
        outcome.replaceChildren(h("div", { class: "note warn mt" }, `승인자${g.approvers && g.approvers.length ? ` (${g.approvers.join(", ")})` : ""}에게 요청됐습니다 `,
          h("a", { href: `#/gates/${enc(g.gate_id)}` }, "리포트 보기 →")));
      }
      return !done && !personDecides;
    });
    m.onClose(() => { timer.stop(); if (watching) watching.stop(); if (location.hash.startsWith(`#/agents/${enc(id)}`)) render(); });
  }).catch((e) => { m.close(); if (e.code === "gate_open" && e.data) openGateConflict(id, version, reason, kind, e.data); else fail(e); });
}

// The agent has a request still moving: show it, and let its requester cancel it for this one.
function openGateConflict(id, version, reason, kind, open) {
  const mine = state.me && open.requester === state.me.user;
  const cancelable = ["validating", "pending_approval"].includes(open.decision);
  const buttons = [{ label: "닫기", run: (close) => close() },
    { label: "그 요청 보기 →", run: (close) => { close(); location.hash = `#/gates/${enc(open.gate_id)}`; } }];
  if (mine && cancelable && version) buttons.push({ label: `${open.version} 요청 취소하고 ${version} 요청`, cls: "primary", run: async (close) => {
    try {
      await post(`/release-gates/${enc(open.gate_id)}/cancel`);
      close();
      toast(`${open.version} 요청을 취소했습니다`, "ok");
      followDeploy(id, version, reason, kind);
    } catch (e) { fail(e); }
  } });
  modal("진행 중인 배포 요청이 있습니다", h("div", {},
    h("div", { class: "row" }, h("b", { class: "mono" }, `${id} ${open.version}`), badge("decision", open.decision),
      open.deployment_status ? badge("deployment", open.deployment_status) : null, h("span", { class: "muted" }, `요청 ${open.requester}`)),
    h("p", { class: "mt" }, "에이전트마다 배포 요청은 하나씩 진행합니다."),
    !cancelable ? h("div", { class: "note warn" }, "운영 반영 중입니다. 끝난 뒤 다시 요청하세요")
      : !mine ? h("div", { class: "note" }, "요청자나 승인자가 끝내야 합니다 (취소·반려)") : null), buttons);
}

// ── 권한 (aops promote) ─────────────────────────────────────────────────────

function openPromote(agent) {
  const input = h("textarea", { placeholder: "group:account users\nuser:someone@example.com" });
  const result = h("div", {});
  const run = async (revoke) => {
    const principals = input.value.split(/\n|,/).map((s) => s.trim()).filter(Boolean);
    if (!principals.length) return toast("대상을 적으세요", "bad");
    try {
      const grants = await post(`/agents/${enc(agent.agent_id)}/promote`, { principals, revoke });
      result.replaceChildren(h("table", { class: "data mt" }, h("thead", {}, h("tr", {}, ["대상", "권한", "객체", ""].map((t) => h("th", {}, t)))),
        h("tbody", {}, grants.map((g) => h("tr", {}, h("td", {}, g.principal), h("td", { class: "mono" }, g.permission), h("td", { class: "mono" }, g.securable),
          h("td", {}, g.granted ? h("span", { class: "badge ok" }, "부여") : h("span", { class: "badge plain" }, "회수")))))));
      toast(revoke ? "권한을 회수했습니다" : "권한을 주었습니다", "ok");
    } catch (e) { fail(e); }
  };
  modal([act("promote"), `: ${agent.agent_id}`], h("div", {},
    h("label", { class: "field" }, h("span", {}, "대상 (한 줄에 하나)"), input,
      h("small", {}, agent.build_type === "code"
        ? "group:<그룹> · user:<이메일> · sp:<application ID>. 코드 기반은 운영 앱에 CAN_USE를 겁니다. 받은 사람은 앱을 열어 씁니다."
        : agent.build_type === "no_code"
          ? "group:<그룹> · user:<이메일> · sp:<application ID>. 노코드는 운영용 Genie에 CAN RUN을 겁니다 (배포 때 SPEC.md의 공개 그룹에는 자동). 받은 사람은 테이블 SELECT와 warehouse 사용 권한도 있어야 답을 받습니다."
          : "group:<계정 그룹> · user:<이메일> · sp:<application ID>. 스킬은 운영 UC Skill에 READ_VOLUME을 겁니다. 받은 사람은 가져오기(load)를 할 수 있습니다.")),
    result), [
    { label: "회수", cls: "danger", run: () => run(true) },
    { label: "권한 주기", cls: "primary", run: () => run(false) }]);
}

// ── 가져오기 (aops load) ────────────────────────────────────────────────────

async function openLoad(agent) {
  // a link (app, Genie) opens in a window made during the click: one opened after the request is often blocked
  const win = agent.build_type === "skill" ? null : window.open("", "_blank");
  try {
    const info = await post(`/agents/${enc(agent.agent_id)}/load`, {});
    if (info.link) {
      const what = agent.build_type === "no_code" ? ["운영용 Genie", "CAN RUN 권한이 있어야 열립니다"] : ["운영 앱", "앱 사용 권한(CAN_USE)이 있어야 열립니다"];
      if (win) { win.location = info.link; return toast(`${what[0]}를 엽니다: ${agent.agent_id} ${info.version}`, "ok", what[1]); }
      return modal(`${what[0]}: ${agent.agent_id} ${info.version}`, h("div", {}, h("p", {}, "브라우저가 새 창을 막았습니다. 아래 링크로 여세요."),
        h("a", { class: "btn primary", href: info.link, target: "_blank" }, `${what[0]} 열기`, icon("external", 14)), h("p", { class: "muted mt" }, what[1])));
    }
    if (win) win.close();
    if (!info.allowed || !info.archive) return toast("가져올 수 없습니다", "bad", "운영 UC Skill 읽기 권한이 필요합니다 (담당자에게 권한 요청)");
    await download(info.archive.url.replace(API, ""), `${agent.agent_id}-${info.version}.zip`);
    toast(`가져옴: ${agent.agent_id} ${info.version}`, "ok", "풀어서 aops run으로 쓰거나 Genie Code 폴더에 올리세요");
  } catch (e) { if (win) win.close(); fail(e); }
}

// ── 배포 요청함 (aops gates · approve · reject · deploy --cancel) ─────────────

async function gatesPage(root, params) {
  let tab = params.get("tab") || (state.toApprove ? "approve" : "mine");
  const pane = h("div", {});
  const tabBar = h("div", { class: "tabs", "data-tour": "gates-tabs" });
  const tabs = [["approve", "승인할 요청", "?to_approve=true"], ["mine", "내 요청", "?mine=true"], ["all", "전체", ""]];
  const load = async () => {
    tabBar.replaceChildren(...tabs.map(([key, label]) => h("button", { class: key === tab ? "on" : "", onclick: () => { tab = key; load(); } },
      icon({ approve: "shield", mine: "send", all: "inbox" }[key], 15), label, key === "approve" && state.toApprove ? h("span", { class: "n" }, state.toApprove) : null)));
    pane.replaceChildren(h("div", { class: "loading" }, h("span", { class: "spinner" })));
    const page = await get(`/release-gates${tabs.find((t) => t[0] === tab)[2]}`);
    pane.replaceChildren(gatesTable(page, { agent: true, approve: tab === "approve" }));
  };
  root.replaceChildren(pageHead("배포 요청함", "요청마다 평가 리포트를 보고 승인·반려합니다", [], "aops gates · approve · reject", "inbox"),
    h("div", { class: "card pad" }, tabBar, pane));
  await load();
}

// Deploy requests: each row opens its report page, where the approver decides
function gatesTable(page, { agent, approve }) {
  const rows = page.items;
  if (!rows.length) return empty("inbox", approve ? "승인할 배포 요청이 없습니다" : "배포 요청이 없습니다",
    approve ? "내가 지정 승인자(또는 승인 그룹)인 요청만 나옵니다. 내 요청은 내가 승인할 수 없습니다." : "");
  return h("table", { class: "data" },
    h("thead", {}, h("tr", {}, [agent ? "에이전트" : "버전", "상태", "내용 검토", "승인자", "요청", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, rows.map((g, i) => h("tr", { class: "click", "data-tour": i === 0 ? "gate-row" : null, onclick: () => { location.hash = `#/gates/${enc(g.gate_id)}`; } },
      h("td", {}, agent ? [h("div", { class: "cell-main" }, g.agent_id), h("div", { class: "cell-sub mono" }, `${g.version} · ${UPGRADE[g.upgrade_type] || g.upgrade_type}`)]
        : [h("div", { class: "cell-main mono" }, g.version), h("div", { class: "cell-sub" }, UPGRADE[g.upgrade_type] || g.upgrade_type)]),
      h("td", {}, badge("decision", g.decision), g.deployment_status ? [" ", badge("deployment", g.deployment_status)] : null,
        g.decision_reason ? h("div", { class: "cell-sub", style: "max-width:320px" }, g.decision_reason) : null),
      h("td", {}, g.review_verdict ? badge("verdict", g.review_verdict) : h("span", { class: "muted" }, "–")),
      h("td", { class: "cell-sub" }, g.approvers && g.approvers.length ? g.approvers.join(", ") : "승인 그룹"),
      h("td", {}, h("div", {}, g.requester), h("div", { class: "cell-sub" }, when(g.requested_at))),
      h("td", { class: "num" }, approve && g.decision === "pending_approval" ? h("span", { class: "btn small primary" }, "리포트 보고 결정") : h("span", { class: "muted" }, "열기 →"))))));
}

// A request in three steps: evaluation, approval, production (the same marks as the agent's lifecycle)
function gateProgress(g, run) {
  const ev = g.decision === "validating" ? ["run", "평가 중"] : !run ? ["na", g.validation_policy_mode === "exempt" ? "면제" : "–"]
    : ["failed", "error", "stale"].includes(run.status) ? ["bad", LABEL.validation[run.status][0]]
      : run.review && run.review.verdict === "fail" ? ["warn", "부적합 있음"] : run.review && run.review.verdict === "warn" ? ["warn", "주의 있음"] : ["done", "통과"];
  const ap = g.decision === "pending_approval" ? ["warn", g.approvers.length ? `${g.approvers.join(", ")} 대기` : "승인 대기"]
    : g.decision === "approved" ? ["done", g.approved_by === "policy:auto" ? "정책 자동 승인" : `${g.approved_by || ""} 승인`]
      : g.decision === "rejected" ? ["bad", "반려"] : g.decision === "canceled" ? ["bad", "취소"] : ["wait", "평가 뒤"];
  const dp = g.deployment_status === "succeeded" ? ["done", `운영 ${g.version}`] : g.deployment_status === "running" ? ["run", "운영 반영 중"]
    : g.deployment_status === "failed" ? ["bad", "배포 실패"] : ["wait", "승인 뒤"];
  return h("ol", { class: "lifecycle three" }, [["평가", ev, "flask"], ["승인", ap, "shield"], ["운영 반영", dp, "live"]].map(([t, [st, text], ico]) => h("li", { class: `st ${st}` },
    h("div", { class: "dot" }, icon(st === "done" ? "check" : st === "bad" ? "alert" : ico, 15)), h("div", { class: "body" }, h("div", { class: "t" }, t), h("div", { class: "s" }, text)))));
}

async function gatePage(root, id) {
  const g = await get(`/release-gates/${enc(id)}`);
  const run = g.evaluation;
  const live = h("div", {}, h("div", { class: "muted" }, "평가 Job을 시작하는 중"));
  const open = ["validating", "pending_approval"].includes(g.decision);
  const ack = h("input", { type: "checkbox", id: "ack" });
  const text = h("textarea", { placeholder: "의견 (승인) · 반려 사유 (반려는 필수)" });
  const decide = async (action) => {
    if (action === "approve" && !ack.checked) return toast("리포트를 확인했다고 체크하세요", "bad");
    if (action === "reject" && !text.value.trim()) return toast("반려 사유를 적으세요", "bad");
    try {
      await post(`/release-gates/${enc(id)}/${action}`, action === "reject" ? { reason: text.value.trim() }
        : { comment: text.value.trim() || null, validation_run_id: run ? run.validation_run_id : null });
      toast(action === "reject" ? "반려했습니다" : "승인했습니다. 운영 반영이 시작됩니다", "ok");
      await refreshCounts(); render();
    } catch (e) { fail(e); }
  };
  const panel = g.can_approve ? h("div", { class: "card pad decide", "data-tour": "gate-decide" },
    h("h2", {}, "승인자 결정"),
    g.approve_as_exception ? h("div", { class: "note warn" }, "개발용 예외로 결정합니다 (정책 dev_approvers): ",
      g.requester === (state.me && state.me.user) ? "내 요청입니다. " : "지정 승인자가 아닙니다. ", "승인은 감사 이력에 예외로 남습니다.") : null,
    h("label", { class: "row ack", for: "ack" }, ack, h("span", {}, `아래 평가 리포트(${run ? run.validation_run_id.slice(0, 12) : "없음"})를 확인했습니다`)),
    h("label", { class: "field mt-s" }, h("span", {}, "의견·사유"), text),
    h("div", { class: "row" }, h("span", { class: "muted" }, "승인하면 운영 반영이 바로 시작됩니다."),
      h("span", { class: "right row" }, h("button", { class: "btn danger", onclick: () => decide("reject") }, "반려"),
        h("button", { class: "btn primary", onclick: () => decide("approve") }, act("approve"))))) : null;
  const waiting = g.decision === "pending_approval" && !g.can_approve
    ? h("div", { class: "note mt-s" }, g.requester === (state.me && state.me.user) ? "내 요청입니다. 지정 승인자가 이 리포트를 보고 결정합니다."
      : `이 요청은 ${g.approvers.length ? `지정 승인자(${g.approvers.join(", ")})` : "승인 그룹"}가 결정합니다.`) : null;
  const request = h("div", { class: "card pad", "data-tour": "gate-request" }, h("h2", {}, "요청"),
    h("dl", { class: `kv ${panel ? "" : "two-col"}` },
      h("dt", {}, "사유"), h("dd", {}, g.reason || "–"),
      h("dt", {}, "지정 승인자"), h("dd", {}, g.approvers.length ? g.approvers.join(", ") : h("span", { class: "muted" }, "없음 (정책의 승인 그룹)")),
      h("dt", {}, "평가·승인 방식"), h("dd", {}, `${MODE[g.validation_policy_mode]} · ${MODE[g.approval_mode] || g.approval_mode} (승인 ${g.approvals}/${g.required_approvals})`),
      g.target_workspace ? [h("dt", {}, "운영 워크스페이스"), h("dd", {}, g.target_workspace)] : null,
      h("dt", {}, "결정"), h("dd", {}, g.decision_reason || (g.approved_by ? `승인 ${g.approved_by === "policy:auto" ? "정책 자동" : g.approved_by}` : "–"))),
    waiting, h("div", { class: "muted mt-s mono", style: "font-size:11.5px" }, `gate ${g.gate_id}${run ? ` · 평가 ${run.validation_run_id}` : ""}`));
  root.replaceChildren(
    pageHead(`배포 요청 ${g.agent_id} ${g.version}`, `${UPGRADE[g.upgrade_type] || g.upgrade_type} · 요청 ${g.requester} · ${when(g.requested_at)}`, [
      h("a", { class: "btn", href: `#/agents/${enc(g.agent_id)}` }, "에이전트 보기"),
      open && g.requester === (state.me && state.me.user) ? h("button", { class: "btn danger", onclick: async () => {
        try { await post(`/release-gates/${enc(id)}/cancel`); toast("취소했습니다", "ok"); render(); } catch (e) { fail(e); } } }, "요청 취소") : null], null, "send"),
    h("div", { class: "card pad", "data-tour": "gate-progress" }, gateProgress(g, run)),
    panel ? h("div", { class: "grid two mt" }, request, panel) : h("div", { class: "mt" }, request),
    g.decision === "validating" ? h("div", { class: "card pad mt" }, h("h2", {}, "평가 진행"), live)
      : run ? h("div", { class: "card pad mt", "data-tour": "gate-report" }, h("h2", {}, "평가 리포트"), run.failure_reason ? h("div", { class: "note bad" }, run.failure_reason) : null, reportView(run))
        : h("div", { class: "card pad mt" }, h("p", { class: "muted" }, "평가 기록이 없습니다")));
  if (g.decision === "validating") {
    const runId = g.validation_run_id || (run && run.validation_run_id);
    if (runId) watchProgress(runId, live);
    every(5000, async () => { const now = await get(`/release-gates/${enc(id)}`); if (now.decision !== "validating") { render(); return false; } return true; });
  }
}

// ── 등록 (aops register) ────────────────────────────────────────────────────

const EXCLUDE_DIRS = new Set(["runs", ".aops", ".claude", ".agents", ".codex", ".git", "__pycache__", ".venv", "node_modules"]);
const EXCLUDE_FILES = new Set(["CLAUDE.md", "AGENTS.md", ".DS_Store"]);

function included(rel) {  // preview only; the server applies the same rule (aops.package.included)
  const parts = rel.split("/");
  if (parts.some((p) => EXCLUDE_DIRS.has(p))) return false;
  return !((parts.length === 1 && EXCLUDE_FILES.has(parts[0])) || parts[parts.length - 1] === ".DS_Store");
}

function registerPage(root, params) {
  const expected = params.get("agent");
  let picked = null;  // { kind: "folder" | "zip" | "workspace", name, files: [{rel, file}], zip, path }
  let bump = "minor";
  const info = h("div", {});
  const result = h("div", {});
  const version = h("input", { type: "text", placeholder: "비우면 자동 (처음 1.0.0)", style: "width:200px" });
  const note = h("input", { type: "text", placeholder: "선택: 무엇을 바꿨는지 (예: 사내 주소·고객 정보 제거)", maxlength: 500 });
  const submit = h("button", { class: "btn primary", "data-tour": "reg-submit", disabled: true, onclick: () => send() }, act("register"));
  const checkBtn = h("button", { class: "btn", "data-tour": "reg-check", disabled: true, title: "템플릿 구성을 지금 바로 점검 (기록 안 함)", onclick: () => selfCheck() }, act("selfcheck"));
  const folderInput = h("input", { type: "file", webkitdirectory: true, multiple: true, style: "display:none", onchange: (e) => pickFolder([...e.target.files]) });
  const zipInput = h("input", { type: "file", accept: ".zip", style: "display:none", onchange: (e) => e.target.files[0] && pickZip(e.target.files[0]) });
  const drop = h("div", { class: "drop", "data-tour": "reg-drop" },
    h("div", { class: "drop-ico" }, icon("upload", 22)),
    h("p", {}, h("b", {}, "에이전트 폴더를 고르거나 zip을 놓으세요")),
    h("div", { class: "row", style: "justify-content:center" },
      h("button", { class: "btn", onclick: () => folderInput.click() }, icon("folder"), "폴더 고르기"), h("button", { class: "btn", onclick: () => zipInput.click() }, icon("package"), "zip 고르기")),
    folderInput, zipInput);
  const wsPath = h("input", { type: "text", placeholder: "/Workspace/Users/<나>/aops/<이름>", style: "flex:1", value: params.get("path") || "" });
  const wsBox = h("div", { class: "row mt", "data-tour": "reg-ws" }, h("span", { class: "muted" }, "또는 워크스페이스 폴더:"), wsPath,
    h("button", { class: "btn", onclick: () => pickWorkspace() }, "이 폴더로"));

  function pickWorkspace() {
    const path = wsPath.value.trim().replace(/\/+$/, "");
    if (!path.startsWith("/Workspace/")) return toast("/Workspace/로 시작하는 폴더를 적으세요", "bad");
    picked = { kind: "workspace", name: expected || path.split("/").pop(), path };
    showPicked();
  }
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault(); drop.classList.remove("over");
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith(".zip")) pickZip(file); else toast("폴더는 '폴더 고르기'로 고르세요 (끌어 놓기는 zip만)", "bad");
  });

  async function pickFolder(files) {
    const entries = files.map((file) => ({ file, rel: file.webkitRelativePath.split("/").slice(1).join("/") }));
    const toml = entries.find((e) => e.rel === "aops.toml");
    if (!toml) return toast("aops.toml이 없는 폴더입니다", "bad", "aops init으로 만든 에이전트 폴더를 고르세요");
    const text = await toml.file.text();
    const name = (text.match(/^\s*name\s*=\s*"([^"]+)"/m) || [])[1];
    const spec = entries.find((e) => e.rel === "SPEC.md");
    const desc = spec ? ((await spec.file.text()).match(/^description:\s*(.*)$/m) || [])[1] : null;
    const kept = entries.filter((e) => included(e.rel));
    picked = { kind: "folder", name, files: kept, skipped: entries.length - kept.length, desc };
    showPicked();
  }

  function pickZip(file) {
    picked = { kind: "zip", name: expected || file.name.replace(/(-\d+\.\d+\.\d+)?\.zip$/, ""), zip: file };
    showPicked();
  }

  function showPicked() {
    const mismatch = expected && picked.name && picked.name !== expected;
    info.replaceChildren(h("div", { class: "card pad mt" },
      h("dl", { class: "kv" },
        h("dt", {}, "에이전트"), h("dd", {}, h("b", {}, picked.name || "(aops.toml에 이름 없음)"), picked.desc ? h("span", { class: "muted" }, ` · ${picked.desc}`) : null),
        h("dt", {}, "보낼 것"), h("dd", {}, picked.kind === "zip" ? `zip ${size(picked.zip.size)}` : picked.kind === "workspace"
          ? h("span", { class: "mono" }, picked.path)
          : `파일 ${picked.files.length}개${picked.skipped ? ` (${picked.skipped}개 제외)` : ""}`)),
      picked.kind === "folder" ? h("div", { class: "files" }, picked.files.map((f) => h("div", {}, f.rel))) : null,
      mismatch ? h("div", { class: "note warn mt" }, `이 폴더는 ${picked.name}입니다 (${expected}의 새 버전이 아닙니다)`) : null));
    submit.disabled = !picked.name;
    checkBtn.disabled = !picked.name;
  }

  function pickedForm() {
    const form = new FormData();
    if (picked.kind === "zip") form.append("archive", picked.zip, picked.zip.name);
    else if (picked.kind === "workspace") form.append("workspace_path", picked.path);
    else picked.files.forEach((f) => form.append("files", f.file, `${picked.name}/${f.rel}`));
    return form;
  }

  async function selfCheck() {
    checkBtn.disabled = true;
    try {
      const r = await api("POST", `/projects/${enc(picked.name)}/validations`, { form: pickedForm() });
      result.replaceChildren(h("div", { class: "card pad mt" }, h("div", { class: "row" }, h("h2", { style: "margin:0" }, `셀프 체크: ${picked.name}`), badge("validation", r.status)),
        h("div", { class: "mt" }, checklist(r.steps))));
    } catch (e) { fail(e); }
    checkBtn.disabled = false;
  }

  async function send() {
    const form = pickedForm();
    if (version.value.trim()) form.append("version", version.value.trim()); else form.append("bump", bump);
    if (note.value.trim()) form.append("note", note.value.trim());
    submit.disabled = true;
    submit.replaceChildren(h("span", { class: "spinner" }), " 등록 중…");
    try {
      const row = await api("POST", `/projects/${enc(picked.name)}/registrations`, { form });
      result.replaceChildren(h("div", { class: "card pad mt" },
        h("div", { class: "row" }, h("h2", { style: "margin:0" }, `등록: ${picked.name} ${row.version}`), badge("version", row.status)),
        h("dl", { class: "kv mt" },
          row.note ? [h("dt", {}, "사유"), h("dd", {}, row.note)] : null,
          h("dt", {}, "버전 zip"), h("dd", { class: "mono" }, row.artifact_path),
          h("dt", {}, "파일 해시"), h("dd", { class: "mono" }, short(row.source_sha256)),
          h("dt", {}, "스킬"), h("dd", {}, row.skills.map((s) => h("div", {}, h("b", {}, s.skill_alias), h("span", { class: "muted" }, ` → 배포하면 UC Skill ${s.uc_name || ""}`)))),
          h("dt", {}, "Git"), h("dd", {}, row.git ? (row.git.status === "synced" ? h("a", { href: row.git.url, target: "_blank" }, row.git.url) : `${row.git.status}: ${row.git.error || ""}`) : "–")),
        h("div", { class: "row mt" },
          h("button", { class: "btn", onclick: () => openEvaluate(picked.name, row.version) }, act("evaluate")),
          h("button", { class: "btn primary", onclick: async (e) => {
            e.target.disabled = true;
            try { const [a, v] = await Promise.all([get(`/agents/${enc(picked.name)}`), get(`/agents/${enc(picked.name)}/versions`)]); openDeploy(picked.name, v.items, a.production_version, row.version, a.build_type); }
            catch (err) { fail(err); }
            e.target.disabled = false;
          } }, act("deploy")),
          h("a", { class: "btn ghost", href: `#/agents/${enc(picked.name)}` }, "에이전트 보기 →"))));
      toast(`등록: ${picked.name} ${row.version}`, "ok");
    } catch (e) { fail(e); }
    submit.disabled = false;
    fill(submit, act("register"));
  }

  const bumpSeg = h("div", { class: "seg" }, [["fix", "fix (1.0.x)"], ["minor", "minor (1.x.0)"], ["major", "major (x.0.0)"]].map(([v, label]) =>
    h("button", { class: v === bump ? "on" : "", onclick: (e) => { bump = v; bumpSeg.querySelectorAll("button").forEach((b) => b.classList.remove("on")); e.target.classList.add("on"); } }, label)));
  if (params.get("path")) pickWorkspace();
  root.replaceChildren(
    pageHead(expected ? `${expected} 새 버전 등록` : "폴더·zip 등록", expected ? "작업 폴더나 PC 폴더·zip으로 새 버전을 저장합니다" : "PC 폴더·zip·워크스페이스 폴더를 버전으로 저장합니다. 처음이면 에이전트가 생깁니다", [], "aops register", "upload"),
    h("div", { class: "card pad" }, drop, wsBox, info,
      h("div", { class: "grid two mt", "data-tour": "reg-version" },
        h("div", { class: "field" }, h("span", {}, "버전 올리는 폭"), bumpSeg, h("small", {}, "처음 등록은 1.0.0")),
        h("label", { class: "field" }, h("span", {}, "또는 버전 직접"), version)),
      h("label", { class: "field", "data-tour": "reg-note" }, h("span", {}, "사유"), note, h("small", {}, "버전 기록과 Git 커밋 메시지에 남습니다")),
      h("div", { class: "row" }, h("span", { class: "muted" }, "등록은 버전을 저장할 뿐입니다. 평가와 승인은 배포 요청 때 합니다."),
        h("span", { class: "right row" }, checkBtn, submit))),
    result);
}

// ── 새 에이전트 (aops init) ─────────────────────────────────────────────────

const KINDS = [["skill", "스킬 기반"], ["code", "코드 기반"], ["no_code", "노코드 (Genie)"]];
const MAX_TABLES = 30;
const ACCESS = { ok: ["서비스 주체 읽기 가능", "ok"], missing: ["서비스 주체 SELECT 없음", "warn"], unknown: ["서비스 주체 확인 못함", "plain"] };

function listError(e, what) {  // a list the requester's token could not read: say why, and that typing still works
  return h("div", { class: "note warn" }, `${what}을 불러오지 못했습니다: ${e.title || e.message || e}`,
    h("div", { class: "mt-s" }, "[직접 입력]으로 적을 수도 있습니다."));
}

// Tables the requester may SELECT (Unity Catalog lists only those), picked catalog → schema → table.
function tablePicker() {
  const picked = new Map();  // full_name → {kind, comment}
  const access = new Map();  // full_name → {status, detail}
  let tables = [];
  let manual = false;
  const catSel = h("select", { "aria-label": "카탈로그" }, h("option", { value: "" }, "카탈로그 불러오는 중…"));
  const schSel = h("select", { "aria-label": "스키마", disabled: true }, h("option", { value: "" }, "스키마"));
  const filter = h("input", { type: "search", placeholder: "이 스키마에서 이름·설명으로 찾기", disabled: true, oninput: () => drawList() });
  const list = h("div", { class: "pick-list" }, h("div", { class: "muted pad-s" }, "카탈로그와 스키마를 고르면 내가 SELECT할 수 있는 테이블이 나옵니다."));
  const chips = h("div", { class: "chips" });
  const count = h("span", { class: "muted" });
  const status = h("div", {});
  const typed = h("textarea", { placeholder: "main.sales.orders\nmain.sales.customers", oninput: () => drawChips() });
  const browse = h("div", {}, h("div", { class: "row" }, catSel, schSel), h("div", { class: "row mt-s" }, filter), list);
  const typedBox = h("div", { style: "display:none" }, typed, h("small", { class: "muted" }, "<catalog>.<schema>.<table> 한 줄에 하나. 내게 SELECT가 없으면 Genie를 만들 때 실패합니다"));
  const toggle = h("button", { type: "button", class: "btn ghost small", onclick: () => {
    manual = !manual; browse.style.display = manual ? "none" : ""; typedBox.style.display = manual ? "" : "none";
    toggle.textContent = manual ? "목록에서 고르기" : "직접 입력"; drawChips();
  } }, "직접 입력");

  const names = () => manual ? typed.value.split(/\n|,/).map((t) => t.trim()).filter(Boolean) : [...picked.keys()];
  const values = () => manual ? { tables: names(), metric_views: [] } : {
    tables: [...picked].filter(([, v]) => v.kind !== "metric_view").map(([k]) => k),
    metric_views: [...picked].filter(([, v]) => v.kind === "metric_view").map(([k]) => k) };

  async function checkAccess(list) {
    const fresh = list.filter((n) => !access.has(n));
    if (!fresh.length) return;
    fresh.forEach((n) => access.set(n, null));
    try {
      (await post("/no-code/table-access", { tables: fresh })).forEach((r) => access.set(r.full_name, r));
    } catch { fresh.forEach((n) => access.delete(n)); }
    drawChips();
  }

  function drawChips() {
    const all = names();
    count.textContent = `${all.length}개 골랐습니다 (5개 이하 권장, 최대 ${MAX_TABLES}개)`;
    if (manual) { fill(chips); fill(status); return; }
    fill(chips, all.map((n) => {
      const a = access.get(n);
      const [label, cls] = a ? ACCESS[a.status] : ["확인 중…", "plain"];
      return h("span", { class: "chip", title: a ? a.detail : "" }, h("span", { class: "mono" }, n),
        picked.get(n).kind === "metric_view" ? h("span", { class: "tag" }, "메트릭 뷰") : null,
        h("span", { class: `badge ${cls}` }, label),
        h("button", { type: "button", class: "x", "aria-label": `${n} 빼기`, onclick: () => { picked.delete(n); drawChips(); drawList(); } }, icon("x", 12)));
    }));
    const missing = all.filter((n) => (access.get(n) || {}).status === "missing");
    fill(status, missing.length ? h("div", { class: "note warn mt-s" }, `aops 서비스 주체가 ${missing.length}개를 읽지 못합니다: 개발용 Genie는 지금 만들 수 있지만, `
      + "운영용 Genie는 서비스 주체 권한으로 돌기 때문에 첫 배포 전에 데이터 주인이 SELECT(와 USE CATALOG·USE SCHEMA)를 주어야 합니다.") : null);
    checkAccess(all);
  }

  function drawList() {
    const q = filter.value.trim().toLowerCase();
    const rows = tables.filter((t) => !q || `${t.name} ${t.comment || ""}`.toLowerCase().includes(q));
    if (!tables.length) return fill(list, h("div", { class: "muted pad-s" }, schSel.value ? "이 스키마에 내가 SELECT할 수 있는 테이블이 없습니다." : "스키마를 고르세요."));
    fill(list, rows.map((t) => {
      const on = picked.has(t.full_name);
      const box = h("input", { type: "checkbox", checked: on, disabled: !on && picked.size >= MAX_TABLES, onchange: (e) => {
        if (e.target.checked) picked.set(t.full_name, { kind: t.kind, comment: t.comment }); else picked.delete(t.full_name);
        drawChips(); drawList();
      } });
      return h("label", { class: `pick-row ${on ? "on" : ""}` }, box,
        h("div", { class: "grow" }, h("div", {}, h("b", {}, t.name), " ", t.kind === "metric_view" ? h("span", { class: "tag" }, "메트릭 뷰")
          : t.table_type && t.table_type !== "MANAGED" ? h("span", { class: "tag" }, t.table_type.toLowerCase()) : null),
          t.comment ? h("div", { class: "cell-sub" }, t.comment) : null),
        h("button", { type: "button", class: "btn ghost small", title: "컬럼 보기", onclick: (e) => { e.preventDefault(); showColumns(t.full_name); } }, "컬럼"));
    }), rows.length ? null : h("div", { class: "muted pad-s" }, "찾는 테이블이 없습니다."));
  }

  async function showColumns(fullName) {
    const m = modal(fullName, h("div", { class: "loading" }, h("span", { class: "spinner" })), [], { wide: true });
    try {
      const t = await get(`/me/tables/${enc(fullName)}`);
      m.body.replaceChildren(t.comment ? h("p", {}, t.comment) : null, h("p", { class: "muted" }, `${t.table_type || ""} · 소유 ${t.owner || "–"} · 컬럼 ${t.columns.length}개`),
        h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["컬럼", "형식", "설명"].map((x) => h("th", {}, x)))),
          h("tbody", {}, t.columns.map((c) => h("tr", {}, h("td", { class: "mono cell-main" }, c.name), h("td", { class: "mono muted" }, c.type || ""), h("td", { class: "muted" }, c.comment || ""))))));
    } catch (e) { m.close(); fail(e); }
  }

  catSel.addEventListener("change", async () => {
    tables = []; drawList(); schSel.disabled = true; filter.disabled = true;
    if (!catSel.value) return;
    fill(schSel, h("option", { value: "" }, "스키마 불러오는 중…"));
    try {
      const r = await get(`/me/catalogs/${enc(catSel.value)}/schemas`);
      fill(schSel, h("option", { value: "" }, r.items.length ? "스키마 고르기" : "쓸 수 있는 스키마가 없습니다"), r.items.map((s) => h("option", { value: s.name, title: s.comment || "" }, s.name)));
      schSel.disabled = !r.items.length;
    } catch (e) { fill(schSel, h("option", { value: "" }, "불러오지 못함")); fill(list, listError(e, "스키마 목록")); }
  });
  schSel.addEventListener("change", async () => {
    tables = []; filter.value = ""; filter.disabled = true;
    if (!schSel.value) return drawList();
    fill(list, h("div", { class: "loading" }, h("span", { class: "spinner" }), " 테이블 불러오는 중…"));
    try {
      const r = await get(`/me/catalogs/${enc(catSel.value)}/schemas/${enc(schSel.value)}/tables`);
      tables = r.items; filter.disabled = false; drawList();
      if (r.truncated) list.append(h("div", { class: "note mt-s" }, "테이블이 많아 앞 1000개만 보입니다. 위 칸에서 이름으로 찾으세요."));
    } catch (e) { fill(list, listError(e, "테이블 목록")); }
  });
  get("/me/catalogs").then((r) => {
    fill(catSel, h("option", { value: "" }, r.items.length ? "카탈로그 고르기" : "쓸 수 있는 카탈로그가 없습니다"),
      r.items.map((c) => h("option", { value: c.name, title: c.comment || "" }, c.name)));
  }).catch((e) => { fill(catSel, h("option", { value: "" }, "불러오지 못함")); fill(list, listError(e, "카탈로그 목록")); });
  drawChips();
  const el = h("div", { class: "field" }, h("div", { class: "row" }, h("span", { class: "label" }, "데이터 (내가 SELECT할 수 있는 테이블)"), h("span", { class: "right" }, toggle)),
    browse, typedBox, h("div", { class: "row mt-s" }, count), chips, status);
  return { el, values, count: () => names().length };
}

// Approvers: workspace users and groups found by name or email (the aops server looks them up), or typed in.
const EMAIL = /^[^@\s,]+@[^@\s,]+\.[^@\s,]+$/;

function approverPicker(required) {
  const chosen = [];
  let timer = null;
  const input = h("input", { type: "search", placeholder: "이름·이메일로 찾기 (2자 이상). 이메일이나 group:<그룹>을 적고 Enter로도 추가" });
  const results = h("div", { class: "pick-list short", style: "display:none" });
  const chips = h("div", { class: "chips" });
  const warn = h("div", {});
  const me = () => (state.me ? state.me.user.toLowerCase() : "");
  const add = (principal, name) => {
    if (!chosen.some((c) => c.principal.toLowerCase() === principal.toLowerCase())) chosen.push({ principal, name: name || principal });
    input.value = ""; results.style.display = "none"; draw();
  };
  const draw = () => {
    fill(chips, chosen.map((c, i) => h("span", { class: "chip" }, c.name !== c.principal ? [h("b", {}, c.name), h("span", { class: "mono muted" }, c.principal)] : h("span", { class: "mono" }, c.principal),
      c.principal.toLowerCase() === me() ? h("span", { class: "badge warn" }, "나") : null,
      h("button", { type: "button", class: "x", "aria-label": `${c.principal} 빼기`, onclick: () => { chosen.splice(i, 1); draw(); } }, icon("x", 12)))));
    const others = chosen.filter((c) => c.principal.toLowerCase() !== me());
    fill(warn, chosen.length && !others.length ? h("div", { class: "note warn mt-s" }, "나만 승인자이면 내 배포 요청을 승인할 사람이 없습니다. 다른 사람이나 그룹을 더하세요.")
      : !chosen.length && required ? h("div", { class: "note warn mt-s" }, "정책이 승인자 지정을 요구합니다. 한 명 이상 고르세요.") : null);
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { results.style.display = "none"; return; }
    timer = setTimeout(async () => {
      try {
        const rows = await get(`/directory?q=${enc(q)}`);
        results.style.display = "";
        fill(results, rows.length ? rows.map((r) => h("button", { type: "button", class: "pick-row", onclick: () => add(r.principal, r.name) },
          h("div", { class: "grow" }, h("b", {}, r.name), h("div", { class: "cell-sub mono" }, r.principal)), h("span", { class: "tag" }, r.kind === "group" ? "그룹" : "사용자")))
          : h("div", { class: "muted pad-s" }, "찾는 사람·그룹이 없습니다. 이메일이나 group:<그룹>을 적고 Enter"));
      } catch (e) { results.style.display = ""; fill(results, h("div", { class: "muted pad-s" }, `${e.title || e}. 이메일이나 group:<그룹>을 적고 Enter`)); }
    }, 300);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const v = input.value.trim();
    if (EMAIL.test(v) || /^group:\S/.test(v)) add(v);
    else toast("이메일이나 group:<그룹> 형태로 적으세요", "bad");
  });
  draw();
  return {
    el: h("div", { class: "field" }, h("span", {}, `배포 요청을 승인할 사람${required ? " · 필수" : ""}`), input, results, chips, warn,
      h("small", {}, "비우면 정책의 승인 그룹이 승인합니다")),
    values: () => chosen.map((c) => c.principal),
  };
}

// SQL warehouses the requester can see; the aops server's one first. Typing an ID still works.
function warehousePicker(note) {
  const sel = h("select", {}, h("option", { value: "" }, "불러오는 중…"));
  const typed = h("input", { type: "text", placeholder: "warehouse ID", style: "display:none" });
  get("/me/warehouses").then((r) => {
    const listed = r.items.some((w) => w.default);  // the server's one is first when I can see it
    fill(sel, listed ? null : h("option", { value: "" }, `aops 기본 (${r.default || "서버 설정"})`), r.items.map((w) => h("option", { value: w.default ? "" : w.id, selected: w.default },
      `${w.name}${w.default ? " (aops 기본)" : ""} · ${[w.serverless ? "serverless" : null, w.size, w.state && w.state.toLowerCase()].filter(Boolean).join(" · ")}`)));
  }).catch(() => { sel.style.display = "none"; typed.style.display = ""; typed.placeholder = "목록을 못 불러왔습니다. warehouse ID (비우면 aops 기본)"; });
  return { el: h("label", { class: "field" }, h("span", {}, "SQL warehouse"), sel, typed, h("small", {}, note)), value: () => (sel.style.display === "none" ? typed.value : sel.value).trim() };
}

// Genie spaces the requester can open (to copy or move), with a search; a URL or ID can be pasted instead.
function spacePicker() {
  let chosen = null;
  let all = [];
  const search = h("input", { type: "search", placeholder: "제목·설명으로 찾기, 또는 Genie 주소·ID 붙여넣기", oninput: () => draw() });
  const list = h("div", { class: "pick-list short" }, h("div", { class: "loading" }, h("span", { class: "spinner" })));
  const picked = h("div", { class: "muted" }, "고른 스페이스 없음");
  const draw = () => {
    const pasted = (search.value.match(/[0-9a-f]{32}/) || [""])[0];
    if (pasted) { chosen = { space_id: pasted, title: (all.find((s) => s.space_id === pasted) || {}).title || "붙여넣은 스페이스" }; showChosen(); }
    const q = search.value.trim().toLowerCase();
    const rows = all.filter((s) => !q || pasted || `${s.title} ${s.description || ""}`.toLowerCase().includes(q));
    fill(list, rows.length ? rows.map((s) => h("button", { type: "button", class: `pick-row ${chosen && chosen.space_id === s.space_id ? "on" : ""}`,
      onclick: () => { chosen = s; showChosen(); draw(); } }, h("div", { class: "grow" }, h("b", {}, s.title), s.description ? h("div", { class: "cell-sub" }, s.description) : null),
      h("span", { class: "mono muted" }, s.space_id.slice(0, 8)))) : h("div", { class: "muted pad-s" }, all.length ? "찾는 스페이스가 없습니다." : "열 수 있는 Genie 스페이스가 없습니다."));
  };
  const showChosen = () => fill(picked, chosen ? [h("b", {}, chosen.title), h("span", { class: "mono muted" }, ` ${chosen.space_id}`)] : "고른 스페이스 없음");
  get("/genie/spaces").then((r) => { all = r.items; draw(); }).catch((e) => fill(list, listError(e, "Genie 스페이스 목록")));
  return { el: h("div", { class: "field" }, h("span", {}, "Genie 스페이스"), search, list, h("div", { class: "row" }, h("span", { class: "muted" }, "고른 것:"), picked)),
    value: () => (chosen ? chosen.space_id : (search.value.match(/[0-9a-f]{32}/) || [""])[0]) };
}

function newPage(root, params) {
  let kind = params.get("type") || "skill";
  let genieMode = "new";
  const input = (placeholder, width) => h("input", { type: "text", placeholder, style: width ? `width:${width}` : "" });
  const choose = (options, value) => h("select", {}, options.map(([v, label]) => h("option", { value: v, selected: v === value }, label)));
  const f = {
    name: input("sales-genie"), display: input("비우면 이름"), description: input("이 에이전트가 하는 일을 한 줄로"), group: input("선택: 계정 그룹"),
    crit: choose([["low", "low"], ["medium", "medium"], ["high", "high"]], "medium"),
    sens: choose([["public", "public"], ["internal", "internal"], ["confidential", "confidential"], ["personal", "personal"]], "internal"),
    sec: choose((state.securityLevels || ["general", "important", "secret"]).map((x) => [x, x]), "general"),
    harness: choose([["claude", "claude"], ["codex", "codex"]], "claude"),
    framework: choose([["langgraph", "LangGraph"], ["openai", "OpenAI Agents SDK"]], "langgraph"),
    consumer: input("선택: 운영 Genie를 쓸 계정 그룹"),
    target: h("select", {}, h("option", { value: "" }, `기본 (${spaces().fallback || "정책의 기본"})`),
      spaces().targets.map((w) => h("option", { value: w.name }, `${w.name} · ${w.host}`))),
    location: input("비우면 /Workspace/Users/<나>/aops/<이름>"),
  };
  const approvers = approverPicker(!!(state.policy && state.policy.approvers_required));
  let pick = null;  // the no-code pickers, made the first time no-code is chosen (they call the lists)
  const pickers = () => pick || (pick = { tables: tablePicker(), space: spacePicker(),
    warehouse: warehousePicker("Genie 질문을 돌릴 warehouse. 내가 쓸 수 있어야 합니다. 비우면 aops 기본 (복사는 원본의 것)") });
  const result = h("div", {});
  const kindBox = h("div", {});
  const help = h("div", {});
  const genieBox = h("div", {});
  const submit = h("button", { class: "btn primary", "data-tour": "new-submit", onclick: () => make() });
  const zipBtn = h("button", { class: "btn", onclick: () => downloadZip() }, icon("download"), "폴더 받기 (zip)");
  const kindHint = h("div", { class: "kind-hint" });

  const drawGenie = () => {
    if (kind !== "no_code") return;
    const p = pickers();
    fill(genieBox,
      h("div", { class: "field" }, h("span", {}, "개발용 Genie"), segment([["new", "테이블로 새로 만들기"], ["copy", "기존 스페이스 복사"], ["move", "기존 스페이스 옮기기"]], genieMode,
        (v) => { genieMode = v; drawGenie(); }),
        h("small", {}, { new: "고른 테이블과 표준 지침으로 시작합니다",
          copy: "원본은 그대로 두고 정의를 복사해 에이전트 폴더에 새로 만듭니다. 원본에 편집 권한이 필요합니다.",
          move: "그 스페이스를 에이전트 폴더로 옮깁니다 (ID·대화 이력 유지). 내가 CAN MANAGE여야 합니다." }[genieMode])),
      genieMode === "new" ? p.tables.el : p.space.el,
      genieMode === "move" ? null : p.warehouse.el,
      h("label", { class: "field" }, h("span", {}, "공개 그룹 (consumer_group)"), f.consumer, h("small", {}, "배포 때 운영용 Genie에 CAN RUN")),
      targetField());
  };
  const targetField = () => kind === "skill" || spaces().targets.length < 2 ? null
    : h("label", { class: "field" }, h("span", {}, "운영 워크스페이스"), f.target,
      h("small", {}, "운영용 Genie·운영 앱이 생기는 곳 (SPEC.md target_workspace). 운영 데이터는 그 워크스페이스가 허용한 카탈로그에서만"));

  const drawKind = () => {
    drawGenie();
    const g = kindGuide(kind);
    fill(kindHint, h("span", { class: `kind-ico k-${kind}` }, icon(g.ico, 16)),
      h("div", { class: "grow" }, h("div", {}, h("b", {}, "이럴 때: "), g.when, h("span", { class: "muted" }, ` (예: ${g.examples.join(" · ")})`)),
        h("div", { class: "muted" }, g.run)),
      h("a", { class: "btn ghost small", href: "#/help" }, "종류 비교"));
    fill(kindBox, kind === "no_code" ? genieBox : [
      kind === "skill" ? h("label", { class: "field" }, h("span", {}, "기본 하네스 (PC에서 aops run)"), f.harness) : null,
      kind === "code" ? h("label", { class: "field" }, h("span", {}, "프레임워크 (Agent Bricks)"), f.framework) : null,
      h("label", { class: "field" }, h("span", {}, "워크스페이스 위치"), f.location, h("small", {}, "이미 있는 폴더에는 만들지 않습니다")),
      targetField()]);
    submit.textContent = kind === "no_code" ? "만들기" : "내 워크스페이스에 만들기";
    zipBtn.style.display = kind === "no_code" ? "none" : "";
    f.description.placeholder = kind === "no_code" ? "필수: 이 Genie가 답하는 질문 (운영 Genie 설명이 됩니다)" : "이 에이전트가 하는 일을 한 줄로";
    const flow = {
      skill: [["개발", "워크스페이스 폴더(Genie Code) 또는 PC"], ["셀프 체크", "템플릿 구성 체크리스트"], ["등록", "폴더를 버전으로"],
        ["평가", "형식 → 내용 검토 → 평가셋"], ["승인", "승인자가 리포트를 보고"], ["운영", "UC Skill"]],
      code: [["개발", "워크스페이스 폴더(Genie Code) 또는 PC"], ["셀프 체크", "Agent Bricks 형식 체크리스트"], ["등록", "폴더를 버전으로"],
        ["평가", "형식 → 내용 검토"], ["승인", "승인자가 리포트를 보고"], ["운영", "운영 앱 agent-bricks-<이름>"]],
      no_code: [["개발", "개발용 Genie"], ["셀프 체크", "Genie 정의 규칙"], ["등록", "없음 (배포 때 버전)"],
        ["평가", "형식 → 내용 검토 → 벤치마크"], ["승인", "승인자가 리포트를 보고"], ["운영", "운영용 Genie"]],
    }[kind];
    fill(help, h("h2", {}, "만든 뒤 흐름"), h("ol", { class: "flow" }, flow.map(([t, d], n) => h("li", {}, h("span", { class: "dot" }, icon(FLOW_ICON[n], 13)),
      h("div", {}, h("b", {}, t), h("div", { class: "muted" }, d))))),
      kind === "code" ? h("div", { class: "note mt" }, "처음 한 번은 30초쯤 걸립니다") : null);
  };

  const body = () => ({ name: f.name.value.trim(), type: kind, harness: f.harness.value, framework: f.framework.value, display_name: f.display.value.trim() || null,
    description: f.description.value.trim() || null, owner_group: f.group.value.trim() || null, service_criticality: f.crit.value,
    data_sensitivity: f.sens.value, security_level: f.sec.value, approvers: approvers.values(),
    target_workspace: kind !== "skill" && f.target.value ? f.target.value : null });
  const approversReady = (b) => {
    const others = b.approvers.filter((a) => !state.me || a.toLowerCase() !== state.me.user.toLowerCase());
    if (state.policy && state.policy.approvers_required && !b.approvers.length) { toast("승인자를 한 명 이상 고르세요 (정책)", "bad"); return false; }
    if (b.approvers.length && !others.length) { toast("나 말고 다른 승인자를 더하세요", "bad"); return false; }
    return true;
  };

  async function downloadZip() {
    const b = body();
    if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(b.name)) return toast("이름은 영문 소문자·숫자·하이픈 63자 이하", "bad");
    try {
      const resp = await fetch(`${API}/projects/template`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
      if (!resp.ok) throw new ApiError(resp.status, await resp.json().catch(() => ({})));
      const url = URL.createObjectURL(await resp.blob());
      const a = h("a", { href: url, download: `${b.name}.zip` }); document.body.append(a); a.click(); a.remove();
      toast(`${b.name}.zip을 받았습니다`, "ok");
    } catch (e) { fail(e); }
  }

  async function makeInWorkspace(b) {
    submit.disabled = true;
    submit.replaceChildren(h("span", { class: "spinner" }), " 만드는 중…");
    try {
      const row = await post("/projects", { ...b, location: f.location.value.trim() || null });
      result.replaceChildren(workspaceCard(row));
      toast(`만들었습니다: ${row.agent_id}`, "ok", "편집기에서 열어 Genie Code로 고친 뒤 이 폴더에서 등록");
    } catch (e) { fail(e); }
    submit.disabled = false;
    submit.textContent = "내 워크스페이스에 만들기";
  }

  async function make() {
    const b = body();
    if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(b.name)) return toast("이름은 영문 소문자·숫자·하이픈 63자 이하", "bad");
    if (!approversReady(b)) return;
    if (kind !== "no_code") return makeInWorkspace(b);
    if (!b.description) return toast("설명을 적으세요 (운영 Genie의 설명이 됩니다)", "bad");
    const p = pickers();
    const space = p.space.value();
    const data = p.tables.values();
    const picked = data.tables.length + data.metric_views.length;
    if (genieMode === "new" && !picked) return toast("데이터로 쓸 테이블을 하나 이상 고르세요", "bad");
    if (genieMode === "new" && picked > MAX_TABLES) return toast(`테이블은 ${MAX_TABLES}개까지입니다`, "bad");
    if (genieMode !== "new" && !space) return toast("복사하거나 옮길 Genie 스페이스를 고르세요", "bad");
    submit.disabled = true;
    submit.replaceChildren(h("span", { class: "spinner" }), " 만드는 중…");
    try {
      const row = await post("/projects", { ...b, genie_mode: genieMode, ...(genieMode === "new" ? data : {}), genie_space_id: genieMode === "new" ? null : space,
        warehouse_id: genieMode === "move" ? null : p.warehouse.value() || null, consumer_group: f.consumer.value.trim() || null });
      result.replaceChildren(h("div", { class: "card pad mt" },
        h("div", { class: "row" }, h("h2", { style: "margin:0" }, `만들었습니다: ${row.agent_id}`), h("span", { class: "tag" }, "노코드")),
        h("dl", { class: "kv mt" },
          h("dt", {}, "에이전트 폴더"), h("dd", { class: "mono" }, row.location),
          h("dt", {}, "개발용 Genie"), h("dd", { class: "mono" }, row.dev_space_id),
          h("dt", {}, "warehouse"), h("dd", { class: "mono" }, row.warehouse_id || "–")),
        row.warnings.map((w) => h("div", { class: "note warn mt" }, w)),
        h("div", { class: "row mt" }, row.links.map((l, i) => h("a", { class: `btn ${i === 0 ? "primary" : ""}`, href: l.url, target: "_blank" }, l.label, icon("external", 14))),
          h("a", { class: "btn ghost", href: `#/agents/${enc(row.agent_id)}` }, "에이전트 보기 →"))));
      toast(`만들었습니다: ${row.agent_id}`, "ok", "Genie에서 고친 뒤 에이전트 화면에서 점검·배포 요청");
    } catch (e) { fail(e); }
    submit.disabled = false;
    submit.textContent = "만들기";
  }

  drawKind();
  const SEC_TOUR = { 1: "new-kind", 2: "new-basic", 3: "new-class", 4: "new-approvers", 5: "new-where" };
  const sec = (n, title, ...kids) => h("section", { class: "form-sec", "data-tour": SEC_TOUR[n] }, h("div", { class: "sec-t" }, h("span", { class: "sec-n" }, n), title), kids);
  root.replaceChildren(
    pageHead("새 에이전트", "종류를 고르고 SPEC 항목을 적으면 에이전트의 SPEC.md가 됩니다", [], "aops init", "plus"),
    h("div", { class: "grid new-grid" },
      h("div", { class: "card pad" },
        sec(1, "종류", segment(KINDS.map(([v, label]) => [v, [icon(KIND_ICON[v], 15), label]]), kind, (v) => { kind = v; drawKind(); }, "kind"), kindHint),
        sec(2, "기본 정보",
          h("div", { class: "grid two" },
            h("label", { class: "field" }, h("span", {}, "이름 (agent_id)"), f.name, h("small", {}, "영문 소문자·숫자·하이픈")),
            h("label", { class: "field" }, h("span", {}, "표시 이름"), f.display)),
          h("label", { class: "field" }, h("span", {}, "설명"), f.description)),
        sec(3, "분류",
          h("div", { class: "grid four" },
            h("label", { class: "field" }, h("span", {}, "중요도"), f.crit),
            h("label", { class: "field" }, h("span", {}, "데이터 민감도"), f.sens),
            h("label", { class: "field" }, h("span", {}, "보안 등급"), f.sec),
            h("label", { class: "field" }, h("span", {}, "담당 팀"), f.group))),
        sec(4, "승인자", approvers.el),
        sec(5, "만들 곳", kindBox),
        h("div", { class: "form-foot row" }, h("span", { class: "muted" }, `담당자(owner)는 나입니다${state.me ? ` (${state.me.user})` : ""}.`), h("span", { class: "right row" }, zipBtn, submit))),
      h("div", { class: "card pad help", "data-tour": "new-flow" }, help)),
    result);
}

function workspaceCard(row) {
  const prompt = row.type === "code" ? "agent/agent.py를 <하고 싶은 일>을 하도록 고쳐줘. 다 되면 이 폴더를 aops에 등록해줘"
    : "agent/skills/my-skill을 <스킬 이름>으로 바꾸고 <규칙>을 적어줘. 다 되면 이 폴더를 aops에 등록해줘";
  return h("div", { class: "card pad mt" },
    h("div", { class: "row" }, h("h2", { style: "margin:0" }, `만들었습니다: ${row.agent_id}`), h("span", { class: "tag" }, BUILD[row.type] || row.type)),
    h("dl", { class: "kv mt" },
      h("dt", {}, "폴더"), h("dd", { class: "mono" }, row.location),
      h("dt", {}, "파일"), h("dd", {}, `${row.files.length}개`, row.open_file ? h("span", { class: "muted" }, ` · 처음 열 파일 ${row.open_file}`) : null)),
    h("div", { class: "note mt" }, "Genie Code에 이렇게 말해 보세요: ", h("code", {}, prompt)),
    h("div", { class: "row mt" }, row.links.map((l, i) => h("a", { class: `btn ${i === 0 ? "primary" : ""}`, href: l.url, target: "_blank" }, l.label, icon("external", 14))),
      h("a", { class: "btn", href: `#/register?agent=${enc(row.agent_id)}&path=${enc(row.location)}` }, "이 폴더에서 등록"),
      h("a", { class: "btn ghost", href: `#/agents/${enc(row.agent_id)}` }, "에이전트 보기 →")));
}

// ── 노코드 점검 (Genie 정의 규칙, 참고용) ─────────────────────────────────────

function genieResult(r) {
  const failed = r.rules.filter((x) => !x.passed);
  return h("div", {},
    h("div", { class: "row" }, h("h2", { style: "margin:0" }, r.title || r.space_id), badge("validation", r.status),
      h("span", { class: "muted" }, `준수율 ${Math.round(r.score * 100)}%`)),
    h("dl", { class: "kv mt" },
      h("dt", {}, "데이터 소스"), h("dd", { class: "mono" }, r.tables.join(", ") || "–"),
      h("dt", {}, "정의 해시"), h("dd", { class: "mono" }, short(r.definition_sha256))),
    h("table", { class: "data mt" }, h("thead", {}, h("tr", {}, ["규칙", "수준", "결과", "고치는 곳"].map((t) => h("th", {}, t)))),
      h("tbody", {}, [...failed, ...r.rules.filter((x) => x.passed)].map((x) => h("tr", {},
        h("td", {}, h("div", { class: "cell-main mono" }, x.id), h("div", { class: "cell-sub" }, x.message)),
        h("td", {}, h("span", { class: `badge ${x.level === "ERROR" ? "bad" : "warn"} plain` }, x.level)),
        h("td", {}, x.passed ? h("span", { class: "badge ok" }, "통과") : h("span", { class: "badge bad" }, "미달")),
        h("td", { class: "muted" }, x.passed ? "" : x.fix))))));
}

function geniePage(root, params) {
  const input = h("input", { type: "text", placeholder: "Genie 스페이스 주소나 ID (32자리)", style: "width:420px", value: params.get("space") || "" });
  const result = h("div", {});
  const run = async () => {
    const id = (input.value.match(/[0-9a-f]{32}/) || [""])[0];
    if (!id) return toast("Genie 스페이스 ID(32자리 16진수)를 넣으세요", "bad");
    result.replaceChildren(h("div", { class: "loading" }, h("span", { class: "spinner" }), " 정의를 읽고 규칙을 돌리는 중…"));
    try {
      result.replaceChildren(h("div", { class: "card pad mt" }, genieResult(await post("/no-code/checks", { space_id: id }))));
    } catch (e) { result.replaceChildren(); fail(e); }
  };
  root.replaceChildren(
    pageHead("Genie 점검", "아무 Genie 스페이스나 노코드 정의 규칙으로 지금 점검합니다 (기록하지 않음)", [], "aops validate --agent", "sparkle"),
    h("div", { class: "card pad" },
      h("div", { class: "row" }, input, h("button", { class: "btn primary", onclick: run }, icon("search"), "점검")),
      h("div", { class: "note mt" }, "aops 서비스 주체가 읽을 수 있는 스페이스만 됩니다")),
    result);
  if (input.value) run();
}

// ── 정책 (aops admin setup --only policy) ───────────────────────────────────

async function policyPage(root) {
  const page = await get("/admin/policies");
  const current = page.items.find((p) => p.status === "active");
  const isAdmin = state.me && state.me.roles.includes("admin");
  const pol = current ? current.policy : {};
  const pick = { validation_mode: pol.validation_mode, approval_mode: pol.approval_mode };
  const required = h("input", { type: "checkbox", checked: !!pol.approvers_required, disabled: !isAdmin });
  const devApprovers = h("input", { type: "text", value: (pol.dev_approvers || []).join(", "), placeholder: "비움 (운영)", disabled: !isAdmin });
  const segFor = (key, options) => {
    const box = h("div", { class: "seg" });
    options.forEach(([v, label]) => box.append(h("button", { class: pick[key] === v ? "on" : "", disabled: !isAdmin, onclick: (e) => {
      pick[key] = v; box.querySelectorAll("button").forEach((b) => b.classList.remove("on")); e.target.classList.add("on");
    } }, label)));
    return box;
  };
  const group = h("input", { type: "text", value: pol.approver_group || "", placeholder: "비우면 요청자가 아닌 누구나", disabled: !isAdmin });
  const save = async () => {
    try {
      const saved = await post("/admin/policies", { ...pick, approver_group: group.value.trim(), approvers_required: required.checked,
        dev_approvers: devApprovers.value.split(",").map((x) => x.trim()).filter(Boolean) });
      toast(`정책 버전 ${saved.policy_version} 적용`, "ok"); render();
    } catch (e) { fail(e); }
  };
  const git = pol.git;
  root.replaceChildren(
    pageHead("정책·평가 프롬프트", current ? `지금 정책 v${current.policy_version} · ${current.created_by} · ${when(current.created_at)}` : "적용된 정책이 없습니다", [], "aops admin setup --only policy", "sliders"),
    h("div", { class: "grid two", style: "align-items:start" },
      h("div", { class: "card pad", "data-tour": "pol-rules" }, h("h2", {}, "평가·승인 규칙"),
        h("div", { class: "field" }, h("span", {}, "평가 방식"), segFor("validation_mode", [["mandatory", "필수"], ["optional", "선택"], ["exempt", "면제"]]),
          h("small", {}, "필수: 실패하면 반려 · 선택: 실패해도 승인자에게 · 면제: 평가 없이")),
        h("div", { class: "field" }, h("span", {}, "승인 방식"), segFor("approval_mode", [["required", "사람 승인"], ["optional", "선택"], ["auto", "자동 승인"]]),
          h("small", {}, "선택: SPEC 중요도 low이고 민감도 public·internal이면 자동, 아니면 승인자")),
        h("label", { class: "field" }, h("span", {}, "승인 그룹"), group, h("small", {}, "지정 승인자가 없을 때 승인")),
        h("label", { class: "row toggle" }, required, h("b", {}, "에이전트마다 지정 승인자 필수"),
          h("small", { class: "muted" }, "없으면 셀프 체크·평가 ERROR")),
        h("label", { class: "field" }, h("span", {}, "개발용 예외 승인자 (본인 요청도 승인)"), devApprovers,
          h("small", { class: (pol.dev_approvers || []).length ? "warn-text" : "" }, "혼자 시험할 때만. 승인마다 예외로 기록, 운영에서는 비웁니다")),
        isAdmin ? h("button", { class: "btn primary", onclick: save }, "새 정책 버전으로 적용")
          : h("div", { class: "note" }, "관리자만 바꿉니다")),
      h("div", { class: "card pad" }, h("h2", {}, "그 밖의 설정"),
        h("dl", { class: "kv" },
          h("dt", {}, "필요 승인 수"), h("dd", {}, pol.required_approvals ?? 1),
          h("dt", {}, "보안 등급"), h("dd", {}, (pol.security_levels || []).join(" · ")),
          h("dt", {}, "Git 사본"), h("dd", {}, git ? `${git.provider} · ${git.owner} · 연결 ${git.connection} · ${git.mode} (저장소 ${git.repo_prefix}<agent_id>)` : "꺼짐")),
        )),
    workspacesCard(pol),
    await reviewPromptsCard(isAdmin),
    h("div", { class: "card mt", "data-tour": "pol-history" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["버전", "상태", "평가", "승인", "승인 그룹", "올린 사람", "시각"].map((t) => h("th", {}, t)))),
      h("tbody", {}, page.items.map((p) => h("tr", {},
        h("td", { class: "mono cell-main" }, `v${p.policy_version}`), h("td", {}, p.status === "active" ? h("span", { class: "badge ok" }, "적용 중") : h("span", { class: "badge plain" }, "이전")),
        h("td", {}, MODE[p.policy.validation_mode] || p.policy.validation_mode), h("td", {}, MODE[p.policy.approval_mode] || p.policy.approval_mode),
        h("td", {}, p.policy.approver_group || "–"), h("td", {}, p.created_by), h("td", { class: "muted" }, when(p.created_at))))))));
}

// The workspaces of the policy: who builds where, where production goes (changed with aops admin setup --workspace)
function workspacesCard(pol) {
  const listed = Object.entries(pol.workspaces || {});
  const roles = { dev: "개발", target: "운영" };
  return h("div", { class: "card pad mt" }, h("h2", { class: "h-ico" }, icon("layers", 16), "워크스페이스"),
    listed.length ? [
      h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["이름", "주소", "역할", "운영 데이터 카탈로그", "warehouse"].map((t) => h("th", {}, t)))),
        h("tbody", {}, listed.map(([name, w]) => h("tr", {},
          h("td", { class: "cell-main" }, name, name === pol.default_target ? h("span", { class: "tag", style: "margin-left:6px" }, "기본 운영") : null),
          h("td", { class: "mono" }, w.host), h("td", {}, (w.roles || []).map((r) => roles[r] || r).join(" · ")),
          h("td", { class: "mono" }, (w.catalogs || []).join(", ") || h("span", { class: "muted" }, "제한 없음")),
          h("td", { class: "mono" }, w.warehouse_id || "–"))))),
      h("p", { class: "muted mt-s" }, "사람은 개발 워크스페이스에서 만들고, 운영 자원(운영용 Genie·운영 앱)은 운영 워크스페이스에 aops 서비스 주체가 만듭니다. ",
        "운영 데이터는 그 워크스페이스의 카탈로그에서만 씁니다. ", pol.workspace_credentials ? `다른 워크스페이스는 시크릿 범위 ${pol.workspace_credentials.secret_scope}의 서비스 주체로 부릅니다.` : "")]
      : h("p", { class: "muted" }, "aops 서버의 워크스페이스 하나에서 개발과 운영을 합니다. 여럿을 쓰려면 aops admin setup --workspace (docs/workspaces.md)"),
    h("div", { class: "cmd mt-s" }, "aops admin setup ... --workspace \"project=https://…;roles=dev,target;catalogs=project;warehouse=<ID>\" --default-target project --only policy --apply"));
}

const PROMPT_KIND = [["common", "공통"], ["skill", "스킬 기반"], ["code", "코드 기반"], ["no_code", "노코드"]];

// The four prompts of the Gate's content review: edit one, save it as a new version (admins); the evaluations record which they used.
async function reviewPromptsCard(isAdmin) {
  const data = await get("/admin/review-prompts");
  let kind = "common";
  const box = h("div", {});
  const draw = () => {
    const p = data.active[kind];
    const text = h("textarea", { class: "mono prompt", disabled: !isAdmin }, p.body);
    const note = h("input", { type: "text", placeholder: "바꾼 이유 (선택)", disabled: !isAdmin, style: "flex:1" });
    const items = h("ul", { class: "check-rules" });
    const preview = () => {
      const found = [];
      let inside = false;
      for (const line of text.value.split("\n")) {
        if (/^#{2,3}\s*체크리스트\s*$/.test(line.trim())) { inside = true; continue; }
        if (inside && line.startsWith("#")) break;
        const m = inside && line.match(/^\s*[-*]\s*`?([a-z][a-z0-9_.-]{1,63})`?\s*[:：]\s*(.+?)\s*$/);
        if (m) found.push([m[1], m[2]]);
      }
      fill(items, found.length ? found.map(([id, t]) => h("li", {}, h("span", { class: "rmark ok" }, "•"), h("div", {}, h("span", { class: "mono" }, id), ` ${t}`)))
        : h("li", {}, h("span", { class: "rmark bad" }, icon("x", 14)), h("div", {}, "'## 체크리스트' 아래에 '- <id>: <통과 조건>' 줄이 없습니다")));
    };
    text.addEventListener("input", preview);
    preview();
    const history = data.history.filter((r) => r.kind === kind);
    fill(box,
      h("div", { class: "row" }, h("b", {}, `${PROMPT_KIND.find((k) => k[0] === kind)[1]} · 지금 버전 v${p.version}`),
        h("span", { class: "muted" }, p.version ? `${p.created_by} · ${when(p.created_at)}${p.note ? ` · ${p.note}` : ""}` : "기본 프롬프트")),
      h("div", { class: "grid two mt-s" }, h("div", {}, text), h("div", {}, h("h3", {}, "체크리스트 미리 보기 (리포트 항목)"), items,
        h("p", { class: "muted mt" }, kind === "common" ? "모든 에이전트에 들어갑니다" : "공통 항목 뒤에 붙습니다 (ID는 공통과 달라야 함)"))),
      isAdmin ? h("div", { class: "row mt-s" }, note, h("button", { class: "btn primary", onclick: async () => {
        try {
          const saved = await post("/admin/review-prompts", { kind, body: text.value, note: note.value.trim() || null });
          toast(`평가 프롬프트 ${PROMPT_KIND.find((k) => k[0] === kind)[1]} v${saved.version} 저장`, "ok"); render();
        } catch (e) { fail(e); }
      } }, "새 버전으로 저장")) : h("div", { class: "note mt-s" }, "관리자만 바꿉니다"),
      history.length ? h("table", { class: "data mt" }, h("thead", {}, h("tr", {}, ["버전", "상태", "바꾼 이유", "올린 사람", "시각"].map((t) => h("th", {}, t)))),
        h("tbody", {}, history.map((r) => h("tr", {}, h("td", { class: "mono" }, `v${r.prompt_version}`), h("td", {}, r.status === "active" ? h("span", { class: "badge ok" }, "적용 중") : h("span", { class: "badge plain" }, "이전")),
          h("td", {}, r.note || "–"), h("td", {}, r.created_by), h("td", { class: "muted" }, when(r.created_at)))))) : null);
  };
  const tabs = h("div", { class: "tabs" });
  const drawTabs = () => fill(tabs, PROMPT_KIND.map(([k, label]) => h("button", { class: k === kind ? "on" : "", onclick: () => { kind = k; drawTabs(); draw(); } },
    label, h("span", { class: "n" }, `v${data.active[k].version}`))));
  drawTabs();
  draw();
  return h("div", { class: "card pad mt", "data-tour": "pol-prompts" }, h("h2", {}, "평가 프롬프트 (배포 전 내용 검토)"),
    h("p", { class: "muted" }, "공통 + 종류별 체크리스트로 모든 파일을 검토합니다. 저장하면 새 버전"),
    tabs, box);
}

// ── 안내: CLI ↔ 화면 ↔ Genie Code ───────────────────────────────────────────

const SCREEN = {
  "aops init": "새 에이전트", "aops list": "에이전트", "aops load": `에이전트 → ${actText("load")}`, "aops validate": `에이전트 → ${actText("selfcheck")} · Genie 점검`,
  "aops evaluate": `에이전트 → ${actText("evaluate")}`, "aops register": `에이전트 → ${actText("register")} · 폴더·zip 등록`, "aops deploy": `에이전트 → ${actText("deploy")}`,
  "aops deploy --cancel": "배포 요청 → 요청 취소", "aops gates": "배포 요청함", "aops approve": "배포 요청 → 승인", "aops reject": "배포 요청 → 반려",
  "aops promote": `에이전트 → ${actText("promote")}`, "aops admin setup": "정책·평가 프롬프트 (정책만, 나머지는 CLI)",
};

// the routes this console uses where a command also has design-only routes
const SCREEN_ROUTES = {
  "aops init": ["POST /api/v1/projects/template"],
  "aops admin setup": ["GET /api/v1/admin/policies", "POST /api/v1/admin/policies"],
};

async function guidePage(root) {
  const rows = await get("/meta/commands");
  const working = rows.filter((r) => SCREEN[r.command]);
  const pcOnly = ["aops run \"<질문>\"", "aops session list", "aops session switch --harness", "aops session show"];
  root.replaceChildren(
    pageHead("CLI·Genie Code", "화면·PC CLI·Genie Code가 같은 aops 서버 API를 부릅니다. 어디서 하든 같은 기록이 남습니다", [], "aops --help", "terminal"),
    h("div", { class: "grid three" },
      h("div", { class: "card pad" }, h("h2", { class: "h-ico" }, icon("grid", 16), "이 화면"), h("p", { class: "muted" }, "브라우저에서 만들기·셀프 체크·등록·평가·배포·승인·권한·정책. Databricks 로그인 그대로 씁니다.")),
      h("div", { class: "card pad" }, h("h2", { class: "h-ico" }, icon("terminal", 16), "PC CLI"), h("p", { class: "muted" }, "aops run으로 Claude·Codex 하네스를 바꿔 가며 개발하고 같은 명령으로 등록·배포합니다."),
        h("div", { class: "cmd" }, "aops register --bump fix\naops deploy")),
      h("div", { class: "card pad" }, h("h2", { class: "h-ico" }, icon("sparkle", 16), "Genie Code"), h("p", { class: "muted" }, "워크스페이스 스킬 aops: \"이 에이전트 등록해줘\", \"배포해줘\". 노트북에서 같은 CLI 코드가 내 신원으로 돕니다."),
        h("div", { class: "cmd" }, "에이전트 폴더 …/price-helper 를\nfix 버전으로 등록하고 배포해줘"))),
    h("div", { class: "card mt" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["CLI 명령", "이 화면", "API"].map((t) => h("th", {}, t)))),
      h("tbody", {}, working.map((r) => h("tr", {}, h("td", { class: "mono cell-main" }, r.command), h("td", {}, SCREEN[r.command]),
        h("td", { class: "mono muted", style: "font-size:12px" }, (SCREEN_ROUTES[r.command] || r.routes).map((x) => h("div", {}, x)))))))),
    h("div", { class: "card pad mt" }, h("h2", {}, "PC에서만 하는 것"),
      h("p", { class: "muted" }, "하네스(Claude·Codex) 실행은 PC에서만"),
      h("div", {}, pcOnly.map((c) => h("span", { class: "tag mono" }, c)))));
}

// ── 도움말: 어떤 에이전트를 만들까 · 버튼 사전 · 투어 ───────────────────────────

// When each kind is the right one, what one makes, and what the people who use it get
const KIND_GUIDE = [
  { kind: "skill", ico: "doc", title: "스킬 기반", when: "규칙·절차·문서 양식을 알려 주면 되는 일",
    examples: ["할인·견적 계산 규칙", "보고서·메일 양식", "사내 업무 절차 안내"],
    make: "SKILL.md에 지시와 규칙을 글로 적습니다. 코드는 없어도 됩니다",
    run: "운영은 UC Skill입니다. 쓰는 사람이 [가져오기]로 받아 Claude·Codex(aops run)나 Genie Code에서 씁니다" },
  { kind: "code", ico: "code", title: "코드 기반", when: "도구 호출·외부 API·여러 단계를 코드로 짜야 하는 일",
    examples: ["조회 → 판단 → 답하는 상담", "사내 API를 부르는 처리", "검색 → 요약 → 메일 초안"],
    make: "Agent Bricks 프로젝트(LangGraph·OpenAI Agents SDK)를 Genie Code나 PC에서 고칩니다",
    run: "운영은 Databricks App(agent-bricks-<이름>)입니다. 권한을 받은 사람이 앱을 열어 씁니다" },
  { kind: "no_code", ico: "chat", title: "노코드 (Genie)", when: "테이블 데이터에 자연어로 묻고 답을 받는 일",
    examples: ["지역별 매출 묻기", "재고·주문 현황 조회", "지표를 질문으로 보기"],
    make: "테이블을 고르면 개발용 Genie가 생깁니다. Genie 화면에서 지침·예시 질문을 다듬습니다",
    run: "운영은 운영용 Genie입니다. 공개 그룹에 CAN RUN이 걸리고, 그 그룹이 Genie에서 묻습니다" },
];
const kindGuide = (kind) => KIND_GUIDE.find((g) => g.kind === kind) || KIND_GUIDE[0];

// What every action button means (the same words on every screen and in the CLI)
const GLOSSARY = [
  ["edit", "에이전트 폴더나 편집기(노코드는 개발용 Genie)를 엽니다. Genie Code에 말하거나 PC에서 고칩니다"],
  ["selfcheck", "템플릿대로 구성됐는지 체크리스트로 봅니다. 언제든 눌러도 되고 기록하지 않습니다"],
  ["register", "지금 폴더를 버전(zip·해시)으로 저장합니다. 등록만으로는 운영에 나가지 않습니다"],
  ["evaluate", "형식 검사 → 내용 검토(모델이 모든 파일을 읽고 리포트) → 평가셋 채점(노코드는 벤치마크). 배포 요청 때 자동으로 돕니다"],
  ["deploy", "버전을 골라 운영에 올려 달라고 요청합니다. 평가 → 승인 → 운영 반영이 이어집니다"],
  ["approve", "승인자가 평가 리포트를 확인하고 결정합니다. 승인하면 운영 반영이 바로 시작됩니다"],
  ["promote", "운영 버전을 쓸 사람·그룹에 권한을 줍니다 (스킬 READ_VOLUME · 앱 CAN_USE · Genie CAN RUN)"],
  ["load", "운영 버전을 씁니다. 스킬은 zip으로 받고, 코드·노코드는 운영 앱·Genie가 열립니다"],
];
const LEGEND = [["done", "check", "끝남 · 통과 · 운영 중"], ["warn", "alert", "주의 · 승인 대기"], ["bad", "alert", "고칠 것 · 실패 · 반려"],
  ["run", "clock", "진행 중"], ["wait", "flag", "아직"]];
const FLOW_ICON = ["edit", "checklist", "package", "flask", "shield", "live"];

const store = {  // small settings of this browser (a blocked storage only forgets them)
  get(key) { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};
const TOUR_DONE = "aops.tours.done";

// A tour points at the real screen, one part at a time. A step names the part by its data-tour mark; `route` opens a
// screen first; `click` waits for the person to press that part (it is open, everything else is not); `missing` is said
// when the part is not on the screen (no agent yet, not an approver); `stop` ends the tour there; `optional` skips it.
const kindStep = (kind) => {
  const g = kindGuide(kind);
  return { el: `kind-${kind}`, click: true, title: `${g.title}: ${g.when}`, text: [`예: ${g.examples.join(" · ")}`, g.make, g.run] };
};
const TOURS = {
  basics: { ico: "compass", title: "처음 둘러보기", desc: "왼쪽 메뉴와 첫 화면이 무엇인지 2분 만에", steps: [
    { route: "#/agents", title: "aops console입니다", text: ["에이전트를 만들고 → 등록 → 평가 → 승인 → 운영까지 한곳에서 합니다.",
      "화면·PC CLI·Genie Code가 같은 aops 서버를 쓰므로, 어디서 하든 같은 기록이 남습니다."] },
    { el: "nav-new", title: "새 에이전트", text: "여기서 시작합니다. 스킬 기반·코드 기반·노코드(Genie) 중 하나를 고릅니다." },
    { el: "nav-agents", title: "에이전트", text: "레지스트리의 모든 에이전트와 상태(운영 버전, 마지막 평가, 진행 중인 요청)를 봅니다." },
    { el: "todos", title: "지금 할 일", text: "승인할 요청 · 진행 중인 내 요청 · 고칠 에이전트 · 운영 중. 카드를 누르면 그 목록으로 갑니다." },
    { el: "nav-gates", title: "배포 요청함", text: "배포 요청이 모이는 곳입니다. 승인자는 평가 리포트를 보고 승인·반려합니다. 숫자는 내가 승인할 요청 수입니다." },
    { el: "nav-register", title: "폴더·zip 등록", text: "PC에서 만든 에이전트 폴더나 zip을 버전으로 올립니다. 처음 올리면 에이전트가 생깁니다." },
    { el: "nav-genie", title: "Genie 점검", text: "아무 Genie 스페이스나 노코드 규칙으로 점검합니다. 기록하지 않습니다." },
    { el: "nav-policy", title: "정책·평가 프롬프트", text: "평가·승인 방식과 내용 검토 기준입니다. 관리자만 바꿉니다." },
    { el: "help-btn", title: "언제든 도움말", text: "다른 투어(에이전트 고르기, 버튼의 뜻, 승인하기)는 여기서 다시 엽니다." },
  ] },
  create: { ico: "plus", title: "어떤 에이전트를 만들까", desc: "세 종류를 언제 쓰는지 보고, 새 에이전트 화면을 직접 눌러 봅니다", steps: [
    { route: "#/agents", el: "nav-new", click: true, title: "새 에이전트를 눌러 보세요", text: "왼쪽 위 [새 에이전트]를 누르면 만들기 화면이 열립니다." },
    { route: "#/new", el: "new-kind", title: "먼저 종류를 고릅니다", text: "무엇을 하는 에이전트인지에 따라 셋 중 하나입니다. 하나씩 눌러 보세요." },
    kindStep("skill"), kindStep("code"), kindStep("no_code"),
    { el: "new-basic", title: "이름과 설명", text: ["이름(agent_id)은 영문 소문자·숫자·하이픈입니다. 폴더·운영 자원·모든 기록이 이 이름을 씁니다.",
      "설명은 이 에이전트가 하는 일을 한 줄로 적습니다."] },
    { el: "new-class", title: "분류", text: ["중요도·데이터 민감도·보안 등급·담당 팀이 SPEC.md에 남습니다.",
      "정책의 승인 방식이 '선택'이면 중요도 low + 민감도 public·internal일 때 자동 승인됩니다."] },
    { el: "new-approvers", title: "승인자", text: "배포 요청을 승인할 사람·그룹입니다. 비우면 정책의 승인 그룹이 승인합니다. 내 요청은 내가 승인할 수 없습니다." },
    { el: "new-where", title: "만들 곳", text: ["스킬·코드: 내 워크스페이스 폴더에 템플릿이 생기고 Genie Code로 고칩니다. [폴더 받기]로 PC에서 고쳐도 됩니다.",
      "노코드: 테이블을 고르거나 기존 Genie 스페이스를 복사·이동합니다."] },
    { el: "new-flow", title: "만든 뒤 흐름", text: "개발 → 셀프 체크 → 등록 → 평가 → 승인 → 운영. 고른 종류에서 무엇이 생기는지 여기 나옵니다." },
    { el: "new-submit", title: "만들기", text: "다 적고 누르면 실제로 만들어집니다. 투어에서는 누르지 않습니다." },
  ] },
  agent: { ico: "grid", title: "에이전트 화면과 버튼의 뜻", desc: "한 에이전트의 6단계와 버튼이 무엇을 하는지", steps: [
    { route: "#/agents", el: "agent-row", click: true, title: "에이전트 하나를 눌러 보세요", text: "목록의 한 줄을 누르면 그 에이전트 화면이 열립니다.",
      missing: "아직 에이전트가 없습니다. [새 에이전트]로 하나 만든 뒤 이 투어를 다시 해 보세요.", stop: true },
    { el: "lifecycle", title: "6단계 한눈에", text: ["개발 → 셀프 체크 → 등록 → 평가 → 승인 → 운영.",
      "초록은 끝남, 주황은 주의·대기, 빨강은 고칠 것, 파랑은 진행 중입니다. 단계마다 그 단계의 버튼이 있습니다."] },
    { el: "step-edit", title: "개발", text: "작업 폴더와 편집기를 엽니다. Genie Code에 \"이렇게 고쳐줘\"라고 말해 고칩니다. 노코드는 개발용 Genie를 엽니다." },
    { el: "step-selfcheck", title: "셀프 체크", text: "템플릿대로 구성됐는지 체크리스트로 봅니다. 언제든 눌러도 되고 기록하지 않습니다. ERROR가 남아 있으면 배포 전 평가에서 반려됩니다." },
    { el: "step-register", title: "등록", text: "지금 폴더를 버전(1.0.0, 1.1.0 …)으로 저장합니다. 등록만으로는 운영에 나가지 않습니다. 노코드는 배포가 성공할 때 버전이 생깁니다." },
    { el: "step-evaluate", title: "평가", text: ["형식 검사 → 내용 검토(모델이 모든 파일을 읽고 리포트) → 평가셋 채점(노코드는 벤치마크).",
      "배포 요청 때 자동으로 돌고, 미리 돌려 볼 수도 있습니다. 도는 동안 무엇을 읽고 검사하는지 실시간으로 보입니다."] },
    { el: "step-approve", title: "승인", text: "승인자가 리포트를 보고 결정합니다. 형식 ERROR만 자동 반려이고, 내용 검토 부적합은 승인자가 판단합니다." },
    { el: "step-deploy", title: "운영", text: "승인되면 운영에 반영됩니다. 스킬은 UC Skill, 코드는 운영 앱, 노코드는 운영용 Genie입니다." },
    { el: "act-deploy", title: "배포 요청", text: "버전을 골라 운영에 올려 달라고 요청합니다. 평가 → 승인 → 운영 반영이 이어집니다. 담당자·담당 팀만 누를 수 있습니다." },
    { el: "act-promote", title: "권한", text: "운영 버전을 쓸 사람·그룹에 권한을 줍니다 (스킬 READ_VOLUME · 앱 CAN_USE · Genie CAN RUN)." },
    { el: "act-load", title: "가져오기", text: "운영 버전을 씁니다. 스킬은 zip으로 받고, 코드·노코드는 운영 앱·Genie가 열립니다." },
    { el: "agent-tabs", title: "기록", text: "버전·평가·배포 요청·배포·이력(감사 기록)을 탭으로 봅니다. 평가 탭의 [리포트]로 지난 평가를 다시 엽니다." },
  ] },
  approve: { ico: "shield", title: "배포 요청 승인하기", desc: "승인자가 리포트를 읽고 결정하는 순서", steps: [
    { route: "#/agents", el: "nav-gates", click: true, title: "배포 요청함을 눌러 보세요", text: "배포 요청은 모두 여기 모입니다." },
    { route: "#/gates", el: "gates-tabs", title: "세 개의 탭", text: ["승인할 요청: 내가 지정 승인자(또는 승인 그룹)인 요청.", "내 요청: 내가 올린 요청. 전체: 모든 요청."] },
    { el: "gate-row", click: true, title: "요청 하나를 열어 보세요", text: "한 줄을 누르면 그 요청의 평가 리포트와 결정 화면이 열립니다.",
      missing: "이 탭에는 요청이 없습니다. [전체] 탭을 보거나, 에이전트 화면의 [배포 요청]으로 요청이 생긴 뒤 다시 해 보세요.", stop: true },
    { el: "gate-progress", title: "평가 → 승인 → 운영 반영", text: "이 요청이 어디까지 왔는지 봅니다." },
    { el: "gate-request", title: "요청 내용", text: "요청 사유, 지정 승인자, 평가·승인 방식, 결정 내용입니다." },
    { el: "gate-report", title: "평가 리포트", text: ["맨 위는 판정(적합·주의·부적합)과 검토 모델의 의견입니다.",
      "체크리스트는 문제 항목이 먼저 나오고, 근거는 파일:줄로 나옵니다. 아래에 품질 평가와 형식 검사 결과가 있습니다."],
      missing: "아직 평가 리포트가 없습니다. 평가가 도는 중이면 이 자리에 진행이 보입니다." },
    { el: "gate-decide", title: "승인자 결정", text: ["리포트를 확인했다고 체크하고, 의견을 적어 [승인]합니다. 반려는 사유가 필요합니다.", "승인하면 운영 반영이 바로 시작됩니다."],
      missing: "결정 칸은 이 요청의 승인자에게만 보입니다. 내 요청은 내가 승인할 수 없습니다." },
  ] },
  register: { ico: "upload", title: "폴더·zip으로 등록하기", desc: "PC에서 만든 에이전트를 버전으로 올리기", steps: [
    { route: "#/register", el: "reg-drop", title: "폴더나 zip 고르기", text: ["aops init으로 만든 에이전트 폴더(aops.toml이 있는 곳)를 고르거나 zip을 끌어 놓습니다.",
      "runs·.git·.venv 같은 폴더와 CLAUDE.md·AGENTS.md는 빠집니다."] },
    { el: "reg-ws", title: "워크스페이스 폴더", text: "Genie Code로 고친 워크스페이스 폴더는 경로를 적어 바로 등록합니다." },
    { el: "reg-version", title: "버전", text: "fix·minor·major 중 올릴 폭을 고르거나 버전을 직접 적습니다. 처음 등록은 1.0.0입니다." },
    { el: "reg-note", title: "사유", text: "무엇을 바꿨는지 적습니다. 버전 기록과 Git 커밋 메시지에 남습니다." },
    { el: "reg-check", title: "셀프 체크", text: "올리기 전에 템플릿 구성을 점검합니다. 기록하지 않습니다." },
    { el: "reg-submit", title: "등록", text: "버전을 저장합니다. 평가와 승인은 배포 요청 때 합니다." },
  ] },
  policy: { ico: "sliders", title: "관리자: 정책과 평가 프롬프트", desc: "평가·승인 규칙과 내용 검토 기준 바꾸기", steps: [
    { route: "#/policy", el: "pol-rules", title: "평가·승인 규칙", text: ["평가 방식: 필수(실패하면 반려) · 선택(실패해도 승인자에게) · 면제.",
      "승인 방식: 사람 승인 · 선택(중요도·민감도가 낮으면 자동) · 자동 승인. 바꾸면 새 정책 버전이 됩니다."] },
    { el: "pol-prompts", title: "평가 프롬프트", text: ["내용 검토 모델이 따르는 체크리스트입니다. 공통 + 종류별로 넷이고, 저장하면 새 버전이 됩니다.",
      "평가마다 어떤 버전을 썼는지 리포트에 남습니다."] },
    { el: "pol-history", title: "정책 이력", text: "누가 언제 어떤 정책을 적용했는지 남습니다." },
  ] },
};

// The tour for the screen one is on, and where in it to start
function contextTour() {
  const { parts } = route();
  switch (parts[0] || "agents") {
    case "agents": return parts[1] ? { id: "agent", at: 1 } : { id: "basics", at: 0 };
    case "new": return { id: "create", at: 1 };
    case "gates": return { id: "approve", at: parts[1] ? 3 : 1 };
    case "register": return { id: "register", at: 0 };
    case "policy": return { id: "policy", at: 0 };
    default: return { id: "basics", at: 0 };
  }
}

const tour = { id: null, i: 0, dir: 1, token: 0, el: null, parts: null, layer: null, tick: null, raf: 0 };
const tourSel = (name) => `[data-tour="${name}"]`;
const shown = (el) => !!el && el.isConnected && el.getClientRects().length > 0;

function startTour(id, at = 0) {
  if (!TOURS[id]) return;
  endTour(false);
  document.querySelectorAll(".menu-pop").forEach((m) => m.remove());
  const nudge = () => { const c = tour.parts.card; c.classList.remove("nudge"); void c.offsetWidth; c.classList.add("nudge"); };
  const block = (cls = "") => h("div", { class: `tour-block ${cls}`, onclick: nudge });
  tour.parts = { top: block(), right: block(), bottom: block(), left: block(), cover: block("tour-cover"),
    ring: h("div", { class: "tour-ring" }), card: h("div", { class: "tour-card", role: "dialog", "aria-live": "polite" }) };
  tour.layer = h("div", { class: "tour" }, Object.values(tour.parts));
  document.body.append(tour.layer);
  Object.assign(tour, { id, i: at, dir: 1 });
  document.addEventListener("keydown", tourKeys);
  document.addEventListener("click", tourClick, true);
  window.addEventListener("resize", placeTour);
  window.addEventListener("scroll", placeTour, true);
  tour.tick = setInterval(placeTour, 400);  // the screen under the tour still loads and moves
  showStep();
}

function endTour(finished) {
  if (!tour.id) return;
  const t = TOURS[tour.id];
  const id = tour.id;
  tour.layer.remove();
  document.removeEventListener("keydown", tourKeys);
  document.removeEventListener("click", tourClick, true);
  window.removeEventListener("resize", placeTour);
  window.removeEventListener("scroll", placeTour, true);
  clearInterval(tour.tick);
  Object.assign(tour, { id: null, el: null, parts: null, layer: null, token: tour.token + 1 });
  if (finished) {
    store.set(TOUR_DONE, [...new Set([...(store.get(TOUR_DONE) || []), id])]);
    toast(`투어를 마쳤습니다: ${t.title}`, "ok", "다른 투어는 오른쪽 위 [도움말]에서");
    if (location.hash.startsWith("#/help")) render();
  }
}

function goStep(i) {
  tour.dir = i >= tour.i ? 1 : -1;
  tour.i = Math.max(0, i);
  showStep();
}

async function showStep() {
  const step = TOURS[tour.id].steps[tour.i];
  if (!step) return endTour(true);
  const token = ++tour.token;
  tour.el = null;
  if (step.route && location.hash.split("?")[0] !== step.route) location.hash = step.route;
  paintCard(step, "wait");
  placeTour();
  let el = null;
  const since = Date.now();
  for (const until = since + 6000; step.el && Date.now() < until && token === tour.token;) {
    el = document.querySelector(tourSel(step.el));
    if (shown(el)) break;
    el = null;
    if (Date.now() - since > 1500 && !document.querySelector(".page .loading, .page .spinner")) break;  // the screen is drawn and has no such part
    await new Promise((resolve) => setTimeout(resolve, 120));
  }
  if (token !== tour.token) return;  // moved on, or the tour ended
  if (step.el && !el && step.optional) return goStep(tour.i + tour.dir);
  tour.el = el;
  if (el) el.scrollIntoView({ block: el.getBoundingClientRect().height > innerHeight * 0.6 ? "start" : "center", behavior: "smooth" });
  paintCard(step, step.el && !el ? "missing" : "show");
  placeTour();
}

function paintCard(step, mode) {
  const t = TOURS[tour.id];
  const n = t.steps.length;
  const missing = mode === "missing";
  const clickable = step.click && mode === "show";
  const last = tour.i === n - 1 || (missing && step.stop);
  const next = last ? h("button", { class: "btn small primary", onclick: () => endTour(tour.i === n - 1) }, tour.i === n - 1 ? "마치기" : "닫기")
    : clickable ? h("button", { class: "btn small", onclick: () => tour.el && tour.el.click() }, "대신 눌러 주기")
      : h("button", { class: "btn small primary", disabled: mode === "wait", onclick: () => goStep(tour.i + 1) }, "다음", icon("arrow", 14));
  fill(tour.parts.card,
    h("div", { class: "tour-head" }, h("span", { class: "tour-ico" }, icon(t.ico, 15)), h("span", { class: "grow muted" }, `${t.title} · ${tour.i + 1}/${n}`),
      h("button", { class: "btn ghost small", title: "투어 끝내기 (Esc)", "aria-label": "투어 끝내기", onclick: () => endTour(false) }, icon("x", 14))),
    h("div", { class: "tour-bar" }, h("div", { style: `width:${Math.round(((tour.i + 1) / n) * 100)}%` })),
    h("h3", {}, step.title),
    mode === "wait" ? h("p", { class: "muted" }, h("span", { class: "spinner" }), " 화면을 여는 중…")
      : [].concat(missing ? step.missing || "이 화면에 그 부분이 없습니다. [다음]으로 넘어가세요." : step.text).map((x) => h("p", {}, x)),
    clickable ? h("div", { class: "tour-hint" }, icon("pointer", 14), "표시된 곳을 직접 눌러 보세요") : null,
    h("div", { class: "tour-foot" },
      tour.i > 0 ? h("button", { class: "btn small ghost", onclick: () => goStep(tour.i - 1) }, icon("back", 14), "이전") : null,
      h("span", { class: "right" }, next)));
}

// The marked part stays lit; the rest is dimmed and does not take clicks (the part does on a `click` step)
function placeTour() {
  if (!tour.id) return;
  cancelAnimationFrame(tour.raf);
  tour.raf = requestAnimationFrame(() => {
    if (!tour.parts) return;
    const step = TOURS[tour.id].steps[tour.i];
    if (tour.el && !tour.el.isConnected && step.el) tour.el = document.querySelector(tourSel(step.el));  // the screen drew itself again
    const { top, right, bottom, left, cover, ring, card } = tour.parts;
    const W = document.documentElement.clientWidth, H = innerHeight;
    const box = (part, x, y, w, ht) => { part.style.cssText = `left:${x}px;top:${y}px;width:${Math.max(0, w)}px;height:${Math.max(0, ht)}px`; };
    const el = shown(tour.el) ? tour.el : null;
    let [x, y, x2, y2] = [W / 2, H / 2, W / 2, H / 2];
    if (el) {
      const r = el.getBoundingClientRect();
      [x, y, x2, y2] = [Math.max(4, r.left - 6), Math.max(4, r.top - 6), Math.min(W - 4, r.right + 6), Math.min(H - 4, r.bottom + 6)];
      [x2, y2] = [Math.max(x, x2), Math.max(y, y2)];
      if (r.height > H * 0.6) y2 = Math.min(y2, y + H * 0.45);  // a tall part: light where it starts, the card goes below
    }
    box(top, 0, 0, W, y); box(bottom, 0, y2, W, H - y2); box(left, 0, y, x, y2 - y); box(right, x2, y, W - x2, y2 - y);
    box(cover, x, y, x2 - x, y2 - y);
    cover.style.display = el && step.click ? "none" : "";
    box(ring, x, y, x2 - x, y2 - y);
    ring.classList.toggle("off", !el);
    const cw = card.offsetWidth, ch = card.offsetHeight, gap = 14;
    let [cx, cy] = [(W - cw) / 2, (H - ch) / 2];
    if (el) {
      if (y2 + gap + ch <= H - 8) [cx, cy] = [x, y2 + gap];
      else if (y - gap - ch >= 8) [cx, cy] = [x, y - gap - ch];
      else if (x2 + gap + cw <= W - 8) [cx, cy] = [x2 + gap, y];
      else if (x - gap - cw >= 8) [cx, cy] = [x - gap - cw, y];
      else [cx, cy] = [W - cw - 24, H - ch - 24];
    }
    card.style.left = `${Math.min(Math.max(8, cx), W - cw - 8)}px`;
    card.style.top = `${Math.min(Math.max(8, cy), H - ch - 8)}px`;
  });
}

function tourClick(e) {
  if (!tour.id || !tour.el || !TOURS[tour.id].steps[tour.i].click || !tour.el.contains(e.target)) return;
  const at = tour.i;
  setTimeout(() => { if (tour.id && tour.i === at) goStep(at + 1); }, 60);  // after the click did its own work (a screen, a choice)
}

function tourKeys(e) {
  if (!tour.id) return;
  if (e.key === "Escape") { e.preventDefault(); return endTour(false); }
  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
  const steps = TOURS[tour.id].steps;
  if (e.key === "ArrowRight" && !steps[tour.i].click && tour.i < steps.length - 1) goStep(tour.i + 1);
  if (e.key === "ArrowLeft" && tour.i > 0) goStep(tour.i - 1);
}

// The top bar's [도움말]: this screen's tour first, then every tour, then the help page
function openHelpMenu(anchor) {
  const open = document.querySelector(".menu-pop");
  if (open) return open.remove();
  const here = contextTour();
  const done = store.get(TOUR_DONE) || [];
  const r = anchor.getBoundingClientRect();
  const item = (ico, label, onclick, extra = null, cls = "") => h("button", { class: `menu-item ${cls}`, onclick }, icon(ico, 16), h("span", { class: "grow" }, label), extra);
  const menu = h("div", { class: "menu-pop", style: `top:${r.bottom + 6}px;right:${Math.max(8, document.documentElement.clientWidth - r.right)}px` },
    item("compass", [h("b", {}, "이 화면 둘러보기"), h("div", { class: "muted" }, TOURS[here.id].title)], () => startTour(here.id, here.at), null, "strong"),
    h("div", { class: "menu-cap" }, "투어"),
    Object.entries(TOURS).map(([id, t]) => item(t.ico, t.title, () => startTour(id), done.includes(id) ? h("span", { class: "ok-text", title: "마친 투어" }, icon("check", 14)) : null)),
    h("div", { class: "menu-sep" }),
    item("help", "도움말 전체 (종류 고르기 · 버튼 사전)", () => { menu.remove(); location.hash = "#/help"; }));
  document.body.append(menu);
  const away = (e) => { if (!menu.contains(e.target) && !anchor.contains(e.target)) { menu.remove(); document.removeEventListener("click", away, true); } };
  document.addEventListener("click", away, true);
}

// First visit: offer the tour once, above the agent list
function welcomeCard() {
  if (store.get("aops.welcome")) return null;
  const close = () => { store.set("aops.welcome", 1); card.remove(); };
  const card = h("div", { class: "welcome" },
    h("span", { class: "tile-ico" }, icon("compass", 20)),
    h("div", { class: "grow" }, h("b", {}, "처음이신가요?"), h("div", { class: "muted" }, "화면을 직접 눌러 보며 2분 만에 익힙니다. 어떤 종류의 에이전트를 만들지도 골라 드립니다.")),
    h("button", { class: "btn primary", onclick: () => { close(); startTour("basics"); } }, icon("compass"), "둘러보기 시작"),
    h("a", { class: "btn", href: "#/help", onclick: () => store.set("aops.welcome", 1) }, "종류 고르기"),
    h("button", { class: "btn ghost", title: "다시 보지 않기", "aria-label": "닫기", onclick: close }, icon("x")));
  return card;
}

// Two questions to the kind that fits
function kindChooser() {
  const box = h("div", { class: "card pad chooser" });
  const ask = (n, q, hint, yes, no) => fill(box,
    h("div", { class: "muted" }, `질문 ${n}/2`),
    h("div", { class: "q mt-s" }, q), h("div", { class: "muted mt-s" }, hint),
    h("div", { class: "row mt" }, h("button", { class: "btn", onclick: yes }, icon("check"), "예"), h("button", { class: "btn", onclick: no }, icon("x"), "아니요")));
  const answer = (kind) => {
    const g = kindGuide(kind);
    fill(box, h("div", { class: "row" }, h("span", { class: `kind-ico k-${kind}` }, icon(g.ico, 20)),
      h("div", {}, h("div", { class: "muted" }, "추천"), h("div", { class: "big-t" }, g.title))),
      h("p", { class: "mt" }, h("b", {}, "언제: "), g.when),
      h("p", { class: "muted" }, g.run),
      h("div", { class: "row mt" }, h("a", { class: "btn primary", href: `#/new?type=${kind}` }, icon("plus"), "이 종류로 만들기"),
        h("button", { class: "btn ghost", onclick: first }, "다시 고르기")));
  };
  const second = () => ask(2, "도구 호출·외부 API·여러 단계를 코드로 짜야 하나요?", "예: 사내 API를 부르고, 결과를 보고 다음 일을 정하는 상담",
    () => answer("code"), () => answer("skill"));
  const first = () => ask(1, "답을 테이블 데이터에서 찾나요?", "예: 매출·재고·주문처럼 SQL로 조회하는 질문", () => answer("no_code"), second);
  first();
  return box;
}

function kindCard(g) {
  return h("div", { class: "card pad kind-card" },
    h("div", { class: "kc-head" }, h("span", { class: `kind-ico k-${g.kind}` }, icon(g.ico, 20)), h("b", {}, g.title)),
    h("dl", {},
      h("dt", {}, "언제"), h("dd", {}, g.when),
      h("dt", {}, "예"), h("dd", { class: "ex" }, g.examples.map((x) => h("span", { class: "tag" }, x))),
      h("dt", {}, "만들기"), h("dd", {}, g.make),
      h("dt", {}, "운영"), h("dd", {}, g.run)),
    h("div", { class: "foot" }, h("a", { class: "btn small", href: `#/new?type=${g.kind}` }, icon("plus", 14), "이 종류로 만들기")));
}

function helpPage(root) {
  const done = store.get(TOUR_DONE) || [];
  root.replaceChildren(
    pageHead("튜토리얼·가이드", "화면을 직접 눌러 보며 배웁니다. 언제든 오른쪽 위 [도움말]에서 다시 엽니다", [], null, "compass"),
    h("h2", { class: "sec-h" }, "투어"),
    h("div", { class: "tour-grid" }, Object.entries(TOURS).map(([id, t]) => h("button", { class: "tour-tile", onclick: () => startTour(id) },
      h("span", { class: "tile-ico" }, icon(t.ico, 20)),
      h("div", { class: "grow" }, h("b", {}, t.title), h("div", { class: "muted" }, t.desc)),
      h("span", { class: `tile-meta ${done.includes(id) ? "done" : ""}` }, done.includes(id) ? [icon("check", 13), "마침"] : `${t.steps.length}단계`)))),
    h("h2", { class: "sec-h mt" }, "어떤 에이전트를 만들까"),
    h("div", { class: "grid chooser-grid" }, kindChooser(), h("div", { class: "card pad chooser-note" },
      h("div", { class: "h-ico" }, icon("info", 16), h("b", {}, "애매할 때")),
      h("ul", { class: "plain-list" }, h("li", {}, "규칙·글로 되는 일은 스킬 기반"), h("li", {}, "테이블 데이터에 묻는 일은 노코드"),
        h("li", {}, "흐름을 코드로 짜야 하는 일만 코드 기반")))),
    h("div", { class: "grid three mt" }, KIND_GUIDE.map(kindCard)),
    h("h2", { class: "sec-h mt" }, "버튼 사전"),
    h("div", { class: "card" }, h("table", { class: "data gloss" }, h("tbody", {}, GLOSSARY.map(([key, text]) => h("tr", {},
      h("td", { style: "width:170px" }, h("span", { class: "gloss-name" }, act(key))), h("td", { class: "muted" }, text)))))),
    h("h2", { class: "sec-h mt" }, "색의 뜻"),
    h("div", { class: "card pad legend" }, LEGEND.map(([st, ico, text]) => h("div", { class: `lg st ${st}` }, h("span", { class: "dot" }, icon(ico, 13)), text))),
    h("div", { class: "note mt" }, "같은 일을 PC CLI·Genie Code로 하는 방법은 ", h("a", { href: "#/guide" }, "CLI·Genie Code"), "에 있습니다."));
}

// ── start ──────────────────────────────────────────────────────────────────

async function start() {
  try {
    [state.version, state.me] = await Promise.all([get("/meta/version").catch(() => null), get("/meta/me")]);
    state.policy = await get("/admin/policies/current").then((p) => p.policy).catch(() => null);
  } catch (error) {
    document.getElementById("app").replaceChildren(h("div", { class: "page" }, h("div", { class: "card pad" },
      h("div", { class: "empty" }, h("b", {}, "aops 서버에 연결하지 못했습니다"), error.title || String(error), error.detail ? h("div", { class: "mt" }, error.detail) : null))));
    return;
  }
  await refreshCounts();
  window.addEventListener("hashchange", render);
  render();
}

start();
