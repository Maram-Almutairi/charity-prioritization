// أولويات الدعم — dashboard (vanilla JS). The working list lives only in this page's memory;
// the server scores what it is sent and stores nothing.
"use strict";

const API = "/api";
const PRIORITY_AR = { high: "عالية", medium: "متوسطة", low: "منخفضة" };
const PRIORITY_RANK = { high: 3, medium: 2, low: 1 };
const STATUS_AR = { new: "جديدة", under_review: "قيد المراجعة", approved: "معتمدة", deferred: "مؤجلة" };
const QUADRANT_AR = { urgent: "تدخّل عاجل", prevent: "وقاية مبكرة", support: "دعم الاحتياج", monitor: "متابعة دورية" };
const GROUPS = [
  { id: "family", title: "الأسرة", note: "" },
  { id: "income", title: "الدخل", note: "ريال شهريًا" },
  { id: "expenses", title: "المصروفات والسكن", note: "ريال شهريًا" },
  { id: "debt", title: "الديون الحالية", note: "اتركها فارغة إذا لا يوجد دين" },
  { id: "health", title: "الصحة والخدمات", note: "" },
];
const STUDENT_FIELDS = ["children_primary", "children_intermediate", "children_secondary", "children_university"];

const fmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const pct = (v) => `${Math.round(v * 100)}%`;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : `h-${Date.now()}-${Math.random().toString(16).slice(2)}`);
const money = (v) => `<span class="num">${fmt.format(Math.round(v || 0))}</span>`;

const state = {
  schema: null, inputFields: [], modelInfo: null,
  households: [], budget: null, allocation: null,
  filter: "all", search: "", editingId: null, lastReport: null,
};

/* ================= API ================= */
// "server": Flask API (local). "browser": the same Python running in the page (static hosting).
let MODE = "server";

function apiError(data) {
  const err = new Error(data.message || (data.fields?._columns) || "تعذّر الاتصال بالخادم.");
  err.fields = data.fields;
  return err;
}

async function api(path, { method = "GET", body, file } = {}) {
  if (MODE === "browser") {
    const { status, json } = await PyEngine.call(path, body ?? null, file);
    if (status !== 200) throw apiError(json);
    return json;
  }
  let form;
  if (file) { form = new FormData(); form.append("file", file); }
  const res = await fetch(API + path, {
    method,
    headers: form ? undefined : { "Content-Type": "application/json" },
    body: form || (body ? JSON.stringify(body) : undefined),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.message || (data.fields?._columns) || "تعذّر الاتصال بالخادم.");
    err.fields = data.fields;
    throw err;
  }
  return data;
}

async function download(path, body, filename) {
  let blob;
  if (MODE === "browser") {
    const { status, json, bytes } = await PyEngine.call(path, body);
    if (status !== 200) throw apiError(json || {});
    blob = new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  } else {
    const res = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).message || "تعذّر إنشاء الملف.");
    blob = await res.blob();
  }
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

const inputsOf = (h) => Object.fromEntries(state.inputFields.map((k) => [k, h[k]]));

/* ================= Feedback ================= */
function toast(message, kind = "info") {
  const el = Object.assign(document.createElement("div"), { className: `toast${kind === "error" ? " toast--error" : ""}`, textContent: message });
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 4200);
}

function confirmDialog(title, text, actions) {
  const dlg = $("#confirm");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  $("#confirm-actions").innerHTML = actions.map((a) =>
    `<button class="btn ${a.cls || ""}" value="${a.value}">${esc(a.label)}</button>`).join("") +
    `<button class="btn btn--ghost" value="cancel">إلغاء</button>`;
  dlg.showModal();
  return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue), { once: true }));
}

/* ================= Navigation ================= */
function showView(name) {
  $$(".segmented [data-view]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === name)));
  $(`.segmented [data-view="${name}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
  if (name === "method") renderMethod();
  if (name === "dashboard") renderMap();
  window.scrollTo({ top: 0 });
}
$$(".segmented [data-view]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

/* ================= Ranking & derived ================= */
const effective = (h) => h.final_priority || h.priority;
function ranked() {
  return [...state.households].sort((a, b) =>
    PRIORITY_RANK[effective(b)] - PRIORITY_RANK[effective(a)] || b.combined_score - a.combined_score);
}

/* ================= Import / demo / add ================= */
async function addFamilies(list, { source }) {
  let mode = "append";
  if (state.households.length && source === "import") {
    const choice = await confirmDialog("إضافة أم استبدال؟",
      `في القائمة الآن ${state.households.length} أسرة. الأسر ذات الرمز نفسه في الملف ستُحدَّث في الحالتين.`,
      [{ value: "append", label: "إضافة إلى القائمة", cls: "btn--primary" }, { value: "replace", label: "استبدال القائمة" }]);
    if (choice === "cancel" || !choice) return false;
    mode = choice;
  }
  if (mode === "replace") state.households = [];
  const byCode = new Map(state.households.map((h) => [h.ref_code, h]));
  let added = 0, updated = 0;
  for (const item of list) {
    const existing = item.ref_code && byCode.get(item.ref_code);
    if (existing) { Object.assign(existing, item, { id: existing.id }); updated++; }
    else { const h = { ...item, id: uid() }; state.households.push(h); byCode.set(h.ref_code, h); added++; }
  }
  refreshAll();
  toast(`أُضيفت ${added} أسرة${updated ? `، وحُدّثت ${updated}` : ""}.`);
  return true;
}

async function importFile(file) {
  if (!file) return;
  if (!/\.(xlsx|xlsm|csv)$/i.test(file.name)) return toast("ارفع ملف Excel ‎(.xlsx)‎ أو CSV.", "error");
  try {
    toast("جارٍ قراءة الملف وتنظيفه…");
    const data = await api("/import", { method: "POST", file });
    state.lastReport = { name: file.name, ...data };
    const ok = await addFamilies(data.households, { source: "import" });
    if (ok !== false) { renderReport(); showView("families"); }
  } catch (err) {
    toast(err.message, "error");
  } finally {
    $("#file-input").value = "";
  }
}

async function loadDemo() {
  try {
    const data = await api("/demo?n=30");
    await addFamilies(data.households, { source: "demo" });
    showView("dashboard");
  } catch (err) { toast(err.message, "error"); }
}

/* ================= Rendering: shared ================= */
function refreshAll() {
  const n = state.households.length;
  $("#nav-count").hidden = !n;
  $("#nav-count").textContent = n;
  $("#dash-empty").hidden = n > 0;
  $("#dash-content").hidden = n === 0;
  renderKpis();
  renderMap();
  renderTopList();
  renderTable();
  scheduleAllocation();
}

function badge(p, lg) { return `<span class="badge badge--${p}${lg ? " badge--lg" : ""}">${PRIORITY_AR[p]}</span>`; }
function quadTag(q) { return `<span class="tag tag--${q}">${QUADRANT_AR[q]}</span>`; }

/* ================= Dashboard ================= */
function renderKpis() {
  const H = state.households;
  if (!H.length) return;
  const high = H.filter((h) => effective(h) === "high").length;
  const prevent = H.filter((h) => h.quadrant === "prevent").length;
  const deficit = H.reduce((s, h) => s + (h.monthly_deficit || 0), 0);
  const review = H.filter((h) => h.needs_review).length;
  const tiles = [
    ["إجمالي الأسر", fmt.format(H.length), "", "في القائمة الحالية"],
    ["أولوية عالية", fmt.format(high), "high", `${pct(high / H.length)} من الأسر`],
    ["وقاية مبكرة", fmt.format(prevent), "prevent", "مخاطر عالية قبل أن تتفاقم الحاجة"],
    ["العجز الشهري الكلي", fmt.format(deficit), "", "ريال — الفرق بين المصروفات والدخل"],
    ["تحتاج مراجعة", fmt.format(review), "review", "ثقة النموذج أقل من 60%"],
  ];
  $("#kpis").innerHTML = tiles.map(([label, value, dot, note]) => `
    <div class="kpi glass">
      <span class="kpi__label">${dot ? `<span class="dot dot--${dot}"></span>` : ""}${label}</span>
      <span class="kpi__value num">${value}</span>
      <span class="kpi__note">${note}</span>
    </div>`).join("");
}

function mapSVG(households, theme = "screen", compact = false) {
  const W = compact ? 420 : 640, H = compact ? 440 : 420;
  const m = compact ? { l: 40, r: 10, t: 14, b: 44 } : { l: 48, r: 18, t: 18, b: 46 };
  const fs = compact ? 1.25 : 1;
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const x = (v) => m.l + (v / 100) * iw;
  const y = (v) => m.t + ih - (v / 100) * ih;
  const T = theme === "print"
    ? { ink: "#0d1726", ink3: "#5f6b82", grid: "#dde3ec", ring: "#fff", quad: { urgent: "rgba(235,104,52,.11)", prevent: "rgba(124,92,230,.09)", support: "rgba(42,120,214,.08)", monitor: "rgba(20,40,80,.02)" }, label: "#33405a" }
    : { ink: "#0d1726", ink3: "#5f6b82", grid: "rgba(20,40,80,.10)", ring: "#ffffff", quad: { urgent: "rgba(235,104,52,.11)", prevent: "rgba(124,92,230,.09)", support: "rgba(42,120,214,.08)", monitor: "rgba(255,255,255,.35)" }, label: "#33405a" };
  const counts = { urgent: 0, prevent: 0, support: 0, monitor: 0 };
  households.forEach((h) => counts[h.quadrant]++);
  const colors = { high: "#eb6834", medium: "#2a78d6", low: "#1baf7a" };
  const quads = [
    ["prevent", 0, 50, 50, 100, "start"], ["urgent", 50, 100, 50, 100, "end"],
    ["monitor", 0, 50, 0, 50, "start"], ["support", 50, 100, 0, 50, "end"],
  ];
  const ticks = [0, 25, 50, 75, 100];
  // Draw the most urgent last so they sit on top.
  const pts = [...households].sort((a, b) => PRIORITY_RANK[effective(a)] - PRIORITY_RANK[effective(b)]);
  return `
  <svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" font-family="Noto Sans Arabic, Segoe UI, sans-serif">
    <rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="#ffffff" fill-opacity="${theme === "print" ? 1 : 0.94}"/>
    ${quads.map(([q, x0, x1, y0, y1]) => `<rect class="quad" x="${x(x0)}" y="${y(y1)}" width="${x(x1) - x(x0)}" height="${y(y0) - y(y1)}" fill="${T.quad[q]}"/>`).join("")}
    ${ticks.map((t) => `<line x1="${x(t)}" x2="${x(t)}" y1="${m.t}" y2="${m.t + ih}" stroke="${T.grid}" ${t === 50 ? 'stroke-dasharray="4 4" stroke-width="1.4"' : ""}/>
      <line x1="${m.l}" x2="${m.l + iw}" y1="${y(t)}" y2="${y(t)}" stroke="${T.grid}" ${t === 50 ? 'stroke-dasharray="4 4" stroke-width="1.4"' : ""}/>`).join("")}
    <g class="axis" font-size="${11 * fs}" fill="${T.ink3}">
      ${ticks.map((t) => `<text x="${x(t)}" y="${m.t + ih + 16}" text-anchor="middle">${t}</text><text x="${m.l - 8}" y="${y(t) + 4}" text-anchor="end">${t}</text>`).join("")}
      <text x="${m.l + iw / 2}" y="${H - 6}" text-anchor="middle" fill="${T.ink}" font-size="${12 * fs}" font-weight="600">درجة الحاجة الآن ←</text>
      <text transform="translate(${12 * fs} ${m.t + ih / 2}) rotate(-90)" text-anchor="middle" fill="${T.ink}" font-size="${12 * fs}" font-weight="600">درجة المخاطر ←</text>
    </g>
    ${pts.map((h) => `<circle class="pt" data-id="${h.id}" cx="${x(h.need_score).toFixed(1)}" cy="${y(h.risk_score).toFixed(1)}" r="6" fill="${colors[effective(h)]}" stroke="${T.ring}" stroke-width="1.6"/>`).join("")}
    ${quads.map(([q, x0, x1, y0, y1, anchor]) => `<text class="quad-label" x="${anchor === "end" ? x(x1) - 8 : x(x0) + 8}" y="${y(y1) + 18 * fs}" text-anchor="${anchor}" font-size="${12 * fs}" font-weight="700" fill="${T.label}" stroke="#fff" stroke-opacity=".92" stroke-width="${4 * fs}" stroke-linejoin="round" paint-order="stroke">${QUADRANT_AR[q]} · ${counts[q]}</text>`).join("")}
    ${theme === "screen" ? pts.map((h) => `<circle class="pt-hit" data-id="${h.id}" cx="${x(h.need_score).toFixed(1)}" cy="${y(h.risk_score).toFixed(1)}" r="12"/>`).join("") : ""}
  </svg>`;
}

function renderMap() {
  const H = state.households;
  if (!H.length || $("#view-dashboard").hidden) return;
  $("#map").innerHTML = mapSVG(H, "screen", $("#map").clientWidth < 560);
  $("#map-legend").innerHTML = ["high", "medium", "low"].map((p) =>
    `<span><span class="dot dot--${p}"></span>${PRIORITY_AR[p]}</span>`).join("");
}

// Signature interaction: glass lens over the hovered family; click opens its sheet.
const lens = $("#lens");
$("#map").addEventListener("pointerover", (ev) => {
  const hit = ev.target.closest(".pt-hit");
  if (!hit) return;
  const h = state.households.find((x) => x.id === hit.dataset.id);
  if (!h) return;
  $$(".pt.is-hover").forEach((p) => p.classList.remove("is-hover"));
  $(`.pt[data-id="${h.id}"]`)?.classList.add("is-hover");
  const box = $(".map-pane").getBoundingClientRect();
  const r = hit.getBoundingClientRect();
  lens.innerHTML = `
    <div class="assessment__head"><b>${esc(h.ref_code)}</b>${badge(effective(h))}</div>
    <div class="lens__row"><span>الحاجة</span><span class="num">${Math.round(h.need_score)}</span></div>
    <div class="lens__row"><span>المخاطر</span><span class="num">${Math.round(h.risk_score)}</span></div>
    <div class="lens__row"><span>العجز الشهري</span><span>${money(h.monthly_deficit)} ريال</span></div>
    <div class="lens__row"><span>التصنيف</span><span>${QUADRANT_AR[h.quadrant]}</span></div>`;
  lens.style.left = `${r.left + r.width / 2 - box.left}px`;
  lens.style.top = `${r.top - box.top}px`;
  lens.hidden = false;
});
$("#map").addEventListener("pointerout", (ev) => {
  if (ev.target.closest(".pt-hit")) { lens.hidden = true; $$(".pt.is-hover").forEach((p) => p.classList.remove("is-hover")); }
});
$("#map").addEventListener("click", (ev) => {
  const hit = ev.target.closest(".pt-hit");
  if (hit) openSheet(hit.dataset.id);
});

function renderTopList() {
  const top = ranked().slice(0, 6);
  $("#toplist").innerHTML = top.map((h, i) => `
    <li><button type="button" data-open="${h.id}">
      <span class="rank num">${i + 1}</span>
      <span class="code">${esc(h.ref_code)}</span>
      ${badge(effective(h))}
      <span class="sub">${QUADRANT_AR[h.quadrant]} · عجز ${money(h.monthly_deficit)} ريال</span>
    </button></li>`).join("");
}
$("#toplist").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-open]");
  if (b) openSheet(b.dataset.open);
});

/* ================= Budget ================= */
let allocTimer = null;
function scheduleAllocation() {
  clearTimeout(allocTimer);
  if (!state.budget || !state.households.length) { state.allocation = null; renderAllocation(); return; }
  allocTimer = setTimeout(runAllocation, 250);
}

async function runAllocation() {
  try {
    const body = { budget: state.budget, households: state.households.map((h) => ({ ...inputsOf(h), id: h.id, final_priority: h.final_priority })) };
    state.allocation = await api("/allocate", { method: "POST", body });
  } catch (err) {
    state.allocation = null;
    toast(err.message, "error");
  }
  renderAllocation();
  renderTable();
}

function renderAllocation() {
  const box = $("#alloc-summary");
  const a = state.allocation;
  if (!a) {
    box.innerHTML = `<p class="alloc__hint">أدخل الميزانية لترى كم أسرة تغطيها، وكم يبقى من الاحتياج.</p>`;
    return;
  }
  const total = a.allocated_total || 1;
  box.innerHTML = `
    <div class="alloc__big"><strong class="num">${pct(a.coverage)}</strong><span>من الاحتياج المقدّر مغطى بهذه الميزانية</span></div>
    <div class="stack" role="img" aria-label="توزيع المبلغ حسب الأولوية">
      ${["high", "medium", "low"].map((t) => `<i class="s-${t}" style="width:${(a.by_tier[t].allocated / total) * 100}%"></i>`).join("")}
    </div>
    <div class="alloc__rows">
      ${["high", "medium", "low"].map((t) => {
        const tier = a.by_tier[t];
        const share = tier.target ? Math.min(1, tier.allocated / tier.target) : 0;
        return `<div class="alloc__row"><span><span class="dot dot--${t}"></span> ${PRIORITY_AR[t]} <span class="tag num">${tier.families}</span></span>
          <span class="bar"><i class="s-${t}" style="width:${share * 100}%;background:var(--${t})"></i></span>
          <span>${money(tier.allocated)} <small class="hint">من</small> ${money(tier.target)}</span></div>`;
      }).join("")}
    </div>
    <div class="alloc__facts">
      <div class="fact"><span>المخصّص</span><b>${money(a.allocated_total)} <small>ريال</small></b></div>
      <div class="fact"><span>المتبقي من الميزانية</span><b>${money(a.remaining)} <small>ريال</small></b></div>
      <div class="fact"><span>إجمالي الاحتياج المقدّر</span><b>${money(a.total_target)} <small>ريال</small></b></div>
      <div class="fact ${a.uncovered ? "fact--warn" : ""}"><span>أسر لم تُغطَّ (تحتاج قرارك)</span><b class="num">${fmt.format(a.uncovered)}</b></div>
    </div>`;
}

$("#budget-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const v = Number(ev.target.budget.value);
  showErrors(ev.target, v > 0 ? {} : { budget: "أدخل ميزانية أكبر من صفر." });
  if (!(v > 0)) return;
  state.budget = v;
  scheduleAllocation();
});

/* ================= Families table ================= */
function visibleFamilies() {
  const q = state.search.trim().toLowerCase();
  return ranked().map((h, i) => ({ h, rank: i + 1 })).filter(({ h }) => {
    if (q && !String(h.ref_code).toLowerCase().includes(q)) return false;
    switch (state.filter) {
      case "all": return true;
      case "review": return h.needs_review;
      case "prevent": return h.quadrant === "prevent";
      default: return effective(h) === state.filter;
    }
  });
}

const ICONS = {
  view: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2.5 10s2.8-5.5 7.5-5.5S17.5 10 17.5 10s-2.8 5.5-7.5 5.5S2.5 10 2.5 10Z" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="10" cy="10" r="2.4" fill="currentColor"/></svg>',
  edit: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m12.8 3.7 3.5 3.5-8.6 8.6-4 .5.5-4 8.6-8.6Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  del: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 6h12M8 6V4.5h4V6m-6.5 0 .8 10h7.4l.8-10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

function renderTable() {
  const rows = visibleFamilies();
  const alloc = new Map((state.allocation?.rows || []).map((r) => [r.id, r]));
  $("#table-empty").hidden = state.households.length > 0;
  $("#table-none").hidden = !state.households.length || rows.length > 0;
  $("#table").hidden = !state.households.length;
  $("#table tbody").innerHTML = rows.map(({ h, rank }) => {
    const a = alloc.get(h.id);
    const decision = ['<option value="">— بدون تعديل —</option>', ...Object.entries(PRIORITY_AR).map(([k, v]) =>
      `<option value="${k}" ${h.final_priority === k ? "selected" : ""}>${v}</option>`)].join("");
    const status = Object.entries(STATUS_AR).map(([k, v]) => `<option value="${k}" ${h.status === k ? "selected" : ""}>${v}</option>`).join("");
    return `<tr data-id="${h.id}" class="${h.final_priority && h.final_priority !== h.priority ? "is-overridden" : ""}">
      <td class="num">${rank}</td>
      <td class="code">${esc(h.ref_code)}</td>
      <td>${badge(h.priority)} ${h.needs_review ? '<span class="flag" title="ثقة النموذج أقل من 60%">مراجعة</span>' : ""}</td>
      <td><select data-field="final_priority" aria-label="قرار المسؤول للأسرة ${esc(h.ref_code)}">${decision}</select></td>
      <td><span class="meter meter--need"><i><b style="width:${h.need_score}%"></b></i><span class="num">${Math.round(h.need_score)}</span></span></td>
      <td><span class="meter meter--risk"><i><b style="width:${h.risk_score}%"></b></i><span class="num">${Math.round(h.risk_score)}</span></span></td>
      <td>${quadTag(h.quadrant)}</td>
      <td>${money(h.monthly_deficit)}</td>
      <td>${a ? money(a.target) : "—"}</td>
      <td>${a ? `${money(a.allocated)}<span class="alloc-note ${a.allocated === 0 && a.target > 0 ? "alloc-note--none" : ""}">${esc(a.allocation_note)}</span>` : "—"}</td>
      <td><select data-field="status" aria-label="حالة الطلب">${status}</select></td>
      <td><input data-field="note" value="${esc(h.note)}" maxlength="500" placeholder="ملاحظة…" aria-label="ملاحظة المسؤول"></td>
      <td><div class="acts">
        <button class="icon-btn" data-act="view" title="عرض الأسباب" aria-label="عرض">${ICONS.view}</button>
        <button class="icon-btn" data-act="edit" title="تعديل" aria-label="تعديل">${ICONS.edit}</button>
        <button class="icon-btn icon-btn--del" data-act="delete" title="حذف" aria-label="حذف">${ICONS.del}</button>
      </div></td>
    </tr>`;
  }).join("");
}

$("#table").addEventListener("change", (ev) => {
  const el = ev.target.closest("[data-field]");
  if (!el) return;
  const h = state.households.find((x) => x.id === el.closest("tr").dataset.id);
  if (!h) return;
  h[el.dataset.field] = el.dataset.field === "final_priority" ? (el.value || null) : el.value;
  if (el.dataset.field === "final_priority") refreshAll();
});

$("#table").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-act]");
  if (!btn) return;
  const h = state.households.find((x) => x.id === btn.closest("tr").dataset.id);
  if (!h) return;
  if (btn.dataset.act === "view") openSheet(h.id);
  if (btn.dataset.act === "edit") startEdit(h);
  if (btn.dataset.act === "delete") deleteFamily(h);
});

async function deleteFamily(h) {
  const ok = await confirmDialog(`حذف الأسرة ${h.ref_code}؟`,
    "تُزال من القائمة الحالية فقط. لخفض أولويتها بدل حذفها، استخدم «قرار المسؤول».",
    [{ value: "yes", label: "حذف", cls: "btn--danger" }]);
  if (ok !== "yes") return;
  state.households = state.households.filter((x) => x.id !== h.id);
  $("#sheet").open && $("#sheet").close();
  refreshAll();
  toast(`حُذفت الأسرة ${h.ref_code}.`);
}

$("#search").addEventListener("input", (ev) => { state.search = ev.target.value; renderTable(); });
$$(".chip").forEach((c) => c.addEventListener("click", () => {
  state.filter = c.dataset.filter;
  $$(".chip").forEach((x) => x.classList.toggle("is-active", x === c));
  renderTable();
}));

/* ================= Import report ================= */
function renderReport() {
  const r = state.lastReport;
  if (!r) { $("#import-report").innerHTML = ""; return; }
  const s = r.summary;
  const ACT = { fixed: "صُحّح", rejected: "رُفض", review: "للتحقق" };
  const label = Object.fromEntries((state.schema?.fields || []).map((f) => [f.name, f.label]));
  $("#import-report").innerHTML = `
    <section class="report glass" aria-label="تقرير تنظيف الملف">
      <div class="report__head">
        <h2>تقرير تنظيف «${esc(r.name)}»</h2>
        <button class="btn btn--ghost btn--sm" type="button" data-action="close-report">إخفاء</button>
      </div>
      <div class="report__stats">
        <div>صفوف مقروءة<b class="num">${s.rows_read}</b></div>
        <div class="is-ok">مقبولة<b class="num">${s.accepted}</b></div>
        <div class="is-fix">صُحّحت تلقائيًا<b class="num">${s.accepted_with_fixes}</b></div>
        <div class="is-review">للتحقق<b class="num">${s.flagged_for_review}</b></div>
        <div class="is-bad">مرفوضة<b class="num">${s.rejected}</b></div>
        <div>صفوف فارغة تُجوهلت<b class="num">${s.blank_rows_skipped}</b></div>
      </div>
      ${s.unknown_columns?.length ? `<p class="alloc__hint" style="margin-top:8px">أعمدة غير معروفة تُجوهلت: ${esc(s.unknown_columns.join("، "))}</p>` : ""}
      ${r.issues.length ? `<details ${s.rejected ? "open" : ""}><summary>تفاصيل كل تعديل ورفض (${r.issues.length})</summary>
        <table class="issues"><thead><tr><th>الصف</th><th>العمود</th><th>القيمة في الملف</th><th>الإجراء</th><th>التفصيل</th></tr></thead><tbody>
        ${r.issues.map((i) => `<tr><td class="num">${i.row}</td><td>${esc(label[i.field] || i.field)}</td><td>${esc(i.value)}</td>
          <td><span class="act act--${i.action}">${ACT[i.action]}</span></td><td>${esc(i.message)}</td></tr>`).join("")}
        </tbody></table></details>` : ""}
    </section>`;
}

/* ================= Form ================= */
const form = $("#family-form");

function buildForm() {
  const fields = state.schema.fields;
  const html = GROUPS.map((g) => {
    const fs = fields.filter((f) => f.group === g.id);
    const numbers = fs.filter((f) => f.kind !== "choice" && !STUDENT_FIELDS.includes(f.name));
    const students = fs.filter((f) => STUDENT_FIELDS.includes(f.name));
    const choices = fs.filter((f) => f.kind === "choice");
    const numHtml = numbers.map((f) => `
      <label class="field" ${f.name === "monthly_rent" ? 'id="rent-field" hidden' : ""}>
        <span>${esc(f.label)}${f.required ? "" : ' <small class="hint">(اختياري)</small>'}</span>
        ${f.kind === "money" ? `<div class="input-unit"><input name="${f.name}" type="number" min="${f.min}" max="${f.max}" step="10" inputmode="decimal"><i>ريال</i></div>`
          : f.kind === "int" ? `<input name="${f.name}" type="number" min="${f.min}" max="${f.max}" step="1" inputmode="numeric" ${STUDENT_FIELDS.includes(f.name) ? 'placeholder="0"' : ""}>`
          : `<input name="${f.name}" type="text" maxlength="${f.max}" autocomplete="off" placeholder="مثال: R-1024">`}
        ${f.hint ? `<small class="hint">${esc(f.hint)}</small>` : ""}
        <em class="err" data-for="${f.name}"></em>
      </label>`).join("");
    const choiceHtml = choices.map((f) => `
      <div class="choice" role="radiogroup" aria-label="${esc(f.label)}">
        <span class="choice__label">${esc(f.label)}</span>
        <div class="seg">${Object.entries(f.choices).map(([v, l]) => `<label><input type="radio" name="${f.name}" value="${v}">${esc(l)}</label>`).join("")}</div>
        <em class="err" data-for="${f.name}"></em>
      </div>`).join("");
    return `<fieldset class="fs"><legend>${g.title}<small>${g.note}</small></legend>
      <div class="grid-fields">${numHtml}</div>
      ${students.length ? `<span class="subhead">عدد الطلاب في كل مرحلة دراسية (اتركها فارغة إذا لا يوجد)</span>
        <div class="grid-4">${students.map((f) => `<label class="field"><span>${esc(f.label.replace("طلاب ", ""))}</span>
          <input name="${f.name}" type="number" min="0" max="${f.max}" step="1" inputmode="numeric" placeholder="0">
          <em class="err" data-for="${f.name}"></em></label>`).join("")}</div>` : ""}
      ${choiceHtml ? `<div class="choice-grid" style="margin-top:12px">${choiceHtml}</div>` : ""}</fieldset>`;
  }).join("");
  $("#form-fields").innerHTML = html;
  $$('input[name="housing_status"]', form).forEach((r) => r.addEventListener("change", toggleRent));
  form.addEventListener("input", updateTotalLine);
  form.addEventListener("input", clearFieldError);
  form.addEventListener("change", clearFieldError);
  updateTotalLine();
}

function clearFieldError(ev) {
  const name = ev.target.name;
  if (!name) return;
  const err = $(`.err[data-for="${name}"]`, form);
  if (err) err.textContent = "";
  $$(`[name="${name}"]`, form).forEach((i) => i.removeAttribute("aria-invalid"));
}

function toggleRent() {
  const rented = form.elements.housing_status?.value === "rented";
  $("#rent-field").hidden = !rented;
  if (!rented) form.elements.monthly_rent.value = "";
  updateTotalLine();
}

function readForm() {
  const d = {};
  for (const f of state.schema.fields) {
    const el = form.elements[f.name];
    d[f.name] = el ? el.value : "";
  }
  return d;
}

function updateTotalLine() {
  const n = (k) => Number(form.elements[k]?.value || 0);
  const expenses = n("basic_expenses") + n("monthly_rent") + n("utility_bills") + n("monthly_debt_payment");
  const income = n("monthly_income");
  if (!expenses && !income) { $("#total-line").innerHTML = ""; return; }
  const diff = income - expenses;
  $("#total-line").innerHTML = `إجمالي المصروفات <b class="num">${fmt.format(expenses)}</b> ريال ·
    ${diff < 0 ? `عجز <b class="neg num">${fmt.format(-diff)}</b>` : `فائض <b class="num">${fmt.format(diff)}</b>`} ريال شهريًا`;
}

function validateClient(d) {
  const e = {};
  for (const f of state.schema.fields) {
    const v = d[f.name];
    if (f.kind === "choice") { if (!v) e[f.name] = "اختر قيمة."; continue; }
    if (f.kind === "text") continue;
    if (v === "" || v == null) { if (f.required) e[f.name] = "هذا الحقل مطلوب."; continue; }
    const num = Number(v);
    if (!Number.isFinite(num)) e[f.name] = "يجب أن يكون رقمًا.";
    else if (f.kind === "int" && !Number.isInteger(num)) e[f.name] = "يجب أن يكون رقمًا صحيحًا.";
    else if (num < f.min || num > f.max) e[f.name] = `يجب أن يكون بين ${fmt.format(f.min)} و ${fmt.format(f.max)}.`;
  }
  const students = STUDENT_FIELDS.reduce((s, k) => s + Number(d[k] || 0), 0);
  if (!e.household_size && students > Number(d.household_size) - 1) e.children_primary = "مجموع الطلاب يجب أن يكون أقل من عدد أفراد الأسرة.";
  if (d.housing_status === "rented" && !(Number(d.monthly_rent) > 0)) e.monthly_rent = "أدخل قيمة الإيجار الشهري للأسرة المستأجرة.";
  if (Number(d.monthly_debt_payment) > 0 && !(Number(d.total_debt) > 0)) e.total_debt = "يوجد قسط شهري بدون مبلغ دين.";
  return e;
}

function showErrors(root, errors = {}) {
  $$(".err", root).forEach((el) => (el.textContent = errors[el.dataset.for] || ""));
  $$("input", root).forEach((i) => i.removeAttribute("aria-invalid"));
  Object.keys(errors).forEach((k) => $$(`[name="${k}"]`, root).forEach((i) => i.setAttribute("aria-invalid", "true")));
  const first = Object.keys(errors)[0];
  if (first) $(`[name="${first}"]`, root)?.focus();
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const mode = ev.submitter?.dataset.mode || "save";
  const data = readForm();
  const errors = validateClient(data);
  showErrors(form, errors);
  $("#form-error").hidden = true;
  if (Object.keys(errors).length) return;
  const buttons = $$("button", form);
  buttons.forEach((b) => (b.disabled = true));
  try {
    const result = await api("/assess", { method: "POST", body: data });
    const clean = { ...inputsFromResult(data) };
    let saved = null;
    if (mode === "save") {
      if (state.editingId) {
        const h = state.households.find((x) => x.id === state.editingId);
        Object.assign(h, clean, result, { ref_code: clean.ref_code || h.ref_code });
        saved = { kind: "edited", h };
        stopEdit();
      } else {
        const h = { ...clean, ...result, id: uid(), final_priority: null, status: "new", note: "",
          ref_code: clean.ref_code || `F-${String(state.households.length + 1).padStart(4, "0")}` };
        state.households.push(h);
        saved = { kind: "added", h };
        form.reset();
        toggleRent();
      }
      refreshAll();
    }
    $("#result").innerHTML = `<div class="pane glass">${assessmentHTML({ ...clean, ...result, ref_code: saved?.h.ref_code || clean.ref_code }, saved)}</div>`;
  } catch (err) {
    if (err.fields) showErrors(form, err.fields);
    $("#form-error").textContent = err.fields ? "راجع الحقول المظللة." : err.message;
    $("#form-error").hidden = false;
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
});

function inputsFromResult(d) {
  const out = {};
  for (const f of state.schema.fields) {
    if (f.kind === "choice" || f.kind === "text") out[f.name] = d[f.name] || (f.kind === "text" ? "" : d[f.name]);
    else out[f.name] = d[f.name] === "" ? 0 : Number(d[f.name]);
  }
  if (out.housing_status !== "rented") out.monthly_rent = 0;
  return out;
}

form.addEventListener("reset", () => { setTimeout(() => { toggleRent(); updateTotalLine(); }); showErrors(form, {}); $("#form-error").hidden = true; });

function startEdit(h) {
  form.reset();
  state.editingId = h.id;
  for (const f of state.schema.fields) {
    const el = form.elements[f.name];
    if (!el) continue;
    const v = h[f.name];
    if (el instanceof RadioNodeList) el.value = String(v);
    else el.value = (f.kind !== "text" && (v === 0 || v == null) && !f.required) ? "" : v;
  }
  setTimeout(() => { toggleRent(); updateTotalLine(); });
  $("#edit-ref").textContent = h.ref_code;
  $("#edit-banner").hidden = false;
  $("#save-btn").textContent = "احفظ التعديل";
  $("#sheet").open && $("#sheet").close();
  showView("form");
}
function stopEdit() {
  state.editingId = null;
  $("#edit-banner").hidden = true;
  $("#save-btn").textContent = "قيّم وأضف للقائمة";
}
$("#cancel-edit").addEventListener("click", () => { form.reset(); stopEdit(); });

/* ================= Assessment card & sheet ================= */
function assessmentHTML(r, saved) {
  const factors = (r.score_breakdown || []).slice(0, 7).map((f) => `
    <li class="factor factor--${f.score}">
      <span>${esc(f.label)}<span class="kind kind--${f.score}">${f.score === "need" ? "حاجة" : "مخاطر"}</span></span>
      <span class="factor__pts num">+${f.points} / ${f.max_points}</span>
      <span class="track"><i style="width:${(f.points / f.max_points) * 100}%"></i></span>
    </li>`).join("");
  const model = (r.model_factors || []).map((f) => esc(f.label)).join("، ");
  return `<div class="assessment">
    <div class="assessment__head">
      <div><small>الأولوية المقترحة${r.ref_code ? ` · ${esc(r.ref_code)}` : ""}</small>${badge(r.priority, true)}</div>
      <div>${quadTag(r.quadrant)} ${r.needs_review ? '<span class="flag">ثقة منخفضة — راجعها يدويًا</span>' : ""}</div>
    </div>
    <div class="scores">
      <div class="score"><div class="score__top"><span class="score__label">درجة الحاجة الآن</span><span class="score__value num">${Math.round(r.need_score)}<small> / 100</small></span></div>
        <div class="track track--need"><i style="width:${r.need_score}%"></i></div></div>
      <div class="score"><div class="score__top"><span class="score__label">مخاطر الأزمة القريبة</span><span class="score__value num">${Math.round(r.risk_score)}<small> / 100</small></span></div>
        <div class="track track--risk"><i style="width:${r.risk_score}%"></i></div></div>
    </div>
    <div class="facts">
      <div class="fact"><span>إجمالي المصروفات</span><b>${money(r.total_expenses)}</b></div>
      <div class="fact"><span>العجز الشهري</span><b>${money(r.monthly_deficit)}</b></div>
      <div class="fact"><span>ثقة النموذج</span><b class="num">${Math.min(99, Math.floor(r.confidence * 100))}%</b></div>
    </div>
    <div><h3 style="margin-bottom:10px">لماذا هذه الدرجات؟</h3><ul class="factors">${factors || "<li class='hint'>لا توجد عوامل مؤثرة.</li>"}</ul></div>
    ${model ? `<p class="model-note">أكثر ما أثّر في تصنيف النموذج: ${model}.</p>` : ""}
    ${saved ? `<p class="model-note" style="color:var(--ok)">${saved.kind === "added" ? "أُضيفت الأسرة إلى القائمة" : "حُفظ التعديل وأُعيد التقييم"} (${esc(saved.h.ref_code)}).
      <button class="btn btn--ghost btn--sm" type="button" data-action="go-families">عرض في القائمة</button></p>` : ""}
    <p class="disc">${esc(state.schema?.disclaimer || "")}</p>
  </div>`;
}

function openSheet(id) {
  const h = state.households.find((x) => x.id === id);
  if (!h) return;
  const lab = Object.fromEntries(state.schema.fields.map((f) => [f.name, f]));
  const show = (k) => {
    const f = lab[k]; const v = h[k];
    return f.kind === "choice" ? esc(f.choices[v]) : f.kind === "money" ? `${money(v)} <small>ريال</small>` : `<span class="num">${esc(v)}</span>`;
  };
  const keys = ["household_size", "monthly_income", "income_source", "basic_expenses", "housing_status", "monthly_rent", "utility_bills", "total_debt", "monthly_debt_payment", "has_health_or_disability"];
  $("#sheet-body").innerHTML = `
    <button class="icon-btn sheet__close" type="button" data-action="close-sheet" aria-label="إغلاق"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
    <h2 id="sheet-title" style="margin-bottom:14px">الأسرة ${esc(h.ref_code)}</h2>
    ${assessmentHTML(h)}
    <h3 style="margin:18px 0 8px">بيانات الأسرة</h3>
    <div class="facts" style="grid-template-columns:1fr 1fr">${keys.map((k) => `<div class="fact"><span>${esc(lab[k].label)}</span><b>${show(k)}</b></div>`).join("")}
      <div class="fact"><span>الطلاب (ابتدائي/متوسط/ثانوي/جامعة)</span><b class="num">${STUDENT_FIELDS.map((k) => h[k] || 0).join(" / ")}</b></div></div>
    <div class="row-actions" style="margin-top:18px">
      <button class="btn" type="button" data-sheet-act="edit">تعديل البيانات</button>
      <button class="btn btn--danger-ghost" type="button" data-sheet-act="delete">حذف</button>
    </div>`;
  $("#sheet").dataset.id = id;
  $("#sheet").showModal();
}
$("#sheet").addEventListener("click", (ev) => {
  if (ev.target === ev.currentTarget) return ev.currentTarget.close(); // backdrop click
  const act = ev.target.closest("[data-sheet-act]")?.dataset.sheetAct;
  const h = state.households.find((x) => x.id === $("#sheet").dataset.id);
  if (act === "edit" && h) startEdit(h);
  if (act === "delete" && h) deleteFamily(h);
});

/* ================= Methodology ================= */
let methodDone = false;
async function renderMethod() {
  if (methodDone) return;
  try {
    const m = state.modelInfo || (state.modelInfo = await api("/model-info"));
    const weights = (w, kind) => Object.entries(w).sort((a, b) => b[1] - a[1]).map(([k, v]) => `
      <li class="factor--${kind}"><span>${esc(m.component_labels[k])}</span><b class="num">${Math.round(v * 100)}</b>
        <span class="track"><i style="width:${(v / 0.35) * 100}%"></i></span></li>`).join("");
    const t = m.metrics.test;
    const fair = (m.metrics.fairness || []).map((r) => `<tr><td>${esc(r.label_ar)}</td><td class="num">${r.n}</td>
      <td class="num">${r.accuracy == null ? "—" : pct(r.accuracy)}</td><td class="num">${r.recall_high == null ? "—" : pct(r.recall_high)}</td></tr>`).join("");
    $("#method").innerHTML = `
      <article class="pane glass"><h2>درجة الحاجة الآن</h2><p class="sub">وضع الأسرة اليوم. الرقم أقصى نقاط يضيفها العامل من 100.</p><ul class="weights">${weights(m.need_weights, "need")}</ul></article>
      <article class="pane glass"><h2>درجة مخاطر الأزمة القريبة</h2><p class="sub">احتمال انقطاع خدمة أو إخلاء أو تعثّر في السداد. القسط يُقاس على حد البنك المركزي السعودي (33.33% من الراتب).</p><ul class="weights">${weights(m.risk_weights, "risk")}</ul></article>
      <article class="pane glass"><h2>النموذج</h2><p class="sub">${m.kind === "logreg" ? "انحدار لوجستي" : "شجرة قرار"} قابل للتفسير، يقترح الأولوية من ${m.features.length} ميزة. مقاس على ${m.metrics.n_test ?? 600} أسرة اختبار.</p>
        <div class="metric"><span>الدقة (Accuracy)</span><b class="num">${pct(t.accuracy)}</b></div>
        <div class="metric"><span>الضبط (Precision)</span><b class="num">${pct(t.precision_macro)}</b></div>
        <div class="metric"><span>الاستدعاء (Recall)</span><b class="num">${pct(t.recall_macro)}</b></div>
        <div class="metric"><span>F1</span><b class="num">${pct(t.f1_macro)}</b></div>
        <div class="metric"><span>اكتشاف الأسر عالية الأولوية</span><b class="num">${pct(t.per_class.high.recall)}</b></div>
        <p class="hint" style="margin-top:10px">هذه الأرقام على أسر تجريبية (تعليم النموذج). وعلى بيانات حقيقية: مؤشر المخاطر رأى 70% من حالات التعثّر قبل سنتين (120 ألف شخص)، ومؤشر الحاجة اختار ضعف الأسر الفقيرة مقارنة بالصدفة (2,973 أسرة).</p></article>
      <article class="pane glass"><h2>مبادئ الاستخدام</h2>
        <ol class="principles">
          <li><b>النموذج يرتّب ولا يقرر.</b> القرار للمسؤول عن التوزيع، ويمكنه تعديل أي أولوية.</li>
          <li><b>لا تخزين.</b> الملف يُقرأ في الذاكرة ثم يُهمل؛ النتائج تبقى في هذه الصفحة حتى تُصدِّرها.</li>
          <li><b>لا أسماء ولا هويات.</b> رمز داخلي فقط.</li>
          <li><b>الحالات غير المؤكدة</b> (ثقة أقل من 60%) تُعلَّم للمراجعة اليدوية.</li>
          <li><b>الأوزان مبدئية</b> وتُراجع دوريًا مع أخصائيي الجمعية.</li>
        </ol></article>
      <article class="pane glass span-all"><h2>فحص العدالة</h2><p class="sub">هل يعامل النموذج الفئات بالتساوي؟ الدقة واكتشاف الأولوية العالية لكل فئة.</p>
        <table class="fair"><thead><tr><th>الفئة</th><th>العدد</th><th>الدقة</th><th>اكتشاف العالية</th></tr></thead><tbody>${fair}</tbody></table></article>
`;
    methodDone = true;
  } catch (err) {
    $("#method").innerHTML = `<p class="form-error">${esc(err.message)}</p>`;
  }
}

/* ================= Export ================= */
async function exportExcel() {
  if (!state.households.length) return toast("لا توجد أسر للتصدير.", "error");
  try {
    const body = { budget: state.budget, households: ranked().map((h) => ({ ...inputsOf(h), id: h.id, final_priority: h.final_priority, status: h.status, note: h.note })) };
    await download("/export", body, `نتائج-ترتيب-الأسر-${new Date().toISOString().slice(0, 10)}.xlsx`);
    toast("نُزّل ملف Excel بالنتائج والقرارات.");
  } catch (err) { toast(err.message, "error"); }
}

function exportPDF() {
  if (!state.households.length) return toast("لا توجد أسر للتصدير.", "error");
  const H = ranked();
  const alloc = new Map((state.allocation?.rows || []).map((r) => [r.id, r]));
  const a = state.allocation;
  const count = (p) => H.filter((h) => effective(h) === p).length;
  $("#print-report").innerHTML = `<div class="pr" dir="rtl">
    <h1>تقرير ترتيب الأسر — أولويات الدعم</h1>
    <p class="meta">تاريخ التقرير: ${new Date().toLocaleDateString("ar-SA-u-nu-latn")} · عدد الأسر: ${H.length}</p>
    <div class="kv">
      <div><span>أولوية عالية</span><b>${count("high")}</b></div><div><span>متوسطة</span><b>${count("medium")}</b></div>
      <div><span>منخفضة</span><b>${count("low")}</b></div><div><span>وقاية مبكرة</span><b>${H.filter((h) => h.quadrant === "prevent").length}</b></div>
      ${a ? `<div><span>الميزانية</span><b>${fmt.format(state.budget)}</b></div><div><span>المخصّص</span><b>${fmt.format(a.allocated_total)}</b></div>
      <div><span>نسبة التغطية</span><b>${pct(a.coverage)}</b></div><div><span>أسر لم تُغطَّ</span><b>${a.uncovered}</b></div>` : ""}
    </div>
    <h2>خريطة الأولوية</h2><div class="map-print">${mapSVG(H, "print")}</div>
    <h2>ترتيب الأسر</h2>
    <table><thead><tr><th>#</th><th>الرمز</th><th>المقترحة</th><th>قرار المسؤول</th><th>الحاجة</th><th>المخاطر</th><th>التصنيف</th><th>العجز</th><th>المقترح</th><th>المخصّص</th><th>الحالة</th><th>ملاحظة</th></tr></thead><tbody>
    ${H.map((h, i) => { const r = alloc.get(h.id); return `<tr><td>${i + 1}</td><td>${esc(h.ref_code)}</td><td>${PRIORITY_AR[h.priority]}</td><td>${h.final_priority ? PRIORITY_AR[h.final_priority] : "—"}</td>
      <td>${Math.round(h.need_score)}</td><td>${Math.round(h.risk_score)}</td><td>${QUADRANT_AR[h.quadrant]}</td><td>${fmt.format(h.monthly_deficit)}</td>
      <td>${r ? fmt.format(r.target) : "—"}</td><td>${r ? fmt.format(r.allocated) : "—"}</td><td>${STATUS_AR[h.status] || ""}</td><td>${esc(h.note)}</td></tr>`; }).join("")}
    </tbody></table>
    <p class="note">${esc(state.schema?.disclaimer || "")} البيانات لم تُخزَّن على أي خادم؛ هذا التقرير هو النسخة الوحيدة.</p></div>`;
  window.print();
}

/* ================= Global actions ================= */
document.addEventListener("click", async (ev) => {
  const el = ev.target.closest("[data-action]");
  if (!el) return;
  switch (el.dataset.action) {
    case "pick-file": $("#file-input").click(); break;
    case "demo": loadDemo(); break;
    case "go-form": stopEdit(); showView("form"); break;
    case "go-families": showView("families"); break;
    case "export-xlsx": exportExcel(); break;
    case "export-pdf": exportPDF(); break;
    case "close-sheet": $("#sheet").close(); break;
    case "close-report": state.lastReport = null; renderReport(); break;
    case "clear-all": {
      if (!state.households.length) return;
      const ok = await confirmDialog("مسح كل الأسر؟", `ستُزال ${state.households.length} أسرة من هذه الصفحة. صدّر النتائج أولًا إن أردت الاحتفاظ بها.`,
        [{ value: "export", label: "صدّر Excel ثم امسح" }, { value: "yes", label: "امسح الكل", cls: "btn--danger" }]);
      if (ok === "export") await exportExcel();
      if (ok === "export" || ok === "yes") {
        state.households = []; state.allocation = null; state.lastReport = null;
        renderReport(); refreshAll(); toast("مُسحت القائمة.");
      }
      break;
    }
  }
});
$("#file-input").addEventListener("change", (ev) => importFile(ev.target.files[0]));

// Drag & drop anywhere on the page.
let dragDepth = 0;
window.addEventListener("dragenter", (ev) => {
  if (![...(ev.dataTransfer?.types || [])].includes("Files")) return;
  dragDepth++; $("#dropveil").hidden = false;
});
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#dropveil").hidden = true; } });
window.addEventListener("dragover", (ev) => ev.preventDefault());
window.addEventListener("drop", (ev) => {
  ev.preventDefault();
  dragDepth = 0; $("#dropveil").hidden = true;
  importFile(ev.dataTransfer?.files?.[0]);
});

window.addEventListener("beforeunload", (ev) => {
  if (state.households.length) { ev.preventDefault(); ev.returnValue = ""; }
});
let resizeTimer = null;
window.addEventListener("resize", () => {
  lens.hidden = true;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(renderMap, 150);
});

/* ================= Boot ================= */
(async function boot() {
  if (!(await PyEngine.hasServer())) {
    MODE = "browser";
    const veil = $("#py-loading");
    veil.hidden = false;
    try {
      await PyEngine.boot((step) => { $("#py-step").textContent = step; });
    } catch (err) {
      $("#py-step").textContent = err.message;
      return;
    }
    veil.hidden = true;
    $(".privacy__long").textContent = "البيانات لا تغادر جهازك";
    // The template is generated by the same Python, in the page.
    $$('a[href="/api/template"]').forEach((a) => a.addEventListener("click", (ev) => {
      ev.preventDefault();
      download("/template", null, "قالب-بيانات-الأسر.xlsx").catch((e) => toast(e.message, "error"));
    }));
  }
  try {
    state.schema = await api("/schema");
    state.inputFields = state.schema.fields.map((f) => f.name);
    $("#disclaimer").textContent = state.schema.disclaimer;
    buildForm();
  } catch (err) {
    toast("تعذّر الاتصال بالخادم. تأكد أنه يعمل.", "error");
  }
  refreshAll();
  const initial = location.hash.replace("#", "");
  if (["dashboard", "families", "form", "method"].includes(initial)) showView(initial);
})();
