/* Assets Manager UI — 그래프, 상세 패널, 드롭 영역, AI 작성 흐름, 가져오기, 기록과 되돌리기 (기획서 v3) */
const APP_VERSION = "0.12.2"; // server.py의 APP_VERSION과 같아야 함
const API = location.port === "8765" ? "" : "http://127.0.0.1:8765";
const TAURI = window.__TAURI__ || null;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fileUrl = (rel) => `${API}/file/${rel.split("/").map(encodeURIComponent).join("/")}`;
const TYPE_LABEL = { rule: "규칙", image: "이미지", web: "웹", document: "문서", writing: "글", skill: "스킬", ai: "AI" };
const KIND_LABEL = { defaults: "기본값", principle: "원칙", set: "세트", reference: "레퍼런스", unregistered: "미등록", agent: "에이전트", chat: "채팅형", skill: "라이브러리 보관" };

const state = { graph: null, cy: null, selected: null, target: localGet("target") || "claude_desktop", authorTarget: localGet("authorTarget") || "codex", pkg: null, author: null, opts: { trim: false, strip: false, all: false } };

function localGet(k) { try { return localStorage.getItem("am." + k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem("am." + k, v); } catch {} }
function toast(msg, action) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
  if (action) { const b = document.createElement("button"); b.textContent = action.label; b.onclick = () => { action.run(); t.remove(); }; t.appendChild(b); }
  document.body.appendChild(t); setTimeout(() => t.remove(), action ? 8000 : 2600);
}
async function api(path, opts) {
  const r = await fetch(API + path, opts);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

async function waitForServer() {
  for (let i = 0; i < 60; i++) {
    try {
      const h = await api("/api/health");
      if (h.version !== APP_VERSION) {
        $("#detail").innerHTML = `<div class="empty"><p class="empty-title">예전 버전 서버에 연결되어 있습니다</p><p>서버 ${esc(h.version || "0.1")} / 화면 ${APP_VERSION}<br>앱 창을 모두 닫고, 남아 있는 서버 창도 닫은 뒤 start.bat을 다시 실행해 주세요.<br>(start.bat이 이전 서버를 자동으로 정리합니다)</p></div>`;
        throw new Error("version mismatch");
      }
      return;
    } catch (e) { if (e.message === "version mismatch") throw e; await new Promise((r) => setTimeout(r, 500)); }
  }
  $("#detail").innerHTML = `<div class="empty"><p class="empty-title">서버에 연결할 수 없습니다</p><p>start.bat을 다시 실행해 주세요.</p></div>`;
  throw new Error("server unreachable");
}

/* ---------------------------------------------------------------- 그래프 */
function nodeClasses(n) {
  const c = [];
  if (n.type === "rule") c.push("rule", n.kind);
  else if (n.type === "ai") c.push("ai", n.kind);
  else if (n.type === "installed_skill") { c.push("skillpkg", "scope-" + n.kind); if (n.managed) c.push("managed"); if (n.stale) c.push("stale"); if (n.edited) c.push("edited"); if (n.tool) c.push("toolskill"); }
  else if (n.type === "skill" && n.kind === "skill") c.push("libskill");
  else c.push("ref", n.type);
  if (n.kind === "unregistered") c.push("unregistered");
  if (n.kind === "output") c.push("output");
  if (n.error) c.push("error");
  if (n.sensitivity === "internal") c.push("internal");
  if (n.image) c.push("img");
  if (n.new) c.push("fresh");
  return c.join(" ");
}

async function loadGraph() {
  const g = await api("/api/graph");
  state.graph = g;
  $("#root").textContent = g.root;
  renderIssues(g.issues);
  renderTopbar(g);
  const css = getComputedStyle(document.documentElement);
  const v = (n) => css.getPropertyValue(n).trim();
  const showAi = $("#show-ai").checked;
  const showSkills = $("#show-skills").checked;
  const nodes = g.nodes.filter((n) => (showAi || n.type !== "ai") && (showSkills || n.type !== "installed_skill"));
  const ids = new Set(nodes.map((n) => n.id));
  const els = [
    ...nodes.map((n) => ({ data: { ...n, bg: n.image ? fileUrl(n.file) : "" }, classes: nodeClasses(n) })),
    ...g.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ data: e, classes: e.kind + (e.id.startsWith("drop:") ? " drop" : e.id.startsWith("auth:") ? " auth" : "") })),
  ];
  // 위치: 현재 화면 > 저장된 layout.json > 이웃 근처 자동 배치 > 전체 자동 배치
  const saved = g.layout || {};
  const live = state.cy ? Object.fromEntries(state.cy.nodes().map((n) => [n.id(), { ...n.position() }])) : {};
  const positions = { ...saved, ...live };
  if (state.cy) state.cy.destroy();
  const layout = { name: "breadthfirst", directed: true, roots: "#_defaults", spacingFactor: 1.1, padding: 40 };
  const known = nodes.filter((n) => positions[n.id]).length;
  state.cy = cytoscape({
    container: $("#graph"), elements: els, wheelSensitivity: 0.25,
    style: [
      { selector: "node", style: { label: "data(label)", "font-family": v("--font"), "font-size": 11, color: v("--ink"), "text-valign": "bottom", "text-margin-y": 6, "text-wrap": "wrap", "text-max-width": 120 } },
      { selector: "node.rule", style: { shape: "round-rectangle", width: 132, height: 40, "background-color": v("--accent"), color: "#fff", "text-valign": "center", "text-margin-y": 0, "font-weight": 600, "font-size": 12 } },
      { selector: "node.principle", style: { "background-opacity": 0.75 } },
      { selector: "node.defaults", style: { "background-color": v("--defaults"), "border-width": 1.5, "border-style": "dashed", "border-color": v("--muted") } },
      { selector: "node.ref", style: { shape: "ellipse", width: 58, height: 58, "background-color": "#d9cdee", "border-width": 2, "border-color": v("--line") } },
      { selector: "node.img", style: { "background-image": "data(bg)", "background-fit": "cover", "background-color": "#fff" } },
      { selector: "node.writing", style: { "background-color": "#f4e2b8" } },
      { selector: "node.web", style: { "background-color": "#bcd9f2" } },
      { selector: "node.document", style: { "background-color": "#e6d3c3" } },
      { selector: "node.output", style: { "border-color": v("--warn"), "border-width": 3, "border-style": "double" } },
      { selector: "node.unregistered", style: { "border-style": "dashed", "border-width": 2.5, "border-color": v("--muted"), "background-opacity": 0.45, "background-image-opacity": 0.5, color: v("--muted") } },
      { selector: "node.libskill", style: { shape: "tag", width: 50, height: 38, "background-color": "#e6f4ec", "border-width": 2, "border-color": v("--accent"), color: v("--accent"), "font-size": 11, "font-weight": 700, "text-valign": "bottom", "text-margin-y": 6 } },
      { selector: "node.skillpkg", style: { shape: "tag", width: 44, height: 34, "background-color": "#efe7fb", "border-width": 2, "border-color": "#8a6cc9", color: "#6b4fb0", "font-size": 10, "font-weight": 600 } },
      { selector: "node.skillpkg.scope-personal", style: { "background-color": "#e8f1fb", "border-color": "#4f86c6", color: "#3a6aa3" } },
      { selector: "node.skillpkg.toolskill", style: { "border-style": "double", "border-width": 4 } },
      { selector: "node.skillpkg.stale", style: { "border-style": "dashed", "border-color": "#d99a1c" } },
      { selector: "node.skillpkg.edited", style: { "border-color": "#c94b4b" } },
      { selector: "node.ai", style: { shape: "diamond", width: 46, height: 46, "background-color": v("--panel"), "border-width": 2, "border-color": v("--warn"), color: v("--warn"), "font-weight": 600 } },
      { selector: "node.fresh", style: { "underlay-color": "#f0b429", "underlay-opacity": 0.4, "underlay-padding": 7, "underlay-shape": "round-rectangle" } },
      { selector: "node.ref.fresh", style: { "underlay-shape": "ellipse" } },
      { selector: "node.flash", style: { "underlay-color": v("--accent"), "underlay-opacity": 0.55, "underlay-padding": 10, "underlay-shape": "round-rectangle" } },
      { selector: "node.internal", style: { "border-color": v("--danger"), "border-width": 3, "border-style": "double" } },
      { selector: "node.error", style: { "border-color": v("--danger"), "border-width": 3 } },
      { selector: "edge", style: { width: 1.6, "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.9, "line-color": v("--ink"), "target-arrow-color": v("--ink"), "font-family": v("--font"), "font-size": 10, color: v("--muted"), "text-background-color": v("--bg"), "text-background-opacity": 1, "text-background-padding": 2 } },
      { selector: "edge.key", style: { label: "data(label)", "line-color": v("--accent"), "target-arrow-color": v("--accent") } },
      { selector: "edge.uses", style: { label: "data(label)", "line-color": v("--accent"), "target-arrow-color": v("--accent"), "target-arrow-shape": "square", "arrow-scale": 0.7 } },
      { selector: "edge.inherits", style: { width: 2.2 } },
      { selector: "edge.info", style: { "line-style": "dashed", "line-color": v("--muted"), "target-arrow-shape": "none", label: "data(label)", opacity: 0.8 } },
      { selector: "edge.drop, edge.auth", style: { "line-color": v("--warn"), color: v("--warn"), opacity: 0.7 } },
      { selector: "node.preview", style: { "border-style": "dashed", "border-width": 2.5, "border-color": v("--accent"), "background-opacity": 0.25, "background-color": v("--accent"), color: v("--accent") } },
      { selector: "edge.preview", style: { "line-style": "dashed", "line-color": v("--accent"), "target-arrow-color": v("--accent"), label: "data(label)" } },
      { selector: "node.droptarget", style: { "overlay-color": v("--accent"), "overlay-opacity": 0.3, "overlay-padding": 10 } },
      { selector: ".faded", style: { opacity: 0.18 } },
      { selector: ".chain", style: { "border-width": 3, "border-color": "#f0b429" } },
      { selector: ".impact", style: { "border-width": 3, "border-color": v("--warn"), "border-style": "dashed" } },
      { selector: ".sens", style: { "border-width": 4, "border-color": v("--danger") } },
      { selector: "node:selected", style: { "overlay-color": v("--accent"), "overlay-opacity": 0.15, "overlay-padding": 6 } },
    ],
    layout: { name: "preset", positions: (n) => positions[n.id()] || { x: 0, y: 0 }, fit: !state.fitted },
  });
  if (!known) { state.cy.layout(layout).run(); saveLayout(true); }
  else placeMissing(positions);
  if (!state.fitted) { state.cy.fit(undefined, 40); state.fitted = true; }
  state.cy.on("dragfree", "node", () => saveLayout());
  state.cy.on("tap", "node", (ev) => select(ev.target.id()));
  state.cy.on("tap", (ev) => { if (ev.target === state.cy) clearHighlight(); });
  if ($("#outcome-dialog").open) previewOutcome();
  if (state.selected && g.nodes.some((n) => n.id === state.selected)) select(state.selected, true);
  else if (state.selected) { state.selected = null; $("#detail").innerHTML = emptyHtml(); }
}

/* 위치 저장 (layout.json) */
function placeMissing(positions) {
  const missing = state.cy.nodes().filter((n) => !positions[n.id()]);
  if (!missing.length) return;
  const bb = state.cy.nodes().filter((n) => positions[n.id()]).boundingBox();
  let k = 0;
  missing.forEach((n) => {
    const nb = n.neighborhood("node").filter((m) => positions[m.id()]);
    if (nb.length) {
      const p = nb[0].position();
      const siblings = missing.filter((m) => m.neighborhood("node").contains(nb[0])).toArray().indexOf(n);
      n.position({ x: p.x + 110 * (siblings + 1), y: p.y + 90 });
    } else {
      n.position({ x: (bb.x1 || 0) + 120 * (k % 6), y: (bb.y2 || 0) + 120 + 110 * Math.floor(k / 6) });
      k++;
    }
  });
  saveLayout();
}

let layoutTimer;
function saveLayout(replace) {
  clearTimeout(layoutTimer);
  layoutTimer = setTimeout(() => {
    if (!state.cy) return;
    const positions = Object.fromEntries(state.cy.nodes().filter((n) => !n.hasClass("preview")).map((n) => [n.id(), n.position()]));
    post("/api/layout", { positions, replace: !!replace }).catch(() => {});
  }, replace ? 0 : 400);
}

function resetLayout() {
  state.cy.layout({ name: "breadthfirst", directed: true, roots: "#_defaults", spacingFactor: 1.1, padding: 40 }).run();
  saveLayout(true);
  toast("배치를 초기화했습니다");
}

function clearHighlight() { state.cy && state.cy.elements().removeClass("faded chain impact sens"); }

function highlight(chain, impact, sens) {
  clearHighlight();
  const keep = state.cy.collection();
  chain.forEach((id) => keep.merge(state.cy.getElementById(id)));
  impact.forEach((id) => keep.merge(state.cy.getElementById(id)));
  const sel = state.cy.getElementById(state.selected);
  keep.merge(sel).merge(sel.outgoers("node")).merge(sel.incomers("node"));
  const edges = keep.edgesWith(keep);
  state.cy.elements().not(keep.union(edges)).addClass("faded");
  chain.forEach((id) => id !== state.selected && state.cy.getElementById(id).addClass("chain"));
  impact.forEach((id) => state.cy.getElementById(id).addClass("impact"));
  (sens || []).forEach((id) => state.cy.getElementById(id).addClass("sens"));
}

function renderIssues(issues) {
  const box = $("#issues");
  box.innerHTML = issues.slice(0, 6).map((i) => `<div class="issue ${i.level}"><b>${esc(i.node)}</b> · ${esc(i.message)}</div>`).join("") + (issues.length > 6 ? `<div class="issue warning">외 ${issues.length - 6}건</div>` : "");
}

function renderTopbar(g) {
  const btn = $("#changes-btn");
  const changed = g.changes.filter((c) => c.status === "changed");
  btn.hidden = !changed.length;
  btn.textContent = `변경 감지: ${changed.length}개`;
  const up = $("#defaults-btn");
  up.hidden = !g.defaults_updates.length;
  up.textContent = `앱 기본값 업데이트 ${g.defaults_updates.length}개`;
  $("#trash-btn").textContent = `휴지통${g.trash_count ? ` ${g.trash_count}` : ""}`;
  const unreg = g.nodes.filter((n) => n.kind === "unregistered");
  const ub = $("#unreg-btn");
  ub.hidden = !unreg.length;
  ub.textContent = `미등록 ${unreg.length}개`;
  const done = (g.requests || []).filter((r) => r.status === "received");
  const rb = $("#result-btn");
  rb.hidden = !done.length;
  rb.textContent = `AI 결과 도착 ${done.length}개`;
  rb.onclick = () => (done[0].kind === "tidy" ? openTidy(done[0].node) : openResult(done[0].id));
  const b = g.backup || {};
  $("#backup").textContent = b.repo ? "Git 기록 중" : b.inside_other ? "버전 보관 (상위 폴더가 Git)" : b.available === false ? "버전 보관 (Git 없음)" : "버전 보관";
  $("#backup").title = b.error ? "Git 오류: " + b.error : "변경은 자동으로 기록되어 되돌릴 수 있습니다";
}

/* ---------------------------------------------------------------- 상세 패널 */
const emptyHtml = () => `<div class="empty"><p class="empty-title">노드를 선택하세요</p><p>규칙 노드(사각형)를 고르면 최종 규칙과 드롭 영역이 열립니다.<br>점선 원은 아직 등록되지 않은 파일입니다. 고르면 AI로 등록할 수 있습니다.<br>파일이나 zip을 이 창에 끌어다 놓으면 라이브러리에 추가됩니다.</p></div>`;

async function select(id, keepScroll) {
  if (!keepScroll && state.selected !== id) $("#detail").scrollTop = 0;
  state.selected = id;
  state.cy.$(":selected").unselect();
  state.cy.getElementById(id).select();
  const n = await api("/api/node/" + encodeURIComponent(id));
  if (id !== state.selected) return;
  if (n.type === "ai") return renderAi(n);
  if (n.type === "installed_skill") return renderSkill(n.skill);
  if (n.type === "skill" && n.kind === "skill") return renderLibSkill(n);
  if (n.type === "rule") return renderRule(n);
  if (n.kind === "unregistered") return renderUnregistered(n);
  highlight([], n.used_by || [], []);
  renderRef(n);
}

function headHtml(n, deletable = true) {
  const chips = [`<span class="chip accent">${TYPE_LABEL[n.type] || n.type}</span>`, `<span class="chip">${KIND_LABEL[n.kind] || n.kind}</span>`];
  if (n.sensitivity === "internal") chips.push(`<span class="chip internal">민감</span>`);
  if (n.authored_by) chips.push(`<span class="chip warnchip">작성: ${esc(state.graph.targets[n.authored_by]?.label || n.authored_by)}</span>`);
  if (n.kind === "output") chips.push(`<span class="chip warnchip">산출물${n.is_golden ? " · 골든 샘플" : ""}</span>`);
  if (n.type !== "ai" && n.exists === false && n.kind !== "unregistered" && n.type !== "rule") chips.push(`<span class="chip internal">원본 없음</span>`);
  const del = deletable && n.kind !== "defaults" ? `<button class="ghost small danger-text" id="del-btn" title="휴지통으로 옮깁니다. 언제든 복구할 수 있습니다.">삭제</button>` : "";
  const tidyBtn = n.type === "rule" && n.kind !== "defaults" ? `<button class="ghost small" id="tidy-btn" title="이 노드 아래(상속 자식, 원칙, 레퍼런스)의 구조를 AI가 분석해 정리안을 냅니다. 반영 전에 전·후를 보여 줍니다">하위 정리 (AI)</button>` : "";
  const exp = deletable && n.kind !== "unregistered" ? `<button class="ghost small" id="export-node-btn" title="AI 대화창에 붙여넣으면 그 대화의 결과물을 이 노드로 보낼 zip을 만들어 줍니다">이 노드로 내보내기 명령 복사</button>` : "";
  return `<div class="head"><div class="head-row"><div>${chips.join("")}</div><div class="row" style="margin:0">${tidyBtn}${exp}${del}</div></div><h1>${esc(n.title)}</h1><div class="meta-line">${esc(n.id)} · ${esc(n.path || n.file || "")}</div></div>` +
    (n.issues || []).map((i) => `<div class="issue ${i.level}" style="margin-bottom:6px">${esc(i.message)}</div>`).join("");
}

function previewHtml(n) {
  const url = n.exists ? fileUrl(n.file) : "";
  if (!n.exists) return `<div class="preview"><pre>원본 파일이 없습니다.</pre></div>`;
  if (n.type === "image") return `<div class="preview"><img src="${url}" alt=""></div>`;
  if (n.type === "web") return `<div class="preview"><iframe sandbox="allow-scripts" src="${url}"></iframe></div>`;
  if (n.type === "document") return `<div class="preview"><iframe src="${url}"></iframe></div>`;
  return `<div class="preview"><pre>${esc(n.text || "(비어 있음)")}</pre></div>`;
}

function versionsHtml(n) {
  const vs = n.versions || [];
  const hist = n.history || [];
  return `<div class="adv"><h3>이전 버전</h3>${vs.length ? `<ul class="vlist">${vs.map((v, i) => `<li><span class="meta-line">${esc(v.ts.replace("T", " "))} · ${esc(v.rel)}${i === 0 ? " · 현재" : ""}</span>${i === 0 ? "" : ` <button class="ghost small" data-restore-rel="${esc(v.rel)}" data-restore-ver="${esc(v.id)}">이 버전으로</button>`}</li>`).join("")}</ul>` : '<div class="muted">아직 보관된 버전이 없습니다.</div>'}
    ${hist.length ? `<h3>드롭 기록</h3><ul class="vlist">${hist.map((h) => `<li class="meta-line">${esc(h.ts.replace("T", " "))} · ${esc(state.graph.targets[h.target]?.label || h.target)} · ${h.method === "drag" ? "드래그(놓음 확인)" : "복사"}${h.kind === "author" ? " · 작성 요청" : ""}</li>`).join("")}</ul>` : ""}</div>`;
}

function bindCommon(n) {
  bindGo();
  $("#del-btn")?.addEventListener("click", () => deleteNode(n));
  $("#export-node-btn")?.addEventListener("click", () => copyExport(n.id));
  $("#tidy-btn")?.addEventListener("click", () => openTidy(n.id));
  $("#promote-btn")?.addEventListener("click", () => promote(n, false));
  $("#rewrite-btn")?.addEventListener("click", () => renderUnregistered(n, true));
  document.querySelectorAll("[data-restore-rel]").forEach((b) => b.addEventListener("click", async () => {
    await post("/api/restore-version", { rel: b.dataset.restoreRel, version: b.dataset.restoreVer });
    toast("이전 버전으로 되돌렸습니다"); await loadGraph();
  }));
}

async function promote(n, overwrite) {
  const r = await post(`/api/node/${encodeURIComponent(n.id)}/promote`, { overwrite });
  if (r.needs_overwrite) {
    $("#promote-box").innerHTML = `<div class="ritem overwrite" style="cursor:default"><div><b>이미 골든 샘플이 있습니다</b><div class="ow"><span class="before">${esc(r.current)}</span> → <span class="after">ref:${esc(n.id)}</span></div><div class="ow-impact">이 변경은 ${r.impact.length}개 노드의 프롬프트를 바꿉니다: ${r.impact.map(esc).join(", ")}</div><div class="row"><button class="danger small" id="promote-yes">기존 기준 교체</button></div></div></div>`;
    $("#promote-yes").addEventListener("click", () => promote(n, true));
    return;
  }
  toast("골든 샘플로 승격했습니다"); await loadGraph(); select(n.id);
}

function renderRef(n) {
  const outInfo = n.kind === "output" ? `<div class="callout">이 결과물은 <b data-go="${esc(n.set)}" class="link">${esc(n.set_title || n.set)}</b> 세트의 산출물입니다${n.task ? ` · 작업: ${esc(n.task)}` : ""}${n.origin?.ts ? `<br><span class="meta-line">출처: ${esc(n.origin.ts.replace("T", " "))} · ${esc(state.graph.targets[n.origin.target]?.label || n.origin.target || "")} · 프롬프트 ${esc(n.origin.prompt_hash || "-")}</span>` : ""}
    ${n.is_golden ? `<br><b>이 세트의 골든 샘플입니다.</b>` : `<div class="row"><button class="small" id="promote-btn">골든 샘플로 승격</button></div><div id="promote-box"></div>`}</div>` : "";
  const missing = n.exists === false ? `<div class="oc-attach">원본이 없습니다. 파일을 이 노드에 끌어다 놓으면 <b>원본으로 채우기</b>를 고를 수 있습니다. 그때까지는 드롭할 때 설명(spec)이 첨부를 대신합니다.</div>` : "";
  $("#detail").innerHTML = headHtml(n) + outInfo + missing + (n.exists === false ? "" : previewHtml(n)) +
    (n.keywords?.length ? `<h3>관련어 (검색용)</h3><div>${n.keywords.map((k) => `<span class="chip">${esc(k)}</span>`).join("")}</div>` : "") +
    `<div class="row"><button class="ghost small" id="rewrite-btn">AI로 설명 ${n.spec ? "다시 " : ""}쓰기</button></div>` +
    `<h3>설명</h3><div>${esc(n.summary) || '<span class="muted">없음</span>'}</div>` +
    `<h3>텍스트 요약 (spec)</h3><div class="muted" style="white-space:pre-wrap">${esc(n.spec) || "없음 — 자동 덜어내기에서 텍스트로 대체할 수 없습니다."}</div>` +
    (n.check?.length ? `<h3>자가 점검</h3><ul class="checks">${n.check.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : "") +
    `<h3>사용하는 규칙 노드</h3><div>${(n.used_by || []).map((u) => `<button class="ghost" data-go="${esc(u)}">${esc(u)}</button>`).join(" ") || '<span class="muted">없음</span>'}</div>` +
    `<h3>태그</h3><div>${(n.tags || []).map((t) => `<span class="chip">${esc(t)}</span>`).join("") || '<span class="muted">없음</span>'}</div>` +
    `<div class="adv"><h3>원본</h3><div class="meta-line">${esc(n.file)} · ${(n.size / 1024).toFixed(0)}KB · 출처: ${esc(n.source)}</div>` +
    (n.extracted && Object.keys(n.extracted).length ? `<h3>추출 값</h3><pre class="code">${esc(JSON.stringify(n.extracted, null, 1))}</pre>` : "") + `</div>` +
    versionsHtml(n);
  bindCommon(n);
}

function valueCell(e) {
  if (e.kind === "ref") {
    const img = e.ref_type === "image" && e.status === "ok" ? `<img src="${fileUrl(e.ref_file)}" alt="">` : "";
    const st = e.status === "empty" ? ' <span class="chip internal">비어 있음</span>' : e.status === "missing" ? ' <span class="chip internal">없음</span>' : "";
    return `<span class="refchip" data-go="${esc(e.ref)}">${img}<span>${esc(e.ref_title || e.ref)}</span></span>${st}`;
  }
  return esc(e.value);
}

function renderRule(n) {
  const a = n.assembly;
  const rows = a ? a.entries.map((e) => `
    <tr class="${e.from_defaults ? "from-defaults" : ""}" data-key="${esc(e.key)}">
      <td class="k">${esc(e.label)}<small>${esc(e.key)}</small></td>
      <td>${valueCell(e)}
        ${e.from_defaults ? ' <span class="chip defaults">기본값</span>' : e.source !== n.id ? ` <span class="chip">← ${esc(e.source)}</span>` : ""}
        <div class="adv adv-box">중요도: ${esc(e.priority)}${e.note ? `<br>지시문(엣지): ${esc(e.note)}` : ""}${e.check?.length ? `<br>점검: ${e.check.map(esc).join(" / ")}` : ""}${e.overridden.length ? `<br>덮어쓴 값: ${e.overridden.map((o) => `${esc(o.source)}=${esc(typeof o.value === "string" ? o.value : JSON.stringify(o.value))}`).join(" → ")}` : ""}</div>
      </td>
    </tr>`).join("") : "";
  const prec = a?.precedence || [];
  const impact = n.descendants || [];
  $("#detail").innerHTML = headHtml(n) +
    (n.body ? `<div class="muted" style="white-space:pre-wrap">${esc(n.body)}</div>` : "") +
    (impact.length ? `<div class="impact">이 노드를 고치면 영향받는 노드 ${impact.length}개: ${impact.map(esc).join(", ")} (그래프에 주황 점선)</div>` : "") +
    `<h3>최종 규칙 <span class="muted" style="font-weight:400">— 회색은 기본값에서 온 값</span></h3>` +
    (a ? `<table class="rules">${rows}</table>` : `<div class="issue error">순환 상속 때문에 조립할 수 없습니다.</div>`) +
    (prec.length ? `<div class="prec">${prec.map((p) => `<div>⇅ ${esc(p.scope)}: <b>${esc(keyLabel(a, p.first))}</b> 우선, 그다음 ${esc(keyLabel(a, p.over))}<span class="adv-inline"> · 출처 ${esc(p.source)}</span></div>`).join("")}</div>` : "") +
    (a?.uses?.length ? `<h3>붙인 원칙 <span class="muted" style="font-weight:400">— 이 세트의 부품. 세트 자신의 규칙이 우선</span></h3><div>${a.uses.map((u) => `<button class="ghost small" data-go="${esc(u)}">${esc(state.graph.nodes.find((x) => x.id === u)?.label || u)}</button>`).join(" ")}</div>` : "") +
    skillsHtml(n, a) +
    (n.users?.length ? `<div class="muted" style="margin-top:6px">이 원칙을 붙인 세트: ${n.users.map((u) => `<span class="link" data-go="${esc(u)}">${esc(state.graph.nodes.find((x) => x.id === u)?.label || u)}</span>`).join(", ")}</div>` : "") +
    `<div class="adv"><h3>조립 순서 (뒤가 우선)</h3><div class="meta-line">${a ? a.chain.map(esc).join(" → ") : "-"}</div></div>` +
    (n.kind === "set" ? `<h3>산출물 ${n.outputs?.length || 0}개 <span class="muted" style="font-weight:400">— 결과 파일을 이 세트에 놓으면 기록됩니다</span></h3><div>${(n.outputs || []).slice(0, 12).map((o) => `<button class="ghost small" data-go="${esc(o.id)}">${esc(o.title)}${n.golden_current === "ref:" + o.id ? " ★" : ""}</button>`).join(" ") || '<span class="muted">아직 없습니다</span>'}</div>` : "") +
    (a && n.kind === "set" ? `<div id="skill-box"><h3>스킬로 내보내기</h3><div class="muted">불러오는 중…</div></div>` : "") +
    (a ? dropHtml() : "") + versionsHtml(n);
  bindCommon(n);
  if (a) { bindDrop(); refreshPackage(); }
  if (a && n.kind === "set") loadSkillBox(n.id);
  bindSkillAttach(n);
  highlight(a ? a.chain : [], impact, []);
}
const keyLabel = (a, k) => a.entries.find((e) => e.key === k)?.label || k;

function renderAi(n) {
  const t = n.target;
  $("#detail").innerHTML = headHtml(n, false) +
    `<div class="ai-stats"><div><b>${n.drops}</b><span>드롭</span></div><div><b>${n.author_requests}</b><span>작성 요청</span></div><div><b>${n.authored.length}</b><span>작성한 노드</span></div></div>` +
    `<h3>작성한 노드</h3><div>${n.authored.map((u) => `<button class="ghost" data-go="${esc(u)}">${esc(u)}</button>`).join(" ") || '<span class="muted">없음</span>'}</div>` +
    `<h3>최근 기록</h3>${n.history.length ? `<ul class="vlist">${n.history.map((h) => `<li class="meta-line">${esc(h.ts.replace("T", " "))} · ${esc(h.node || (h.files || []).join(", "))} · ${h.kind === "author" ? "작성 요청" : "드롭"} · ${h.method === "drag" ? "드래그" : "복사"}</li>`).join("")}</ul>` : '<div class="muted">아직 기록이 없습니다.</div>'}` +
    `<div class="adv"><h3>대상 설정 (_defaults/targets.yaml)</h3><div class="meta-line">${esc(KIND_LABEL[t.kind] || t.kind)} · 첨부 ${t.max_files}개 · 파일 ${t.max_file_mb}MB · ${t.max_chars}자 · 민감 자료 ${t.allow_internal ? "허용" : "불가"}</div></div>`;
  bindGo();
  highlight([], n.authored, []);
}

function bindGo() { document.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => select(el.dataset.go))); }

async function deleteNode(n) {
  const users = n.used_by?.length ? `\n이 노드를 쓰는 규칙: ${n.used_by.join(", ")}` : "";
  const info = await post("/api/delete", { id: n.id });
  state.selected = null;
  await loadGraph();
  $("#detail").innerHTML = emptyHtml();
  toast(`"${n.title}"을(를) 휴지통으로 옮겼습니다.${users ? " 연결된 규칙에 경고가 뜹니다." : ""}`, { label: "되돌리기", run: async () => { await post("/api/restore-trash", { id: info.trash_id }); await loadGraph(); select(n.id); } });
}

/* ---------------------------------------------------------------- 드롭 영역 (규칙 노드) */
function segHtml(current, attr) {
  return `<div class="seg">${Object.entries(state.graph.targets).map(([k, t]) => `<button ${attr}="${k}" class="${k === current ? "on" : ""}">${esc(t.label)}</button>`).join("")}</div>`;
}

function dropHtml() {
  return `<div class="drop">
    ${segHtml(state.target, "data-target")}
    <input class="oc-input" id="task" value="${esc((state.tasks || {})[state.selected] || "")}" placeholder="이번 작업 (선택): 예) AI 비서 소개 글 — 비슷한 과거 산출물이 참고 예시로 붙습니다">
    <div class="gate-row"><label class="toggle" title="켜면 AI가 결과물을 바로 만들지 않고, 이해한 내용·적용할 규칙·결과물 구성을 먼저 보여 준 뒤 승인을 기다립니다"><input type="checkbox" id="opt-gate"> 승인 게이트 <span class="muted">— AI가 계획을 먼저 보여 주고 승인을 기다림</span></label><span class="link small" id="gate-save" hidden>이 세트 기본값으로 저장</span></div>
    <div class="status" id="pkg-status">준비 중…</div>
    <div class="handle" id="handle" draggable="${TAURI ? "false" : "true"}">
      <span>⠿</span><div>여기를 잡고 AI 앱으로 끌어다 놓기<small id="handle-sub"></small></div>
    </div>
    <div class="row" id="pkg-actions"></div>
    <div id="pkg-log"></div>
    <div class="adv">
      <label class="toggle" style="margin-top:12px"><input type="checkbox" id="opt-all" ${state.opts.all ? "checked" : ""}> 덜어낼 때 세 앱 모두 같은 기준(가장 작은 한도) 적용</label>
      <h3>드롭되는 파일</h3><div id="pkg-files" class="meta-line"></div>
      <h3>프롬프트 미리보기</h3><pre class="code" id="pkg-prompt"></pre>
    </div>
  </div>`;
}

function bindDrop() {
  document.querySelectorAll("[data-target]").forEach((b) => b.addEventListener("click", () => {
    state.target = b.dataset.target; localSet("target", state.target);
    state.opts = { trim: false, strip: false, all: state.opts.all };
    document.querySelectorAll("[data-target]").forEach((x) => x.classList.toggle("on", x === b));
    refreshPackage();
  }));
  $("#opt-all").addEventListener("change", (e) => { state.opts.all = e.target.checked; refreshPackage(); });
  $("#opt-gate").addEventListener("change", (e) => { (state.gates ||= {})[state.selected] = e.target.checked; refreshPackage(); });
  $("#gate-save").addEventListener("click", async () => {
    const id = state.selected, gate = $("#opt-gate").checked;
    await post(`/api/node/${encodeURIComponent(id)}/gate`, { gate });
    delete state.gates[id];
    toast(gate ? "이 세트는 이제 기본으로 승인 게이트를 붙입니다" : "이 세트는 이제 기본으로 바로 실행 프롬프트를 만듭니다");
    await loadGraph();
  });
  let tt;
  $("#task").addEventListener("input", (e) => { (state.tasks ||= {})[state.selected] = e.target.value; clearTimeout(tt); tt = setTimeout(refreshPackage, 450); });
  bindHandle($("#handle"), () => state.pkg, (p) => ({ node: state.selected, target: p.target, kind: "prompt", prompt_hash: p.prompt_hash, task: p.task || undefined }));
}

/* 손잡이: 데스크톱은 네이티브 파일 드래그, 브라우저는 텍스트 드래그. 놓으면 히스토리에 기록 */
function bindHandle(h, getPkg, recordOf) {
  if (TAURI) {
    h.addEventListener("mousedown", (ev) => { if (ev.button === 0) startNativeDrag(getPkg(), recordOf); });
  } else {
    h.addEventListener("dragstart", (ev) => {
      const p = getPkg(); if (!p) return ev.preventDefault();
      state.outbound = true;
      ev.dataTransfer.setData("text/plain", p.clipboard);
    });
    h.addEventListener("dragend", (ev) => {
      state.outbound = false;
      const p = getPkg();
      if (p && ev.dataTransfer.dropEffect !== "none") record({ ...recordOf(p), method: "drag" });
    });
  }
}

async function record(rec) {
  try { await post("/api/history", rec); } catch {}
  loadGraph().catch(() => {});
}

async function refreshPackage() {
  const id = state.selected;
  const q = new URLSearchParams({ target: state.target, trim: state.opts.trim ? 1 : 0, strip: state.opts.strip ? 1 : 0, all: state.opts.all ? 1 : 0, task: (state.tasks || {})[id] || "" });
  const gOverride = (state.gates || {})[id];
  if (gOverride !== undefined) q.set("gate", gOverride ? "1" : "0");
  let p;
  try { p = await api(`/api/package/${encodeURIComponent(id)}?${q}`); } catch (e) { $("#pkg-status").innerHTML = `<span class="bad">${esc(e.message)}</span>`; return; }
  if (id !== state.selected) return;
  state.pkg = p;
  $("#opt-gate").checked = !!p.gate;
  $("#gate-save").hidden = gOverride === undefined || gOverride === p.set_gate;
  const L = p.limits;
  const fileCount = p.files.length, chars = p.prompt.length;
  const lines = [];
  const over = (k) => p.violations.some((v) => v.kind === k);
  lines.push(`<span class="${over("files") ? "bad" : "ok"}">파일 ${fileCount} / ${L.max_files}개</span> · <span class="${over("chars") ? "bad" : "ok"}">프롬프트 ${chars.toLocaleString()} / ${L.max_chars.toLocaleString()}자</span>`);
  p.violations.filter((v) => v.kind === "size").forEach((v) => lines.push(`<span class="bad">${esc(v.message)}</span>`));
  const ex = p.entries.filter((e) => e.key.startsWith("_example_"));
  if (ex.length) lines.push(`<span class="ok">참고 예시 ${ex.length}개: ${ex.map((e) => `${esc(e.ref_title)}${e.mode === "drop" ? " (덜어냄)" : e.mode === "text" ? " (글로)" : ""}`).join(", ")}</span>`);
  else if (p.task) lines.push(`<span class="muted">이 작업과 비슷한 과거 산출물이 없습니다</span>`);
  lines.push(p.gate ? `<span class="ok">승인 게이트: AI가 계획을 먼저 보여 주고 "승인"을 기다립니다${p.task ? "" : " · 작업이 비어 있어 작업을 말할 때까지 기다립니다"}</span>`
    : `<span class="warnline">바로 실행: 진행 방식 단락 없이 규칙만 넘어갑니다${p.task ? "" : " · 작업이 비어 있으니 붙여넣은 뒤 작업을 따로 말해 주세요"}</span>`);
  p.warnings.forEach((w) => lines.push(`<span class="warnline">⚠ ${esc(w)}</span>`));
  if (p.sensitive.length) lines.push(`<span class="${p.needs_strip ? "bad" : "warnline"}">민감 자료 포함: ${p.sensitive.map(esc).join(", ")}${p.needs_strip ? ` — ${esc(p.target_label)}에는 허용되지 않음` : ""}</span>`);
  $("#pkg-status").innerHTML = lines.map((l) => `<div>${l}</div>`).join("");
  const blocked = p.violations.length || p.needs_strip;
  $("#handle").classList.toggle("blocked", !!blocked);
  $("#handle-sub").textContent = blocked ? "한도나 민감도 문제를 먼저 해결하세요 (그래도 끌 수는 있습니다)"
    : TAURI ? `prompt.md + 레퍼런스 ${fileCount - 1}개 · 경로 포함 프롬프트는 클립보드에도 복사됩니다` : "브라우저 모드: 경로 포함 프롬프트 텍스트가 끌어집니다";
  const acts = [];
  if (p.can_trim) acts.push(`<button class="warn" id="do-trim">자동 덜어내기</button>`);
  if (p.needs_strip) acts.push(`<button class="danger" id="do-strip">민감 자료를 텍스트로 대체</button>`);
  if (state.opts.trim || state.opts.strip) acts.push(`<button class="ghost" id="do-reset">원래대로</button>`);
  acts.push(`<button id="do-copy">프롬프트 복사 (경로 포함)</button>`);
  $("#pkg-actions").innerHTML = acts.join("");
  $("#do-trim")?.addEventListener("click", () => { state.opts.trim = true; refreshPackage(); });
  $("#do-strip")?.addEventListener("click", () => { state.opts.strip = true; refreshPackage(); });
  $("#do-reset")?.addEventListener("click", () => { state.opts.trim = state.opts.strip = false; refreshPackage(); });
  $("#do-copy").addEventListener("click", () => copy(p.clipboard).then(() => { toast("클립보드에 복사했습니다"); record({ node: id, target: p.target, kind: "prompt", method: "copy", prompt_hash: p.prompt_hash, task: p.task || undefined }); }));
  $("#pkg-log").innerHTML = p.log.length ? `<ul class="log">${p.log.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : "";
  $("#pkg-files").innerHTML = p.files.map(esc).join("<br>");
  $("#pkg-prompt").textContent = p.prompt;
  document.querySelectorAll(".rules tr").forEach((tr) => {
    const e = p.entries.find((x) => x.key === tr.dataset.key);
    tr.classList.toggle("dropped", e && e.mode === "drop");
  });
  const node = await api("/api/node/" + encodeURIComponent(id));
  if (id === state.selected) highlight(p.chain, node.descendants || [], p.sensitive);
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); }
  catch { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
}

async function startNativeDrag(p, recordOf) {
  if (!p) return;
  copy(p.clipboard).catch(() => {});
  state.outbound = true;
  const { invoke, Channel } = TAURI.core;
  const ch = new Channel();
  ch.onmessage = (m) => {
    state.outbound = false;
    if (m.result === "Dropped") { toast(`${p.target_label}(으)로 ${p.files.length}개 파일을 넘겼습니다`); record({ ...recordOf(p), method: "drag" }); }
  };
  try {
    await invoke("plugin:drag|start_drag", { item: p.files, image: p.drag_icon, options: { mode: "copy" }, onEvent: ch });
  } catch (e) { state.outbound = false; toast("드래그 실패: " + e); }
}

/* ---------------------------------------------------------------- AI로 등록하기 (미등록 노드) */
function renderUnregistered(n, rewrite) {
  const all = state.graph.nodes.filter((x) => x.kind === "unregistered");
  const ex = n.extracted || {};
  const exRows = Object.entries(ex).filter(([k]) => !["file", "kind", "bytes"].includes(k)).slice(0, 12)
    .map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td class="meta-line">${esc(typeof v === "object" ? JSON.stringify(v) : v)}</td></tr>`).join("");
  $("#detail").innerHTML = headHtml(n) + previewHtml(n) +
    (rewrite ? `<div class="callout">AI가 이 노드의 설명(spec, 점검 목록, 태그, 관련어)을 다시 씁니다. id와 연결은 그대로 유지됩니다. <button class="ghost small" data-go="${esc(n.id)}">돌아가기</button></div>`
      : `<div class="callout">아직 등록되지 않은 파일입니다. 아래에서 AI를 고르고 손잡이를 끌어다 놓으면, 그 AI가 규격대로 <code>.meta.yaml</code>을 만들어 줍니다.</div>`) +
    `<h3>앱이 뽑은 값 (extract.py)</h3>${exRows ? `<table class="rules">${exRows}</table>` : '<div class="muted">뽑을 값이 없습니다.</div>'}` +
    `<div class="drop">
      ${segHtml(state.authorTarget, "data-atarget")}
      <textarea id="author-note" rows="2" placeholder="요청 (선택): 예) 블로그 차트 스타일로 등록해 줘. 세트도 하나 만들어 줘">${esc((state.notes || {})[n.id] || "")}</textarea>
      ${all.length > 1 ? `<label class="toggle"><input type="checkbox" id="author-all"> 미등록 파일 ${all.length}개 한 번에</label>` : ""}
      <div class="status" id="author-status"></div>
      <div class="handle" id="author-handle" draggable="${TAURI ? "false" : "true"}"><span>⠿</span><div>AI로 등록하기: 끌어다 놓기<small id="author-sub"></small></div></div>
      <div class="row"><button class="primary" id="author-local" title="이 컴퓨터의 Claude Code·Codex가 바로 .meta.yaml을 씁니다">로컬 AI로 등록</button><button id="author-copy">작성 프롬프트 복사</button></div>
      <div class="adv"><h3>작성 프롬프트 미리보기</h3><pre class="code" id="author-prompt"></pre></div>
    </div>`;
  bindCommon(n);
  document.querySelectorAll("[data-atarget]").forEach((b) => b.addEventListener("click", () => {
    state.authorTarget = b.dataset.atarget; localSet("authorTarget", state.authorTarget);
    document.querySelectorAll("[data-atarget]").forEach((x) => x.classList.toggle("on", x === b));
    refreshAuthor(n);
  }));
  let timer;
  $("#author-note").addEventListener("input", (e) => { (state.notes ||= {})[n.id] = e.target.value; clearTimeout(timer); timer = setTimeout(() => refreshAuthor(n), 400); });
  $("#author-all")?.addEventListener("change", () => refreshAuthor(n));
  bindHandle($("#author-handle"), () => state.author, (p) => ({ target: p.target, kind: "author", files: p.rels, prompt_hash: p.prompt_hash }));
  $("#author-copy").addEventListener("click", () => {
    const p = state.author; if (!p) return;
    copy(p.clipboard).then(() => { toast("작성 프롬프트를 복사했습니다"); record({ target: p.target, kind: "author", method: "copy", files: p.rels, prompt_hash: p.prompt_hash }); });
  });
  $("#author-local").addEventListener("click", () => ensureLocalAI("파일 등록", () => {
    const files = state.author?.rels || [n.path];
    runLocal({ kind: "authoring", files, note: (state.notes || {})[n.id] || "" }, { label: `파일 등록 (${files.length}개)`,
      onDone: async () => { await loadGraph(); toast("등록을 마쳤습니다. 새 노드를 확인하세요"); } });
  }));
  highlight([], [], []);
  refreshAuthor(n);
}

async function refreshAuthor(n) {
  const all = $("#author-all")?.checked;
  const rels = all ? state.graph.nodes.filter((x) => x.kind === "unregistered").map((x) => x.file) : [n.file];
  const q = new URLSearchParams({ target: state.authorTarget, files: rels.join("|"), note: $("#author-note")?.value || "" });
  let p;
  try { p = await api(`/api/authoring?${q}`); } catch (e) { $("#author-status").innerHTML = `<span class="bad">${esc(e.message)}</span>`; return; }
  if (state.selected !== n.id) return;
  state.author = p;
  $("#author-status").innerHTML = p.agent
    ? `<div class="ok">${esc(p.target_label)}: 에이전트가 내 PC에서 extract.py를 실행하고 원본 옆에 파일을 씁니다. 저장되면 자동으로 반영됩니다.</div>`
    : `<div class="ok">${esc(p.target_label)}: prompt.md + extract.py + 원본 ${p.rels.length}개를 넘깁니다. AI가 준 <b>assets-import.zip</b>을 이 창에 끌어다 놓으세요.</div>`;
  $("#author-sub").textContent = TAURI ? `파일 ${p.files.length}개` : "브라우저 모드: 프롬프트 텍스트가 끌어집니다";
  $("#author-prompt").textContent = p.prompt;
}

/* ---------------------------------------------------------------- 가져오기 (창에 끌어다 놓기) */
function showDropOverlay(on) { $("#drop-overlay").hidden = !on; }

function nodeAt(clientX, clientY) {
  if (!state.cy) return null;
  const r = $("#graph").getBoundingClientRect();
  const x = clientX - r.left, y = clientY - r.top;
  if (x < 0 || y < 0 || x > r.width || y > r.height) return null;
  const hit = state.cy.nodes().filter((n) => { const b = n.renderedBoundingBox(); return x >= b.x1 && x <= b.x2 && y >= b.y1 && y <= b.y2; });
  return hit.length ? hit[hit.length - 1] : null;
}

function hoverDrop(clientX, clientY) {
  state.cy?.nodes().removeClass("droptarget");
  const n = nodeAt(clientX, clientY);
  if (n && !n.id().startsWith("ai:")) n.addClass("droptarget");
  $("#drop-overlay-text").textContent = n && !n.id().startsWith("ai:")
    ? `"${n.data("label")}"에 놓으면 AI로 노드를 추가하도록 요청합니다` : "여기에 놓으면 라이브러리에 추가됩니다 (폴더는 AI 요청)";
}

const outcomeOn = () => localGet("showOutcome") !== "0"; // 예상 결과 창 (기본 켜짐)

async function handleDrop(body) {
  showDropOverlay(false);
  state.cy?.nodes().removeClass("droptarget");
  // 결과 확인 중이면 놓은 파일은 원본 짝 맞추기로 (Q65)
  if (state.outcome?.case === "result" && $("#outcome-dialog").open && !(body.paths || []).some((p) => /\.zip$/i.test(p)) && !(body.uploads || []).some((u) => /\.zip$/i.test(u.name))) {
    try {
      const pv = await post(`/api/request/${state.outcome.sid}/attach`, { paths: body.paths, uploads: body.uploads });
      const missing = pv.items.filter((i) => i.missing_original).length;
      state.outcome.details = pv; renderOutcome();
      toast(missing && pv.unassigned?.length ? `파일을 받았습니다. 자동으로 짝을 정하지 못한 ${missing}개는 항목 옆에서 골라 주세요` : missing ? `파일을 받았습니다. 원본 없는 항목이 ${missing}개 남았습니다` : "원본을 모두 짝지었습니다");
    } catch (e) { toast("원본 추가 실패: " + e.message); }
    return;
  }
  let o;
  try { o = await post("/api/stage", body); } catch (e) { return toast("놓기 실패: " + e.message); }
  if (o.case === "tidy") return showTidyPreview(o.sid, o.details);
  if (!outcomeOn() && o.simple) {
    const opt = o.options.find((x) => x.default) || o.options[0];
    return commitOutcome(o, opt.id, {});
  }
  openOutcome(o);
}

function setupInbound() {
  if (TAURI) {
    const { listen } = TAURI.event;
    const toCss = (pos) => ({ x: (pos?.x || 0) / (window.devicePixelRatio || 1), y: (pos?.y || 0) / (window.devicePixelRatio || 1) });
    listen("tauri://drag-enter", (e) => { if (state.outbound) return; showDropOverlay(true); const p = toCss(e.payload?.position); hoverDrop(p.x, p.y); });
    listen("tauri://drag-over", (e) => { if (state.outbound) return; const p = toCss(e.payload?.position); hoverDrop(p.x, p.y); });
    listen("tauri://drag-leave", () => { showDropOverlay(false); state.cy?.nodes().removeClass("droptarget"); });
    listen("tauri://drag-drop", (e) => {
      if (state.outbound) { showDropOverlay(false); return; } // 내가 내보내는 드래그가 창 위에 놓인 경우
      const p = toCss(e.payload?.position);
      const n = nodeAt(p.x, p.y);
      handleDrop({ paths: e.payload?.paths || [], node: n ? n.id() : null });
    });
  } else {
    document.addEventListener("dragover", (ev) => {
      if (state.outbound || !ev.dataTransfer?.types?.includes("Files")) return;
      ev.preventDefault(); showDropOverlay(true); hoverDrop(ev.clientX, ev.clientY);
    });
    document.addEventListener("dragleave", (ev) => { if (ev.relatedTarget === null) { showDropOverlay(false); state.cy?.nodes().removeClass("droptarget"); } });
    document.addEventListener("drop", async (ev) => {
      if (!ev.dataTransfer?.items?.length) return;
      ev.preventDefault();
      const n = nodeAt(ev.clientX, ev.clientY);
      const entries = [...ev.dataTransfer.items].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
      const folder = entries.some((e) => e.isDirectory);
      const files = [];
      const walk = (entry, prefix) => new Promise((res) => {
        if (entry.isFile) return entry.file((f) => { files.push({ f, name: prefix + f.name }); res(); }, res);
        const reader = entry.createReader(), all = [];
        const read = () => reader.readEntries(async (batch) => {
          if (!batch.length) { for (const c of all) await walk(c, prefix + entry.name + "/"); return res(); }
          all.push(...batch); read();
        }, res);
        read();
      });
      for (const e of entries) await walk(e, "");
      const uploads = await Promise.all(files.slice(0, 60).map(({ f, name }) => new Promise((res) => {
        const r = new FileReader();
        r.onload = () => res({ name, b64: String(r.result).split(",")[1] || "" });
        r.readAsDataURL(f);
      })));
      handleDrop({ uploads, folder, node: n ? n.id() : null });
    });
  }
}

/* ---------------------------------------------------------------- 예상 결과 창 (v3.3) */
const TYPE_ICON = { image: "🖼", web: "</>", document: "📄", writing: "✎" };
const fmtVal = (v) => (typeof v === "string" ? v : JSON.stringify(v));
const itemTitle = (id) => { const it = state.outcome?.details?.items?.find((x) => x.id === id); return it ? `새 노드 '${it.title || it.node_id}'` : id; };

function openOutcome(o) {
  state.outcome = o;
  state.outcomeOpt = (o.options.find((x) => x.default) || o.options[0])?.id;
  state.outcomeStep = "choose";
  renderOutcome();
  if (!$("#outcome-dialog").open) $("#outcome-dialog").show();
}

async function openResult(rid) {
  const pv = await api(`/api/request/${rid}/preview`);
  const r = pv.request;
  openOutcome({ sid: rid, case: "result", what: { zip: "AI 결과" }, where: { node: r.node, title: r.node_title, kind: r.node_kind },
    options: [{ id: "apply", label: "체크한 항목 적용", desc: "AI가 만든 노드와 연결 중 체크한 것만 적용합니다.", default: true }], details: pv, simple: false });
}

function whatText(o) {
  if (o.case === "result") return `AI 결과 zip${o.what.files ? ` + 파일 ${o.what.files}개` : ""}`;
  if (o.case === "zip") return `zip 파일 (${esc(o.what.zip)})`;
  return `${o.what.folder ? "폴더 · " : ""}파일 ${o.what.files}개 (${(o.what.types || []).map(esc).join(", ")})`;
}

function renderOutcome() {
  const o = state.outcome;
  if (!o) return;
  const where = o.where?.node ? `<b>${esc(o.where.title || o.where.node)}</b> <span class="chip">${esc(KIND_LABEL[o.where.kind] || o.where.kind || "")}</span>` : "빈 공간";
  let body = "";
  if (state.outcomeStep === "send") body = sendHtml();
  else if (o.case === "result") body = resultHtml(o.details);
  else body = choiceHtml(o);
  $("#outcome-body").innerHTML = `
    <div class="oc-row"><span class="oc-k">무엇을</span><span>${whatText(o)}</span></div>
    <div class="oc-row"><span class="oc-k">어디에</span><span>${where}</span></div>
    ${body}`;
  $("#outcome-ok").hidden = state.outcomeStep === "send";
  $("#outcome-ok").textContent = o.case === "result" ? "체크한 항목 적용" : state.outcomeOpt?.startsWith("ai_") ? "요청 만들기" : "확인";
  bindOutcome();
  previewOutcome();
}

function choiceHtml(o) {
  const d = o.details || {};
  const opt = o.options.find((x) => x.id === state.outcomeOpt) || o.options[0];
  const cards = o.options.map((x) => `<label class="oc-opt ${x.id === state.outcomeOpt ? "on" : ""}"><input type="radio" name="oc-opt" value="${esc(x.id)}" ${x.id === state.outcomeOpt ? "checked" : ""}>
    <div><b>${esc(x.label)}</b>${x.default ? ' <span class="chip">추천</span>' : ""}<div class="muted">${esc(x.desc)}</div></div></label>`).join("");
  let extra = "";
  if (opt.id === "output") {
    const drops = d.recent_drops || [];
    extra = `<h3>어떤 드롭으로 만든 결과인가요? <span class="muted" style="font-weight:400">— 출처로 연결됩니다</span></h3>` +
      (drops.length ? drops.map((h, i) => `<label class="oc-src"><input type="radio" name="oc-drop" value="${esc(h.ts)}" ${i === 0 ? "checked" : ""}> ${esc(h.ts.replace("T", " "))} · ${esc(state.graph.targets[h.target]?.label || h.target)} · ${h.task ? esc(h.task) : '<span class="muted">작업 설명 없음</span>'}</label>`).join("")
        + `<label class="oc-src"><input type="radio" name="oc-drop" value=""> 출처 없이 기록</label>`
        : `<div class="muted">이 세트의 드롭 기록이 없습니다. 출처 없이 기록됩니다.</div>`) +
      `<input class="oc-input" id="oc-task" placeholder="작업 설명 (선택, 비우면 고른 드롭의 작업 설명을 씁니다)">`;
  } else if (opt.id === "golden" && d.golden_current) {
    extra = `<div class="ritem overwrite" style="cursor:default"><div><b>이미 골든 샘플이 있습니다</b><div class="ow"><span class="before">${esc(d.golden_current)}</span> → <span class="after">이 파일</span></div>
      <label class="toggle" style="margin-top:6px"><input type="checkbox" id="oc-overwrite"> 기존 기준을 이 파일로 교체 (끄면 후보로만 등록)</label></div></div>`;
  } else if (opt.id.startsWith("ai_")) {
    extra = `<div class="callout">AI에게 이렇게 요청합니다: <b>${esc(opt.id === "ai_new_set" ? "새 세트를 만들어 이 노드를 상속" : d.recommended || "")}</b><br><span class="muted">AI가 다르게 판단하면 이유를 적어 돌려주고, 결과는 적용 전에 이 창에서 다시 확인합니다.</span></div>
      <h3>보낼 AI</h3>${segHtml(state.authorTarget, "data-rtarget")}
      <textarea id="request-note" class="oc-input" rows="2" placeholder="요청 (선택): 예) 개발 블로그용 새 세트로 만들어 줘"></textarea>`;
  }
  const files = (d.files || []);
  const fileList = files.length ? `<h3>파일 ${files.length}개</h3><table class="rules">${files.map((f) => `<tr><td class="k">${TYPE_ICON[f.type] || ""} ${esc(f.name)}${f.orig !== f.name ? `<small>${esc(f.orig)}</small>` : ""}</td><td class="meta-line">${esc(f.type)} · ${(f.size / 1024).toFixed(0)}KB${f.existing ? ` · <span class="chip">이미 있음 → ${esc(f.existing)}</span>` : ""}${f.extracted?.palette?.length ? ` · ${f.extracted.palette.slice(0, 5).map((c) => `<i class="sw" style="background:${esc(c)}"></i>`).join("")}` : ""}</td></tr>`).join("")}</table>` : "";
  const warn = (d.skipped?.length || d.over_limit?.length) ? `<div class="issue warning" style="margin-top:8px">${d.skipped?.length ? `지원하지 않는 형식 ${d.skipped.length}개 제외. ` : ""}${d.over_limit?.length ? `50개를 넘어 ${d.over_limit.length}개 제외.` : ""}</div>` : "";
  return `<div class="oc-will"><span class="oc-k">이렇게 됩니다</span><div><b>${esc(opt.label)}</b> — ${esc(opt.desc)}</div></div>
    ${o.options.length > 1 ? `<h3>다른 선택</h3><div class="oc-opts">${cards}</div>` : ""}${extra}${fileList}${warn}`;
}

function resultHtml(pv) {
  const groups = [["create", "새 노드"], ["link", "놓은 노드의 하위로 자동 연결"], ["add", "규칙에 키 추가"], ["overwrite", "기존 키 덮어쓰기 (기본 꺼짐)"], ["relation", "참고 연결"], ["same", "변화 없음"]];
  const unassigned = pv.unassigned || [];
  const itemHtml = (it) => {
    const dis = it.error ? "disabled" : "";
    let main = "";
    if (it.kind === "create") {
      main = `<b>${esc(it.title || it.path)}</b> <span class="meta-line">${esc(it.node_id || "")} · ${esc(it.node_type || "")}${it.output ? " · 산출물" : ""} · ${esc(it.path)}</span>${it.summary ? `<div class="muted">${esc(it.summary)}</div>` : ""}`;
      if (it.src) main += `<div class="meta-line">원본: ${esc(it.src)}</div>`;
      if (it.missing_original && unassigned.length) main += `<div class="oc-assign">원본 고르기: <select data-assign="${esc(it.path)}"><option value="">(원본 없이 등록)</option>${unassigned.map((u) => `<option>${esc(u)}</option>`).join("")}</select></div>`;
    } else if (it.kind === "add" || it.kind === "same") main = `<b>${esc(it.node_title || it.node)}</b>에 <code>${esc(it.key)}</code> = ${esc(fmtVal(it.after))}`;
    else if (it.kind === "overwrite") main = `<b>${esc(it.node_title || it.node)}</b>의 <code>${esc(it.key)}</code><div class="ow"><span class="before">${esc(fmtVal(it.before))}</span> → <span class="after">${esc(fmtVal(it.after))}</span></div><div class="ow-impact">이 변경은 ${it.impact.length}개 노드의 프롬프트를 바꿉니다: ${it.impact.map(esc).join(", ")}</div>`;
    else if (it.kind === "relation") main = `${esc(it.from)} <span class="chip">${esc(it.type)}</span> ${esc(it.to)}`;
    else if (it.kind === "link") main = `<b>${esc(it.title || it.node_id)}</b> → <b>${esc(it.target_title)}</b> <span class="chip">${it.link === "inherits" ? "상속 (규칙이 내려감)" : it.link === "variant_of" ? "변형" : "하위 (참고)"}</span>`;
    return `<label class="ritem ${it.kind} ${it.error ? "has-error" : ""}" data-item="${esc(it.id)}">
      <input type="checkbox" ${it.checked ? "checked" : ""} ${dis} data-check="${esc(it.id)}">
      <div>${main}${it.reason ? `<div class="reason">이유: ${esc(it.reason)}</div>` : ""}
      ${it.error ? `<div class="bad">${esc(it.error)}</div>` : ""}${(it.warnings || []).map((w) => `<div class="warnline">⚠ ${esc(w)}</div>`).join("")}
      ${it.depends_on?.length ? `<div class="meta-line">필요한 항목: ${it.depends_on.map((d) => esc(itemTitle(d))).join(", ")} (빼면 이 항목도 빠짐)</div>` : ""}</div></label>`;
  };
  const missing = pv.items.filter((i) => i.missing_original).length;
  const cands = state.graph.nodes.filter((n) => n.type !== "ai" && n.kind !== "unregistered");
  const pick = !pv.request.node ? `<div class="oc-attach"><b>어느 노드 아래에 둘까요?</b> 빈 공간에 놓아서 연결할 노드가 없습니다.<br>
    <select id="oc-target"><option value="">(연결하지 않음)</option>${cands.map((n) => `<option value="${esc(n.id)}">${esc(n.label)} · ${esc(KIND_LABEL[n.kind] || n.kind)}</option>`).join("")}</select>
    <span class="muted">고르면 그 노드의 하위로 자동 연결됩니다. 다음부터는 zip을 노드 위에 놓으면 됩니다.</span></div>` : "";
  return `${pick}${pv.summary ? `<div class="callout">${esc(pv.summary)}</div>` : ""}
    ${pv.errors.map((e) => `<div class="issue error">${esc(e)}</div>`).join("")}
    ${missing ? `<div class="oc-attach">원본이 없는 항목 ${missing}개${pv.missing_declared?.length ? ` (AI가 알려 준 파일: ${pv.missing_declared.map(esc).join(", ")})` : ""}<br><b>내려받은 파일을 여러 개 한 번에 이 창에 끌어다 놓으면</b> 짝을 맞춥니다. 없어도 설명만으로 등록됩니다.</div>` : ""}
    ${groups.map(([k, label]) => { const its = pv.items.filter((i) => i.kind === k); return its.length ? `<h3 class="g-${k}">${label} ${its.length}개</h3>${its.map(itemHtml).join("")}` : ""; }).join("")}
    <div class="row"><button class="ghost danger-text small" id="oc-discard">결과 버리기</button></div>`;
}

function sendHtml() {
  const p = state.requestPkg, req = state.request;
  if (!p) return `<div class="muted">요청을 만드는 중…</div>`;
  return `<div class="status"><div class="ok">${p.agent
      ? `${esc(p.target_label)}: 에이전트가 결과를 <code>inbox/${esc(req.id)}/out/</code>에 씁니다. 다 되면 상단에 <b>"AI 결과 도착"</b>이 뜹니다.`
      : `${esc(p.target_label)}: ${p.files.length}개 파일을 넘깁니다${Object.keys(p.merged).length ? ` (한도 때문에 ${Object.keys(p.merged).map(esc).join(", ")}로 합침)` : ""}. AI가 준 <b>assets-import.zip</b>을 이 창에 끌어다 놓으세요.`}</div></div>
    <div class="handle" id="request-handle" draggable="${TAURI ? "false" : "true"}"><span>⠿</span><div>AI 앱으로 끌어다 놓기<small>${TAURI ? `파일 ${p.files.length}개` : "브라우저 모드: 프롬프트 텍스트가 끌어집니다"}</small></div></div>
    <div class="row"><button class="primary" id="request-local" title="이 컴퓨터의 Claude Code·Codex로 바로 실행하고, 끝나면 결과를 엽니다">로컬 AI로 바로 실행</button><button id="request-copy">요청 프롬프트 복사</button></div>
    <div class="adv"><h3>요청 프롬프트 미리보기</h3><pre class="code">${esc(p.prompt)}</pre></div>`;
}

function bindOutcome() {
  document.querySelectorAll('[name="oc-opt"]').forEach((r) => r.addEventListener("change", () => { state.outcomeOpt = r.value; renderOutcome(); }));
  document.querySelectorAll("[data-rtarget]").forEach((b) => b.addEventListener("click", () => {
    state.authorTarget = b.dataset.rtarget; localSet("authorTarget", state.authorTarget);
    document.querySelectorAll("[data-rtarget]").forEach((x) => x.classList.toggle("on", x === b));
  }));
  document.querySelectorAll("[data-check]").forEach((c) => c.addEventListener("change", () => { syncDeps(); previewOutcome(); }));
  document.querySelectorAll("[data-assign]").forEach((sel) => sel.addEventListener("change", async () => {
    state.outcome.details = await post(`/api/request/${state.outcome.sid}/assign`, { path: sel.dataset.assign, file: sel.value || null });
    renderOutcome();
  }));
  $("#oc-target")?.addEventListener("change", async (e) => {
    const pv = await post(`/api/request/${state.outcome.sid}/target`, { node: e.target.value || null });
    const n = state.graph.nodes.find((x) => x.id === pv.request.node);
    state.outcome.details = pv; state.outcome.where = { node: pv.request.node, title: n?.label, kind: n?.kind };
    renderOutcome();
  });
  $("#oc-discard")?.addEventListener("click", async () => { await post(`/api/request/${state.outcome.sid}/discard`); closeOutcome(); await loadGraph(); toast("결과를 버렸습니다"); });
  if (state.outcomeStep === "send" && state.requestPkg) {
    const req = state.request, p = state.requestPkg;
    const rec = (pk) => ({ node: req.node, target: pk.target, kind: "request", files: req.files.map((f) => f.name), prompt_hash: pk.prompt_hash, request: req.id });
    bindHandle($("#request-handle"), () => state.requestPkg, rec);
    $("#request-copy").addEventListener("click", () => copy(p.clipboard).then(() => { toast("요청 프롬프트를 복사했습니다"); record({ ...rec(p), method: "copy" }); }));
    $("#request-local").addEventListener("click", () => ensureLocalAI("AI에게 맡기기", () => {
      const rid = req.id;
      closeOutcome();
      runLocal({ kind: "request", request: rid, note: req.note || "" }, { label: "AI에게 맡기기", onDone: async () => { await loadGraph(); openResult(rid); } });
    }));
  }
  syncDeps();
}

function checkedIds() { return [...document.querySelectorAll("#outcome-body [data-check]")].filter((c) => c.checked && !c.disabled).map((c) => c.dataset.check); }

function syncDeps() {
  const items = state.outcome?.details?.items;
  if (!items) return;
  const on = new Set(checkedIds());
  items.forEach((it) => {
    const box = document.querySelector(`[data-check="${CSS.escape(it.id)}"]`);
    if (!box || it.error) return;
    const blocked = (it.depends_on || []).some((d) => !on.has(d));
    box.disabled = blocked;
    box.closest(".ritem").classList.toggle("blocked", blocked);
  });
}

/* 그래프에 점선으로 미리 보기 */
function previewOutcome() {
  if (!state.cy) return;
  state.cy.remove(".preview");
  const o = state.outcome;
  if (!o) return;
  const anchor = o.where?.node && state.cy.getElementById(o.where.node);
  const bb = state.cy.nodes().boundingBox();
  const cx = anchor && anchor.length ? anchor.position().x : (bb.x1 + bb.x2) / 2;
  const addNode = (id, label, cls, k, n) => state.cy.add({ group: "nodes", data: { id, label }, classes: "preview " + cls, position: { x: cx + 130 * (k - (n - 1) / 2), y: bb.y2 + 130 } });
  if (o.case === "result") {
    const on = new Set(checkedIds()), created = {};
    const news = o.details.items.filter((i) => i.kind === "create" && on.has(i.id));
    news.forEach((i, k) => { addNode("preview:" + i.node_id, i.title || i.node_id, i.node_type === "rule" ? "rule set" : "ref " + i.node_type, k, news.length); created[i.node_id] = "preview:" + i.node_id; });
    const resolve = (x) => created[x] || (state.cy.getElementById(x).length ? x : null);
    o.details.items.filter((i) => on.has(i.id) && ["add", "overwrite"].includes(i.kind)).forEach((i) => {
      const ref = typeof i.after === "string" && i.after.startsWith("ref:") ? resolve(i.after.slice(4)) : null;
      if (ref && resolve(i.node)) state.cy.add({ group: "edges", data: { id: "preview:e:" + i.id, source: i.node, target: ref, label: i.key }, classes: "preview key" });
    });
    o.details.items.filter((i) => on.has(i.id) && i.kind === "relation").forEach((i) => {
      const a = resolve(i.from), b = resolve(i.to);
      if (a && b) state.cy.add({ group: "edges", data: { id: "preview:r:" + i.id, source: a, target: b, label: i.type }, classes: "preview info" });
    });
    o.details.items.filter((i) => on.has(i.id) && i.kind === "link").forEach((i) => {
      const a = resolve(i.node_id), b = resolve(i.target);
      if (!a || !b) return;
      if (i.link === "inherits") state.cy.add({ group: "edges", data: { id: "preview:l:" + i.id, source: b, target: a, label: "상속" }, classes: "preview" });
      else state.cy.add({ group: "edges", data: { id: "preview:l:" + i.id, source: b, target: a, label: i.link === "variant_of" ? "변형" : "하위" }, classes: "preview info" });
    });
    news.filter((i) => i.output && o.where?.node).forEach((i) => state.cy.add({ group: "edges", data: { id: "preview:o:" + i.id, source: o.where.node, target: "preview:" + i.node_id, label: "산출물" }, classes: "preview info" }));
  } else if (["output", "golden", "variant", "alternative"].includes(state.outcomeOpt) && anchor && anchor.length) {
    const f = (o.details.files || [])[0];
    const label = { output: "산출물", golden: "골든 샘플", variant: "변형", alternative: "대안" }[state.outcomeOpt];
    addNode("preview:new", f ? f.name : label, "ref " + (f?.type || "image"), 0, 1);
    state.cy.add({ group: "edges", data: { id: "preview:e:new", source: o.where.node, target: "preview:new", label }, classes: "preview " + (state.outcomeOpt === "golden" ? "key" : "info") });
  }
  if (state.cy.nodes(".preview").length && !state.outcomeFitted) { state.cy.animate({ fit: { eles: state.cy.elements(), padding: 50 } }, { duration: 300 }); state.outcomeFitted = true; }
}

async function confirmOutcome() {
  const o = state.outcome;
  try {
    if (o.case === "result") {
      const keep = Object.fromEntries(state.cy.nodes(".preview").map((n) => [n.id().slice("preview:".length), n.position()]));
      const r = await commitOutcome(o, "apply", { selected: checkedIds() }, true);
      if (Object.keys(keep).length) await post("/api/layout", { positions: keep }).catch(() => {});
      return r;
    }
    const params = {};
    if (state.outcomeOpt === "output") {
      params.drop_ts = document.querySelector('[name="oc-drop"]:checked')?.value || "";
      params.task = $("#oc-task")?.value || "";
    }
    if (state.outcomeOpt === "golden") params.overwrite = !!$("#oc-overwrite")?.checked;
    const note = $("#request-note")?.value || "";
    const r = await post(`/api/stage/${o.sid}/commit`, { option: state.outcomeOpt, params });
    if (r.next === "request") {
      state.request = r.request; state.requestPkg = null; state.outcomeStep = "send"; renderOutcome();
      const q = new URLSearchParams({ target: state.authorTarget, note });
      state.requestPkg = await api(`/api/request/${r.request.id}/package?${q}`);
      renderOutcome();
      return;
    }
    afterCommit(r);
  } catch (e) { toast("실행 실패: " + e.message); }
}

async function commitOutcome(o, option, params, fromPanel) {
  const r = await post(`/api/stage/${o.sid}/commit`, { option, params });
  afterCommit(r);
  return r;
}

async function afterCommit(r) {
  closeOutcome();
  await loadGraph();
  const created = r.created || [];
  const msg = r.message || (r.applied ? `적용했습니다: ${r.applied.length}개${r.skipped?.length ? ` (제외 ${r.skipped.length}개)` : ""}` : `${created.length}개를 넣었습니다`);
  toast(msg, created.length && !r.applied ? { label: "되돌리기", run: async () => { await post("/api/undo", { created }); await loadGraph(); toast("되돌렸습니다 (휴지통에 있음)"); } } : null);
  const node = r.node || (created[0] && state.graph.nodes.find((x) => x.path === created[0] || x.file === created[0])?.id);
  if (node && state.graph.nodes.some((x) => x.id === node)) select(node);
}

function closeOutcome() {
  state.cy?.remove(".preview");
  state.outcomeFitted = false;
  if ($("#outcome-dialog").open) $("#outcome-dialog").close();
}

async function cancelOutcome() {
  const o = state.outcome;
  if (o && state.outcomeStep !== "send" && o.case !== "result") await post(`/api/stage/${o.sid}/discard`).catch(() => {});
  state.outcome = null;
  closeOutcome();
}

/* 내보내기 명령 (Q63~Q64) */
async function copyExport(nodeId) {
  try {
    const r = await api("/api/export-command" + (nodeId ? `?node=${encodeURIComponent(nodeId)}` : ""));
    await copy(r.prompt);
    record({ node: nodeId || undefined, kind: "export", method: "copy", request: r.request || undefined });
    toast(nodeId ? "내보내기 명령을 복사했습니다. AI 대화창에 붙여넣고, 받은 zip을 이 창 아무 곳에나 놓으세요"
      : "내보내기 명령을 복사했습니다. AI 대화창에 붙여넣고, 받은 zip을 넣을 노드 위에 놓으세요");
  } catch (e) { toast("복사 실패: " + e.message); }
}

/* MCP와 RAG (v3.2 13.5) */
async function openMcp() {
  const c = await api("/api/mcp-config");
  $("#mcp-body").innerHTML = `<p>Claude 데스크톱이 이 라이브러리를 직접 검색하고 조립하게 하려면, Claude 데스크톱의 설정 파일(<code>claude_desktop_config.json</code>)의 <code>mcpServers</code>에 아래 내용을 넣고 Claude 데스크톱을 다시 시작하세요.</p>
    <pre class="code">${esc(c.text)}</pre><div class="row"><button id="mcp-copy">설정 복사</button></div>
    <h3>도구</h3><ul class="checks"><li><b>search</b>: 세트·레퍼런스·산출물 검색</li><li><b>get</b>: 노드 상세</li><li><b>related</b>: 그래프 이웃</li><li><b>assemble</b>: 세트를 내 표준대로 조립 (+ 작업 설명으로 참고 예시)</li></ul>
    <h3>RAG 내보내기</h3><p class="muted">다른 RAG 도구에서 쓸 수 있도록 노드를 조각(JSONL)으로 내보냅니다. 민감 자료는 빠집니다.</p>
    <div class="row"><button id="rag-export">JSONL 내보내기</button><span class="meta-line" id="rag-result"></span></div>`;
  $("#mcp-copy").addEventListener("click", () => copy(c.text).then(() => toast("설정을 복사했습니다")));
  $("#rag-export").addEventListener("click", async () => { const r = await api("/api/rag/export"); $("#rag-result").textContent = `${r.chunks}개 조각 → ${r.path}`; });
  $("#mcp-dialog").showModal();
}

/* ---------------------------------------------------------------- 변경 감지, 휴지통, 기본값 */
async function openChanges() {
  const changes = state.graph.changes.filter((c) => c.status === "changed");
  $("#changes-list").innerHTML = changes.map((c) => `<div class="change"><div class="change-head"><b>${esc(c.id)}</b>
    <span><button class="ghost small" data-go-close="${esc(c.id)}">보기</button> <button class="small" data-approve="${esc(c.id)}">확인함</button></span></div>
    ${c.diff ? `<pre class="code">${esc(c.diff)}</pre>` : ""}</div>`).join("") || '<p class="muted">변경 없음</p>';
  document.querySelectorAll("[data-approve]").forEach((b) => b.addEventListener("click", () => approve(b.dataset.approve)));
  document.querySelectorAll("[data-go-close]").forEach((b) => b.addEventListener("click", () => { $("#changes-dialog").close(); select(b.dataset.goClose); }));
  $("#changes-dialog").showModal();
}
async function approve(id) {
  await post("/api/approve", id ? { id } : {});
  await loadGraph();
  if (state.graph.changes.some((c) => c.status === "changed")) openChanges(); else $("#changes-dialog").close();
}

async function openTrash() {
  const items = await api("/api/trash");
  $("#trash-list").innerHTML = items.map((t) => `<div class="change"><div class="change-head"><div><b>${esc(t.title || t.node)}</b> <span class="meta-line">${esc(t.ts.replace("T", " "))}</span><div class="meta-line">${t.files.map(esc).join(", ")}</div></div>
    <button class="small" data-untrash="${esc(t.id)}">복구</button></div></div>`).join("") || '<p class="muted">휴지통이 비어 있습니다.</p>';
  document.querySelectorAll("[data-untrash]").forEach((b) => b.addEventListener("click", async () => {
    const r = await post("/api/restore-trash", { id: b.dataset.untrash });
    toast(`복구했습니다: ${r.restored.join(", ")}`); await loadGraph(); openTrash();
  }));
  $("#trash-dialog").showModal();
}

async function upgradeDefaults() {
  const ups = state.graph.defaults_updates;
  if (!ups.length) return;
  const r = await post("/api/defaults/upgrade");
  await loadGraph();
  toast(`앱 기본값을 업데이트했습니다: ${r.upgraded.join(", ")} (이전 판은 버전 보관됨)`);
}

/* ---------------------------------------------------------------- 시작 */
$("#advanced").checked = localGet("advanced") === "1";
$("#show-ai").checked = localGet("showAi") !== "0";
$("#show-skills").checked = localGet("showSkills") !== "0";
$("#show-skills").addEventListener("change", (e) => { localSet("showSkills", e.target.checked ? "1" : "0"); state.cy?.destroy(); state.cy = null; loadGraph(); });
document.body.classList.toggle("advanced", $("#advanced").checked);
$("#advanced").addEventListener("change", (e) => { document.body.classList.toggle("advanced", e.target.checked); localSet("advanced", e.target.checked ? "1" : "0"); });
$("#show-ai").addEventListener("change", (e) => { localSet("showAi", e.target.checked ? "1" : "0"); state.cy?.destroy(); state.cy = null; loadGraph(); });
$("#reload").addEventListener("click", () => loadGraph().then(() => toast("폴더를 다시 읽었습니다")));
$("#changes-btn").addEventListener("click", openChanges);
$("#trash-btn").addEventListener("click", openTrash);
$("#defaults-btn").addEventListener("click", upgradeDefaults);
$("#unreg-btn").addEventListener("click", () => { const u = state.graph.nodes.find((n) => n.kind === "unregistered"); if (u) select(u.id); });
$("#approve-all").addEventListener("click", () => approve(null));
$("#layout-reset").addEventListener("click", resetLayout);
$("#outcome-ok").addEventListener("click", () => confirmOutcome());
$("#tidy-close").addEventListener("click", () => $("#tidy-dialog").close());
$("#outcome-cancel").addEventListener("click", () => cancelOutcome());
$("#export-btn").addEventListener("click", () => copyExport(null));
$("#mcp-btn").addEventListener("click", () => openMcp());
$("#settings-btn").addEventListener("click", () => openSettings());
$("#show-outcome").checked = outcomeOn();
$("#show-outcome").addEventListener("change", (e) => { localSet("showOutcome", e.target.checked ? "1" : "0"); toast(e.target.checked ? "놓을 때마다 예상 결과를 먼저 보여 줍니다" : "선택이 필요한 경우에만 예상 결과를 보여 줍니다"); });
document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
window.addEventListener("focus", () => state.graph && loadGraph().catch(() => {}));
setupInbound();
waitForServer().then(async () => { await loadGraph(); refreshAiBadge(); });


/* ---------------------------------------------------------------- 하위 정리 (v3.4, Q72-A) */
const OP_ICON = { set_body: "¶", set_inherits: "⇣", set_uses: "⊕", move_rule: "⇄", set_rule: "✎", create_rule: "+", rename: "Aa", set_relations: "⋯", trash: "🗑" };
const nodeLabel = (id, g) => (g?.nodes || state.graph.nodes).find((x) => x.id === id)?.label || id;

async function openTidy(nodeId, goal) {
  state.tidy = { node: nodeId, target: localGet("tidyTarget") || "chatgpt_web", selected: null, goal: goal || "" };
  let o = {};
  if (!goal) try { o = await api(`/api/tidy/open?node=${encodeURIComponent(nodeId)}`); } catch (e) { return toast("정리 창을 열 수 없습니다: " + e.message); }
  if (o.request && o.preview && !o.preview.waiting) return showTidyPreview(o.request, o.preview);
  if (o.request) { Object.assign(state.tidy, { rid: o.request, req: o }); }
  else await newTidyRequest();
  renderTidyRequest();
  if (!$("#tidy-dialog").open) $("#tidy-dialog").showModal();
}

async function newTidyRequest() {
  const r = await api(`/api/tidy?node=${encodeURIComponent(state.tidy.node)}&target=${state.tidy.target}${state.tidy.goal ? "&goal=" + encodeURIComponent(state.tidy.goal) : ""}`);
  Object.assign(state.tidy, { rid: r.request, req: r });
}

function renderTidyRequest() {
  const t = state.tidy, r = t.req, n = state.graph.nodes.find((x) => x.id === t.node);
  const targets = Object.entries(state.graph.targets);
  const agent = state.graph.targets[t.target]?.kind === "agent";
  $("#tidy-title").textContent = `하위 정리: ${n?.label || t.node}`;
  $("#tidy-body").innerHTML = `
    <p class="muted">이 노드 아래의 구조를 AI가 분석해 정리안을 냅니다. 앱은 AI를 직접 부르지 않습니다 — 요청을 복사해 AI에게 주고, 답을 여기에 붙여넣으세요. 반영하기 전에 전·후 그래프와 이유, 최종 프롬프트 변화를 보여 줍니다.</p>
    <div class="tidy-steps">
      <div class="tidy-step"><b>1</b> 앱이 찾은 구조 문제 ${r.diagnostics?.length || 0}개
        <ul class="tidy-diag">${(r.diagnostics || []).map((d) => `<li>${esc(d.message)}</li>`).join("") || "<li>찾은 문제가 없습니다. 그래도 AI에게 더 나은 나눔을 물어볼 수 있습니다.</li>"}</ul></div>
      <div class="tidy-step"><b>2</b> AI에게 정리 요청 보내기
        <div class="row"><select id="tidy-target">${targets.map(([k, v]) => `<option value="${esc(k)}" ${k === t.target ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select>
        <button class="primary small" id="tidy-local" title="이 컴퓨터의 Claude Code·Codex로 바로 실행하고, 끝나면 미리보기를 엽니다">로컬 AI로 바로 실행</button>
        <button class="ghost small" id="tidy-copy">정리 요청 복사</button></div>
        ${agent ? `<div class="muted">에이전트는 답을 <code>inbox/${esc(t.rid)}/out/plan.yaml</code>에 직접 씁니다. 다 썼으면 아래 "AI 답 확인"을 누르세요.</div>` : `<div class="muted">요청 id ${esc(t.rid)} · 대화창에 붙여넣으면 AI가 yaml 코드 블록 하나로 답합니다.</div>`}</div>
      <div class="tidy-step"><b>3</b> AI의 답 붙여넣기
        <textarea id="tidy-answer" rows="7" placeholder="AI가 준 plan.yaml 코드 블록을 그대로 붙여넣으세요 (앞뒤 설명이 섞여 있어도 됩니다)"></textarea>
        <div class="row"><button class="primary" id="tidy-receive">미리보기</button>${agent ? `<button class="ghost" id="tidy-check">AI 답 확인</button>` : ""}<span class="muted">zip으로 받았다면 창에 놓아도 됩니다</span></div></div>
    </div>
    <h3>지금 구조</h3><div class="tidy-graph" id="tidy-g-now"></div>`;
  miniGraph("#tidy-g-now", r.graph || { nodes: [], edges: [] }, []);
  $("#tidy-target").addEventListener("change", async (e) => { t.target = e.target.value; localSet("tidyTarget", t.target); await newTidyRequest(); renderTidyRequest(); });
  $("#tidy-copy").addEventListener("click", () => copy(r.prompt).then(() => toast("정리 요청을 복사했습니다. AI 대화창에 붙여넣으세요")));
  $("#tidy-local").addEventListener("click", () => {
    const rid = t.rid;
    const go = () => { $("#tidy-dialog").close(); runLocal({ kind: "tidy", request: rid }, { label: "하위 정리",
      onDone: async () => showTidyPreview(rid, await post(`/api/tidy/${rid}/preview`, {})) }); };
    ensureLocalAI("하위 정리", go);
  });
  $("#tidy-receive").addEventListener("click", async () => {
    const text = $("#tidy-answer").value.trim();
    if (!text) return toast("AI의 답을 먼저 붙여넣어 주세요");
    try { showTidyPreview(t.rid, await post(`/api/tidy/${t.rid}/receive`, { text })); } catch (e) { toast("읽을 수 없는 답입니다: " + e.message); }
  });
  $("#tidy-check")?.addEventListener("click", async () => {
    const pv = await post(`/api/tidy/${t.rid}/preview`, {});
    if (pv.waiting) return toast(pv.message);
    showTidyPreview(t.rid, pv);
  });
  $("#tidy-apply").hidden = true;
}

function itemText(it, g) {
  const L = (id) => `<b>${esc(nodeLabel(id, g))}</b>`;
  const list = (a) => (a && a.length ? a.map((x) => esc(nodeLabel(x, g))).join(", ") : "없음");
  switch (it.op) {
    case "set_inherits": return `${L(it.node)} 상속: ${list(it.before)} → <b>${list(it.after)}</b>`;
    case "set_uses": return `${L(it.node)} 원칙: ${list(it.before)} → <b>${list(it.after)}</b>`;
    case "move_rule": return `<code>${esc(it.key_label || it.key)}</code> 키를 ${L(it.node)} → ${L(it.to)}로 옮김`;
    case "set_rule": return `${L(it.node)}의 <code>${esc(it.key_label || it.key)}</code> 값 변경<div class="ow"><span class="before">${esc(fmtVal(it.before ?? "(없음)"))}</span><br><span class="after">${esc(fmtVal(it.after))}</span></div>`;
    case "create_rule": return `새 ${it.kind === "set" ? "세트" : "원칙"} <b>${esc(it.title || it.node)}</b> <code>${esc(it.node)}</code>${it.after?.rules ? ` · 키 ${Object.keys(it.after.rules).map(esc).join(", ")}` : ""}`;
    case "rename": return `${esc(it.before)} → <b>${esc(it.after)}</b>`;
    case "set_relations": return `${L(it.node)} 연결: ${(it.before || []).length}개 → <b>${(it.after || []).length}개</b>${(it.after || []).map((r) => ` <span class="chip">${esc(r.type)} → ${esc(nodeLabel(r.target, g))}</span>`).join("")}`;
    case "set_body": return `${L(it.node)} 설명 바꿈<div class="ow"><span class="before">${esc(it.before)}</span><br><span class="after">${esc(it.after)}</span></div>`;
    case "trash": return `${L(it.node)}을(를) 휴지통으로`;
    default: return esc(it.op || "?");
  }
}

function showTidyPreview(rid, pv) {
  state.tidy = state.tidy || {};
  Object.assign(state.tidy, { rid, pv, node: pv.request?.node || state.tidy.node });
  if (pv.waiting) { toast(pv.message); return; }
  const g = pv.graph_after;
  const changed = new Set(pv.items.filter((i) => i.checked && !i.error).flatMap((i) => [i.node, i.to].filter(Boolean)));
  const prompts = pv.prompts || [];
  const same = prompts.every((p) => p.same);
  $("#tidy-title").textContent = `하위 정리 미리보기: ${nodeLabel(state.tidy.node)}`;
  $("#tidy-body").innerHTML = `
    ${pv.summary ? `<div class="oc-will"><b>정리안</b><span>${esc(pv.summary)}</span></div>` : ""}
    ${pv.why ? `<div class="tidy-why"><b>왜 이렇게 바꾸나요</b><p>${esc(pv.why).replace(/\n/g, "<br>")}</p></div>` : ""}
    ${(pv.errors || []).map((e) => `<div class="issue error">${esc(e)}</div>`).join("")}
    <div class="tidy-graphs">
      <div><h3>정리 전 <span class="muted">구조 문제 ${pv.diag_before.length}개</span></h3><div class="tidy-graph" id="tidy-g-before"></div></div>
      <div><h3>정리 후 <span class="muted">구조 문제 ${pv.diag_after.length}개</span></h3><div class="tidy-graph" id="tidy-g-after"></div></div>
    </div>
    <div class="tidy-legend"><span><i class="ln solid"></i>상속</span><span><i class="ln accent"></i>원칙·키 (프롬프트에 들어감)</span><span><i class="ln dashed"></i>참고 연결</span><span><i class="dot changed"></i>바뀌는 노드</span></div>
    <h3>최종 프롬프트 ${same ? '<span class="chip ok">그대로 ✓</span>' : '<span class="chip internal">바뀜</span>'}</h3>
    <div class="tidy-prompts">${prompts.map((p) => p.same ? `<div class="muted">✓ ${esc(p.title)}: 정리 전과 글자 하나까지 같습니다</div>` :
      `<details class="tidy-pdiff" open><summary>⚠ ${esc(p.title)}: ${p.removed ? "세트가 사라집니다" : `키 ${p.changed_keys.length}개가 달라집니다`}</summary>
        ${p.changed_keys.map((k) => `<div class="ow"><code>${esc(k.label)}</code> <span class="before">${esc(fmtVal(k.before ?? "(없음)"))}</span> → <span class="after">${esc(fmtVal(k.after ?? "(없어짐)"))}</span></div>`).join("")}
        <pre>${esc(p.diff)}</pre></details>`).join("") || '<div class="muted">이 범위에는 세트가 없습니다</div>'}</div>
    <h3>변경 ${pv.items.length}개 <span class="muted" style="font-weight:400">— 체크를 끄면 그 변경만 빼고 다시 미리 봅니다</span></h3>
    <div>${pv.items.map((it) => `
      <label class="ritem ${it.error ? "has-error" : ""} ${it.op === "trash" || it.op === "set_rule" ? "overwrite" : ""}">
        <input type="checkbox" data-tidy="${esc(it.id)}" ${it.checked ? "checked" : ""} ${it.error ? "disabled" : ""}>
        <div><span class="chip">${OP_ICON[it.op] || "·"} ${esc(it.label)}</span> ${itemText(it, g)}
          ${it.reason ? `<div class="reason">이유: ${esc(it.reason)}</div>` : ""}
          ${(it.warnings || []).map((w) => `<div class="ow-impact">${esc(w)}</div>`).join("")}
          ${it.error ? `<div class="issue error">적용 안 됨: ${esc(it.error)}</div>` : ""}</div>
      </label>`).join("") || '<div class="muted">AI가 바꿀 것이 없다고 답했습니다.</div>'}</div>
    ${pv.diag_after.length ? `<h3>정리 후에도 남는 구조 문제</h3><ul class="tidy-diag">${pv.diag_after.map((d) => `<li>${esc(d.message)}</li>`).join("")}</ul>` : ""}
    <div class="row"><button class="ghost small" id="tidy-again">다른 답 붙여넣기</button></div>`;
  miniGraph("#tidy-g-before", pv.graph_before, []);
  miniGraph("#tidy-g-after", g, [...changed]);
  document.querySelectorAll("[data-tidy]").forEach((c) => c.addEventListener("change", async () => {
    const selected = [...document.querySelectorAll("[data-tidy]")].filter((x) => x.checked && !x.disabled).map((x) => x.dataset.tidy);
    state.tidy.selected = selected;
    showTidyPreview(rid, await post(`/api/tidy/${rid}/preview`, { selected }));
  }));
  $("#tidy-again").addEventListener("click", async () => {
    const o = await api(`/api/tidy?node=${encodeURIComponent(state.tidy.node)}&target=${state.tidy.target || "chatgpt_web"}${state.tidy.goal ? "&goal=" + encodeURIComponent(state.tidy.goal) : ""}`);
    Object.assign(state.tidy, { rid: o.request, req: o }); renderTidyRequest();
  });
  const ok = pv.items.some((i) => i.checked && !i.error) && !(pv.errors || []).length;
  $("#tidy-apply").hidden = false;
  $("#tidy-apply").disabled = !ok;
  $("#tidy-apply").textContent = same ? "반영하기" : "프롬프트가 바뀌는 것을 확인했고 반영하기";
  $("#tidy-apply").onclick = () => applyTidy(rid);
  if (!$("#tidy-dialog").open) $("#tidy-dialog").showModal();
}

async function applyTidy(rid) {
  const selected = [...document.querySelectorAll("[data-tidy]")].filter((x) => x.checked && !x.disabled).map((x) => x.dataset.tidy);
  let r;
  try { r = await post(`/api/tidy/${rid}/apply`, { selected }); } catch (e) { return toast("반영 실패: " + e.message); }
  $("#tidy-dialog").close();
  await loadGraph();
  arrangeTidy(state.tidy.node, r.scope || []);
  toast(`정리했습니다: 파일 ${r.written.length}개${r.trashed.length ? `, 휴지통 ${r.trashed.length}개` : ""} (바뀌기 전 상태는 버전 보관에 있습니다)`);
}

/* 정리한 범위만 다시 배치: 세트는 상속 깊이 순으로 아래로, 원칙은 세트 오른쪽 줄, 레퍼런스는 왼쪽 줄 */
function arrangeTidy(rootId, scope) {
  const cy = state.cy; if (!cy) return;
  const ids = new Set(scope);
  const rootNode = cy.getElementById(rootId); if (!rootNode.length) return;
  const base = { ...rootNode.position() };
  const edges = state.graph.edges;
  const sets = scope.filter((id) => state.graph.nodes.find((n) => n.id === id)?.kind === "set" || id === rootId);
  const depth = (id, seen = new Set()) => { if (seen.has(id)) return 0; seen.add(id); const ps = edges.filter((e) => e.kind === "inherits" && e.target === id && ids.has(e.source)).map((e) => e.source); return ps.length ? 1 + Math.max(...ps.map((p) => depth(p, seen))) : 0; };
  const placed = new Set();
  sets.map((id) => [id, depth(id)]).sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0])).forEach(([id], i) => {
    const pos = { x: base.x + 40 * i, y: base.y + 190 * i };
    cy.getElementById(id).position(pos); placed.add(id);
    const parts = edges.filter((e) => e.kind === "uses" && e.source === id && ids.has(e.target) && !placed.has(e.target)).map((e) => e.target);
    parts.forEach((p, j) => { cy.getElementById(p).position({ x: pos.x + 190 + 165 * j, y: pos.y + 70 }); placed.add(p); });
    const refs = edges.filter((e) => (e.kind === "key" && e.source === id || e.kind === "info" && e.source === id && e.label === "출처") && ids.has(e.target) && !placed.has(e.target)).map((e) => e.target);
    refs.forEach((p, j) => { cy.getElementById(p).position({ x: pos.x - 170 - 120 * j, y: pos.y + 70 }); placed.add(p); });
  });
  saveLayout();
}

function miniGraph(sel, sg, changed) {
  const el = $(sel); if (!el || !window.cytoscape) return;
  const css = getComputedStyle(document.documentElement);
  const v = (n) => css.getPropertyValue(n).trim();
  const ch = new Set(changed);
  const els = [...sg.nodes.map((n) => ({ data: { ...n, label: n.label }, classes: nodeClasses(n) + (n.outside ? " outside" : "") + (ch.has(n.id) ? " changed" : "") })),
    ...sg.edges.map((e) => ({ data: e, classes: e.kind }))];
  const roots = sg.nodes.filter((n) => n.outside).map((n) => "#" + CSS.escape(n.id));
  const cy = cytoscape({ container: el, elements: els, userZoomingEnabled: true, boxSelectionEnabled: false, autoungrabify: false,
    style: [
      { selector: "node", style: { label: "data(label)", "font-family": v("--font"), "font-size": 10, color: v("--ink"), "text-valign": "bottom", "text-margin-y": 4, "text-wrap": "wrap", "text-max-width": 110 } },
      { selector: "node.rule", style: { shape: "round-rectangle", width: 120, height: 34, "background-color": v("--accent"), color: "#fff", "text-valign": "center", "text-margin-y": 0, "font-weight": 600 } },
      { selector: "node.principle", style: { "background-opacity": 0.72 } },
      { selector: "node.ref", style: { shape: "ellipse", width: 40, height: 40, "background-color": "#e6d3c3", "border-width": 1.5, "border-color": v("--line") } },
      { selector: "node.outside", style: { opacity: 0.45 } },
      { selector: "node.changed", style: { "underlay-color": "#f0b429", "underlay-opacity": 0.55, "underlay-padding": 6, "underlay-shape": "round-rectangle" } },
      { selector: "edge", style: { width: 1.5, "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.8, "line-color": v("--ink"), "target-arrow-color": v("--ink"), "font-size": 9, color: v("--muted"), "text-background-color": v("--panel"), "text-background-opacity": 1 } },
      { selector: "edge.inherits", style: { width: 2.2 } },
      { selector: "edge.key, edge.uses", style: { label: "data(label)", "line-color": v("--accent"), "target-arrow-color": v("--accent") } },
      { selector: "edge.uses", style: { "target-arrow-shape": "square", "arrow-scale": 0.6 } },
      { selector: "edge.info", style: { "line-style": "dashed", "line-color": v("--muted"), "target-arrow-shape": "none", label: "data(label)", opacity: 0.8 } },
    ],
  });
  const layout = { name: "breadthfirst", directed: true, roots: roots.length ? roots.join(",") : undefined, spacingFactor: 0.95, padding: 16 };
  // 창이 열리기 전에 만들어지면 크기가 0이라 배치가 뭉친다 → 보이는 다음 프레임에 다시 배치
  const relayout = () => { cy.resize(); cy.layout(layout).run(); cy.fit(undefined, 16); };
  relayout();
  setTimeout(relayout, 60);
}


/* ---------------------------------------------------------------- 로컬 AI 연결 (v3.5, Q76-B) */
const JOB_KIND_LABEL = { tidy: "하위 정리", request: "AI에게 맡기기", authoring: "파일 등록" };

async function refreshAiBadge() {
  try {
    const st = await api("/api/local-ai");
    const d = st.tools.find((t) => t.name === st.default);
    $("#settings-btn").innerHTML = `설정 <span class="ai-dot ${st.ready ? "on" : ""}" title="${st.ready ? `로컬 AI: ${esc(d?.label || "")}` : "로컬 AI 연결 안 됨"}"></span>`;
    return st;
  } catch (e) { return null; }
}

/* AI가 필요한 기능: 연결돼 있으면 바로, 아니면 설정 창을 '안내 모드'로 띄우고 연결되면 이어서 실행 */
async function ensureLocalAI(feature, action) {
  let st;
  try { st = await api("/api/local-ai"); } catch (e) { return toast("설정을 읽지 못했습니다: " + e.message); }
  if (st.ready) return action(st);
  for (const d of document.querySelectorAll("dialog[open]")) if (d.id !== "settings-dialog" && d.id !== "outcome-dialog") d.close();
  openSettings({ reason: feature, then: action });
}

async function openSettings(opts = {}) {
  state.settings = { ...opts, results: (state.settings || {}).results || {} };
  const st = await api("/api/local-ai");
  renderSettings(st);
  if (!$("#settings-dialog").open) $("#settings-dialog").showModal();
}

function renderSettings(st) {
  const o = state.settings || {};
  const tools = st.tools;
  const d = tools.find((t) => t.name === st.default);
  const guide = o.reason ? `<div class="callout ${st.ready ? "ok" : "warn"}">
      ${st.ready ? `<b>${esc(d.label)}</b>에 연결됐습니다. <b>${esc(o.reason)}</b>을(를) 이어서 실행할 수 있습니다.
        <div class="row" style="margin-top:8px"><button class="primary" id="set-continue">${esc(o.reason)} 계속하기</button></div>`
      : `<b>${esc(o.reason)}</b>은(는) AI가 필요한 기능입니다. 이 컴퓨터의 AI 도구를 연결하면 복사·붙여넣기 없이 버튼 하나로 실행됩니다.
        <ol class="steps"><li>아래에서 설치된 도구를 찾습니다 (없으면 설치 명령을 터미널에 붙여넣고 로그인).</li><li><b>연결 시험</b>을 눌러 응답을 확인합니다.</li><li>연결되면 <b>계속하기</b>가 나타납니다.</li></ol>
        <div class="muted">연결하지 않아도 각 창의 "요청 복사"로 지금처럼 AI 앱에 붙여넣어 쓸 수 있습니다.</div>`}
    </div>` : "";
  $("#settings-body").innerHTML = guide + `
    <h3>로컬 AI 도구 <span class="chip ${st.ready ? "ok" : ""}">${st.ready ? `연결됨 · ${esc(d.label)}` : "연결 안 됨"}</span></h3>
    <p class="muted">이 컴퓨터에 설치된 AI 명령줄 도구를 연결합니다. 도구는 이미 로그인한 구독 계정을 그대로 쓰고, 앱은 API 키를 받지 않습니다.
      "하위 정리", "AI에게 맡기기", "파일 등록"의 <b>로컬 AI로 바로 실행</b> 버튼이 이 도구를 씁니다. 결과는 언제나 반영하기 전에 미리 보여 줍니다.</p>
    <div class="row"><button id="set-detect">다시 찾기</button><span class="muted">PATH와 기본 설치 위치를 살펴봅니다</span></div>
    ${tools.map((t) => toolCard(t, st)).join("")}
    ${st.running.length ? `<h3>실행 중</h3>${st.running.map((j) => `<div class="meta-line">${esc(j.tool_label)} · ${esc(JOB_KIND_LABEL[j.kind] || j.kind)} · ${esc(j.started)}</div>`).join("")}` : ""}`;
  $("#set-detect").addEventListener("click", async (e) => { e.target.disabled = true; e.target.textContent = "찾는 중…"; renderSettings(await post("/api/local-ai/detect", {})); refreshAiBadge(); });
  $("#set-continue")?.addEventListener("click", () => { const then = o.then; state.settings.then = null; $("#settings-dialog").close(); then && then(st); });
  document.querySelectorAll("[data-default]").forEach((r) => r.addEventListener("change", async () => { renderSettings(await post("/api/local-ai/config", { default: r.dataset.default })); refreshAiBadge(); }));
  document.querySelectorAll("[data-enable]").forEach((c) => c.addEventListener("change", async () => { renderSettings(await post("/api/local-ai/config", { tools: { [c.dataset.enable]: { enabled: c.checked } } })); refreshAiBadge(); }));
  document.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", async () => {
    const name = b.dataset.test; b.disabled = true; b.textContent = "시험 중… (최대 3분)";
    const r = await post("/api/local-ai/test", { tool: name });
    state.settings.results[name] = r;
    renderSettings(await api("/api/local-ai")); refreshAiBadge();
  }));
  document.querySelectorAll("[data-path-save]").forEach((b) => b.addEventListener("click", async () => {
    const name = b.dataset.pathSave, v = $(`#path-${name}`).value;
    renderSettings(await post("/api/local-ai/config", { tools: { [name]: { custom_path: v } } })); refreshAiBadge();
  }));
  document.querySelectorAll("[data-args-save]").forEach((b) => b.addEventListener("click", async () => {
    const name = b.dataset.argsSave, lines = $(`#args-${name}`).value.split("\n").map((x) => x.trim()).filter(Boolean);
    renderSettings(await post("/api/local-ai/config", { tools: { [name]: { args: lines } } })); toast("실행 인자를 저장했습니다");
  }));
  document.querySelectorAll("[data-args-reset]").forEach((b) => b.addEventListener("click", async () => {
    renderSettings(await post("/api/local-ai/config", { tools: { [b.dataset.argsReset]: { args: null } } })); toast("기본 인자로 되돌렸습니다");
  }));
  document.querySelectorAll("[data-copycmd]").forEach((b) => b.addEventListener("click", () => copy(b.dataset.copycmd).then(() => toast("명령을 복사했습니다. 터미널에 붙여넣으세요"))));
  callerHtml().then((h) => {
    if (!h || !$("#settings-dialog").open) return;
    $("#caller-box")?.remove();
    $("#settings-body").insertAdjacentHTML("beforeend", `<div id="caller-box">${h}</div>`);
    const box = $("#caller-box");
    box.querySelectorAll("[data-caller]").forEach((b) => b.addEventListener("click", async () => {
      try { await post("/api/caller", { item: b.dataset.caller, action: b.dataset.act }); toast(b.dataset.act === "remove" ? "제거했습니다" : "설치했습니다"); }
      catch (e) { toast("하지 못했습니다: " + e.message); }
      renderSettings(await api("/api/local-ai"));
    }));
    box.querySelectorAll("[data-copycmd]").forEach((b) => b.addEventListener("click", () => copy(b.dataset.copycmd).then(() => toast("명령을 복사했습니다"))));
  });
}

function toolCard(t, st) {
  const r = (state.settings.results || {})[t.name];
  const isDefault = st.default === t.name;
  const testLine = r ? `<div class="${r.ok ? "ok" : "bad"}">${r.ok ? "✓ 응답을 받았습니다" : "✗ 응답이 올바르지 않습니다 — 터미널에서 로그인 상태를 확인하세요"}</div>${r.output ? `<pre class="code small">${esc(r.output)}</pre>` : ""}`
    : t.tested ? `<div class="${t.test_ok ? "ok" : "bad"}">${t.test_ok ? "✓" : "✗"} 마지막 시험 ${esc(t.tested.replace("T", " "))}</div>` : `<div class="muted">아직 시험하지 않았습니다</div>`;
  return `<div class="tool-card ${t.found ? "found" : ""} ${isDefault ? "default" : ""}">
    <div class="tool-head">
      <label class="toggle"><input type="radio" name="ai-default" data-default="${t.name}" ${isDefault ? "checked" : ""} ${t.found ? "" : "disabled"}> <b>${esc(t.label)}</b></label>
      <span class="chip ${t.found ? "ok" : ""}">${t.found ? `찾음${t.version ? ` · ${esc(t.version)}` : ""}` : "찾지 못함"}</span>
      ${t.found ? `<label class="toggle small"><input type="checkbox" data-enable="${t.name}" ${t.enabled ? "checked" : ""}> 사용</label>` : ""}
    </div>
    <div class="meta-line">${t.path ? esc(t.path) : "PATH와 기본 설치 위치에서 찾지 못했습니다"}</div>
    ${t.found ? `<div class="row"><button class="small" data-test="${t.name}">연결 시험</button></div>${testLine}`
      : `<div class="install"><div class="muted">설치하기 — 아래 명령 중 하나를 터미널에 붙여넣은 뒤, 새 터미널에서 로그인하고 <b>다시 찾기</b>를 누르세요.</div>
         ${t.install.map((i) => `<div class="cmd-row"><span class="chip">${esc(i.label)}</span><code>${esc(i.cmd)}</code><button class="ghost small" data-copycmd="${esc(i.cmd)}">복사</button></div>`).join("")}
         <div class="muted">로그인: ${esc(t.login)} · <a href="${esc(t.docs)}" target="_blank" rel="noopener">공식 안내</a></div></div>`}
    <details class="tool-adv"><summary>고급</summary>
      <div class="row"><input class="oc-input" id="path-${t.name}" value="${esc(t.custom_path || "")}" placeholder="직접 경로 지정 (예: C:\\Users\\me\\.local\\bin\\claude.exe)"><button class="small" data-path-save="${t.name}">경로 저장</button></div>
      <div class="muted">실행 인자 (한 줄에 하나, <code>{instruction}</code> 자리에 요청 지시가 들어갑니다)</div>
      <textarea id="args-${t.name}" rows="5" class="code-input">${esc((t.args || []).join("\n"))}</textarea>
      <div class="row"><button class="small" data-args-save="${t.name}">인자 저장</button><button class="ghost small" data-args-reset="${t.name}">기본값으로</button></div>
    </details>
  </div>`;
}

/* 실행과 진행 표시 (화면 오른쪽 아래) */
async function runLocal(body, { label, onDone }) {
  let job;
  try { job = await post("/api/local-ai/run", body); }
  catch (e) {
    if (/needs_setup|연결된 로컬 AI|찾지 못했|꺼져/.test(e.message)) return openSettings({ reason: label, then: () => runLocal(body, { label, onDone }) });
    return toast("실행하지 못했습니다: " + e.message);
  }
  if (job.error && job.needs_setup) return openSettings({ reason: label, then: () => runLocal(body, { label, onDone }) });
  state.jobs = state.jobs || {};
  state.jobs[job.id] = { label, onDone, showLog: false };
  renderJobs();
  pollJob(job.id);
}

async function pollJob(jid) {
  let j;
  try { j = await api(`/api/local-ai/job/${jid}`); } catch (e) { setTimeout(() => pollJob(jid), 3000); return; }
  const meta = state.jobs[jid]; if (!meta) return;
  meta.job = j;
  renderJobs();
  if (j.state === "running") return setTimeout(() => pollJob(jid), 2000);
  if (j.state === "done") {
    toast(`${meta.label}: ${j.tool_label}가 끝냈습니다 (${j.seconds}초)`);
    delete state.jobs[jid]; renderJobs();
    try { await meta.onDone?.(j); } catch (e) { toast("결과를 여는 중 오류: " + e.message); }
  }
}

function renderJobs() {
  const box = $("#job-bar");
  const items = Object.entries(state.jobs || {});
  box.hidden = !items.length;
  box.innerHTML = items.map(([jid, m]) => {
    const j = m.job || { state: "running", tool_label: "", elapsed: 0 };
    const t = j.elapsed != null ? `${Math.floor(j.elapsed / 60)}:${String(j.elapsed % 60).padStart(2, "0")}` : "";
    const head = j.state === "running" ? `<span class="spin"></span><b>${esc(m.label)}</b> · ${esc(j.tool_label || "로컬 AI")} 실행 중 ${t}`
      : j.state === "cancelled" ? `<b>${esc(m.label)}</b> · 취소했습니다`
      : `<span class="bad">✗</span> <b>${esc(m.label)}</b> · 실패: ${esc(j.error || `종료 코드 ${j.code}`)}`;
    return `<div class="job ${j.state}"><div class="job-head">${head}</div>
      <div class="row">${j.state === "running" ? `<button class="ghost small" data-jcancel="${jid}">취소</button>` : `<button class="ghost small" data-jclose="${jid}">닫기</button><button class="ghost small" data-jsettings>설정 열기</button>`}
        <button class="ghost small" data-jlog="${jid}">${m.showLog ? "로그 숨기기" : "로그 보기"}</button></div>
      ${m.showLog ? `<pre class="code small">${esc(j.log || "(아직 출력 없음)")}</pre>` : ""}</div>`;
  }).join("");
  box.querySelectorAll("[data-jcancel]").forEach((b) => b.addEventListener("click", async () => { await post(`/api/local-ai/job/${b.dataset.jcancel}/cancel`, {}); }));
  box.querySelectorAll("[data-jclose]").forEach((b) => b.addEventListener("click", () => { delete state.jobs[b.dataset.jclose]; renderJobs(); }));
  box.querySelectorAll("[data-jlog]").forEach((b) => b.addEventListener("click", () => { state.jobs[b.dataset.jlog].showLog = !state.jobs[b.dataset.jlog].showLog; renderJobs(); }));
  box.querySelectorAll("[data-jsettings]").forEach((b) => b.addEventListener("click", () => openSettings()));
}


/* ---------------------------------------------------------------- 스킬 연동 (v3.6, Q77-A) */
const SKILL_STATE = { ok: "설치됨", absent: "없음", edited: "직접 고침", foreign: "같은 이름의 다른 스킬" };

async function loadSkillBox(setId) {
  let st;
  try { st = await api(`/api/skills/set/${encodeURIComponent(setId)}`); }
  catch (e) { const b = $("#skill-box"); if (b) b.innerHTML = `<h3>스킬로 내보내기</h3><div class="issue warning">${esc(e.message)}</div>`; return; }
  if (state.selected !== setId || !$("#skill-box")) return;
  const sel = (state.skillOpts ||= {})[setId] ||= { scope: localGet("skillScope") || "library", agents: ["claude", "agents"] };
  const rows = st.installs;
  const cell = (scope, agent) => {
    const r = rows.find((x) => x.scope === scope && x.agent === agent);
    if (!r || r.state === "absent") return `<span class="muted">—</span>`;
    if (r.state === "ok") return r.stale ? `<span class="chip warnchip">갱신 필요</span>` : `<span class="chip ok">최신</span>`;
    return `<span class="chip internal">${SKILL_STATE[r.state]}</span>`;
  };
  const installed = rows.some((r) => r.state !== "absent" && r.scope === sel.scope && sel.agents.includes(r.agent));
  $("#skill-box").innerHTML = `<h3>스킬로 내보내기 <span class="muted" style="font-weight:400">— 이 세트의 최종 규칙을 Claude Code·Codex 등이 읽는 스킬 폴더로</span></h3>
    <table class="rules skill-table"><tr><td class="k"></td><td><b>Claude Code</b><small>.claude/skills</small></td><td><b>Codex · Gemini CLI 등</b><small>.agents/skills</small></td></tr>
      <tr><td class="k">라이브러리 안<small>이 라이브러리에서 실행할 때만</small></td><td>${cell("library", "claude")}</td><td>${cell("library", "agents")}</td></tr>
      <tr><td class="k">개인 폴더<small>이 컴퓨터의 모든 프로젝트</small></td><td>${cell("personal", "claude")}</td><td>${cell("personal", "agents")}</td></tr></table>
    <div class="row">
      <label class="toggle"><input type="radio" name="skill-scope" value="library" ${sel.scope === "library" ? "checked" : ""}> 라이브러리 안</label>
      <label class="toggle"><input type="radio" name="skill-scope" value="personal" ${sel.scope === "personal" ? "checked" : ""}> 개인 폴더</label>
      <span class="muted">·</span>
      <label class="toggle"><input type="checkbox" data-skill-agent="claude" ${sel.agents.includes("claude") ? "checked" : ""}> Claude Code</label>
      <label class="toggle"><input type="checkbox" data-skill-agent="agents" ${sel.agents.includes("agents") ? "checked" : ""}> Codex 등</label>
    </div>
    <div class="row"><button class="primary small" id="skill-export">${installed ? "다시 내보내기" : "스킬로 내보내기"}</button>
      ${installed ? `<button class="ghost small danger-text" id="skill-remove">이 위치에서 제거</button>` : ""}
      <span class="meta-line">스킬 이름 <code>${esc(st.name)}</code> · 첨부 ${st.files.length}개</span></div>
    ${st.warnings.map((w) => `<div class="issue warning">${esc(w)}</div>`).join("")}
    <div id="skill-conflict"></div>
    <details class="tool-adv"><summary>설명과 SKILL.md 미리보기</summary>
      <div class="muted" style="margin:6px 0">AI는 이 설명을 보고 스킬을 쓸지 정합니다. 바꾸려면 세트 프런트매터에 <code>skill_description:</code>을 쓰세요.</div>
      <div class="callout">${esc(st.description)}</div><pre class="code small" style="max-height:320px">${esc(st.skill_md)}</pre></details>`;
  document.querySelectorAll('[name="skill-scope"]').forEach((r) => r.addEventListener("change", () => { sel.scope = r.value; localSet("skillScope", r.value); loadSkillBox(setId); }));
  document.querySelectorAll("[data-skill-agent]").forEach((c) => c.addEventListener("change", () => {
    sel.agents = [...document.querySelectorAll("[data-skill-agent]")].filter((x) => x.checked).map((x) => x.dataset.skillAgent); loadSkillBox(setId);
  }));
  $("#skill-export").addEventListener("click", () => exportSkill(setId, sel, false));
  $("#skill-remove")?.addEventListener("click", async () => {
    const r = await post("/api/skills/remove", { set: setId, scope: sel.scope, agents: sel.agents });
    toast(r.refused.length ? `직접 고친 스킬 ${r.refused.length}개는 지우지 않았습니다` : `스킬을 ${r.removed.length}곳에서 제거했습니다`);
    await loadGraph(); loadSkillBox(setId);
  });
}

async function exportSkill(setId, sel, overwrite) {
  if (!sel.agents.length) return toast("설치할 도구를 하나 이상 고르세요");
  const r = await post("/api/skills/export", { set: setId, scope: sel.scope, agents: sel.agents, overwrite });
  if (!r.ok) {
    $("#skill-conflict").innerHTML = `<div class="callout warn"><b>덮어쓰기 전에 확인하세요.</b>
      ${r.conflicts.map((c) => `<div style="margin-top:6px">${c.state === "edited" ? "누군가 이 스킬을 직접 고쳤습니다" : "같은 이름의 다른 스킬이 이미 있습니다"}: <code>${esc(c.path)}</code></div>
        <details><summary>바뀌는 내용 보기</summary><pre class="code small">${esc(c.diff)}</pre></details>`).join("")}
      <div class="muted" style="margin-top:6px">고친 내용을 남기려면, 그 내용을 세트 규칙에 옮긴 뒤 다시 내보내세요.</div>
      <div class="row"><button class="small danger-text" id="skill-overwrite">덮어쓰기</button><button class="ghost small" id="skill-cancel">그대로 두기</button></div></div>`;
    $("#skill-overwrite").addEventListener("click", () => exportSkill(setId, sel, true));
    $("#skill-cancel").addEventListener("click", () => { $("#skill-conflict").innerHTML = ""; });
    return;
  }
  toast(`스킬 ${r.name}을(를) ${r.written.length}곳에 내보냈습니다`);
  await loadGraph(); loadSkillBox(setId);
}

function renderSkill(s) {
  const set = s.set && state.graph.nodes.find((x) => x.id === s.set);
  const chips = [`<span class="chip accent">스킬</span>`, `<span class="chip">${esc(s.scope_label)}</span>`];
  if (s.tool) chips.push(`<span class="chip">Assets Manager 도구 스킬</span>`);
  else if (s.managed) chips.push(`<span class="chip ok">이 앱이 내보냄</span>`);
  else chips.push(`<span class="chip">직접 설치한 스킬</span>`);
  if (s.stale) chips.push(`<span class="chip warnchip">갱신 필요</span>`);
  if (s.edited) chips.push(`<span class="chip internal">직접 고침</span>`);
  $("#detail").innerHTML = `<div class="head"><div class="head-row"><div>${chips.join("")}</div></div><h1>${esc(s.name)}</h1></div>
    <h3>설명 <span class="muted" style="font-weight:400">— AI는 이 문장을 보고 스킬을 쓸지 정합니다</span></h3><div class="callout">${esc(s.description || "(설명 없음)")}</div>
    ${set ? `<div style="margin-top:10px">원본 세트: <span class="link" data-go="${esc(set.id)}">${esc(set.label)}</span>${s.stale ? " — 세트가 바뀌었습니다. 세트에서 <b>다시 내보내기</b>를 누르세요." : ""}</div>` : ""}
    ${s.tool ? `<div class="muted" style="margin-top:10px">로컬 AI 실행 때 도구가 라이브러리 다루는 법(plan.yaml 형식, 정리 방법, 메타 규격)을 읽는 스킬입니다. 앱이 시작할 때 자동으로 맞춥니다.</div>` : ""}
    <h3>설치 위치</h3>${s.locations.map((l) => `<div class="meta-line">${esc(l.agent_label)} · ${esc(l.path)} · 파일 ${l.files}개${l.state === "edited" ? " · 직접 고침" : ""}</div>`).join("")}
    ${!s.managed && !s.tool ? `<h3>앱으로 가져오기 <span class="muted" style="font-weight:400">— 폴더째 라이브러리에 보관하고 노드에 붙입니다</span></h3>
      <div class="row"><select id="imp-node">${ruleOptions("global")}</select>
        <label class="toggle" title="원래 폴더의 SKILL.md 이름만 바꿔 도구가 읽지 않게 합니다. 언제든 되살릴 수 있습니다"><input type="checkbox" id="imp-disable" checked> 원래 스킬 끄기 (지침이 두 번 들어가지 않게)</label></div>
      <div class="muted" id="imp-hint"></div>
      <div class="row"><button class="primary small" id="imp-go">가져오기</button></div>` : ""}`;
  document.querySelectorAll("#detail [data-go]").forEach((b) => b.addEventListener("click", () => select(b.dataset.go)));
  const hint = () => { const v = $("#imp-node")?.value; if ($("#imp-hint")) $("#imp-hint").textContent = scopeHint(v); };
  $("#imp-node")?.addEventListener("change", hint); hint();
  $("#imp-go")?.addEventListener("click", async () => {
    const loc = s.locations.find((l) => l.agent === "claude") || s.locations[0];
    try {
      const r = await post("/api/skills/import", { path: loc.path, node: $("#imp-node").value || null, disable_original: $("#imp-disable").checked });
      toast(`스킬을 가져왔습니다${r.disabled_original ? " · 원래 스킬은 껐습니다" : ""}`);
      await loadGraph(); select(r.id);
    } catch (e) { toast("가져오지 못했습니다: " + e.message); }
  });
  highlight(set ? [set.id] : [], [], []);
}


/* ---------------------------------------------------------------- 라이브러리 스킬 (v3.7, Q78-A) */
function ruleOptions(selected, exclude = []) {
  const rules = state.graph.nodes.filter((n) => n.type === "rule" && n.kind !== "defaults" && !exclude.includes(n.id));
  rules.sort((a, b) => (a.kind === b.kind ? a.label.localeCompare(b.label) : a.kind === "principle" ? -1 : 1));
  return `<option value="">(붙이지 않음)</option>` + rules.map((r) => `<option value="${esc(r.id)}" ${r.id === selected ? "selected" : ""}>${esc(r.label)} · ${r.kind === "set" ? "세트" : "원칙"}</option>`).join("");
}
function scopeHint(nodeId) {
  if (!nodeId) return "그래프에 보관만 하고, 나중에 원하는 노드에 붙일 수 있습니다.";
  const kids = state.graph.edges.filter((e) => e.kind === "inherits" && e.source === nodeId).length;
  const n = state.graph.nodes.find((x) => x.id === nodeId);
  return n?.kind === "principle" && kids ? `'${n.label}'을(를) 상속하는 세트 ${kids}개 모두에 이 스킬이 들어갑니다. 특정 작업에만 쓰는 스킬이면 그 세트를 고르세요.` : "이 노드로 조립할 때 스킬 이름·설명·경로가 함께 들어갑니다.";
}
function skillsHtml(n, a) {
  const libSkills = state.graph.nodes.filter((x) => x.type === "skill" && x.kind === "skill");
  if (!libSkills.length && !a?.skills?.length) return "";
  const own = (n.uses_raw || []);
  const list = (a?.skills || []).map((k) => `<button class="ghost small" data-go="${esc(k.id)}" title="${esc(k.description)}">🏷 ${esc(k.title)}</button>`).join(" ");
  const avail = libSkills.filter((x) => !(a?.skills || []).some((k) => k.id === x.id));
  return `<h3>함께 쓰는 스킬 <span class="muted" style="font-weight:400">— 조립할 때 이름·설명·경로만 들어가고, AI가 해당될 때 읽습니다</span></h3>
    <div>${list || '<span class="muted">없음</span>'}</div>
    ${avail.length ? `<div class="row"><select id="sk-attach">${avail.map((x) => `<option value="${esc(x.id)}">${esc(x.label)}</option>`).join("")}</select><button class="ghost small" id="sk-attach-go">이 노드에 붙이기</button></div>` : ""}`;
}
function bindSkillAttach(n) {
  $("#sk-attach-go")?.addEventListener("click", async () => {
    await post("/api/skills/attach", { skill: $("#sk-attach").value, node: n.id });
    toast("스킬을 붙였습니다"); await loadGraph(); select(n.id, true);
  });
}
function renderLibSkill(n) {
  const users = n.users || [];
  const all = n.descendants || [];
  const label = (id) => state.graph.nodes.find((x) => x.id === id)?.label || id;
  const o = n.origin || {};
  $("#detail").innerHTML = headHtml(n) +
    `<h3>설명 <span class="muted" style="font-weight:400">— AI는 이 문장을 보고 스킬을 쓸지 정합니다</span></h3><div class="callout">${esc(n.description || "(설명 없음)")}</div>
    <h3>붙인 노드</h3><div>${users.map((u) => `<span class="chip"><span class="link" data-go="${esc(u)}">${esc(label(u))}</span> <span class="link danger-text" data-detach="${esc(u)}" title="이 노드에서 떼기">✕</span></span>`).join(" ") || '<span class="muted">아직 없습니다</span>'}</div>
    ${all.length > users.length ? `<div class="muted" style="margin-top:4px">상속으로 함께 받는 세트: ${all.filter((x) => !users.includes(x)).map((x) => esc(label(x))).join(", ")}</div>` : ""}
    <div class="row"><select id="sk-node">${ruleOptions("", users)}</select><button class="small" id="sk-node-go">붙이기</button></div>
    <div class="muted" id="sk-hint"></div>
    <h3>규칙형 내용은 원칙으로</h3>
    <div class="muted">말투·형식·금지 사항처럼 키로 쓸 수 있는 내용은 원칙 노드로 옮기면 키 단위로 덮어쓰고 점검할 수 있습니다. 절차·스크립트는 스킬로 남깁니다.</div>
    <div class="row"><button class="small" id="sk-split" ${users.length ? "" : "disabled title='먼저 노드에 붙이세요'"}>원칙으로 풀기 (AI)</button></div>
    <h3>파일 ${n.files.length}개</h3><div class="meta-line">${n.files.map(esc).join(" · ")}</div>
    ${o.origin ? `<h3>가져온 곳</h3><div class="meta-line">${esc(o.origin)} · ${esc((o.imported || "").replace("T", " "))}</div>
      ${o.disabled_original ? `<div class="muted">원래 스킬은 꺼 두었습니다(같은 지침이 두 번 들어가지 않게).</div><div class="row"><button class="ghost small" id="sk-restore">원래 스킬 되살리기</button></div>` : ""}` : ""}
    <details class="tool-adv"><summary>SKILL.md 본문</summary><pre class="code small" style="max-height:360px">${esc(n.body || "")}</pre></details>` + versionsHtml(n);
  bindCommon(n);
  const hint = () => { $("#sk-hint").textContent = $("#sk-node").value ? scopeHint($("#sk-node").value) : ""; };
  $("#sk-node").addEventListener("change", hint);
  $("#sk-node-go").addEventListener("click", async () => {
    const node = $("#sk-node").value; if (!node) return toast("붙일 노드를 고르세요");
    await post("/api/skills/attach", { skill: n.id, node }); toast("붙였습니다"); await loadGraph(); select(n.id, true);
  });
  document.querySelectorAll("[data-detach]").forEach((b) => b.addEventListener("click", async () => {
    await post("/api/skills/attach", { skill: n.id, node: b.dataset.detach, detach: true }); toast("뗐습니다"); await loadGraph(); select(n.id, true);
  }));
  $("#sk-split").addEventListener("click", () => openTidy(users[0], "skill:" + n.id));
  $("#sk-restore")?.addEventListener("click", async () => { await post("/api/skills/restore-original", { skill: n.id }); toast("원래 스킬을 되살렸습니다"); await loadGraph(); select(n.id, true); });
  highlight(users, all, []);
}

/* AI가 Assets Manager를 부르게 하기 (설정 창 섹션) */
async function callerHtml() {
  let c; try { c = await api("/api/caller"); } catch (e) { return ""; }
  return `<h3>AI가 Assets Manager를 부르게 하기</h3>
    <p class="muted">다른 프로젝트에서 일하는 Claude Code·Codex가 작업을 시작하기 전에 이 라이브러리에서 맞는 표준을 받아 따르게 합니다. 스킬은 AI가 설명을 보고 부르고, 전역 지침 한 줄은 더 확실하게 부르게 합니다(강제는 아닙니다).</p>
    ${c.items.map((i) => `<div class="cmd-row"><span class="chip ${i.installed ? (i.fresh ? "ok" : "warnchip") : ""}">${i.installed ? (i.fresh ? "설치됨" : "갱신 필요") : "없음"}</span>
      <span style="flex:1">${esc(i.label)}<br><span class="meta-line">${esc(i.path)}</span></span>
      ${i.installed && i.ours ? `${i.fresh ? "" : `<button class="small" data-caller="${i.id}" data-act="install">갱신</button>`}<button class="ghost small" data-caller="${i.id}" data-act="remove">제거</button>` : i.installed ? `<span class="muted">같은 이름의 다른 것</span>` : `<button class="small" data-caller="${i.id}" data-act="install">설치</button>`}</div>`).join("")}
    <div class="muted" style="margin-top:6px">직접 부르는 명령</div><div class="cmd-row"><code>${esc(c.command)}</code><button class="ghost small" data-copycmd="${esc(c.command)}">복사</button></div>`;
}

/* ---------------------------------------------------------------- v3.8 콘솔 패널 (Q80-A)
   콘솔 탭: 앱 안 `am` 명령 (서버의 파이썬이 바로 실행, 셸 없음). 줄 편집·기록·한글 입력은 여기서 처리.
   AI 탭: 연결한 Claude Code·Codex를 서버 pty로 띄운 진짜 터미널. 노드 변경은 `am`으로 하고, 앱은 파일 변경을 감지해 그래프를 갱신한다.
   리소스: 패널을 열 때 xterm을 만들고, AI 프로세스는 "+ AI"를 눌러야 뜬다. 탭을 닫으면 종료. 변경 감지는 창이 보이고 포커스일 때만. */
const CON = { tabs: [], active: null, opened: false, sig: null, snap: null };
const CON_SCROLLBACK = 5000;
const CON_PROMPT = "\x1b[38;5;36mam>\x1b[0m ";

function conTheme() {
  const css = getComputedStyle(document.documentElement);
  const v = (n) => css.getPropertyValue(n).trim();
  return { background: v("--panel"), foreground: v("--ink"), cursor: v("--accent"), selectionBackground: v("--accent-soft") };
}
function conNewTerm(el) {
  const term = new Terminal({ scrollback: CON_SCROLLBACK, fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--mono"), fontSize: 13, cursorBlink: true, convertEol: false, theme: conTheme(), allowProposedApi: false });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit); term.open(el);
  return { term, fit };
}
function conToggle(show) {
  const p = $("#console-panel");
  const on = show ?? p.hidden;
  p.hidden = !on; localSet("console", on ? "1" : "0");
  if (on && !CON.opened) conInit();
  if (on) setTimeout(conFitAll, 0);
  if (on) conFocus();
}
function conInit() {
  CON.opened = true;
  try { const h = localGet("consoleH"); if (h) $("#console-panel").style.setProperty("--con-h", h + "px"); } catch {}
  conAddConsole();
  new ResizeObserver(() => conFitAll()).observe($("#con-body"));
  // 높이 조절
  const rz = $("#con-resize");
  rz.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const startY = e.clientY, startH = $("#console-panel").offsetHeight;
    const mv = (ev) => { const h = Math.max(140, Math.min(window.innerHeight * 0.75, startH + startY - ev.clientY)); $("#console-panel").style.setProperty("--con-h", h + "px"); };
    const up = () => { document.removeEventListener("mousemove", mv); document.removeEventListener("mouseup", up); localSet("consoleH", String($("#console-panel").offsetHeight)); conFitAll(); if (state.cy) state.cy.resize(); };
    document.addEventListener("mousemove", mv); document.addEventListener("mouseup", up);
  });
}
function conFitAll() {
  for (const t of CON.tabs) if (!t.pane.hidden) {
    try { t.fit.fit(); } catch {}
    if (t.term.cols < 20 && !t.refit) { t.refit = setTimeout(() => { t.refit = null; try { t.fit.fit(); } catch {} }, 200); } // 아직 크기가 잡히기 전
  }
  if (state.cy) state.cy.resize();
}
function conFocus() { const t = CON.tabs.find((x) => x.id === CON.active); if (t) setTimeout(() => t.term.focus(), 0); }

function conRenderTabs() {
  $("#con-tablist").innerHTML = CON.tabs.map((t) => `<span class="con-tab ${t.id === CON.active ? "on" : ""} ${t.dead ? "dead" : ""}" data-tab="${t.id}">${esc(t.label)}${t.kind === "ai" ? `<button class="x" data-x="${t.id}" title="닫기 (프로세스 종료)">×</button>` : ""}</span>`).join("");
  for (const el of document.querySelectorAll("[data-tab]")) el.onclick = (e) => { if (!e.target.dataset.x) conActivate(el.dataset.tab); };
  for (const el of document.querySelectorAll("[data-x]")) el.onclick = () => conClose(el.dataset.x);
  const t = CON.tabs.find((x) => x.id === CON.active);
  $("#con-hint").textContent = !t ? "" : t.kind === "console" ? "am help · ↑↓ 기록 · Tab 완성 · 바꾸기 전 상태는 보관되고 am undo로 되돌립니다"
    : "이 라이브러리에서 실행 중 · 노드는 am 명령으로 바뀌고 그래프에 바로 반영됩니다 · 탭을 닫으면 종료";
}
function conActivate(id) {
  CON.active = id;
  for (const t of CON.tabs) t.pane.hidden = t.id !== id;
  conRenderTabs(); setTimeout(() => { conFitAll(); conFocus(); }, 0);
}
function conPane() {
  const pane = document.createElement("div"); pane.className = "con-pane";
  const note = document.createElement("div"); note.className = "con-note"; note.hidden = true;
  const xt = document.createElement("div"); xt.className = "xt";
  pane.append(note, xt); $("#con-body").appendChild(pane);
  return { pane, note, xt };
}

/* 콘솔 탭 */
function conAddConsole() {
  const { pane, note, xt } = conPane();
  const { term, fit } = conNewTerm(xt);
  const tab = { id: "console", kind: "console", label: "콘솔", pane, note, term, fit, line: "", cur: 0, hist: [], hi: -1, busy: false };
  try { tab.hist = JSON.parse(localGet("consoleHist") || "[]"); } catch {}
  CON.tabs.push(tab);
  term.write("Assets Manager 콘솔 — \x1b[1mam help\x1b[0m 로 명령을 봅니다. (예: am show <id>, am inherit <id> <부모>, am use <세트> <원칙>)\r\n");
  term.write(CON_PROMPT);
  term.onData((d) => conConsoleInput(tab, d));
  conActivate("console");
}
function conRedraw(tab) {
  const t = tab.term;
  t.write("\r\x1b[2K" + CON_PROMPT + tab.line);
  const back = conWidth(tab.line.slice(tab.cur));
  if (back) t.write(`\x1b[${back}D`);
}
function conWidth(s) { let w = 0; for (const ch of s) w += /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/.test(ch) ? 2 : 1; return w; }
function conIds() { return (state.graph?.nodes || []).filter((n) => n.type !== "ai" && n.type !== "installed_skill" && n.kind !== "unregistered").map((n) => n.id); }
const CON_CMDS = ["help", "ls", "show", "find", "standard", "inherit", "use", "unuse", "set", "unset", "rename", "new", "undo", "tidy"];
function conComplete(tab) {
  const before = tab.line.slice(0, tab.cur);
  const m = before.match(/(\S*)$/); const word = m[1];
  const first = !/\S\s/.test(before.replace(/^am\s+/, ""));
  const pool = first ? CON_CMDS : conIds();
  const hits = pool.filter((x) => x.startsWith(word));
  if (!hits.length) return;
  if (hits.length === 1) {
    const add = hits[0].slice(word.length) + " ";
    tab.line = before + add + tab.line.slice(tab.cur); tab.cur += add.length; return conRedraw(tab);
  }
  let pre = hits[0]; for (const h of hits) while (!h.startsWith(pre)) pre = pre.slice(0, -1);
  if (pre.length > word.length) { const add = pre.slice(word.length); tab.line = before + add + tab.line.slice(tab.cur); tab.cur += add.length; return conRedraw(tab); }
  tab.term.write("\r\n" + hits.slice(0, 60).join("  ") + "\r\n"); conRedraw(tab);
}
async function conConsoleInput(tab, d) {
  if (tab.busy) return;
  // 여러 줄 붙여넣기: 줄마다 실행
  if (d.length > 1 && /[\r\n]/.test(d) && !d.startsWith("\x1b")) {
    const lines = d.split(/\r\n|\r|\n/);
    for (let i = 0; i < lines.length; i++) {
      tab.line = tab.line.slice(0, tab.cur) + lines[i] + tab.line.slice(tab.cur); tab.cur = tab.line.length;
      if (i < lines.length - 1) { conRedraw(tab); await conSubmit(tab); }
    }
    return conRedraw(tab);
  }
  switch (d) {
    case "\r": return conSubmit(tab);
    case "\x7f": case "\b":
      if (tab.cur > 0) { const a = [...tab.line.slice(0, tab.cur)]; a.pop(); const head = a.join(""); tab.line = head + tab.line.slice(tab.cur); tab.cur = head.length; conRedraw(tab); } return;
    case "\x1b[3~": if (tab.cur < tab.line.length) { const rest = [...tab.line.slice(tab.cur)]; rest.shift(); tab.line = tab.line.slice(0, tab.cur) + rest.join(""); conRedraw(tab); } return;
    case "\x1b[D": if (tab.cur > 0) { tab.cur -= [...tab.line.slice(0, tab.cur)].pop().length; conRedraw(tab); } return;
    case "\x1b[C": if (tab.cur < tab.line.length) { tab.cur += [...tab.line.slice(tab.cur)][0].length; conRedraw(tab); } return;
    case "\x1b[H": case "\x01": tab.cur = 0; return conRedraw(tab);
    case "\x1b[F": case "\x05": tab.cur = tab.line.length; return conRedraw(tab);
    case "\x1b[A": case "\x1b[B": {
      if (!tab.hist.length) return;
      if (d === "\x1b[A") tab.hi = tab.hi < 0 ? tab.hist.length - 1 : Math.max(0, tab.hi - 1);
      else tab.hi = tab.hi < 0 ? -1 : tab.hi + 1 >= tab.hist.length ? -1 : tab.hi + 1;
      tab.line = tab.hi < 0 ? "" : tab.hist[tab.hi]; tab.cur = tab.line.length; return conRedraw(tab);
    }
    case "\t": return conComplete(tab);
    case "\x03": tab.term.write("^C\r\n"); tab.line = ""; tab.cur = 0; tab.hi = -1; return tab.term.write(CON_PROMPT);
    case "\x0c": tab.term.clear(); return conRedraw(tab);
    case "\x15": tab.line = tab.line.slice(tab.cur); tab.cur = 0; return conRedraw(tab);
  }
  if (d.startsWith("\x1b")) return; // 그 밖의 제어 시퀀스는 무시
  const text = d.replace(/[\x00-\x1f\x7f]/g, "");
  if (!text) return;
  tab.line = tab.line.slice(0, tab.cur) + text + tab.line.slice(tab.cur); tab.cur += text.length; conRedraw(tab);
}
async function conSubmit(tab) {
  const line = tab.line.trim();
  tab.term.write("\r\n"); tab.line = ""; tab.cur = 0; tab.hi = -1;
  if (!line) return tab.term.write(CON_PROMPT);
  if (tab.hist[tab.hist.length - 1] !== line) { tab.hist.push(line); tab.hist = tab.hist.slice(-200); localSet("consoleHist", JSON.stringify(tab.hist)); }
  if (line === "clear" || line === "cls") { tab.term.clear(); return tab.term.write(CON_PROMPT); }
  tab.busy = true;
  try {
    const res = await conPost("/api/console", { line });
    const out = (res.output || "").replace(/\r?\n/g, "\r\n");
    tab.term.write(res.code ? `\x1b[31m${out}\x1b[0m` : out);
    if (res.changed && res.changed.length) await conRefresh(res.changed);
  } catch (e) { tab.term.write(`\x1b[31m오류: ${conErr(e)}\x1b[0m\r\n`); }
  tab.busy = false;
  tab.term.write(CON_PROMPT);
}

/* 서버가 다시 시작되는 몇 초 동안은 연결이 끊긴다: 한 번 기다렸다 다시 시도하고, 그래도 안 되면 알아볼 수 있는 말로 */
async function conPost(path, body) {
  try { return await post(path, body); }
  catch (e) {
    if (!/fetch|network|load failed/i.test(e.message)) throw e;
    await new Promise((r) => setTimeout(r, 1500));
    return await post(path, body);
  }
}
function conErr(e) {
  return /fetch|network|load failed/i.test(e.message)
    ? "서버에 연결하지 못했습니다 (서버가 다시 시작 중이거나 꺼짐). 잠시 뒤 다시 해 보고, 계속되면 start.bat을 다시 실행하세요."
    : e.message;
}

/* AI 탭 */
async function conAddAI(tool) {
  if (CON.tabs.some((t) => t.kind === "ai" && !t.dead)) {
    const live = CON.tabs.find((t) => t.kind === "ai" && !t.dead);
    toast("AI 탭은 하나만 띄웁니다. 열려 있는 탭으로 이동합니다."); return conActivate(live.id);
  }
  const { pane, note, xt } = conPane();
  pane.hidden = true;
  const { term, fit } = conNewTerm(xt);
  const tab = { id: "ai-" + Date.now(), kind: "ai", tool, label: tool === "codex" ? "Codex" : "Claude Code", pane, note, term, fit, sid: null, next: 0, dead: false, idleAsked: false };
  CON.tabs.push(tab); conActivate(tab.id);
  await new Promise((r) => setTimeout(r, 30)); try { fit.fit(); } catch {}
  term.write(`\x1b[2m${tab.label} 시작 중… (작업 폴더: 라이브러리, am 명령 사용 가능)\x1b[0m\r\n`);
  let res;
  try { res = await conPost("/api/term/start", { tool, cols: term.cols, rows: term.rows }); } catch (e) { res = { error: conErr(e) }; }
  if (res.error) {
    term.write(`\x1b[31m${res.error}\x1b[0m\r\n`); tab.dead = true; conRenderTabs();
    if (res.need === "connect") ensureLocalAI("AI 탭", () => { conClose(tab.id); conAddAI(tool); });
    return;
  }
  tab.sid = res.session.id; tab.label = res.session.label || tab.label; conRenderTabs();
  // 입력: 짧게 모아서 보낸다
  let q = "", timer = null;
  term.onData((d) => { if (tab.dead) return; q += d; if (!timer) timer = setTimeout(() => { const data = q; q = ""; timer = null; post(`/api/term/${tab.sid}/write`, { data }).catch(() => {}); }, 8); tab.idleAsked = false; note.hidden = true; });
  term.onResize(({ cols, rows }) => { if (!tab.dead) post(`/api/term/${tab.sid}/resize`, { cols, rows }).catch(() => {}); });
  conReadLoop(tab);
}
async function conReadLoop(tab) {
  // 긴 폴링: 서버가 새 출력이 올 때까지 최대 8초 기다렸다 돌려준다 (기다리는 동안 CPU를 쓰지 않음)
  while (!tab.dead && CON.tabs.includes(tab)) {
    let r;
    try { r = await api(`/api/term/${tab.sid}/read?since=${tab.next}&wait=8`); }
    catch (e) { await new Promise((ok) => setTimeout(ok, 1500)); if (/없는 세션/.test(e.message)) { tab.dead = true; break; } continue; }
    if (r.data) tab.term.write(r.data);
    tab.next = r.next;
    if (!r.alive) { tab.dead = true; tab.term.write(`\r\n\x1b[2m[종료됨${r.exit_code != null ? ": " + r.exit_code : ""}] 탭을 닫거나 + AI로 새로 여세요.\x1b[0m\r\n`); break; }
    const hint = CON.idleHint || 1800;
    if (r.idle > hint && !tab.idleAsked) {
      tab.idleAsked = true;
      tab.note.innerHTML = `${Math.round(r.idle / 60)}분 동안 입력이 없었습니다. ${esc(tab.label)}을(를) 닫아 메모리를 비울까요? <button data-act="close">닫기</button><button data-act="keep" class="ghost">계속 두기</button>`;
      tab.note.hidden = false;
      tab.note.querySelector("[data-act=close]").onclick = () => conClose(tab.id);
      tab.note.querySelector("[data-act=keep]").onclick = () => { tab.note.hidden = true; };
    }
  }
  conRenderTabs();
}
async function conClose(id) {
  const tab = CON.tabs.find((t) => t.id === id);
  if (!tab || tab.kind !== "ai") return;
  tab.dead = true;
  if (tab.sid) post(`/api/term/${tab.sid}/close`, {}).catch(() => {});
  tab.term.dispose(); tab.pane.remove();
  CON.tabs = CON.tabs.filter((t) => t !== tab);
  conActivate("console");
}

/* 파일 변경 감지 → 그래프 갱신, 바뀐 노드 깜빡이기 */
function conSnapshot() { return Object.fromEntries((state.graph?.nodes || []).map((n) => [n.id, JSON.stringify(n)])); }
async function conRefresh(hint) {
  if (document.querySelector("dialog[open]")) { CON.pending = true; return; } // 대화상자를 보는 중이면 닫힌 뒤에
  const before = conSnapshot();
  await loadGraph();
  try { CON.sig = (await api("/api/changes")).sig; } catch {}
  const after = conSnapshot();
  const changed = new Set(hint || []);
  for (const [id, j] of Object.entries(after)) if (before[id] !== j && Object.keys(before).length) changed.add(id);
  for (const id of changed) {
    const n = state.cy && state.cy.getElementById(id);
    if (n && n.length) { n.addClass("flash"); setTimeout(() => n.removeClass("flash"), 2400); }
  }
}
async function conPoll() {
  const active = document.visibilityState === "visible" && document.hasFocus();
  if (active && state.graph) {
    try {
      const { sig } = await api("/api/changes");
      if (CON.sig && sig !== CON.sig) await conRefresh();
      else if (CON.pending && !document.querySelector("dialog[open]")) { CON.pending = false; await conRefresh(); }
      CON.sig = sig;
    } catch {}
  }
  setTimeout(conPoll, active ? 2000 : 4000);
}

$("#console-btn").addEventListener("click", () => conToggle());
$("#con-hide").addEventListener("click", () => conToggle(false));
$("#con-add").addEventListener("change", (e) => { const v = e.target.value; e.target.value = ""; if (v) conAddAI(v); });
document.addEventListener("keydown", (e) => { if (e.ctrlKey && e.key === "`") { e.preventDefault(); conToggle(); } });
waitForServer().then(async () => {
  try { const t = await api("/api/term"); CON.idleHint = t.idle_hint; CON.termOk = t.available; if (!t.available.ok) $("#con-add").title = t.available.hint; } catch {}
  try { CON.sig = (await api("/api/changes")).sig; } catch {}
  setTimeout(conPoll, 2000);
  if (localGet("console") === "1") conToggle(true);
}).catch(() => {});
