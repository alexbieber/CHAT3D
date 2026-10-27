// Shared helpers: API calls, session, nav, toast.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const C3D = { me: null, config: null };

async function api(path, opts = {}) {
  const init = { credentials: "same-origin", ...opts };
  if (init.json !== undefined) {
    init.body = JSON.stringify(init.json);
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
    delete init.json;
  }
  const res = await fetch(path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.detail || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return body;
}

let _toastT;
function toast(msg, ok = false) {
  let t = $("#toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; t.setAttribute("role", "alert"); document.body.append(t); }
  t.textContent = msg; t.classList.toggle("ok", ok); t.hidden = false;
  clearTimeout(_toastT); _toastT = setTimeout(() => (t.hidden = true), ok ? 4000 : 9000);
}

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function fmtDate(iso) { return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : ""; }
function money(minor, cur) { return new Intl.NumberFormat(cur === "INR" ? "en-IN" : "en-US", { style: "currency", currency: cur, maximumFractionDigits: minor % 100 ? 2 : 0 }).format(minor / 100); }

const LOGO = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 3 7v10l9 5 9-5V7z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M3 7l9 5 9-5M12 12v10" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>`;

function renderNav(active) {
  const nav = $("#nav");
  if (!nav) return;
  const me = C3D.me, appName = C3D.config?.app_name || "Chat3D";
  const link = (href, label) => `<a href="${href}" ${active === href ? 'aria-current="page"' : ""}>${label}</a>`;
  nav.innerHTML = `<div class="wrap">
    <a class="logo" href="/">${LOGO}${esc(appName)}</a>
    <nav class="links">${me ? link("/app", "Studio") : ""}${link("/pricing", "Pricing")}${link("/developers", "Developers")}</nav>
    <div class="right">${me ? `
      <a class="credit-pill ${me.credits < 10 ? "low" : ""}" href="/pricing" title="Buy credits"><span id="nav-credits">${me.credits}</span> <span id="nav-credits-word">${me.credits === 1 ? "credit" : "credits"}</span></a>
      <div class="menu"><button aria-haspopup="true" aria-expanded="false" id="menu-btn" aria-label="Account menu">${esc((me.name || me.email)[0].toUpperCase())}</button>
        <div class="menu-list" hidden id="menu-list">
          <div class="who">${esc(me.email)}</div>
          <a href="/app">Studio</a><a href="/account">Account &amp; billing</a><a href="/account#keys">API keys</a>
          ${me.is_admin ? '<a href="/admin">Admin</a>' : ""}
          <button id="logout-btn">Sign out</button>
        </div></div>` : `
      <a class="plain" href="/login">Sign in</a><a class="btn primary small" href="/signup">Start free</a>`}
    </div></div>`;
  const mb = $("#menu-btn");
  if (mb) {
    mb.onclick = (e) => { e.stopPropagation(); const l = $("#menu-list"); l.hidden = !l.hidden; mb.setAttribute("aria-expanded", !l.hidden); };
    document.addEventListener("click", () => { const l = $("#menu-list"); if (l) l.hidden = true; });
    $("#logout-btn").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); location.href = "/"; };
  }
  if (C3D.config?.test_mode && !$(".banner")) {
    const b = document.createElement("div"); b.className = "banner";
    b.textContent = "Test mode: images and models are placeholders, no real generation runs.";
    nav.after(b);
  }
}

function setCredits(n) { if (C3D.me) C3D.me.credits = n; const el = $("#nav-credits"); if (el) { el.textContent = n; el.parentElement.classList.toggle("low", n < 10); $("#nav-credits-word").textContent = n === 1 ? "credit" : "credits"; } }

function renderFooter() {
  const f = $("#footer");
  if (!f) return;
  const c = C3D.config || {};
  f.innerHTML = `<div class="wrap"><div>© ${new Date().getFullYear()} ${esc(c.company || "")}. ${esc(c.app_name || "Chat3D")} builds on Microsoft TRELLIS.2.<br>Support: <a href="mailto:${esc(c.support_email)}">${esc(c.support_email)}</a></div>
    <nav><a href="/pricing">Pricing</a><a href="/developers">API</a><a href="/terms">Terms</a><a href="/privacy">Privacy</a><a href="/refunds">Refunds</a><a href="/acceptable-use">Acceptable use</a></nav></div>`;
}

async function boot({ active, requireAuth = false, requireAdmin = false } = {}) {
  const [cfg, me] = await Promise.all([
    api("/api/config").catch(() => ({})),
    api("/api/auth/me").catch(() => null),
  ]);
  C3D.config = cfg; C3D.me = me;
  if (requireAuth && !me) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`; throw new Error("redirect"); }
  if (requireAdmin && !me?.is_admin) { location.href = "/app"; throw new Error("redirect"); }
  renderNav(active); renderFooter();
  return { cfg, me };
}
