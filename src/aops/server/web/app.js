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
    let problem = {};
    try { problem = await resp.json(); } catch { problem = { title: (await resp.text()).slice(0, 200) }; }
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
  validation: { queued: ["대기", "info run"], running: ["검사 중", "info run"], passed: ["통과", "ok"], failed: ["실패", "bad"], stale: ["무효", "warn"], error: ["오류", "bad"] },
  decision: { validating: ["검사 중", "info run"], pending_approval: ["승인 대기", "warn"], approved: ["승인됨", "ok"], rejected: ["반려", "bad"], canceled: ["취소", "plain"] },
  deployment: { running: ["배포 중", "info run"], succeeded: ["완료", "ok"], failed: ["실패", "bad"] },
  git: { synced: ["Git", "ok"], failed: ["Git 실패", "bad"], skipped: ["Git 건너뜀", "warn"] },
};
const BUILD = { skill: "스킬", code: "코드", no_code: "노코드" };
const UPGRADE = { initial: "첫 배포", major: "major", minor: "minor", patch: "patch", rollback: "되돌리기" };
const EVENT = {
  register: "등록", validate: "검사", gate_requested: "배포 요청", gate_approved: "승인", gate_rejected: "반려",
  gate_canceled: "취소", deploy: "운영 반영", rollback: "되돌리기", promote: "권한", load: "가져오기", git: "Git",
};
const MODE = { mandatory: "필수", optional: "선택", exempt: "면제", required: "사람 승인", auto: "자동 승인" };

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
      h("header", {}, h("h2", {}, title), h("button", { class: "btn ghost", onclick: close, "aria-label": "닫기" }, "✕")),
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
  ["agents", "▦", "에이전트"],
  ["gates", "✓", "배포 요청함"],
  ["register", "↑", "등록"],
  ["new", "+", "새 에이전트"],
  ["sep"],
  ["policy", "⚙", "정책"],
  ["guide", "?", "CLI·Genie Code 안내"],
];

function layout(active, crumbs, content) {
  const nav = h("nav", { class: "nav" }, NAV.map(([key, ico, label]) => key === "sep" ? h("div", { class: "nav-sep" }) :
    h("a", { href: `#/${key}`, class: active === key ? "active" : "" }, h("span", { class: "ico" }, ico), label,
      key === "gates" && state.toApprove ? h("span", { class: "count" }, state.toApprove) : null)));
  const me = state.me;
  const side = h("aside", { class: "side" },
    h("div", { class: "brand" }, h("div", { class: "logo" }, "ao"), h("div", {}, "aops", h("small", {}, "AgentOps console"))),
    nav,
    h("div", { class: "side-foot" },
      me ? [h("div", { class: "user" }, me.user), h("div", {}, me.roles.map((r) => ({ developer: "개발자", approver: "승인자", admin: "관리자" })[r]).join(" · "))] : null,
      state.version ? h("div", { style: "margin-top:6px" }, `aops ${state.version.aops} · ${state.version.edition}`) : null));
  const top = h("div", { class: "topbar" }, h("div", { class: "crumbs" }, crumbs), h("div", { class: "spacer" }),
    h("a", { class: "btn small", href: `${API}/docs`, target: "_blank" }, "API 문서"));
  return h("div", { class: "shell" }, side, h("div", { class: "main" }, top, h("div", { class: "page" }, content)));
}

function pageHead(title, sub, actions = []) {
  return h("div", { class: "page-head" }, h("div", { class: "title" }, h("h1", {}, title), sub ? h("div", { class: "sub" }, sub) : null),
    h("div", { class: "actions" }, actions));
}

// ── router ─────────────────────────────────────────────────────────────────

function route() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [path, query] = hash.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(query || "");
  return { parts, params };
}

async function render() {
  for (const t of [...state.timers]) t.stop();
  document.querySelectorAll(".backdrop").forEach((b) => b.remove());
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
      case "gates": show("gates", [h("b", {}, "배포 요청함")], body); await gatesPage(body, params); break;
      case "register": show("register", [h("b", {}, "등록")], body); registerPage(body, params); break;
      case "new": show("new", [h("b", {}, "새 에이전트")], body); newPage(body); break;
      case "policy": show("policy", [h("b", {}, "정책")], body); await policyPage(body); break;
      case "guide": show("guide", [h("b", {}, "CLI·Genie Code 안내")], body); await guidePage(body); break;
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
  const page = await get("/agents");
  const items = page.items;
  let q = "", type = "", life = "";
  const table = h("div", {});
  const draw = () => {
    const rows = items.filter((a) => (!type || a.build_type === type) && (!life || a.lifecycle === life) &&
      (!q || `${a.agent_id} ${a.name} ${a.description || ""} ${a.owner}`.toLowerCase().includes(q)));
    table.replaceChildren(rows.length ? h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["에이전트", "종류", "최신", "운영", "마지막 검사", "대기", "담당", "바뀐 시각"].map((t) => h("th", {}, t)))),
      h("tbody", {}, rows.map((a) => h("tr", { class: "click", onclick: () => { location.hash = `#/agents/${enc(a.agent_id)}`; } },
        h("td", {}, h("div", { class: "cell-main" }, a.name || a.agent_id),
          h("div", { class: "cell-sub" }, [a.name && a.name !== a.agent_id ? a.agent_id : null, a.description].filter(Boolean).join(" · "))),
        h("td", {}, h("span", { class: "tag" }, BUILD[a.build_type] || a.build_type)),
        h("td", { class: "mono" }, a.latest_version || "–"),
        h("td", {}, a.production_version ? h("span", { class: "badge ok" }, a.production_version) : h("span", { class: "muted" }, "없음")),
        h("td", {}, badge("validation", a.last_validation)),
        h("td", {}, a.pending_gates ? h("span", { class: "badge warn" }, `${a.pending_gates}건`) : h("span", { class: "muted" }, "–")),
        h("td", {}, h("div", {}, a.owner), a.owner_group ? h("div", { class: "cell-sub" }, a.owner_group) : null),
        h("td", { class: "muted" }, when(a.updated_at))))))
      : h("div", { class: "empty" }, h("b", {}, items.length ? "조건에 맞는 에이전트가 없습니다" : "등록된 에이전트가 없습니다"),
        items.length ? "" : "새 에이전트를 받아 고친 뒤 등록하세요."));
  };
  const seg = (options, set) => {
    const box = h("div", { class: "seg" });
    options.forEach(([value, label], i) => box.append(h("button", { class: i === 0 ? "on" : "", onclick: (e) => {
      box.querySelectorAll("button").forEach((b) => b.classList.remove("on")); e.target.classList.add("on"); set(value); draw();
    } }, label)));
    return box;
  };
  const prod = items.filter((a) => a.production_version).length;
  const pending = items.reduce((n, a) => n + (a.pending_gates || 0), 0);
  const failed = items.filter((a) => a.last_validation === "failed").length;
  root.replaceChildren(
    pageHead("에이전트", "레지스트리에 등록된 에이전트 (aops list)", [
      h("a", { class: "btn", href: "#/new" }, "+ 새 에이전트"), h("a", { class: "btn primary", href: "#/register" }, "↑ 등록")]),
    h("div", { class: "grid four" },
      stat("에이전트", items.length, "등록된 전체"), stat("운영 중", prod, "운영 버전이 있는 에이전트"),
      stat("진행 중인 배포 요청", pending, "검사 중·승인 대기"), stat("마지막 검사 실패", failed, "다시 고쳐 등록할 것")),
    h("div", { class: "card mt" },
      h("div", { class: "toolbar", style: "padding:12px 14px 0" },
        h("input", { type: "search", placeholder: "이름·ID·설명·담당으로 찾기", oninput: (e) => { q = e.target.value.toLowerCase(); draw(); } }),
        seg([["", "모든 종류"], ["skill", "스킬"], ["code", "코드"], ["no_code", "노코드"]], (v) => { type = v; }),
        seg([["", "모든 상태"], ["production", "운영 중"], ["registered", "등록됨"]], (v) => { life = v; })),
      table));
  draw();
}

function stat(label, value, hint) {
  return h("div", { class: "card stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), h("div", { class: "hint" }, hint));
}

// ── 에이전트 상세 ────────────────────────────────────────────────────────────

async function agentPage(root, id, params) {
  const [agent, versions] = await Promise.all([get(`/agents/${enc(id)}`), get(`/agents/${enc(id)}/versions`)]);
  const latest = versions.items[0];
  let appUrl = null;
  if (agent.build_type === "code" && agent.production_version) {
    const deploys = await get(`/agents/${enc(id)}/deployments`).catch(() => ({ items: [] }));
    appUrl = (deploys.items.find((d) => d.status === "succeeded" && d.app_url) || {}).app_url || null;
  }
  const isOwner = state.me && (state.me.user === agent.owner || (agent.owner_group && state.me.groups.includes(agent.owner_group)));
  const tabs = [["versions", "버전", versions.items.length], ["checks", "검사"], ["gates", "배포 요청"], ["deploys", "배포"], ["events", "감사 이력"]];
  let tab = params.get("tab") || "versions";
  const pane = h("div", {});
  const tabBar = h("div", { class: "tabs" });
  const drawTabs = () => tabBar.replaceChildren(...tabs.map(([key, label, n]) => h("button", { class: key === tab ? "on" : "", onclick: () => { tab = key; drawTabs(); loadTab(); } },
    label, n !== undefined ? h("span", { class: "n" }, n) : null)));
  const loadTab = async () => {
    pane.replaceChildren(h("div", { class: "loading" }, h("span", { class: "spinner" })));
    try {
      if (tab === "versions") pane.replaceChildren(versionsTab(agent, versions.items));
      if (tab === "checks") pane.replaceChildren(checksTab(await get(`/agents/${enc(id)}/validation-runs`)));
      if (tab === "gates") pane.replaceChildren(gatesTable(await get(`/release-gates?agent_id=${enc(id)}`), { agent: false, reload: loadTab }));
      if (tab === "deploys") pane.replaceChildren(deploysTab(await get(`/agents/${enc(id)}/deployments`)));
      if (tab === "events") pane.replaceChildren(eventsTab(await get(`/agents/${enc(id)}/events`)));
    } catch (e) { pane.replaceChildren(h("div", { class: "note bad" }, e.title || String(e))); }
  };
  root.replaceChildren(
    pageHead(agent.name || agent.agent_id, [agent.name && agent.name !== agent.agent_id ? agent.agent_id : null, agent.description].filter(Boolean).join(" · "), [
      h("a", { class: "btn", href: `#/register?agent=${enc(id)}` }, "↑ 새 버전 등록"),
      h("button", { class: "btn", disabled: !latest, onclick: () => openValidate(id, latest.version) }, "검사"),
      h("button", { class: "btn", disabled: !latest, onclick: () => openLoad(agent) }, "가져오기"),
      h("button", { class: "btn", disabled: !agent.production_version || !isOwner, title: isOwner ? "" : "담당자·담당 팀만", onclick: () => openPromote(agent) }, "권한"),
      h("button", { class: "btn primary", disabled: !latest, onclick: () => openDeploy(id, versions.items, agent.production_version) }, "배포")]),
    h("div", { class: "grid two" },
      h("div", { class: "card pad" }, h("h2", {}, "운영"),
        h("dl", { class: "kv" },
          h("dt", {}, "상태"), h("dd", {}, badge("lifecycle", agent.lifecycle)),
          h("dt", {}, "운영 버전"), h("dd", {}, agent.production_version ? h("b", {}, agent.production_version) : "없음"),
          agent.build_type === "code" ? [h("dt", {}, "운영 앱"), h("dd", {}, appUrl ? h("a", { href: appUrl, target: "_blank" }, `${appUrl.replace("https://", "").split(".")[0]} ↗`) : "–")] : null,
          h("dt", {}, "최신 버전"), h("dd", { class: "mono" }, agent.latest_version || "–"),
          h("dt", {}, "마지막 검사"), h("dd", {}, badge("validation", agent.last_validation)),
          h("dt", {}, "진행 중 요청"), h("dd", {}, agent.pending_gates ? `${agent.pending_gates}건` : "없음"))),
      h("div", { class: "card pad" }, h("h2", {}, "SPEC"),
        h("dl", { class: "kv" },
          h("dt", {}, "종류"), h("dd", {}, BUILD[agent.build_type] || agent.build_type),
          h("dt", {}, "담당"), h("dd", {}, agent.owner, agent.owner_group ? h("span", { class: "muted" }, ` · ${agent.owner_group}`) : null),
          h("dt", {}, "중요도·민감도"), h("dd", {}, `${agent.service_criticality} · ${agent.data_sensitivity}`),
          h("dt", {}, "보안 등급"), h("dd", {}, agent.security_level),
          h("dt", {}, "내 권한"), h("dd", {}, isOwner ? "담당자 (등록·권한·모든 버전)" : "조회·운영 버전 가져오기")))),
    h("div", { class: "card pad mt" }, tabBar, pane));
  drawTabs();
  loadTab();
}

function versionsTab(agent, versions) {
  if (!versions.length) return h("div", { class: "empty" }, h("b", {}, "버전이 없습니다"));
  const prodIndex = versions.findIndex((v) => v.status === "production");
  return h("table", { class: "data" },
    h("thead", {}, h("tr", {}, ["버전", "상태", "등록", "크기·해시", "Git", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, versions.map((v, i) => h("tr", {},
      h("td", {}, h("div", { class: "cell-main mono" }, v.version), h("div", { class: "cell-sub" }, v.harness ? `기본 하네스 ${v.harness}` : "")),
      h("td", {}, badge("version", v.status)),
      h("td", {}, h("div", {}, v.registered_by || ""), h("div", { class: "cell-sub" }, when(v.registered_at || v.created_at))),
      h("td", {}, h("div", {}, size(v.size_bytes)), h("div", { class: "cell-sub mono" }, short(v.source_sha256))),
      h("td", {}, v.git_url ? h("a", { href: v.git_url, target: "_blank" }, `v${v.version} ↗`) : badge("git", v.git_status)),
      h("td", { class: "num" }, h("div", { class: "row", style: "justify-content:flex-end" },
        h("button", { class: "btn ghost small", onclick: () => openVersion(agent.agent_id, v.version) }, "자세히"),
        h("button", { class: "btn ghost small", onclick: () => openValidate(agent.agent_id, v.version) }, "검사"),
        v.status !== "production" ? h("button", { class: "btn small", onclick: () => openDeploy(agent.agent_id, versions, agent.production_version, v.version) },
          prodIndex >= 0 && i > prodIndex ? "되돌리기" : "배포") : null))))));
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

function checksTab(page) {
  if (!page.items.length) return h("div", { class: "empty" }, h("b", {}, "검사 기록이 없습니다"), "배포를 요청하면 검사가 먼저 돌거나, 검사만 따로 돌릴 수 있습니다.");
  return h("div", {}, page.items.map((r) => h("div", { class: "card pad", style: "box-shadow:none" },
    h("div", { class: "row" }, h("b", { class: "mono" }, r.version), badge("validation", r.status), h("span", { class: "muted" }, `정책 v${r.policy_version}`),
      h("span", { class: "muted right" }, when(r.started_at))),
    r.steps.length ? h("ul", { class: "steps mt" }, r.steps.map(stepItem)) : null,
    r.failure_reason ? h("div", { class: "note bad mt" }, r.failure_reason) : null)));
}

function stepItem(step) {
  const ok = step.status === "passed";
  const cls = ok ? "ok" : step.status === "failed" ? "bad" : step.status === "running" ? "run" : "wait";
  return h("li", {}, h("span", { class: `mark ${cls}` }, ok ? "✓" : step.status === "failed" ? "✕" : ""),
    h("div", {}, h("div", { class: "name" }, step.name), step.detail ? h("div", { class: "detail" }, step.detail) : null));
}

function deploysTab(page) {
  if (!page.items.length) return h("div", { class: "empty" }, h("b", {}, "배포 기록이 없습니다"));
  return h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["버전", "결과", "대상", "시작", "끝"].map((t) => h("th", {}, t)))),
    h("tbody", {}, page.items.map((d) => h("tr", {},
      h("td", { class: "mono cell-main" }, d.version), h("td", {}, badge("deployment", d.status), d.error ? h("div", { class: "cell-sub" }, d.error) : null),
      h("td", { class: "mono", style: "max-width:380px" }, d.target || "–"), h("td", { class: "muted" }, when(d.started_at)), h("td", { class: "muted" }, when(d.ended_at))))));
}

function eventsTab(page) {
  if (!page.items.length) return h("div", { class: "empty" }, h("b", {}, "기록이 없습니다"));
  const describe = (e) => {
    const d = e.detail || {};
    if (e.event_type === "git") return `${d.action === "production" ? "production 브랜치" : "커밋·태그"} ${d.status || ""}${d.error ? ` · ${d.error}` : ""}`;
    if (e.event_type === "promote") return `${d.revoke ? "회수" : "부여"} ${(d.principals || []).join(", ")}`;
    if (e.event_type === "gate_rejected") return d.reason || "";
    if (e.event_type === "deploy" || e.event_type === "rollback") return d.previous ? `이전 ${d.previous}` : "첫 운영";
    if (e.event_type === "validate") return d.status || "";
    return "";
  };
  return h("ul", { class: "timeline" }, page.items.map((e) => h("li", {},
    h("span", { class: "when" }, when(e.at)), h("span", {}, h("span", { class: "tag" }, EVENT[e.event_type] || e.event_type), h("span", { class: "mono" }, e.version || "")),
    h("span", {}, h("span", {}, e.actor), h("span", { class: "muted" }, describe(e) ? ` · ${describe(e)}` : "")))));
}

// ── 검사 (aops validate) ────────────────────────────────────────────────────

function openValidate(id, version) {
  const list = h("ul", { class: "steps" }, h("li", {}, h("span", { class: "mark run" }), h("div", {}, h("div", { class: "name" }, "검사 요청 중…"))));
  const status = h("div", { class: "row" }, h("b", { class: "mono" }, `${id} ${version}`));
  const m = modal("검사 (validate Job)", h("div", {}, status, h("p", { class: "muted mt" }, "배포 전 Gate와 같은 검사를 따로 돌립니다. 결과는 레지스트리에 남습니다."), list),
    [{ label: "닫기", run: (close) => close() }]);
  post(`/projects/${enc(id)}/validations`, { version }).then((run) => {
    const timer = every(3000, async () => {
      const r = await get(`/validations/${enc(run.validation_run_id)}`);
      status.replaceChildren(h("b", { class: "mono" }, `${id} ${r.version}`), badge("validation", r.status), h("span", { class: "muted" }, `정책 v${r.policy_version}`));
      list.replaceChildren(...(r.steps.length ? r.steps.map(stepItem) : [h("li", {}, h("span", { class: "mark run" }), h("div", {}, h("div", { class: "name" }, "validate Job 실행 중…"), h("div", { class: "detail" }, "보통 1분 안팎")))]));
      return !["passed", "failed", "stale", "error"].includes(r.status);
    });
    m.onClose(() => timer.stop());
  }).catch((e) => { m.close(); fail(e); });
}

// ── 배포 (aops deploy) ──────────────────────────────────────────────────────

function openDeploy(id, versions, production, preset) {
  const select = h("select", {}, versions.map((v) => h("option", { value: v.version, selected: v.version === (preset || versions[0].version) },
    `${v.version}${v.status === "production" ? " (운영)" : ""}`)));
  const reason = h("input", { type: "text", placeholder: "선택: 요청 사유 (예: 할인 규정 보강)" });
  const body = h("div", {},
    h("label", { class: "field" }, h("span", {}, "배포할 버전"), select, h("small", {}, production ? `지금 운영 버전 ${production}. 더 낮은 버전을 고르면 되돌리기입니다.` : "운영 버전이 없습니다 (첫 배포)")),
    h("label", { class: "field" }, h("span", {}, "사유"), reason),
    h("div", { class: "note" }, "검사(Gate) → 승인(정책) → 배포 Job 순서로 진행됩니다. 사람 승인이 필요하면 요청만 남고, 승인자가 배포 요청함에서 처리합니다."));
  modal("배포 요청", body, [
    { label: "취소", run: (close) => close() },
    { label: "배포 요청", cls: "primary", run: (close) => { close(); followDeploy(id, select.value, reason.value || null); } }]);
}

function followDeploy(id, version, reason) {
  const head = h("div", { class: "row" }, h("b", { class: "mono" }, `${id} ${version}`), h("span", { class: "spinner" }));
  const list = h("ul", { class: "steps mt" });
  const outcome = h("div", {});
  let gateId = null;
  const cancelBtn = h("button", { class: "btn danger", disabled: true, onclick: async () => {
    try { await post(`/release-gates/${enc(gateId)}/cancel`); toast("배포 요청을 취소했습니다", "ok"); } catch (e) { fail(e); }
  } }, "요청 취소");
  const m = modal("배포 진행", h("div", {}, head, list, outcome), [cancelBtn, h("button", { class: "btn", onclick: () => m.close() }, "닫기")], { wide: true });
  const item = (cls, name, detail) => h("li", {}, h("span", { class: `mark ${cls}` }, cls === "ok" ? "✓" : cls === "bad" ? "✕" : ""),
    h("div", {}, h("div", { class: "name" }, name), detail ? h("div", { class: "detail" }, detail) : null));
  post(`/projects/${enc(id)}/deploy-requests`, { version, reason }).then((gate) => {
    gateId = gate.gate_id;
    const timer = every(3000, async () => {
      const g = await get(`/release-gates/${enc(gateId)}`);
      const steps = g.validation ? g.validation.steps : [];
      const open = ["validating", "pending_approval"].includes(g.decision);
      const personDecides = g.decision === "pending_approval" && (g.approval_mode !== "auto" || !!g.decision_reason);
      cancelBtn.disabled = !open;
      fill(head, h("b", { class: "mono" }, `${id} ${g.version}`), badge("decision", g.decision), g.deployment_status ? badge("deployment", g.deployment_status) : null,
        h("span", { class: "muted" }, `${UPGRADE[g.upgrade_type] || g.upgrade_type} · 검사 ${MODE[g.validation_policy_mode]} · 승인 ${MODE[g.approval_mode] || g.approval_mode}`),
        (open && !personDecides) || g.deployment_status === "running" ? h("span", { class: "spinner" }) : null);
      const rows = [item("ok", "배포 요청", `gate ${g.gate_id} · 요청 ${g.requester}`)];
      if (g.decision === "validating") rows.push(item("run", "검사 중", "validate Job (보통 1분 안팎)"));
      else if (g.validation) rows.push(item(g.validation.status === "passed" ? "ok" : "bad", `검사 ${LABEL.validation[g.validation.status]?.[0] || g.validation.status}`,
        steps.map((s) => `${s.status === "passed" ? "✓" : "✗"} ${s.name}`).join("  ")));
      else if (g.validation_policy_mode === "exempt") rows.push(item("ok", "검사 면제", "정책"));
      if (g.decision === "pending_approval") rows.push(item(personDecides ? "wait" : "run", personDecides ? "승인자 필요" : "정책 자동 승인 중", g.decision_reason || "승인자가 배포 요청함에서 승인합니다"));
      if (["approved"].includes(g.decision)) rows.push(item("ok", "승인됨", g.approved_by === "policy:auto" ? "정책 자동 승인" : `승인자 ${g.approved_by || ""}`));
      if (g.deployment_status === "running") rows.push(item("run", "운영 반영 중", "스킬 기반: 배포 Job이 UC Skill 내용을 바꿈 (1~2분) · 코드 기반: aops 서버가 운영 앱을 배포 (4~5분)"));
      if (g.deployment_status === "succeeded") rows.push(item("ok", "운영 반영 완료", `운영 버전 ${g.version}`));
      if (g.deployment_status === "failed") rows.push(item("bad", "배포 실패", "배포 탭의 기록을 확인하세요"));
      if (g.decision === "rejected") rows.push(item("bad", "반려", g.decision_reason || ""));
      if (g.decision === "canceled") rows.push(item("bad", "취소됨", g.decision_reason || ""));
      list.replaceChildren(...rows);
      if (steps.some((s) => s.status === "failed")) outcome.replaceChildren(h("h3", { class: "mt" }, "검사 결과"), h("ul", { class: "steps" }, steps.map(stepItem)));
      const done = ["rejected", "canceled"].includes(g.decision) || ["succeeded", "failed"].includes(g.deployment_status);
      if (done) { refreshCounts(); if (g.deployment_status === "succeeded") toast(`운영 반영 완료: ${id} ${g.version}`, "ok"); }
      if (personDecides) { outcome.replaceChildren(h("div", { class: "note warn mt" }, "승인자에게 요청됐습니다. 이 창을 닫아도 요청은 남습니다 (배포 요청함).")); }
      return !done && !personDecides;
    });
    m.onClose(() => { timer.stop(); if (location.hash.startsWith(`#/agents/${enc(id)}`)) render(); });
  }).catch((e) => { m.close(); fail(e); });
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
  modal(`권한: ${agent.agent_id}`, h("div", {},
    h("label", { class: "field" }, h("span", {}, "대상 (한 줄에 하나)"), input,
      h("small", {}, agent.build_type === "code"
        ? "group:<그룹> · user:<이메일> · sp:<application ID>. 코드 기반은 운영 앱에 CAN_USE를 겁니다. 받은 사람은 앱을 열어 씁니다."
        : "group:<계정 그룹> · user:<이메일> · sp:<application ID>. 스킬은 운영 UC Skill에 READ_VOLUME을 겁니다. 받은 사람은 가져오기(load)를 할 수 있습니다.")),
    result), [
    { label: "회수", cls: "danger", run: () => run(true) },
    { label: "권한 주기", cls: "primary", run: () => run(false) }]);
}

// ── 가져오기 (aops load) ────────────────────────────────────────────────────

async function openLoad(agent) {
  try {
    const info = await post(`/agents/${enc(agent.agent_id)}/load`, {});
    if (info.link) { window.open(info.link, "_blank"); return toast(`운영 앱을 엽니다: ${agent.agent_id} ${info.version}`, "ok", "앱 사용 권한(CAN_USE)이 있어야 열립니다"); }
    if (!info.allowed || !info.archive) return toast("가져올 수 없습니다", "bad", "운영 UC Skill 읽기 권한이 필요합니다 (담당자에게 권한 요청)");
    await download(info.archive.url.replace(API, ""), `${agent.agent_id}-${info.version}.zip`);
    toast(`가져옴: ${agent.agent_id} ${info.version}`, "ok", "풀어서 aops run으로 쓰거나 Genie Code 폴더에 올리세요");
  } catch (e) { fail(e); }
}

// ── 배포 요청함 (aops gates · approve · reject · deploy --cancel) ─────────────

async function gatesPage(root, params) {
  let tab = params.get("tab") || (state.me && state.me.roles.includes("approver") ? "approve" : "mine");
  const pane = h("div", {});
  const tabBar = h("div", { class: "tabs" });
  const tabs = [["approve", "승인할 요청", "?to_approve=true"], ["mine", "내 요청", "?mine=true"], ["all", "전체", ""]];
  const load = async () => {
    tabBar.replaceChildren(...tabs.map(([key, label]) => h("button", { class: key === tab ? "on" : "", onclick: () => { tab = key; load(); } }, label)));
    pane.replaceChildren(h("div", { class: "loading" }, h("span", { class: "spinner" })));
    const page = await get(`/release-gates${tabs.find((t) => t[0] === tab)[2]}`);
    pane.replaceChildren(gatesTable(page, { agent: true, reload: async () => { await refreshCounts(); render(); }, approve: tab === "approve" }));
  };
  root.replaceChildren(pageHead("배포 요청함", "배포 요청과 승인 (aops gates · approve · reject · deploy --cancel)"), h("div", { class: "card pad" }, tabBar, pane));
  await load();
}

function gatesTable(page, { agent, reload, approve }) {
  const rows = page.items;
  if (!rows.length) return h("div", { class: "empty" }, h("b", {}, approve ? "승인할 배포 요청이 없습니다" : "배포 요청이 없습니다"),
    approve ? "내 요청은 내가 승인할 수 없어 여기에 나오지 않습니다." : "");
  const me = state.me ? state.me.user : "";
  return h("table", { class: "data" },
    h("thead", {}, h("tr", {}, [agent ? "에이전트" : "버전", "변경 폭·방식", "상태", "사유", "요청", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, rows.map((g) => {
      const open = ["validating", "pending_approval"].includes(g.decision);
      return h("tr", {},
        h("td", {}, agent ? [h("a", { class: "cell-main", href: `#/agents/${enc(g.agent_id)}` }, g.agent_id), h("div", { class: "cell-sub mono" }, g.version)] : h("span", { class: "mono cell-main" }, g.version)),
        h("td", {}, h("div", {}, UPGRADE[g.upgrade_type] || g.upgrade_type), h("div", { class: "cell-sub" }, `검사 ${MODE[g.validation_policy_mode]} · ${MODE[g.approval_mode] || g.approval_mode}`)),
        h("td", {}, badge("decision", g.decision), g.deployment_status ? h("div", { style: "margin-top:4px" }, badge("deployment", g.deployment_status)) : null),
        h("td", { style: "max-width:300px" }, g.decision_reason || (g.approved_by ? `승인 ${g.approved_by === "policy:auto" ? "정책 자동" : g.approved_by}` : h("span", { class: "muted" }, "–"))),
        h("td", {}, h("div", {}, g.requester), h("div", { class: "cell-sub" }, when(g.requested_at))),
        h("td", { class: "num" }, h("div", { class: "row", style: "justify-content:flex-end" },
          h("button", { class: "btn ghost small", onclick: () => openGate(g.gate_id) }, "자세히"),
          approve && g.decision === "pending_approval" ? [
            h("button", { class: "btn small danger", onclick: () => decide(g, "reject", reload) }, "반려"),
            h("button", { class: "btn small primary", onclick: () => decide(g, "approve", reload) }, "승인")] : null,
          open && g.requester === me ? h("button", { class: "btn small danger", onclick: async () => {
            try { await post(`/release-gates/${enc(g.gate_id)}/cancel`); toast("취소했습니다", "ok"); reload(); } catch (e) { fail(e); }
          } }, "취소") : null)));
    })));
}

function decide(gate, action, reload) {
  const text = h("textarea", { placeholder: action === "reject" ? "반려 사유 (필수)" : "선택: 의견" });
  modal(action === "reject" ? "반려" : "승인", h("div", {},
    h("p", {}, h("b", {}, `${gate.agent_id} ${gate.version}`), ` · 요청 ${gate.requester}`),
    action === "approve" ? h("div", { class: "note" }, "승인하면 배포 Job이 바로 운영 버전을 바꿉니다.") : null,
    h("label", { class: "field mt" }, h("span", {}, action === "reject" ? "사유" : "의견"), text)), [
    { label: "닫기", run: (close) => close() },
    { label: action === "reject" ? "반려" : "승인", cls: action === "reject" ? "danger" : "primary", run: async (close) => {
      if (action === "reject" && !text.value.trim()) return toast("반려 사유를 적으세요", "bad");
      try {
        await post(`/release-gates/${enc(gate.gate_id)}/${action}`, action === "reject" ? { reason: text.value.trim() } : { comment: text.value.trim() || null });
        close(); toast(action === "reject" ? "반려했습니다" : "승인했습니다. 배포 Job이 시작됩니다", "ok"); reload();
      } catch (e) { fail(e); }
    } }]);
}

async function openGate(gateId) {
  try {
    const g = await get(`/release-gates/${enc(gateId)}`);
    modal(`배포 요청 ${g.agent_id} ${g.version}`, h("div", {},
      h("dl", { class: "kv" },
        h("dt", {}, "상태"), h("dd", {}, badge("decision", g.decision), " ", g.deployment_status ? badge("deployment", g.deployment_status) : null),
        h("dt", {}, "변경 폭"), h("dd", {}, UPGRADE[g.upgrade_type] || g.upgrade_type),
        h("dt", {}, "검사·승인 방식"), h("dd", {}, `${MODE[g.validation_policy_mode]} · ${MODE[g.approval_mode] || g.approval_mode} (필요 승인 ${g.required_approvals}, 받은 승인 ${g.approvals})`),
        h("dt", {}, "요청"), h("dd", {}, `${g.requester} · ${when(g.requested_at)}`),
        h("dt", {}, "승인"), h("dd", {}, g.approved_by || "–"),
        h("dt", {}, "사유"), h("dd", {}, g.decision_reason || "–"),
        h("dt", {}, "gate"), h("dd", { class: "mono" }, g.gate_id)),
      g.validation ? [h("h2", { class: "mt" }, "검사"), h("ul", { class: "steps" }, g.validation.steps.map(stepItem))] : null), [], { wide: true });
  } catch (e) { fail(e); }
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
  let picked = null;  // { kind: "folder" | "zip", name, files: [{rel, file}], zip }
  let bump = "minor";
  const info = h("div", {});
  const result = h("div", {});
  const version = h("input", { type: "text", placeholder: "비우면 자동 (처음 1.0.0)", style: "width:200px" });
  const submit = h("button", { class: "btn primary", disabled: true, onclick: () => send() }, "등록");
  const folderInput = h("input", { type: "file", webkitdirectory: true, multiple: true, style: "display:none", onchange: (e) => pickFolder([...e.target.files]) });
  const zipInput = h("input", { type: "file", accept: ".zip", style: "display:none", onchange: (e) => e.target.files[0] && pickZip(e.target.files[0]) });
  const drop = h("div", { class: "drop" },
    h("p", {}, h("b", {}, "에이전트 폴더(aops.toml이 있는 폴더)를 고르거나 버전 zip을 놓으세요")),
    h("p", { class: "muted" }, "하네스 설정(.claude, .agents, CLAUDE.md, AGENTS.md)과 runs·.aops는 빼고 묶습니다. PC의 aops register와 같은 규칙입니다."),
    h("div", { class: "row", style: "justify-content:center" },
      h("button", { class: "btn", onclick: () => folderInput.click() }, "폴더 고르기"), h("button", { class: "btn", onclick: () => zipInput.click() }, "zip 고르기")),
    folderInput, zipInput);
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
        h("dt", {}, "보낼 것"), h("dd", {}, picked.kind === "zip" ? `zip ${size(picked.zip.size)}` : `파일 ${picked.files.length}개${picked.skipped ? ` (하네스 설정·기록 ${picked.skipped}개 제외)` : ""}`)),
      picked.kind === "folder" ? h("div", { class: "files" }, picked.files.map((f) => h("div", {}, f.rel))) : null,
      mismatch ? h("div", { class: "note warn mt" }, `이 폴더는 ${picked.name}입니다 (${expected}의 새 버전이 아닙니다)`) : null));
    submit.disabled = !picked.name;
  }

  async function send() {
    const form = new FormData();
    if (picked.kind === "zip") form.append("archive", picked.zip, picked.zip.name);
    else picked.files.forEach((f) => form.append("files", f.file, `${picked.name}/${f.rel}`));
    if (version.value.trim()) form.append("version", version.value.trim()); else form.append("bump", bump);
    submit.disabled = true;
    submit.replaceChildren(h("span", { class: "spinner" }), " 등록 중…");
    try {
      const row = await api("POST", `/projects/${enc(picked.name)}/registrations`, { form });
      result.replaceChildren(h("div", { class: "card pad mt" },
        h("div", { class: "row" }, h("h2", { style: "margin:0" }, `등록: ${picked.name} ${row.version}`), badge("version", row.status)),
        h("dl", { class: "kv mt" },
          h("dt", {}, "버전 zip"), h("dd", { class: "mono" }, row.artifact_path),
          h("dt", {}, "파일 해시"), h("dd", { class: "mono" }, short(row.source_sha256)),
          h("dt", {}, "스킬"), h("dd", {}, row.skills.map((s) => h("div", {}, h("b", {}, s.skill_alias), h("span", { class: "muted" }, ` → 배포하면 UC Skill ${s.uc_name || ""}`)))),
          h("dt", {}, "Git"), h("dd", {}, row.git ? (row.git.status === "synced" ? h("a", { href: row.git.url, target: "_blank" }, row.git.url) : `${row.git.status}: ${row.git.error || ""}`) : "–")),
        h("div", { class: "row mt" },
          h("button", { class: "btn", onclick: () => openValidate(picked.name, row.version) }, "검사"),
          h("button", { class: "btn primary", onclick: async (e) => {
            e.target.disabled = true;
            try { const [a, v] = await Promise.all([get(`/agents/${enc(picked.name)}`), get(`/agents/${enc(picked.name)}/versions`)]); openDeploy(picked.name, v.items, a.production_version, row.version); }
            catch (err) { fail(err); }
            e.target.disabled = false;
          } }, "배포"),
          h("a", { class: "btn ghost", href: `#/agents/${enc(picked.name)}` }, "에이전트 보기 →"))));
      toast(`등록: ${picked.name} ${row.version}`, "ok");
    } catch (e) { fail(e); }
    submit.disabled = false;
    submit.replaceChildren("등록");
  }

  const bumpSeg = h("div", { class: "seg" }, [["fix", "fix (1.0.x)"], ["minor", "minor (1.x.0)"], ["major", "major (x.0.0)"]].map(([v, label]) =>
    h("button", { class: v === bump ? "on" : "", onclick: (e) => { bump = v; bumpSeg.querySelectorAll("button").forEach((b) => b.classList.remove("on")); e.target.classList.add("on"); } }, label)));
  root.replaceChildren(
    pageHead("등록", expected ? `${expected}의 새 버전 (aops register)` : "버전 저장 (aops register). 처음이면 에이전트가 생깁니다"),
    h("div", { class: "card pad" }, drop, info,
      h("div", { class: "grid two mt" },
        h("div", { class: "field" }, h("span", {}, "버전 올리는 폭"), bumpSeg, h("small", {}, "처음 등록은 1.0.0")),
        h("label", { class: "field" }, h("span", {}, "또는 버전 직접"), version)),
      h("div", { class: "row" }, h("span", { class: "muted" }, "등록은 검사를 요구하지 않습니다. 검사와 승인은 배포 요청 때 합니다."), h("span", { class: "right" }, submit))),
    result);
}

// ── 새 에이전트 (aops init) ─────────────────────────────────────────────────

function newPage(root) {
  const name = h("input", { type: "text", placeholder: "price-helper", style: "width:280px" });
  const harness = h("select", {}, h("option", { value: "claude" }, "claude"), h("option", { value: "codex" }, "codex"));
  const kind = h("select", {}, h("option", { value: "skill" }, "스킬 기반 (누구나: 하네스 + 스킬)"), h("option", { value: "code" }, "코드 기반 (Agent Bricks 프로젝트)"));
  const make = async () => {
    const value = name.value.trim();
    if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(value)) return toast("이름은 영문 소문자·숫자·하이픈 63자 이하", "bad");
    try {
      const resp = await fetch(`${API}/projects/template`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: value, type: kind.value, harness: harness.value }) });
      if (!resp.ok) throw new ApiError(resp.status, await resp.json().catch(() => ({})));
      const url = URL.createObjectURL(await resp.blob());
      const a = h("a", { href: url, download: `${value}.zip` }); document.body.append(a); a.click(); a.remove();
      toast(`${value}.zip을 받았습니다`, "ok");
    } catch (e) { fail(e); }
  };
  root.replaceChildren(
    pageHead("새 에이전트", "aops init과 같은 에이전트 폴더를 zip으로 받습니다"),
    h("div", { class: "grid two" },
      h("div", { class: "card pad" },
        h("label", { class: "field" }, h("span", {}, "이름 (agent_id)"), name, h("small", {}, "영문 소문자·숫자·하이픈. 레지스트리·UC Skill 이름에 쓰입니다")),
        h("label", { class: "field" }, h("span", {}, "종류"), kind, h("small", {}, "코드 기반은 agentbricks init 템플릿에 aops.toml·SPEC.md를 더해 줍니다 (처음 한 번은 서버가 Agent Bricks CLI를 설치해 30초쯤 걸림)")),
        h("label", { class: "field" }, h("span", {}, "기본 하네스 (스킬 기반)"), harness),
        h("button", { class: "btn primary", onclick: make }, "폴더 받기 (zip)")),
      h("div", { class: "card pad" }, h("h2", {}, "다음 순서"),
        h("ol", { style: "margin:0;padding-left:18px;line-height:1.9" },
          h("li", {}, "zip을 풀고 ", h("code", {}, "SPEC.md"), "의 description, ", h("code", {}, "agent/skills/my-skill"), " 폴더 이름과 SKILL.md를 내 스킬로 바꿉니다"),
          h("li", {}, "PC에서 ", h("code", {}, 'aops run "질문"'), "으로 시험합니다 (Claude·Codex, 개인 MLflow 실험에 trace)"),
          h("li", {}, "등록: 이 화면의 ", h("a", { href: "#/register" }, "등록"), ", PC의 ", h("code", {}, "aops register"), ", 또는 Genie Code에 \"등록해줘\""),
          h("li", {}, "배포 요청 → 검사 → 승인 → 운영. 템플릿 그대로면 검사에서 반려됩니다")),
        h("div", { class: "note mt" }, "코드 기반: agentbricks dev로 시험하고 같은 방법으로 등록·배포합니다. 운영 앱(agent-bricks-<이름>)은 aops 서버가 서비스 주체로 배포합니다. 노코드(Genie Agent)는 준비 중입니다."))));
}

// ── 정책 (aops admin setup --only policy) ───────────────────────────────────

async function policyPage(root) {
  const page = await get("/admin/policies");
  const current = page.items.find((p) => p.status === "active");
  const isAdmin = state.me && state.me.roles.includes("admin");
  const pol = current ? current.policy : {};
  const pick = { validation_mode: pol.validation_mode, approval_mode: pol.approval_mode };
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
      const saved = await post("/admin/policies", { ...pick, approver_group: group.value.trim() });
      toast(`정책 버전 ${saved.policy_version} 적용`, "ok"); render();
    } catch (e) { fail(e); }
  };
  const git = pol.git;
  root.replaceChildren(
    pageHead("정책", current ? `지금 정책 버전 ${current.policy_version} · ${current.created_by} · ${when(current.created_at)}` : "적용된 정책이 없습니다"),
    h("div", { class: "grid two" },
      h("div", { class: "card pad" }, h("h2", {}, "검사·승인"),
        h("div", { class: "field" }, h("span", {}, "검사 방식"), segFor("validation_mode", [["mandatory", "필수"], ["optional", "선택"], ["exempt", "면제"]]),
          h("small", {}, "필수: 실패하면 반려 · 선택: 실패해도 승인자에게 · 면제: 검사 없이")),
        h("div", { class: "field" }, h("span", {}, "승인 방식"), segFor("approval_mode", [["required", "사람 승인"], ["optional", "선택"], ["auto", "자동 승인"]]),
          h("small", {}, "선택: SPEC 중요도 low이고 민감도 public·internal이면 자동, 아니면 승인자")),
        h("label", { class: "field" }, h("span", {}, "승인 그룹"), group),
        isAdmin ? h("button", { class: "btn primary", onclick: save }, "새 정책 버전으로 적용")
          : h("div", { class: "note" }, "정책은 워크스페이스 관리자(admins)만 바꿉니다. CLI: aops admin setup --only policy")),
      h("div", { class: "card pad" }, h("h2", {}, "그 밖의 설정"),
        h("dl", { class: "kv" },
          h("dt", {}, "필요 승인 수"), h("dd", {}, pol.required_approvals ?? 1),
          h("dt", {}, "보안 등급"), h("dd", {}, (pol.security_levels || []).join(" · ")),
          h("dt", {}, "Git 사본"), h("dd", {}, git ? `${git.provider} · ${git.owner} · 연결 ${git.connection} · ${git.mode} (저장소 ${git.repo_prefix}<agent_id>)` : "꺼짐")),
        h("div", { class: "note mt" }, "레지스트리·서버·Job·권한·Genie Code 스킬 설치는 관리자가 CLI로 합니다: aops admin setup"))),
    h("div", { class: "card mt" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["버전", "상태", "검사", "승인", "승인 그룹", "올린 사람", "시각"].map((t) => h("th", {}, t)))),
      h("tbody", {}, page.items.map((p) => h("tr", {},
        h("td", { class: "mono cell-main" }, `v${p.policy_version}`), h("td", {}, p.status === "active" ? h("span", { class: "badge ok" }, "적용 중") : h("span", { class: "badge plain" }, "이전")),
        h("td", {}, MODE[p.policy.validation_mode] || p.policy.validation_mode), h("td", {}, MODE[p.policy.approval_mode] || p.policy.approval_mode),
        h("td", {}, p.policy.approver_group || "–"), h("td", {}, p.created_by), h("td", { class: "muted" }, when(p.created_at))))))));
}

// ── 안내: CLI ↔ 화면 ↔ Genie Code ───────────────────────────────────────────

const SCREEN = {
  "aops init": "새 에이전트", "aops list": "에이전트", "aops load": "에이전트 → 가져오기", "aops validate": "에이전트 → 검사",
  "aops register": "등록", "aops deploy": "에이전트 → 배포", "aops deploy --cancel": "배포 요청함 → 취소", "aops gates": "배포 요청함",
  "aops approve": "배포 요청함 → 승인", "aops reject": "배포 요청함 → 반려", "aops promote": "에이전트 → 권한",
  "aops admin setup": "정책 (정책만, 나머지는 CLI)",
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
    pageHead("CLI·Genie Code 안내", "세 가지 방법이 같은 aops 서버 API를 부릅니다. 결과는 어디서 하든 같은 레지스트리에 남습니다."),
    h("div", { class: "grid three" },
      h("div", { class: "card pad" }, h("h2", {}, "이 화면"), h("p", { class: "muted" }, "브라우저에서 등록·검사·배포·승인·권한·정책. Databricks 로그인 그대로 씁니다.")),
      h("div", { class: "card pad" }, h("h2", {}, "PC CLI"), h("p", { class: "muted" }, "aops run으로 Claude·Codex 하네스를 바꿔 가며 개발하고 같은 명령으로 등록·배포합니다."),
        h("div", { class: "cmd" }, "aops register --bump fix\naops deploy")),
      h("div", { class: "card pad" }, h("h2", {}, "Genie Code"), h("p", { class: "muted" }, "워크스페이스 스킬 aops: \"이 에이전트 등록해줘\", \"배포해줘\". 노트북에서 같은 CLI 코드가 내 신원으로 돕니다."),
        h("div", { class: "cmd" }, "에이전트 폴더 …/price-helper 를\nfix 버전으로 등록하고 배포해줘"))),
    h("div", { class: "card mt" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["CLI 명령", "이 화면", "API"].map((t) => h("th", {}, t)))),
      h("tbody", {}, working.map((r) => h("tr", {}, h("td", { class: "mono cell-main" }, r.command), h("td", {}, SCREEN[r.command]),
        h("td", { class: "mono muted", style: "font-size:12px" }, (SCREEN_ROUTES[r.command] || r.routes).map((x) => h("div", {}, x)))))))),
    h("div", { class: "card pad mt" }, h("h2", {}, "PC에서만 하는 것"),
      h("p", { class: "muted" }, "하네스(Claude·Codex)를 내 PC에서 실행하는 일은 화면에 없습니다. 개발 trace는 개인 MLflow 실험(/Users/<나>/aops/<에이전트>)에 쌓입니다."),
      h("div", {}, pcOnly.map((c) => h("span", { class: "tag mono" }, c)))));
}

// ── start ──────────────────────────────────────────────────────────────────

async function start() {
  try {
    [state.version, state.me] = await Promise.all([get("/meta/version").catch(() => null), get("/meta/me")]);
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
