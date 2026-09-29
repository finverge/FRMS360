// Minimal vanilla SPA for the control-plane console. Talks only to the gateway (/api/*).
const API = "/api";
let token = localStorage.getItem("cp_token") || null;
// The refresh token is the revocable half of the session. The access token is short-lived
// by design, so the console silently exchanges this one rather than asking the user to
// sign in again every half hour.
let refreshToken = localStorage.getItem("cp_refresh") || null;
let pendingLogin = null;   // { email } while a second factor is outstanding

const VIEWS = ["login-view", "mfa-view", "enrol-view", "codes-view", "reset-view",
               "app-view"];
function showView(id) {
  VIEWS.forEach((v) => { const el = $(v); if (el) el.hidden = v !== id; });
}

function storeSession(data) {
  token = data.access_token;
  role = data.role;
  localStorage.setItem("cp_token", token);
  localStorage.setItem("cp_role", role);
  if (data.refresh_token) {
    refreshToken = data.refresh_token;
    localStorage.setItem("cp_refresh", refreshToken);
  }
}
let role = localStorage.getItem("cp_role") || null;
let selectedTenant = null;

const $ = (id) => document.getElementById(id);

// Anything a user typed goes through this before it reaches innerHTML. Case reasons,
// staff names and examination notes are free text entered by tenant users, and a
// compliance record that executes script when an auditor opens it is not a record.
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

async function api(path, { method = "GET", body, auth = true, _retry = false } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && token) headers["Authorization"] = `Bearer ${token}`;
  // Every write is labelled, so a dropped response can be retried without the action
  // happening twice. The key identifies this attempt, not this endpoint: a deliberate
  // second identical write is a different intent and must get its own key.
  if (MUTATING.has(method.toUpperCase())) {
    headers["Idempotency-Key"] =
      (crypto.randomUUID ? crypto.randomUUID()
                         : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  }
  busy(+1);
  let res, text;
  try {
    res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    text = await res.text();
  } finally {
    busy(-1);
  }
  const data = text ? JSON.parse(text) : null;

  // An expired access token is routine, not a failure: exchange the refresh token once
  // and replay. Only once - a loop here would hammer the endpoint that is rejecting us.
  if (res.status === 401 && auth && refreshToken && !path.startsWith("/auth/") && !_retry) {
    if (await tryRefresh()) return api(path, { method, body, auth, _retry: true });
  }

  if (!res.ok) {
    // A password_reset-scoped token can only reach /auth/change-password. If a restored
    // session still needs a reset, drop the user into the reset screen rather than
    // leaving the console in a half-loaded state.
    if (res.status === 403 && /password change required/i.test(data?.error?.message || "")
        && !path.startsWith("/auth/")) {
      showResetView({ forced: true });
    }
    throw new Error(data?.error?.message || `HTTP ${res.status}`);
  }
  return data;
}

async function tryRefresh() {
  if (!refreshToken) return false;
  try {
    const res = await fetch(API + "/auth/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) throw new Error("refresh rejected");
    storeSession(await res.json());
    return true;
  } catch {
    // The session is genuinely over - a revoked token, a detected replay, or an idle
    // timeout. Clear it and let the user sign in rather than leaving a half-working app.
    localStorage.clear();
    token = refreshToken = null;
    showView("login-view");
    return false;
  }
}

// ---- loading feedback ----
// One counter for all in-flight requests: a dashboard load fires several in parallel, so
// the bar must survive until the LAST finishes rather than the first.
let inFlight = 0;

function busy(delta) {
  inFlight = Math.max(0, inFlight + delta);
  const bar = $("busy-bar");
  if (bar) bar.hidden = inFlight === 0;
}

/** Veil a panel while its data is being fetched. */
function setLoading(elementId, on, text = "Loading\u2026") {
  const el = $(elementId);
  if (!el) return;
  el.classList.toggle("is-loading", on);
  const existing = el.querySelector(":scope > .load-veil");
  if (on && !existing) {
    const veil = document.createElement("div");
    veil.className = "load-veil";
    veil.innerHTML = `<div class="veil-inner"><div class="spinner"></div>
      <div class="load-text">${esc(text)}</div></div>`;
    el.appendChild(veil);
  } else if (!on && existing) {
    existing.remove();
  }
}

/** Placeholder tiles, so the KPI row keeps its height instead of collapsing and jumping. */
function skeletonKpis(count = 8) {
  $("kpi-row").innerHTML = Array.from({ length: count }, () =>
    `<div class="kpi skeleton"><div class="label">&nbsp;</div><div class="value">&nbsp;</div></div>`
  ).join("");
}

/** Disable a button and show progress while an action runs. */
async function withButton(btn, label, fn) {
  const original = btn.textContent;
  btn.setAttribute("aria-busy", "true");
  btn.textContent = label;
  try {
    return await fn();
  } finally {
    btn.removeAttribute("aria-busy");
    btn.textContent = original;
  }
}

// ---- Auth ----
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").hidden = true;
  try {
    const data = await api("/auth/login", {
      method: "POST", auth: false,
      body: { email: $("login-email").value, password: $("login-password").value },
    });
    // The token here may be limited in scope. storeSession keeps it because the next
    // step needs it, but nothing else in the app will work until the step completes.
    storeSession(data);
    pendingLogin = { email: $("login-email").value };

    if (data.must_change_password) {
      // password_reset-scoped — nothing else will work until it's changed.
      $("r-current").value = $("login-password").value;
      showResetView({ forced: true });
    } else if (data.mfa_enrolment_required) {
      await startEnrolment();
    } else if (data.mfa_required) {
      $("mfa-account").textContent = pendingLogin.email;
      $("mfa-code").value = "";
      showView("mfa-view");
      $("mfa-code").focus();
    } else {
      enterApp();
    }
  } catch (err) {
    $("login-error").textContent = err.message; $("login-error").hidden = false;
  }
});

// ---- BR-109: self-service sandbox signup, no login needed ----------------------
$("sandbox-toggle").addEventListener("click", () => {
  $("sandbox-form").hidden = !$("sandbox-form").hidden;
});
$("sandbox-cancel").addEventListener("click", () => {
  $("sandbox-form").hidden = true;
  $("sandbox-form").reset();
  $("sandbox-error").hidden = true;
  $("sandbox-success").hidden = true;
});
$("sandbox-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("sandbox-error").hidden = true;
  $("sandbox-success").hidden = true;
  try {
    const tenant = await api("/tenants/self-service", {
      method: "POST", auth: false,
      body: {
        slug: $("sandbox-slug").value.trim(),
        legal_name: $("sandbox-legal-name").value,
        display_name: $("sandbox-display-name").value,
        entity_type: $("sandbox-entity-type").value,
        admin_email: $("sandbox-email").value,
        admin_password: $("sandbox-password").value,
      },
    });
    $("sandbox-success").textContent =
      `Sandbox "${tenant.display_name}" is ready. Sign in above with the email and ` +
      `password you just chose.`;
    $("sandbox-success").hidden = false;
    $("login-email").value = $("sandbox-email").value;
    $("sandbox-form").reset();
  } catch (err) {
    $("sandbox-error").textContent = err.message; $("sandbox-error").hidden = false;
  }
});

// ---- second factor -------------------------------------------------------------
$("mfa-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("mfa-error").hidden = true;
  try {
    storeSession(await api("/auth/mfa/verify", {
      method: "POST", body: { code: $("mfa-code").value.trim() },
    }));
    enterApp();
  } catch (err) {
    $("mfa-error").textContent = err.message;
    $("mfa-error").hidden = false;
    $("mfa-code").value = "";
    $("mfa-code").focus();
  }
});

$("mfa-cancel").onclick = () => {
  localStorage.clear();
  token = refreshToken = role = null;
  showView("login-view");
};

async function startEnrolment() {
  try {
    const d = await api("/auth/mfa/enrol", { method: "POST" });
    // The QR arrives as SVG from the server: no client-side QR library to load, which
    // matters for the air-gapped deployments.
    $("enrol-qr").innerHTML = d.qr_svg;
    $("enrol-secret").textContent = d.secret;
    $("enrol-code").value = "";
    $("enrol-error").hidden = true;
    showView("enrol-view");
    $("enrol-code").focus();
  } catch (err) {
    $("login-error").textContent = err.message;
    $("login-error").hidden = false;
    showView("login-view");
  }
}

$("enrol-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("enrol-error").hidden = true;
  try {
    const d = await api("/auth/mfa/activate", {
      method: "POST", body: { code: $("enrol-code").value.trim() },
    });
    showRecoveryCodes(d.backup_codes);
  } catch (err) {
    $("enrol-error").textContent = err.message;
    $("enrol-error").hidden = false;
    $("enrol-code").value = "";
    $("enrol-code").focus();
  }
});

let _codes = [];
function showRecoveryCodes(codes) {
  _codes = codes || [];
  $("codes-list").innerHTML = _codes.map((c) => `<li>${c}</li>`).join("");
  $("codes-ack").checked = false;
  $("codes-continue").disabled = true;
  showView("codes-view");
}

// The acknowledgement is not paperwork: these codes cannot be shown again, and a user
// who clicks past them has silently lost their only way back in without an administrator.
$("codes-ack").onchange = (e) => { $("codes-continue").disabled = !e.target.checked; };

$("codes-copy").onclick = () => navigator.clipboard.writeText(_codes.join("\n"));
$("codes-download").onclick = () => {
  const header = `Recovery codes for ${pendingLogin ? pendingLogin.email : "your account"}`;
  const blob = new Blob(
    [header + "\nEach code works once. Keep them somewhere safe and private.\n\n" +
     _codes.join("\n") + "\n"], { type: "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "frms-recovery-codes.txt";
  a.click();
  URL.revokeObjectURL(a.href);
};

$("codes-continue").onclick = async () => {
  // Enrolment is complete but this token is still mfa_pending-scoped: the factor has
  // been set up, not yet presented. Ask for a code, which is also a useful rehearsal.
  $("mfa-account").textContent = pendingLogin ? pendingLogin.email : "";
  $("mfa-code").value = "";
  showView("mfa-view");
  $("mfa-code").focus();
};

$("logout").addEventListener("click", async () => {
  // Tell the server, so the session is genuinely revoked rather than just forgotten
  // by this browser. Best effort: a failure here must not trap the user in the app.
  if (refreshToken) {
    try {
      await fetch(API + "/auth/logout", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
    } catch { /* the local session is cleared regardless */ }
  }
  localStorage.clear();
  token = refreshToken = role = null;
  if (inboxTimer) { clearInterval(inboxTimer); inboxTimer = null; }
  showView("login-view");
});

// ---- Password change (forced after a temp password, or voluntary from the topbar) ----
let resetForced = false;

async function showResetView({ forced }) {
  resetForced = forced;
  showView("reset-view");
  $("reset-error").hidden = true;
  $("reset-cancel").hidden = forced;   // no escape hatch when forced
  $("reset-reason").textContent = forced
    ? "Your temporary password must be changed before you continue."
    : "Choose a new password for your account.";
  if (!forced) $("r-current").value = "";
  $("r-new").value = ""; $("r-confirm").value = "";
  try {
    const p = await api("/auth/password-policy", { auth: false });
    $("reset-policy").textContent = p.policy;
  } catch {}
}

$("change-pw").onclick = () => showResetView({ forced: false });
$("reset-cancel").onclick = () => showView("app-view");

$("reset-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("reset-error"); err.hidden = true;
  if ($("r-new").value !== $("r-confirm").value) {
    err.textContent = "New passwords do not match."; err.hidden = false; return;
  }
  try {
    const data = await api("/auth/change-password", { method: "POST", body: {
      current_password: $("r-current").value, new_password: $("r-new").value,
    }});
    // Swap in the fresh full-scope token.
    token = data.access_token; role = data.role;
    localStorage.setItem("cp_token", token); localStorage.setItem("cp_role", role);
    enterApp();
  } catch (e2) { err.textContent = e2.message; err.hidden = false; }
});

// ---- Modules & role-based access ----
let me = null;              // /auth/me payload
let currentModule = null;

async function enterApp() {
  showView("app-view");
  try {
    me = await api("/auth/me");
  } catch (err) {
    // Never fake a session. A stale or rejected token used to fall back to a
    // monitoring-only view, which rendered as a working app with one module, one
    // dashboard and no tenants - indistinguishable from a broken product. Send the
    // user back to sign in and say why.
    localStorage.clear();
    token = null; role = null; me = null;
    showView("login-view");
    $("login-error").textContent =
      "Your session is no longer valid. Please sign in again.";
    $("login-error").hidden = false;
    return;
  }
  $("who").textContent = `${me.role_label || me.role}`;
  // Clear the previous session's view so a new sign-in lands on THIS role's primary
  // dashboard. Without this, signing out of a broader role and into a narrower one
  // keeps whatever tab the last user was on if the new role also happens to allow it.
  currentDash = null;
  renderModuleNav();
  renderDashboardTabs();
  // Only roles that administer a tenant get the tenant list / onboarding control.
  $("new-tenant-btn").hidden = !me.can_admin_tenant;
  // A shared link carries its filters in the hash; apply them before the first fetch so
  // the recipient sees exactly the view that was shared.
  const deep = restoreFiltersFromUrl();
  if (deep?.dash && (me.dashboards || []).some((d) => d.key === deep.dash)) {
    currentDash = deep.dash;
    document.querySelectorAll("[data-dash]").forEach((x) =>
      x.classList.toggle("active", x.dataset.dash === deep.dash));
  }
  pendingDeepTenant = deep?.tenant || null;
  loadTenants();
  if (hasModule("audit")) loadAudit();
}
let pendingDeepTenant = null;

const hasModule = (k) => !!me?.modules?.some((m) => m.key === k);

function renderModuleNav() {
  const nav = $("module-nav");
  nav.innerHTML = me.modules.map((m, i) =>
    `<button class="mod-btn${i === 0 ? " active" : ""}" data-mod="${esc(m.key)}" title="${esc(m.description || "")}">
       <span class="mod-icon">${esc(m.icon || "•")}</span>${esc(m.label)}</button>`).join("");
  nav.querySelectorAll("[data-mod]").forEach((b) => {
    b.onclick = () => selectModule(b.dataset.mod);
  });
  selectModule(me.modules[0]?.key || "monitoring");
}

function selectModule(key) {
  currentModule = key;
  document.querySelectorAll("#module-nav [data-mod]").forEach((b) =>
    b.classList.toggle("active", b.dataset.mod === key));
  // Show only the panels belonging to this module.
  document.querySelectorAll("[data-module]").forEach((el) => {
    const mine = el.dataset.module === key;
    if (!mine) el.hidden = true;
    else if (el.id === "analytics-panel") el.hidden = !selectedTenant;
    else if (el.id === "detail-panel") el.hidden = !selectedTenant;
    else if (el.id === "new-tenant-panel") el.hidden = true;
    else el.hidden = false;
  });
  // The persona rail belongs to Monitoring only.
  $("persona-bar").hidden = key !== "monitoring";
  $("empty-state").hidden = !!selectedTenant || key !== "control_plane";
  if (key === "monitoring" && selectedTenant) loadDashboard();
  if (key === "audit") loadAudit();
}

function renderDashboardTabs() {
  const allowed = (me.dashboards || []).map((d) => d.key);
  const container = document.getElementById("dash-tabs");
  const byKey = {};
  document.querySelectorAll("#dash-tabs [data-dash]").forEach((b) => {
    byKey[b.dataset.dash] = b;
    b.hidden = !allowed.includes(b.dataset.dash);
  });
  // The server lists a role's dashboards with its PRIMARY one first. Reorder the tabs to
  // match, so each persona lands on their own view rather than whatever is first in the
  // markup (an RBI inspector should open on Inspection, not AML).
  allowed.forEach((key) => { if (byKey[key]) container.appendChild(byKey[key]); });
  (me.dashboards || []).forEach((d) => {
    const b = byKey[d.key];
    if (b && d.persona) b.title = `${d.persona}${d.question ? " — " + d.question : ""}`;
  });

  const primary = allowed[0];
  if (primary && !allowed.includes(currentDash)) currentDash = primary;
  document.querySelectorAll("[data-dash]").forEach((x) =>
    x.classList.toggle("active", x.dataset.dash === currentDash));
}

function _auditQuery(limit) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (selectedTenant) params.set("tenant_id", selectedTenant.id);
  if ($("audit-roles-only")?.checked) params.set("action_prefix", "role.");
  return params;
}

async function loadAudit() {
  const rows = $("audit-rows");
  try {
    const items = await api(`/audit?${_auditQuery(25)}`);
    if (!items.length) { rows.innerHTML = `<tr><td colspan="5" class="muted">No activity yet</td></tr>`; return; }
    rows.innerHTML = items.map((a) => {
      const t = new Date(a.ts).toLocaleString();
      const target = a.target_id ? `${a.target_type}:${a.target_id.slice(0, 8)}` : "—";
      const cls = a.status === "success" ? "active" : "";
      return `<tr><td>${esc(t)}</td><td>${esc(a.actor || "—")}</td><td>${esc(a.action)}</td>
              <td>${esc(target)}</td><td><span class="badge ${cls}">${esc(a.status)}</span></td></tr>`;
    }).join("");
  } catch { rows.innerHTML = `<tr><td colspan="5" class="muted">—</td></tr>`; }
}

$("audit-roles-only")?.addEventListener("change", loadAudit);

$("audit-download").onclick = async () => {
  // Fetched rather than linked: the download needs the bearer token, same reasoning
  // as openFiling().
  try {
    const res = await fetch(`${API}/audit/export?${_auditQuery(500)}`,
                            { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = new Blob([await res.text()], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = $("audit-roles-only")?.checked ? "audit-role-changes.csv" : "audit-export.csv";
    a.click(); URL.revokeObjectURL(a.href);
  } catch (err) { alert(`Could not download the audit report: ${esc(err.message)}`); }
};

// ---- Tenants ----
async function loadTenants() {
  try {
    const tenants = await api("/tenants");
    const ul = $("tenant-list"); ul.innerHTML = "";
    tenants.forEach((t) => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${esc(t.display_name)}</span><span class="dot ${t.status}"></span>`;
      li.title = `${esc(t.slug)} · ${t.status}`;
      li.onclick = () => selectTenant(t, li);
      ul.appendChild(li);
      if (pendingDeepTenant && t.id === pendingDeepTenant) {
        pendingDeepTenant = null;
        setTimeout(() => selectTenant(t, li), 0);
      }
    });
  } catch (err) {
    if (String(err.message).includes("403")) $("who").textContent = `${role} (limited)`;
  }
}

$("new-tenant-btn").onclick = () => {
  $("empty-state").hidden = true; $("detail-panel").hidden = true;
  $("new-tenant-panel").hidden = false;
  updateEntityHint();
};
$("cancel-tenant").onclick = () => { $("new-tenant-panel").hidden = true; $("empty-state").hidden = false; };

// UCB tier only means anything for a UCB (see TenantCreate.ucb_tier / policy.py's
// _for()) - keep it hidden for every other entity type so it isn't posted where it
// would just be silently ignored.
//
// Only "payments_bank" is excluded today - a PB licence cannot extend credit, so the
// 9 credit-linked rules (loan-account misuse, borrowal-account conduct, banker/loan-
// request qualitative signals) are never seeded for one. Mirrors
// config_service/app/ews_catalogue.py's CREDIT_LINKED_RULES by hand.
const ENTITY_TYPES_WITHOUT_CREDIT = new Set(["payments_bank"]);
function updateEntityHint() {
  const excluded = ENTITY_TYPES_WITHOUT_CREDIT.has($("t-entity-type").value);
  $("t-entity-hint").textContent = excluded
    ? "This licence cannot extend credit - the 9 loan/borrowal-linked EWS rules will not be seeded."
    : "Seeded with the full 28-rule EWS catalogue.";
}
$("t-entity-type").addEventListener("change", () => {
  $("t-ucb-tier-wrap").hidden = $("t-entity-type").value !== "urban_cooperative";
  updateEntityHint();
});

$("tenant-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("tenant-error").hidden = true;
  try {
    const entityType = $("t-entity-type").value;
    await api("/tenants", { method: "POST", body: {
      slug: $("t-slug").value, plan: $("t-plan").value,
      legal_name: $("t-legal").value, display_name: $("t-display").value,
      entity_type: entityType,
      ucb_tier: entityType === "urban_cooperative" ? Number($("t-ucb-tier").value) : null,
      admin_email: $("t-email").value, admin_password: $("t-pass").value,
      primary_color: $("t-primary").value, accent_color: $("t-accent").value,
    }});
    $("tenant-form").reset();
    $("t-ucb-tier-wrap").hidden = true;
    $("new-tenant-panel").hidden = true; $("empty-state").hidden = false;
    loadTenants(); loadAudit();
  } catch (err) { $("tenant-error").textContent = err.message; $("tenant-error").hidden = false; }
});

// ---- Tenant detail + branding ----
async function selectTenant(t, li) {
  selectedTenant = t;
  startInboxPolling();
  loadDeliverySettings();
  loadDeliveryProblems();
  document.querySelectorAll(".tenant-list li").forEach((el) => el.classList.remove("active"));
  li.classList.add("active");
  $("empty-state").hidden = true; $("new-tenant-panel").hidden = true; $("detail-panel").hidden = false;

  $("d-name").textContent = t.display_name;
  $("d-meta").textContent = `${esc(t.slug)} · ${t.region} · ${t.plan}`;
  renderLifecycle(t);
  $("config-form").hidden = true; $("config-view").hidden = true;

  // Reveal whichever module is active for the newly selected tenant.
  selectModule(currentModule || (hasModule("monitoring") ? "monitoring" : "control_plane"));
  if (hasModule("control_plane")) {
    await loadBranding(t.id);
    await loadRoles(t.id);
    await loadUsers(t.id);
    await loadConfigs(t.id);
    await loadProviders(t.id);
  }
  resetDrill();
  await renderSavedViews();
  if (hasModule("monitoring")) await loadDashboard();
}

// ---- Tenant lifecycle ----
function renderLifecycle(t) {
  const chip = $("d-status");
  chip.textContent = t.status;
  chip.className = "status-chip s-" + t.status;
  $("act-suspend").hidden = !(t.status === "active" || t.status === "degraded");
  $("act-resume").hidden = t.status !== "suspended";
  $("act-offboard").hidden = t.status === "offboarded";
  // BR-109: a self-service sandbox, visible to everyone with access to this tenant;
  // only platform_admin may act on it - promotion is a Fraud360 commercial decision,
  // not something the bank's own tenant_admin can do to itself.
  $("d-sandbox").hidden = !t.is_sandbox;
  $("act-promote").hidden = !(t.is_sandbox && t.status === "active" && role === "platform_admin");
}

async function tenantAction(action) {
  if (!selectedTenant) return;
  if (action === "offboard" &&
      !confirm(`Offboard ${esc(selectedTenant.display_name)}? Its admins will be locked out. Data is retained and exportable.`))
    return;
  try {
    const updated = await api(`/tenants/${selectedTenant.id}/${action}`, { method: "POST" });
    selectedTenant = updated;
    renderLifecycle(updated);
    loadTenants(); loadAudit();
  } catch (err) { alert(err.message); }
}
$("act-suspend").onclick = () => tenantAction("suspend");
$("act-resume").onclick = () => tenantAction("resume");
$("act-offboard").onclick = () => tenantAction("offboard");
$("act-promote").onclick = async () => {
  if (!selectedTenant) return;
  if (!confirm(`Promote ${esc(selectedTenant.display_name)} out of sandbox? It becomes ` +
              `a billable tenant and may submit live traffic from this point on.`))
    return;
  try {
    const updated = await api(`/tenants/${selectedTenant.id}/promote`, { method: "POST" });
    selectedTenant = updated;
    renderLifecycle(updated);
    loadTenants(); loadAudit();
  } catch (err) { alert(err.message); }
};
$("act-export").onclick = async () => {
  if (!selectedTenant) return;
  const bundle = await api(`/tenants/${selectedTenant.id}/export`);
  const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${esc(selectedTenant.slug)}-export.json`;
  a.click(); URL.revokeObjectURL(a.href);
  loadAudit();
};

async function loadBranding(tenantId) {
  try {
    const b = await api(`/branding/${tenantId}`);
    $("b-display").value = b.display_name || "";
    $("b-logo").value = b.logo_url || "";
    $("b-primary").value = b.primary_color; $("b-accent").value = b.accent_color;
    $("b-neutral").value = b.neutral_color; $("b-theme").value = b.default_theme;
    $("b-domain").value = b.custom_domain || "";
    updatePreview();
  } catch { /* no branding yet */ }
}

function updatePreview() {
  const pv = $("brand-preview");
  pv.style.setProperty("--pv-primary", $("b-primary").value);
  pv.style.setProperty("--pv-accent", $("b-accent").value);
  $("pv-name").textContent = $("b-display").value || "Tenant";
  $("pv-logo").textContent = ($("b-display").value || "T").trim().charAt(0).toUpperCase();
}
["b-display", "b-primary", "b-accent"].forEach((id) => $(id).addEventListener("input", updatePreview));

$("branding-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedTenant) return;
  await api(`/branding/${selectedTenant.id}`, { method: "PUT", body: {
    display_name: $("b-display").value, logo_url: $("b-logo").value,
    primary_color: $("b-primary").value, accent_color: $("b-accent").value,
    neutral_color: $("b-neutral").value, default_theme: $("b-theme").value,
    custom_domain: $("b-domain").value,
  }});
  const saved = $("branding-saved"); saved.hidden = false; setTimeout(() => (saved.hidden = true), 1800);
  loadAudit();
});

let currentConfigs = [];

// ---- User management ----
async function loadUsers(tenantId) {
  const rows = $("user-rows");
  try {
    const users = await api(`/tenants/${tenantId}/users`);
    if (!users.length) { rows.innerHTML = `<tr><td colspan="4" class="muted">No users</td></tr>`; return; }
    rows.innerHTML = users.map((u) => {
      const added = new Date(u.created_at).toLocaleDateString();
      const del = users.length > 1
        ? `<button class="link-btn danger" onclick="removeUser('${u.id}','${esc(u.email)}')">Remove</button>`
        : `<span class="muted" title="A tenant must keep at least one administrator">only admin</span>`;
      const roleOptions = (currentTenantRoles.length ? currentTenantRoles : [{ name: u.role, label: u.role }])
        .map((r) => `<option value="${esc(r.name)}"${r.name === u.role ? " selected" : ""}>${esc(r.label)}</option>`)
        .join("");
      return `<tr><td>${esc(u.email)}</td>
              <td><select class="inline-select" data-prev="${esc(u.role)}"
                    onchange="updateUserRole('${u.id}', this)">${roleOptions}</select></td>
              <td>${added}</td><td>${del}</td></tr>`;
    }).join("");
  } catch { rows.innerHTML = `<tr><td colspan="4" class="muted">—</td></tr>`; }
}

async function updateUserRole(userId, selectEl) {
  if (!selectedTenant) return;
  const newRole = selectEl.value;
  const prevRole = selectEl.dataset.prev;
  if (newRole === prevRole) return;
  try {
    await api(`/tenants/${selectedTenant.id}/users/${userId}`,
              { method: "PUT", body: { role: newRole } });
    selectEl.dataset.prev = newRole;
    await loadRoles(selectedTenant.id); loadAudit();
  } catch (err) {
    selectEl.value = prevRole; // the change was refused - do not show a role that never took effect
    alert(`Could not change role: ${esc(err.message)}`);
  }
}

async function removeUser(userId, email) {
  if (!selectedTenant) return;
  if (!confirm(`Remove ${email} from ${esc(selectedTenant.display_name)}?`)) return;
  try {
    await api(`/tenants/${selectedTenant.id}/users/${userId}`, { method: "DELETE" });
    await loadUsers(selectedTenant.id); await loadRoles(selectedTenant.id); loadAudit();
  } catch (err) { alert(err.message); }
}

$("invite-user-btn").onclick = () => {
  $("invite-form").hidden = !$("invite-form").hidden;
  $("invite-error").hidden = true; $("invite-result").hidden = true;
};
$("cancel-invite").onclick = () => { $("invite-form").hidden = true; };

$("invite-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedTenant) return;
  $("invite-error").hidden = true; $("invite-result").hidden = true;
  try {
    const u = await api(`/tenants/${selectedTenant.id}/users`, {
      method: "POST", body: { email: $("u-email").value, role: $("u-role").value },
    });
    // emailed: the tenant's own configured mail relay delivered the credential
    // directly - nothing to show or relay by hand. Falls back to the original
    // on-screen password when no channel is configured or the send failed.
    $("invite-result").textContent = u.emailed
      ? `Invited ${esc(u.email)} — sign-in details emailed to them directly.`
      : `Invited ${esc(u.email)}. Temporary password: ${u.temp_password} (shown once — share it securely; no email channel is configured for this tenant)`;
    $("invite-result").hidden = false;
    $("u-email").value = "";
    await loadUsers(selectedTenant.id); await loadRoles(selectedTenant.id); loadAudit();
  } catch (err) { $("invite-error").textContent = err.message; $("invite-error").hidden = false; }
});

// ---- Roles (BR-112 view, BR-113 write path) ----
const FIXED_ROLE_NAMES = ["tenant_admin", "analyst", "investigator", "risk_manager",
  "principal_officer", "supervisor", "board", "cro", "data_scientist", "rbi_inspector"];
// Cached so the Users table's role picker (loadUsers) can build its <option>s
// without a second fetch - loadRoles always runs first (see selectTenant).
let currentTenantRoles = [];

async function loadRoles(tenantId) {
  const rows = $("role-rows");
  try {
    const roles = await api(`/tenants/${tenantId}/roles`);
    currentTenantRoles = roles;
    if (!roles.length) { rows.innerHTML = `<tr><td colspan="7" class="muted">No roles</td></tr>`; return; }
    rows.innerHTML = roles.map((r) => {
      const mods = r.modules.map((m) => esc(m.label)).join(", ") || "—";
      const custom = r.source === "custom";
      const fixed = FIXED_ROLE_NAMES.includes(r.name);
      const pending = r.elevation_pending
        ? `<span class="badge muted" title="A different administrator must confirm this before it takes effect">pending confirm</span>` : "";
      const del = fixed
        ? `<span class="muted" title="One of the platform's fixed roles">fixed</span>`
        : r.member_count > 0
          ? `<span class="muted" title="Reassign its users first">in use</span>`
          : `<button class="link-btn danger" onclick="deleteRole('${esc(r.name)}')">Delete</button>`;
      return `<tr><td>${esc(r.label)} <span class="mono muted">${esc(r.name)}</span>
              ${custom ? '<span class="badge">custom</span>' : ''} ${pending}</td>
              <td>${mods}</td><td>${r.can_admin_tenant ? "✓" : "—"}</td>
              <td>${r.can_reveal_pii ? "✓" : "—"}</td>
              <td title="May propose or confirm a configuration activation">${r.can_activate_config ? "✓" : "—"}</td>
              <td>${r.member_count}</td>
              <td>${del}</td></tr>`;
    }).join("");
    // The invite form's role picker should offer every role this tenant actually
    // has, not just the ten built in - otherwise a custom role can be created but
    // never assigned to anyone through the console.
    const picker = $("u-role");
    if (picker) {
      const current = picker.value;
      picker.innerHTML = roles.map((r) =>
        `<option value="${esc(r.name)}">${esc(r.label)}</option>`).join("");
      if (roles.some((r) => r.name === current)) picker.value = current;
    }
  } catch { rows.innerHTML = `<tr><td colspan="7" class="muted">—</td></tr>`; }
}

async function deleteRole(name) {
  if (!selectedTenant) return;
  if (!confirm(`Delete the role "${name}"? This cannot be undone.`)) return;
  try {
    await api(`/tenants/${selectedTenant.id}/roles/${name}`, { method: "DELETE" });
    await loadRoles(selectedTenant.id); loadAudit();
  } catch (err) { alert(err.message); }
}

$("new-role-btn").onclick = () => {
  $("new-role-form").hidden = !$("new-role-form").hidden;
  $("role-error").hidden = true;
};
$("cancel-new-role").onclick = () => { $("new-role-form").hidden = true; };

$("new-role-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedTenant) return;
  $("role-error").hidden = true;
  const modules = [...$("r-modules").selectedOptions].map((o) => o.value);
  const dashboards = [...$("r-dashboards").selectedOptions].map((o) => o.value);
  try {
    await api(`/tenants/${selectedTenant.id}/roles`, { method: "POST", body: {
      name: $("r-name").value, label: $("r-label").value,
      modules, dashboards,
      can_admin_tenant: $("r-admin").checked, can_reveal_pii: $("r-pii").checked,
      can_activate_config: $("r-activate").checked,
    }});
    $("new-role-form").hidden = true; $("new-role-form").reset();
    await loadRoles(selectedTenant.id); loadAudit();
  } catch (err) { $("role-error").textContent = err.message; $("role-error").hidden = false; }
});

// ------------------------------------------- identity providers (federated sign-in)
// Mirrors the u-role list (invite form): a group can only ever be mapped to a role a
// tenant may itself assign - platform roles are not offered, matching the server-side
// refusal in _validate_role_fields.
const TENANT_ROLES = [
  ["analyst", "Fraud Analyst (L1)"], ["investigator", "Senior Investigator (L2)"],
  ["risk_manager", "Fraud Risk Manager"], ["principal_officer", "Principal Officer (PMLA)"],
  ["supervisor", "Compliance Supervisor"], ["board", "Board / Executive"],
  ["cro", "CRO / ACB / Special Committee"], ["data_scientist", "Model Risk / Data Science"],
  ["rbi_inspector", "Internal Audit / RBI Inspection"], ["tenant_admin", "Tenant Administrator"],
];
let currentProviders = [];

function _roleOptions(selected) {
  return TENANT_ROLES.map(([v, label]) =>
    `<option value="${esc(v)}" ${v === selected ? "selected" : ""}>${esc(label)}</option>`).join("");
}

function addIdpRoleRow(group = "", role = "analyst") {
  const row = document.createElement("div");
  row.className = "param-row idp-role-row";
  row.innerHTML = `<input placeholder="Directory group, e.g. FRAUD-ANALYSTS" class="idp-role-group" value="${esc(group)}" />
    <select class="idp-role-role">${_roleOptions(role)}</select>
    <button type="button" class="row-remove" onclick="removeRow(this, '.idp-role-row')">Remove</button>`;
  $("idp-role-rows").appendChild(row);
}

function _idpProtocolToggle() {
  const saml = $("idp-protocol").value === "saml";
  $("idp-oidc-fields").hidden = saml;
  $("idp-saml-fields").hidden = !saml;
}
$("idp-protocol").addEventListener("change", _idpProtocolToggle);

async function loadProviders(tenantId) {
  const rows = $("idp-rows");
  $("add-idp-btn").hidden = !(me && me.can_admin_tenant);
  try {
    currentProviders = await api(`/auth/sso/admin/${tenantId}/providers`);
    if (!currentProviders.length) {
      rows.innerHTML = `<tr><td colspan="5" class="muted">No identity providers configured — sign-in is by platform password only</td></tr>`;
      return;
    }
    rows.innerHTML = currentProviders.map((p) => {
      const actions = me && me.can_admin_tenant
        ? `<button class="link-btn" data-act="edit-idp" data-id="${p.id}">Edit</button>
           <button class="link-btn danger" data-act="delete-idp" data-id="${p.id}">Delete</button>`
        : "";
      return `<tr>
        <td class="mono-cell">${esc(p.slug)}</td><td>${esc(p.display_name)}</td>
        <td>${esc(p.protocol.toUpperCase())}</td>
        <td><span class="badge ${p.enabled ? "active" : "draft"}">${p.enabled ? "enabled" : "disabled"}</span></td>
        <td class="row-actions">${actions}</td></tr>`;
    }).join("");
  } catch { rows.innerHTML = `<tr><td colspan="5" class="muted">—</td></tr>`; }
}

function _openIdpForm(p) {
  $("idp-form").hidden = false;
  $("idp-error").hidden = true;
  $("idp-id").value = p ? p.id : "";
  $("idp-slug").value = p ? p.slug : "";
  $("idp-display").value = p ? p.display_name : "";
  $("idp-protocol").value = p ? p.protocol : "oidc";
  $("idp-enabled").checked = p ? p.enabled : true;
  $("idp-issuer").value = p ? p.issuer : "";
  $("idp-client-id").value = p ? p.client_id : "";
  $("idp-client-secret").value = "";
  $("idp-client-secret").placeholder = p && p.has_client_secret ? "unchanged" : "";
  $("idp-discovery").value = p ? p.discovery_url : "";
  $("idp-authorize-url").value = p ? p.authorize_url : "";
  $("idp-token-url").value = p ? p.token_url : "";
  $("idp-jwks-url").value = p ? p.jwks_url : "";
  $("idp-scopes").value = p ? p.scopes : "openid email profile";
  $("idp-email-claim").value = p ? p.email_claim : "email";
  $("idp-groups-claim").value = p ? p.groups_claim : "groups";
  $("idp-saml-sso-url").value = p ? p.saml_sso_url : "";
  $("idp-saml-cert").value = "";
  $("idp-saml-cert").placeholder = p && p.has_saml_certificate ? "unchanged" : "";
  $("idp-saml-email-attr").value = p ? p.saml_email_attribute : "";
  $("idp-saml-groups-attr").value = p ? p.saml_groups_attribute : "";
  $("idp-role-rows").innerHTML = "";
  Object.entries((p && p.role_mapping) || {}).forEach(([g, r]) => addIdpRoleRow(g, r));
  $("idp-default-role").innerHTML = _roleOptions(p ? p.default_role : "analyst");
  $("idp-jit").checked = p ? p.jit_provisioning : true;
  _idpProtocolToggle();
}

$("add-idp-btn").onclick = () => _openIdpForm(null);
$("idp-cancel").onclick = () => { $("idp-form").hidden = true; };

$("idp-rows").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn || !selectedTenant) return;
  const p = currentProviders.find((x) => x.id === btn.dataset.id);
  if (!p) return;
  if (btn.dataset.act === "edit-idp") {
    _openIdpForm(p);
  } else if (btn.dataset.act === "delete-idp") {
    if (!confirm(`Remove "${p.display_name}"? Staff who sign in through it will fall back to their platform password.`)) return;
    try {
      await api(`/auth/sso/admin/${selectedTenant.id}/providers/${p.id}`, { method: "DELETE" });
      await loadProviders(selectedTenant.id); loadAudit();
    } catch (err) { alert(err.message); }
  }
});

$("idp-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedTenant) return;
  const err = $("idp-error");
  err.hidden = true;

  const role_mapping = {};
  document.querySelectorAll("#idp-role-rows .idp-role-row").forEach((row) => {
    const g = row.querySelector(".idp-role-group").value.trim();
    if (g) role_mapping[g] = row.querySelector(".idp-role-role").value;
  });

  const body = {
    slug: $("idp-slug").value.trim(), display_name: $("idp-display").value.trim(),
    protocol: $("idp-protocol").value, enabled: $("idp-enabled").checked,
    issuer: $("idp-issuer").value.trim(),
    client_id: $("idp-client-id").value.trim(),
    client_secret: $("idp-client-secret").value,
    discovery_url: $("idp-discovery").value.trim(),
    authorize_url: $("idp-authorize-url").value.trim(),
    token_url: $("idp-token-url").value.trim(),
    jwks_url: $("idp-jwks-url").value.trim(),
    scopes: $("idp-scopes").value.trim(),
    email_claim: $("idp-email-claim").value.trim(),
    groups_claim: $("idp-groups-claim").value.trim(),
    saml_sso_url: $("idp-saml-sso-url").value.trim(),
    saml_certificate: $("idp-saml-cert").value,
    saml_email_attribute: $("idp-saml-email-attr").value.trim(),
    saml_groups_attribute: $("idp-saml-groups-attr").value.trim(),
    role_mapping, default_role: $("idp-default-role").value,
    jit_provisioning: $("idp-jit").checked,
  };

  const id = $("idp-id").value;
  try {
    if (id) {
      await api(`/auth/sso/admin/${selectedTenant.id}/providers/${id}`,
                { method: "PATCH", body });
    } else {
      await api(`/auth/sso/admin/${selectedTenant.id}/providers`,
                { method: "POST", body });
    }
    $("idp-form").hidden = true;
    await loadProviders(selectedTenant.id); loadAudit();
  } catch (ex) { err.textContent = ex.message; err.hidden = false; }
});

// Which lane a "rule"-kind config's name (a rule id, e.g. "VEL-01") belongs to.
// Lane A: exactly the set decision-service's inline lane evaluates in the payment
// window - copied from services/decision_service/app/inline_rules.py's INLINE_SOURCE,
// not re-derived, so this list cannot silently drift from what actually runs inline.
// Lane C: no dedicated engine exists yet (see docs/LANE_C_DESIGN_AND_USE_CASES.md) -
// the QUAL-* rules are its closest built analogue: qualitative, entered by credit
// monitoring rather than computed from a transaction, same theme as Lane C's periodic
// borrower assessment. They are scored by the same near-real-time engine as Lane B
// today, not a separate one.
// Everything else "rule"-kind is Lane B, the near-real-time engine's own set.
const LANE_A_RULE_IDS = new Set([
  "VEL-01", "VEL-03", "SME-01", "SME-02", "BEH-01", "LAY-01", "LAY-02", "LAY-04", "CHN-01",
]);
const LANE_C_RULE_IDS = new Set(["QUAL-01", "QUAL-02", "QUAL-03"]);

const LANE_NOTES = {
  a: "Lane A — evaluated inline, inside the payment authorisation window (POST /decide). "
    + "Exactly the rule ids decision-service's inline lane can measure from account "
    + "counters or from what the channel already sends.",
  b: "Lane B — scored near-real-time, after ingestion, with full account and network "
    + "context. Everything not eligible for Lane A or thematically Lane C.",
  c: "Lane C — \"Periodic Borrower Assessment\" has no dedicated scoring engine yet "
    + "(see docs/LANE_C_DESIGN_AND_USE_CASES.md). These qualitative, credit-monitoring-"
    + "entered rules are its closest built analogue today; they are still scored by the "
    + "same near-real-time engine as Lane B, not a separate one.",
};

function _laneOf(cfg) {
  if (cfg.kind !== "rule") return null;
  if (LANE_A_RULE_IDS.has(cfg.name)) return "a";
  if (LANE_C_RULE_IDS.has(cfg.name)) return "c";
  return "b";
}

let currentLaneFilter = "all";

function renderConfigRows() {
  const rows = $("config-rows");
  const note = $("lane-note");
  if (currentLaneFilter === "all") {
    note.hidden = true;
  } else {
    note.textContent = LANE_NOTES[currentLaneFilter];
    note.hidden = false;
  }
  const filtered = currentLaneFilter === "all" ? currentConfigs
    : currentConfigs.filter((c) => _laneOf(c) === currentLaneFilter);

  if (!currentConfigs.length) { rows.innerHTML = `<tr><td colspan="5" class="muted">No configs</td></tr>`; return; }
  if (!filtered.length) {
    rows.innerHTML = `<tr><td colspan="5" class="muted">No configs in this lane</td></tr>`;
    return;
  }
  rows.innerHTML = filtered.map((c) => {
    const activate = c.status === "active" ? "" :
      `<button class="link-btn" data-act="activate" data-id="${c.id}">Activate</button>`;
    return `<tr>
      <td>${esc(c.kind)}</td><td>${esc(c.name)}</td><td>${esc(c.version)}</td>
      <td><span class="badge ${c.status}">${c.status}</span></td>
      <td class="row-actions">
        <button class="link-btn" data-act="view" data-id="${c.id}">View</button>${activate}
        <button class="link-btn" data-act="duplicate" data-id="${c.id}">Duplicate</button>
      </td></tr>`;
  }).join("");
}

document.querySelectorAll("#lane-tabs [data-lane]").forEach((btn) => {
  btn.addEventListener("click", () => {
    currentLaneFilter = btn.dataset.lane;
    document.querySelectorAll("#lane-tabs [data-lane]").forEach((b) =>
      b.classList.toggle("active", b === btn));
    renderConfigRows();
  });
});

async function loadConfigs(tenantId) {
  const rows = $("config-rows");
  try {
    currentConfigs = await api(`/configs/${tenantId}`);
    renderConfigRows();
  } catch { rows.innerHTML = `<tr><td colspan="5" class="muted">—</td></tr>`; }
}

// Bumps the trailing numeric segment: "1.0.1" -> "1.0.2". Configs are immutable once
// created (see models.py) - there is no "edit", only a new version to activate - so
// Duplicate has to propose a version number that will not collide with the one it
// copied from. Falls back to appending a segment if the version does not end in a
// number, rather than guessing at semver.
function _bumpVersion(v) {
  const parts = String(v || "1.0.0").split(".");
  const last = parseInt(parts[parts.length - 1], 10);
  if (Number.isFinite(last)) {
    parts[parts.length - 1] = String(last + 1);
    return parts.join(".");
  }
  return `${v}.1`;
}

function _openConfigFormFrom(cfg, { bumpVersion } = {}) {
  $("config-msg").hidden = true;
  $("config-view").hidden = true;
  $("config-form").hidden = false;
  $("c-kind").value = cfg.kind;
  $("c-name").value = cfg.name;
  $("c-version").value = bumpVersion ? _bumpVersion(cfg.version) : cfg.version;
  $("c-body").value = JSON.stringify(cfg.body, null, 2);
  // The structured editor only ever understands a rule's config.parameters/exitCond
  // itions/bands shape (see renderStructuredEditor) - typology, network_map and policy
  // bodies fall through it harmlessly today, so only offer structured mode where it
  // will actually render something.
  if (cfg.kind === "rule" && cfg.body && cfg.body.config) {
    editorMode = "structured";
    syncToStructured();
    $("structured-editor").hidden = false; $("json-wrap").hidden = true;
    $("mode-structured").classList.add("active"); $("mode-json").classList.remove("active");
  } else {
    editorMode = "json";
    $("structured-editor").hidden = true; $("json-wrap").hidden = false;
    $("mode-json").classList.add("active"); $("mode-structured").classList.remove("active");
  }
}

// Delegate View / Activate / Duplicate clicks inside the config table.
$("config-rows").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn || !selectedTenant) return;
  const id = btn.dataset.id;
  const cfg = currentConfigs.find((c) => c.id === id);
  if (btn.dataset.act === "view") {
    const view = $("config-view");
    view.textContent = JSON.stringify(cfg.body, null, 2);
    view.hidden = false;
  } else if (btn.dataset.act === "activate") {
    try {
      await api(`/configs/${selectedTenant.id}/${id}/activate`, { method: "POST" });
      await loadConfigs(selectedTenant.id); loadAudit();
    } catch (err) { alert(err.message); }
  } else if (btn.dataset.act === "duplicate") {
    _openConfigFormFrom(cfg, { bumpVersion: true });
  }
});

// Starter templates so the editor isn't a blank box.
const CONFIG_TEMPLATES = {
  typology: JSON.stringify({
    id: "mule-layering@1.0.0", cfg: "1.0.0", desc: "Mule-account layering typology (LAY family)",
    rules: [{ id: "018@1.0.0", cfg: "1.0.0", termId: "t018",
      wghts: [{ ref: ".x00", wght: 0 }, { ref: ".02", wght: 50 }, { ref: ".03", wght: 150 }] }],
    expression: "t018 + t024 + t030 + t044",
    workflow: { alertThreshold: 150, interdictionThreshold: 300 },
  }, null, 2),
  rule: JSON.stringify({
    id: "018@1.0.0", cfg: "1.0.0", desc: "Pass-through drain velocity",
    config: {
      parameters: [{ ParameterName: "lookbackMs", ParameterValue: 600000, ParameterType: "number" }],
      exitConditions: [{ subRuleRef: ".x00", reason: "No qualifying inflow in window" }],
      bands: [
        { subRuleRef: ".01", upperLimit: 0.5, reason: "Outflow < 50% of recent inflow" },
        { subRuleRef: ".03", lowerLimit: 0.9, reason: "Outflow >= 90% (pass-through)" }],
    },
  }, null, 2),
  network_map: JSON.stringify({
    active: true, cfg: "1.0.0",
    messages: [{ id: "004@1.0.0", cfg: "1.0.0", txTp: "pacs.008.001.10",
      typologies: [{ id: "mule-layering@1.0.0", cfg: "1.0.0", rules: [{ id: "018@1.0.0", cfg: "1.0.0" }] }] }],
  }, null, 2),
  // Shape matches config_service/app/policy.py:policy_body() exactly, seeded with the
  // commercial_bank defaults, so an admin edits real numbers rather than inventing the
  // schema from scratch. Activation refuses this without a completed "attestation" block
  // (BR-104) - that gate lives server-side, not in this editor.
  policy: JSON.stringify({
    entity_type: "commercial_bank",
    entity_label: "Commercial Bank",
    governing_direction: "Fraud Risk Management in Commercial Banks, 2026 (RBI/DoS/2026-27/412)",
    reporting_to: ["RBI", "FIU-IND"],
    ucb_tier: null,
    // PMLA Rule 7 designation. No sensible default - an STR cannot be filed until this
    // is set and the version below is activated.
    principal_officer_name: "",
    thresholds: {
      natural_justice_days: 21, show_cause_within_days: 7,
      fmr_filing_days: 7, str_filing_days: 7, staff_accountability_days: 180,
      ews_examination_days: 30,
      lea_referral_paise: 1_000_000_00 * 10, board_reporting_paise: 1_000_000_00 * 10,
      material_fraud_paise: 100_000_00 * 10,
      severity_critical_score: 380, severity_high_score: 260, severity_medium_score: 150,
      sla_breach_hours: 24, stalled_case_days: 30,
      sla_hours_by_severity: { critical: 4, high: 12, medium: 24, low: 72 },
      retain_sessions_days: 90, retain_notifications_days: 180,
      retain_raw_ingest_days: 400, retain_transactions_days: 1825,
      retain_alerts_days: 730, retain_closed_cases_days: 1825,
      retain_fraud_records_days: 3650, retain_audit_days: 2920,
      special_committee: true, audit_committee: true, board_of_management: false,
      board_review_frequency: "quarterly",
    },
    // Required before this version can be activated - see attestation.py. approved_by
    // is one of: board, acb, board_of_management, scbmf, risk_committee.
    attestation: {
      approved_by: "risk_committee", approved_on: "", reference: "",
      note: "",
    },
  }, null, 2),
};

$("add-config-btn").onclick = () => { $("config-form").hidden = false; $("config-view").hidden = true; };
$("c-cancel").onclick = () => { $("config-form").hidden = true; };
// ---- Structured rule editor ----
let editorMode = "structured";
let currentRuleBody = null;

// Parse rule config into structured UI (parameters, bands, exit conditions).
function renderStructuredEditor(body) {
  currentRuleBody = body;
  const ed = $("structured-editor");
  if (!body || !body.config) { ed.innerHTML = "<p class='muted'>Load a template to get started</p>"; return; }
  const cfg = body.config;

  let html = "<div class='editor-section'><h5>ID & Description</h5>";
  html += `<label>ID <input id="e-id" value="${esc(body.id || '')}" /></label>`;
  html += `<label>Description <input id="e-desc" value="${esc(body.desc || '')}" /></label></div>`;

  html += "<div class='editor-section'><h5>Parameters</h5><div id='params'>";
  (cfg.parameters || []).forEach((p) => {
    html += `<div class='param-row'>
      <input placeholder="Name" class='param-name' value='${esc(p.ParameterName || '')}' />
      <input placeholder="Value" class='param-value' type='number' value='${esc(p.ParameterValue ?? '')}' />
      <select class='param-type'>
        <option ${p.ParameterType === 'number' ? 'selected' : ''}>number</option>
        <option ${p.ParameterType === 'string' ? 'selected' : ''}>string</option>
      </select>
      <button type='button' class='row-remove' onclick='removeRow(this, ".param-row")'>Remove</button>
    </div>`;
  });
  html += "</div><button type='button' class='add-btn' onclick='addParam()'>+ Parameter</button></div>";

  html += "<div class='editor-section'><h5>Exit Conditions</h5><div id='exits'>";
  (cfg.exitConditions || []).forEach((e) => {
    html += `<div class='exit-row'>
      <input placeholder=".x00" class='exit-ref' value='${esc(e.subRuleRef || '')}' />
      <input placeholder="Reason" class='exit-reason' value='${esc(e.reason || '')}' style='grid-column: span 2;' />
      <button type='button' class='row-remove' onclick='removeRow(this, ".exit-row")'>Remove</button>
    </div>`;
  });
  html += "</div><button type='button' class='add-btn' onclick='addExit()'>+ Exit Condition</button></div>";

  html += "<div class='editor-section'><h5>Bands</h5><div id='bands'>";
  (cfg.bands || []).forEach((b) => {
    html += `<div class='band-row'>
      <input placeholder=".01" class='band-ref' value='${esc(b.subRuleRef || '')}' />
      <input placeholder="Lower" type='number' class='band-lower' value='${esc(b.lowerLimit ?? '')}' />
      <input placeholder="Upper" type='number' class='band-upper' value='${esc(b.upperLimit ?? '')}' />
      <input placeholder="Reason" class='band-reason' value='${esc(b.reason || '')}' />
      <button type='button' class='row-remove' onclick='removeRow(this, ".band-row")'>Remove</button>
    </div>`;
  });
  html += "</div><button type='button' class='add-btn' onclick='addBand()'>+ Band</button></div>";

  ed.innerHTML = html;
}

// Sync structured → JSON.
function syncToJSON() {
  if (editorMode !== "structured") return;
  // Scope each query to its own container — the row classes are distinct, but scoping
  // keeps a future layout change from silently mixing rows between sections.
  const q = (sel) => Array.from(document.querySelectorAll(sel));
  const params = q("#params .param-row").map((row) => ({
    ParameterName: row.querySelector(".param-name")?.value || "",
    ParameterValue: parseFloat(row.querySelector(".param-value")?.value) || 0,
    ParameterType: row.querySelector(".param-type")?.value || "number",
  }));
  const exits = q("#exits .exit-row").map((row) => ({
    subRuleRef: row.querySelector(".exit-ref")?.value || "",
    reason: row.querySelector(".exit-reason")?.value || "",
  }));
  const bands = q("#bands .band-row").map((row) => {
    const b = { subRuleRef: row.querySelector(".band-ref")?.value || "" };
    const lower = parseFloat(row.querySelector(".band-lower")?.value);
    if (!isNaN(lower)) b.lowerLimit = lower;
    const upper = parseFloat(row.querySelector(".band-upper")?.value);
    if (!isNaN(upper)) b.upperLimit = upper;
    b.reason = row.querySelector(".band-reason")?.value || "";
    return b;
  });
  currentRuleBody = {
    id: document.getElementById("e-id")?.value || "rule@1.0.0",
    cfg: "1.0.0",
    desc: document.getElementById("e-desc")?.value || "",
    config: { parameters: params, exitConditions: exits, bands },
  };
  $("c-body").value = JSON.stringify(currentRuleBody, null, 2);
}

// Sync JSON → structured (when pasting JSON).
function syncToStructured() {
  try {
    currentRuleBody = JSON.parse($("c-body").value);
    renderStructuredEditor(currentRuleBody);
  } catch {}
}

function removeRow(btn, sel) { btn.closest(sel)?.remove(); syncToJSON(); }

function addParam() {
  const row = document.createElement("div");
  row.className = "param-row";
  row.innerHTML = `<input placeholder='Name' class='param-name' />
    <input placeholder='Value' type='number' class='param-value' />
    <select class='param-type'><option>number</option><option>string</option></select>
    <button type='button' class='row-remove' onclick='removeRow(this, ".param-row")'>Remove</button>`;
  document.getElementById("params").appendChild(row);
  syncToJSON();
}
function addExit() {
  const row = document.createElement("div");
  row.className = "exit-row";
  row.innerHTML = `<input placeholder='.x00' class='exit-ref' />
    <input placeholder='Reason' class='exit-reason' style='grid-column: span 2;' />
    <button type='button' class='row-remove' onclick='removeRow(this, ".exit-row")'>Remove</button>`;
  document.getElementById("exits").appendChild(row);
  syncToJSON();
}
function addBand() {
  const row = document.createElement("div");
  row.className = "band-row";
  row.innerHTML = `<input placeholder='.01' class='band-ref' />
    <input placeholder='Lower' type='number' class='band-lower' />
    <input placeholder='Upper' type='number' class='band-upper' />
    <input placeholder='Reason' class='band-reason' />
    <button type='button' class='row-remove' onclick='removeRow(this, ".band-row")'>Remove</button>`;
  document.getElementById("bands").appendChild(row);
  syncToJSON();
}

$("mode-structured").onclick = () => {
  editorMode = "structured";
  syncToStructured();
  $("structured-editor").hidden = false; $("json-wrap").hidden = true;
  $("mode-structured").classList.add("active"); $("mode-json").classList.remove("active");
};
$("mode-json").onclick = () => {
  // Sync BEFORE flipping the mode — syncToJSON() only runs while mode is "structured".
  syncToJSON();
  editorMode = "json";
  $("structured-editor").hidden = true; $("json-wrap").hidden = false;
  $("mode-json").classList.add("active"); $("mode-structured").classList.remove("active");
};

$("c-template").onclick = () => {
  const tmpl = CONFIG_TEMPLATES[$("c-kind").value] || "{}";
  $("c-body").value = tmpl;
  editorMode = "structured";
  syncToStructured();
  $("structured-editor").hidden = false; $("json-wrap").hidden = true;
  $("mode-structured").classList.add("active"); $("mode-json").classList.remove("active");
  const body = JSON.parse(tmpl);
  if (body.id && !$("c-name").value) $("c-name").value = String(body.id).split("@")[0];
};

$("config-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("config-msg"); msg.hidden = true;
  if (!selectedTenant) return;
  // Pull the latest structured-editor state into the JSON body, otherwise edits made
  // in structured mode (without toggling to JSON) would be silently dropped.
  syncToJSON();
  let body;
  try { body = JSON.parse($("c-body").value); }
  catch (err) { msg.textContent = "Body is not valid JSON: " + err.message; msg.hidden = false; return; }
  try {
    await api(`/configs/${selectedTenant.id}`, { method: "POST", body: {
      kind: $("c-kind").value, name: $("c-name").value, version: $("c-version").value, body,
    }});
    $("config-form").reset(); $("config-form").hidden = true;
    await loadConfigs(selectedTenant.id); loadAudit();
  } catch (err) { msg.textContent = err.message; msg.hidden = false; }
});


// ---------------------------------------- simulate a threshold change (BR-311)
// The backfill simulator already answers "what would this band have caught"; what was
// missing was the path from trying a value to activating it. Nothing here writes: the
// simulation is explicitly read-only, and the operator still has to create a version.

/** The operative threshold in a rule body: the band flagged operative carries it. */
function operativeThreshold(body) {
  const bands = body && body.config && body.config.bands;
  if (!Array.isArray(bands)) return null;
  const band = bands.find((b) => b.operative) || bands[bands.length - 1];
  if (!band) return null;
  const v = band.lowerLimit ?? band.upperLimit;
  return v === undefined || v === null || v === "" ? null : Number(v);
}

function simRow(rid, would, actual) {
  const delta = would - actual;
  const cls = delta > 0 ? "up" : delta < 0 ? "down" : "same";
  const sign = delta > 0 ? "+" : "";
  return `<tr><td class="mono-cell">${esc(rid)}</td>
    <td class="num">${esc(actual.toLocaleString())}</td>
    <td class="num">${esc(would.toLocaleString())}</td>
    <td class="num sim-${cls}">${esc(sign)}${esc(delta.toLocaleString())}</td></tr>`;
}

async function simulateThreshold() {
  const out = $("c-sim-result");
  out.hidden = false;
  if (!selectedTenant) { out.innerHTML = `<p class="error">Select a tenant first.</p>`; return; }

  syncToJSON();
  let body;
  try { body = JSON.parse($("c-body").value); }
  catch (err) {
    out.innerHTML = `<p class="error">Body is not valid JSON: ${esc(err.message)}</p>`;
    return;
  }
  const ruleId = String(body.id || "").split("@")[0];
  const threshold = operativeThreshold(body);
  if (!ruleId || threshold === null) {
    // Better than simulating the wrong thing: a rule with no operative band has no
    // single number to vary, and guessing which one to move would mislead.
    out.innerHTML = `<p class="error">This rule has no operative band with a threshold,
      so there is nothing to simulate. Add a band marked operative.</p>`;
    return;
  }

  const days = Number($("c-sim-window").value || 90);
  const to = new Date();
  const from = new Date(to.getTime() - days * 86400000);
  out.innerHTML = `<p class="muted">Scoring ${esc(days)} days of history at
    ${esc(ruleId)} = ${esc(threshold)}…</p>`;

  let res;
  try {
    res = await api(`/analytics/${selectedTenant.id}/detection/backfill`, {
      method: "POST",
      body: { window_from: from.toISOString(), window_to: to.toISOString(),
              mode: "simulate", thresholds: { [ruleId]: threshold } },
    });
  } catch (err) {
    out.innerHTML = `<p class="error">${esc(err.message)}</p>`;
    return;
  }

  const would = res.would_fire || {};
  const actual = res.actual || {};
  const rules = Array.from(new Set([...Object.keys(would), ...Object.keys(actual)])).sort();
  const rows = rules.map((r) => simRow(r, would[r] || 0, actual[r] || 0)).join("")
    || `<tr><td colspan="4" class="muted">Nothing fired either way.</td></tr>`;
  const mine = (would[ruleId] || 0) - (actual[ruleId] || 0);

  out.innerHTML = `
    <div class="sim-head">
      <b>${esc(ruleId)} at ${esc(threshold)}</b> over ${esc(days)} days
      &middot; ${esc((res.transactions || 0).toLocaleString())} transactions scored
    </div>
    <p class="sim-verdict ${mine > 0 ? "up" : mine < 0 ? "down" : "same"}">
      ${mine === 0 ? "No change to what this rule would have raised."
        : mine > 0 ? `This band would have raised <b>${mine.toLocaleString()}</b> more
                      alert(s) on ${esc(ruleId)}.`
                   : `This band would have raised <b>${Math.abs(mine).toLocaleString()}</b>
                      fewer alert(s) on ${esc(ruleId)}.`}
    </p>
    <table class="cfg-table sim-table">
      <thead><tr><th>Rule</th><th>Raised</th><th>Would raise</th><th>Change</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="muted sim-note">${esc(res.note || "")}
      Nothing has been written &mdash; create a version to make this band live.</p>`;
}

if ($("c-simulate")) $("c-simulate").onclick = simulateThreshold;

// ================= Analytics =================
let currentDash = "analyst";
let currentDrill = "alert";
// PII is masked server-side by default. Revealing requires the role capability plus a
// justification, and the server audits it — the UI only carries the request.
let piiReveal = { on: false, justification: "" };

const multi = (id) => Array.from($(id).selectedOptions).map((o) => o.value);

// ---- date presets ----
function applyPreset(key) {
  if (!key) return;
  const now = new Date(), z = (d) => d.toISOString().slice(0, 10);
  const start = new Date(now);
  if (key === "today") start.setHours(0, 0, 0, 0);
  else if (key === "7d") start.setDate(now.getDate() - 7);
  else if (key === "30d") start.setDate(now.getDate() - 30);
  else if (key === "mtd") start.setDate(1);
  else if (key === "qtd") { start.setMonth(Math.floor(now.getMonth() / 3) * 3, 1); }
  else if (key === "ytd") { start.setMonth(0, 1); }
  $("f-from").value = z(start);
  $("f-to").value = z(new Date(now.getTime() + 86400000));   // inclusive of today
}

// Every filter control, declared once: id -> {param, kind}. Used to build the query,
// serialise to a URL, restore from one, and save named views — so a new filter is added
// in exactly one place.
const FILTER_MAP = [
  ["f-from", "date_from", "date"], ["f-to", "date_to", "date"],
  ["f-rail", "rails", "multi"], ["f-family", "families", "multi"],
  ["f-severity", "severities", "multi"], ["f-region", "regions", "multi"],
  ["f-product", "products", "multi"], ["f-segment", "segments", "multi"],
  ["f-state", "states", "multi"], ["f-disposition", "dispositions", "multi"],
  ["f-fmrcat", "fmr_categories", "multi"], ["f-rule", "rules", "multi"],
  ["f-typology", "typologies", "multi"], ["f-assignee", "assignees", "multi"],
  ["f-account", "account", "text"], ["f-branch", "branches", "text"],
  ["f-amtmin", "min_amount_paise", "rupees"], ["f-amtmax", "max_amount_paise", "rupees"],
  ["f-fmrstatus", "fmr_status", "select"], ["f-strstatus", "str_status", "select"],
  ["f-rfa", "rfa_only", "bool"], ["f-njbreach", "nj_breach_only", "bool"],
  ["f-source", "sources", "multi"],
];

function buildFilterParams() {
  const p = new URLSearchParams();
  for (const [id, param, kind] of FILTER_MAP) {
    const el = $(id); if (!el) continue;
    if (kind === "multi") multi(id).forEach((v) => p.append(param, v));
    else if (kind === "date") { if (el.value) p.append(param, new Date(el.value).toISOString()); }
    else if (kind === "rupees") { if (el.value !== "") p.append(param, String(Math.round(Number(el.value) * 100))); }
    else if (kind === "bool") { if (el.checked) p.append(param, "true"); }
    else if (el.value.trim()) p.append(param, el.value.trim());
  }
  return p;
}

// ---- URL sharing: the active filter set IS the link ----
function filtersToQuery() {
  const p = buildFilterParams();
  if (currentDash) p.set("dash", currentDash);
  if (selectedTenant) p.set("tenant", selectedTenant.id);
  return p.toString();
}
function syncUrl() {
  history.replaceState(null, "", "#" + filtersToQuery());
}
function restoreFiltersFromUrl() {
  const q = new URLSearchParams(location.hash.replace(/^#/, ""));
  if (![...q.keys()].length) return null;
  applyFilterState(q);
  return { dash: q.get("dash"), tenant: q.get("tenant") };
}
function applyFilterState(q) {
  for (const [id, param, kind] of FILTER_MAP) {
    const el = $(id); if (!el) continue;
    const vals = q.getAll(param);
    if (kind === "multi") {
      Array.from(el.options).forEach((o) => (o.selected = vals.includes(o.value)));
    } else if (kind === "bool") {
      el.checked = vals[0] === "true";
    } else if (kind === "date") {
      el.value = vals[0] ? vals[0].slice(0, 10) : "";
    } else if (kind === "rupees") {
      el.value = vals[0] ? String(Number(vals[0]) / 100) : "";
    } else {
      el.value = vals[0] || "";
    }
  }
}

// ---- saved views (server-side, per tenant) ----
// Views used to live in localStorage: lost on a cache clear, invisible on another
// machine, impossible to hand to a colleague. A view is a working artefact, so it is
// owned by a person and stored against the tenant.
let savedViews = [];

async function renderSavedViews() {
  const sel = $("saved-views");
  if (!sel || !selectedTenant) return;
  try {
    savedViews = await api(`/analytics/${selectedTenant.id}/views`);
  } catch {
    savedViews = [];
  }
  const mine = savedViews.filter((v) => v.mine);
  const team = savedViews.filter((v) => !v.mine);
  const opt = (v) => `<option value="${v.id}">${v.shared ? "\u25C9 " : ""}${esc(v.name)}</option>`;
  sel.innerHTML =
    `<option value="">Saved views…</option>` +
    (mine.length ? `<optgroup label="My views">${mine.map(opt).join("")}</optgroup>` : "") +
    (team.length ? `<optgroup label="Shared with me">${team.map(opt).join("")}</optgroup>` : "");
  reflectViewSelection(savedViews.find((v) => v.id === sel.value));
}

function describeFilters() {
  const bits = [];
  if ($("f-from").value || $("f-to").value)
    bits.push(`period ${$("f-from").value || "…"} → ${$("f-to").value || "…"}`);
  for (const [id, param, kind] of FILTER_MAP) {
    if (id === "f-from" || id === "f-to") continue;
    const el = $(id); if (!el) continue;
    if (kind === "multi") { const v = multi(id); if (v.length) bits.push(`${param}: ${v.join(",")}`); }
    else if (kind === "bool") { if (el.checked) bits.push(param); }
    else if (el.value.trim()) bits.push(`${param}: ${el.value.trim()}`);
  }
  return bits.length ? "filters — " + bits.join("  ·  ") : "filters — none (all data)";
}

const inr = (paise) => {
  const r = paise / 100;
  if (Math.abs(r) >= 1e7) return "₹" + (r / 1e7).toFixed(2) + " Cr";
  if (Math.abs(r) >= 1e5) return "₹" + (r / 1e5).toFixed(2) + " L";
  return "₹" + r.toLocaleString("en-IN", { maximumFractionDigits: 0 });
};
function fmtMetric(m) {
  if (m.unit === "inr_paise") return inr(m.value);
  if (m.unit === "ratio") return (m.value * 100).toFixed(1) + "%";
  // "number" is a raw magnitude (a rule score, alerts-per-case) - not a percentage.
  if (m.unit === "number") return Number(m.value).toFixed(1);
  if (m.unit === "seconds") {
    const s = m.value; if (s >= 3600) return (s / 3600).toFixed(1) + " h";
    if (s >= 60) return (s / 60).toFixed(0) + " m"; return s.toFixed(0) + " s";
  }
  return Number(m.value).toLocaleString("en-IN");
}
// Metrics where a non-zero value is a problem, not an achievement.
const BAD_IF_NONZERO = ["fmr_overdue_count", "str_overdue_count", "nj_breach_count"];

function renderKpis(metrics, prev) {
  $("kpi-row").innerHTML = Object.values(metrics).map((m) => {
    // Comparison is deliberately NOT shown for volatile metrics: they are point-in-time
    // readings, so "vs previous period" would be meaningless.
    let delta = "";
    if (prev && prev[m.name] && !m.volatile) {
      const b = prev[m.name].value, a = m.value;
      if (b !== 0 || a !== 0) {
        const pct = b === 0 ? null : ((a - b) / Math.abs(b)) * 100;
        const up = a >= b;
        delta = `<div class="kpi-delta ${up ? "up" : "down"}">${up ? "▲" : "▼"} ` +
                (pct === null ? "new" : `${Math.abs(pct).toFixed(1)}%`) +
                ` <span class="muted">vs prev</span></div>`;
      }
    }
    let cls = "";
    if (BAD_IF_NONZERO.includes(m.name)) cls = m.value > 0 ? "bad" : "good";
    else if (m.name === "precision") cls = m.value >= 0.3 ? "good" : "warn";
    return `<div class="kpi ${cls}"><div class="label">${esc(m.label)}</div>
            <div class="value">${fmtMetric(m)}</div>
            <div class="sub">${m.unit === "inr_paise" ? "exact: " + m.value.toLocaleString() + " paise" : esc(m.name)}</div>
            ${delta}</div>`;
  }).join("");
}

// ================= Charts (ECharts) =================
const chartInstances = [];
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// Categorical palette derived from the tenant's brand primary so charts inherit branding.
function palette() {
  const base = cssVar("--brand-primary") || "#0F6E7A";
  return [base, "#19A6B8", "#6B5B95", "#C77F17", "#2E8B6F", "#C4453B",
          "#8A93A1", "#4E7CA1", "#9C6B4E", "#5C8A3C"];
}
const SEV_COLOR = { critical: "#C4453B", high: "#C77F17", medium: "#0F6E7A", low: "#2E8B6F" };
const DISP_COLOR = { true_positive: "#2E8B6F", false_positive: "#C4453B", pending: "#C77F17" };

function chartTheme() {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  return {
    text: dark ? "#C7CED7" : "#2C333D",
    muted: dark ? "#8A93A1" : "#6B7481",
    split: dark ? "#262E39" : "#EDF0F3",
  };
}

// Which dimension each breakdown represents — this is what a chart click drills into.
const BREAKDOWN_DIM = {
  by_family: "family", by_severity: "severity", by_disposition: "disposition",
  by_rail: "rail", by_region: "region", by_product: "product",
  by_fmr_category: "fmr_category", cases_by_state: "state", by_rule: "rule",
  by_analyst: "analyst", by_typology: "typology", by_config_version: "config_version",
  by_segment: "segment", cases_by_assignee: "assignee",
  precision_by_family: "family", value_by_rail: "rail", by_status: "status",
};
const CHART_TITLES = {
  by_family: "Alerts by EWS family", by_severity: "Alerts by severity",
  by_disposition: "Alerts by disposition", by_rail: "By rail",
  by_fmr_category: "Fraud value by FMR category", by_region: "By region",
  by_product: "By product", cases_by_state: "Cases by lifecycle state",
  by_rule: "Alerts by rule", by_analyst: "Workload by analyst",
  by_typology: "By typology", by_config_version: "Alerts by config version",
  by_segment: "By customer segment", cases_by_assignee: "Cases by assignee",
  precision_by_family: "Precision by EWS family", value_by_rail: "Value by rail",
  by_status: "By transaction status",
};

function disposeCharts() {
  while (chartInstances.length) { try { chartInstances.pop().dispose(); } catch {} }
}

function renderCharts(breakdowns, unitHint) {
  disposeCharts();
  const th = chartTheme();
  const entries = Object.entries(breakdowns).filter(([, rows]) => rows && rows.length);
  $("chart-grid").innerHTML = entries.map(([key], i) => {
    const dim = BREAKDOWN_DIM[key];
    return `<div class="chart">
      <div class="chart-head"><h5>${esc(CHART_TITLES[key] || key)}</h5>
        ${dim ? '<span class="drill-cue">click to drill &rsaquo;</span>' : ""}</div>
      <div class="chart-canvas" id="chart-${i}"></div>
      <div class="chart-total"><span class="muted">Total</span><b id="ctot-${i}"></b></div>
    </div>`;
  }).join("");

  entries.forEach(([key, rows], i) => {
    const money = key === "by_fmr_category" || (unitHint === "inr" && key !== "cases_by_state");
    const dim = BREAKDOWN_DIM[key];
    const el = document.getElementById(`chart-${i}`);
    const chart = echarts.init(el, null, { renderer: "canvas" });
    chartInstances.push(chart);

    const fmt = (v) => (money ? inr(v) : Number(v).toLocaleString("en-IN"));
    document.getElementById(`ctot-${i}`).textContent =
      fmt(rows.reduce((a, b) => a + b.value, 0));

    // Doughnut reads better for a small set of mutually exclusive states.
    const isPie = ["by_severity", "by_disposition"].includes(key) && rows.length <= 6;
    const colorFor = (k) =>
      key === "by_severity" ? SEV_COLOR[k] :
      key === "by_disposition" ? DISP_COLOR[k] : null;

    const common = {
      color: palette(),
      tooltip: {
        trigger: isPie ? "item" : "axis",
        axisPointer: { type: "shadow" },
        valueFormatter: (v) => fmt(v),
      },
      grid: { left: 8, right: 18, top: 16, bottom: 8, containLabel: true },
    };

    const opt = isPie ? {
      ...common,
      legend: { bottom: 0, textStyle: { color: th.muted, fontSize: 11 }, itemHeight: 8 },
      series: [{
        type: "pie", radius: ["46%", "72%"], center: ["50%", "44%"],
        avoidLabelOverlap: true, itemStyle: { borderRadius: 4, borderColor: "transparent", borderWidth: 2 },
        label: { show: false }, labelLine: { show: false },
        data: rows.map((r) => ({
          name: r.key, value: r.value,
          itemStyle: colorFor(r.key) ? { color: colorFor(r.key) } : undefined,
        })),
      }],
    } : {
      ...common,
      xAxis: {
        type: "value", axisLabel: { color: th.muted, fontSize: 10,
          formatter: (v) => (money ? inr(v) : v.toLocaleString("en-IN")) },
        splitLine: { lineStyle: { color: th.split } },
      },
      yAxis: {
        type: "category", inverse: true,
        data: rows.map((r) => r.key),
        axisLabel: { color: th.text, fontSize: 11 },
        axisLine: { show: false }, axisTick: { show: false },
      },
      series: [{
        type: "bar", barMaxWidth: 22,
        itemStyle: {
          borderRadius: [0, 4, 4, 0],
          color: (p) => colorFor(p.name) || palette()[p.dataIndex % palette().length],
        },
        data: rows.map((r) => r.value),
      }],
    };
    chart.setOption(opt);

    if (dim) {
      el.style.cursor = "pointer";
      chart.on("click", (p) => {
        const value = isPie ? p.name : rows[p.dataIndex].key;
        drillInto(dim, value);
      });
    }
  });
}

addEventListener("resize", () => chartInstances.forEach((c) => { try { c.resize(); } catch {} }));

function renderRecon(recon) {
  const panel = $("recon-panel");
  if (!recon) { panel.hidden = true; return; }
  panel.hidden = false;
  const s = $("recon-status");
  const ok = recon.status === "pass";
  s.textContent = ok ? `PASS ${recon.summary.passed}/${recon.summary.total}`
                     : `FAIL ${recon.summary.failed}`;
  s.style.background = ok ? "color-mix(in srgb, var(--ok) 15%, transparent)"
                          : "color-mix(in srgb, var(--err) 15%, transparent)";
  s.style.color = ok ? "var(--ok)" : "var(--err)";
  $("recon-rows").innerHTML = recon.checks.map((c) => `
    <tr><td class="mono-cell">${c.id}</td><td>${esc(c.title)}</td>
        <td>${Number(c.expected).toLocaleString()}</td>
        <td>${Number(c.actual).toLocaleString()}</td>
        <td>${c.delta === 0 ? "0" : Number(c.delta).toLocaleString()}</td>
        <td class="${c.passed ? "recon-ok" : "recon-bad"}">${c.passed ? "PASS" : "FAIL"}</td></tr>`).join("");
}

// ================= 6-stage drill =================
// Stage 1 is the unfiltered portfolio; stages 2-4 are progressively narrower slices
// added by clicking a chart; stage 5 lists the records; stage 6 is a single record's
// evidence trail. Each step only ever ADDS a filter, so a figure at any stage is the
// same metric definition seen through a narrower lens.
const STAGES = [
  { key: "portfolio",   label: "Portfolio",    hint: "Whole book. Click a chart segment to narrow." },
  { key: "segment",     label: "Segment",      hint: "Sliced by rail / region / product." },
  { key: "signal",      label: "Signal",       hint: "Which EWS family or rule fired." },
  { key: "case",        label: "Case",         hint: "Cases and their lifecycle state." },
  { key: "records",     label: "Records",      hint: "The underlying alerts, cases and transactions." },
  { key: "evidence",    label: "Evidence",     hint: "One record with the trail that justifies it." },
];
// Dimensions that belong to each stage, so a click advances to the right level.
const DIM_STAGE = {
  rail: 1, region: 1, product: 1, segment: 1,
  family: 2, rule: 2, severity: 2, disposition: 2,
  state: 3, fmr_category: 3,
};
let drillPath = [];    // [{dim, value, stage}]
let stageIndex = 0;

function drillFilterParams() {
  const p = buildFilterParams();
  const MAP = { rail: "rails", family: "families", severity: "severities", region: "regions",
                product: "products", segment: "segments", state: "states",
                fmr_category: "fmr_categories", disposition: "dispositions" };
  drillPath.forEach((d) => { if (MAP[d.dim]) p.append(MAP[d.dim], d.value); });
  return p;
}

function renderStageBar() {
  const bar = $("stage-bar");
  bar.innerHTML = STAGES.map((s, i) => {
    const cls = i === stageIndex ? "current" : i < stageIndex ? "done" : "";
    // Several dimensions can belong to one stage (family + severity are both "Signal"),
    // so show every value pinned at that stage rather than just the first.
    const crumbs = drillPath.filter((d) => d.stage === i).map((d) => d.value);
    const label = crumbs.length ? `${s.label}: ${crumbs.join(" · ")}` : s.label;
    return `<li class="${cls}">
      <button data-stage="${i}" ${i > stageIndex ? "disabled" : ""}>
        <span class="stage-num">${i + 1}</span> ${esc(label)}</button>
      </li>${i < STAGES.length - 1 ? '<li class="sep">›</li>' : ""}`;
  }).join("");
  bar.querySelectorAll("[data-stage]").forEach((b) => {
    b.onclick = () => goToStage(Number(b.dataset.stage));
  });
  const applied = drillPath.map((d) =>
    `<span class="stage-filter">${esc(d.dim)}=${esc(d.value)}</span>`).join(" ");
  $("stage-hint").innerHTML = STAGES[stageIndex].hint + (applied ? "  " + applied : "");
}

function drillInto(dim, value) {
  // Ignore a repeat click on a value already in the path.
  if (drillPath.some((d) => d.dim === dim && d.value === value)) return;
  const target = Math.min((DIM_STAGE[dim] ?? stageIndex) , STAGES.length - 2);
  drillPath.push({ dim, value, stage: target });
  stageIndex = Math.min(Math.max(target + 1, stageIndex + 1), STAGES.length - 2);
  $("evidence-panel").hidden = true;
  loadDashboard();
}

function goToStage(i) {
  if (i >= stageIndex) return;
  drillPath = drillPath.filter((d) => d.stage < i);
  stageIndex = i;
  $("evidence-panel").hidden = true;
  loadDashboard();
}

function resetDrill() {
  drillPath = []; stageIndex = 0; pageOffset = 0; $("evidence-panel").hidden = true;
}

async function loadDashboard() {
  refreshSourceBanner();
  if (!selectedTenant) return;
  const p = drillFilterParams();
  $("active-filters").textContent = describeFilters();
  $("an-tenant").textContent = `· ${esc(selectedTenant.display_name)}`;
  renderStageBar();
  skeletonKpis();
  setLoading("trend-panel", true, "Loading trend\u2026");
  setLoading("chart-grid", true, "Loading charts\u2026");
  setLoading("drill-panel", true, "Loading records\u2026");
  try {
    const d = await api(`/analytics/${selectedTenant.id}/${currentDash}?${p}`);
    // Comparison period: refetch the same dashboard over the immediately preceding
    // window of equal length. Requires an explicit range - "previous" is undefined
    // without one.
    let prev = null;
    if ($("f-compare")?.checked && $("f-from").value && $("f-to").value) {
      const from = new Date($("f-from").value), to = new Date($("f-to").value);
      const span = to - from;
      const q = new URLSearchParams(p);
      q.set("date_from", new Date(from - span).toISOString());
      q.set("date_to", from.toISOString());
      try { prev = (await api(`/analytics/${selectedTenant.id}/${currentDash}?${q}`)).metrics; }
      catch { prev = null; }
    }
    syncUrl();
    // Name the persona whose view this is — with 8 dashboards the tab label alone
    // doesn't say who it's for.
    const meta = (me?.dashboards || []).find((x) => x.key === currentDash);
    $("dash-persona").textContent = d.persona || meta?.persona || "";
    $("dash-persona").title = meta?.question || "";
    renderKpis(d.metrics, prev);
    renderCharts(d.breakdowns || {}, currentDash === "analyst" ? "count" : "inr");
    renderRecon(d.reconciliation);
    renderDormant(d.dormant);
    await loadTrend();
    await loadDrill();
    await loadGraph();
    await loadDrift();
    await loadBoardPacks();
    await loadLanePanel();
  } catch (err) {
    disposeCharts();
    $("kpi-row").innerHTML = `<div class="kpi bad"><div class="label">Error</div>
      <div class="value" style="font-size:14px">${esc(err.message)}</div></div>`;
  } finally {
    // Always clear the veils - a failed load must not leave the page spinning forever.
    setLoading("trend-panel", false);
    setLoading("chart-grid", false);
    setLoading("drill-panel", false);
  }
}

// ---- stage 6: evidence ----
const evKv = (obj, skip = []) => `<dl class="ev-kv">` + Object.entries(obj)
  .filter(([k]) => !skip.includes(k))
  .map(([k, v]) => {
    let out = v;
    if (v === null || v === undefined) out = "—";
    else if (k.endsWith("_paise")) out = inr(v);
    else if (k.endsWith("_ts") || k === "ts") out = new Date(v).toLocaleString();
    return `<dt>${esc(k).replace(/_/g, " ")}</dt><dd>${esc(out)}</dd>`;
  }).join("") + `</dl>`;

async function showEvidence(entity, id) {
  if (!selectedTenant) return;
  stageIndex = STAGES.length - 1;
  renderStageBar();
  const panel = $("evidence-panel");
  panel.hidden = false;
  $("ev-title").textContent = `· ${entity} ${id}`;
  $("ev-body").innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const q = new URLSearchParams();
    if (piiReveal.on) { q.set("reveal", "true"); q.set("justification", piiReveal.justification); }
    const d = await api(`/analytics/${selectedTenant.id}/evidence/${entity}/${id}?${q}`);
    if (d.error) { $("ev-body").innerHTML = `<p class="error">${esc(d.error)}</p>`; return; }
    let html = "";
    // The trace answers "why", which a score alone cannot. Show it first.
    const a = d.alert;
    if (a && a.matched_reason) {
      const obs = a.observed_value, thr = a.threshold_value;
      html += `<div class="trace-card">
        <div class="trace-head">Why this fired</div>
        <div class="trace-rule">${esc(a.rule_id)}<span class="trace-band">${esc(a.sub_rule_ref)}</span></div>
        <div class="trace-reason">${esc(a.matched_reason)}</div>
        ${obs !== null && obs !== undefined ? `<div class="trace-obs">
          <span>observed <b>${esc(obs)}</b></span><span class="muted">vs threshold ${thr}</span>
          <span class="muted">${a.observed_unit || ""}</span></div>` : ""}
        <div class="trace-cfg muted">evaluated under config version
          <b>${esc(a.config_version)}</b> — the rules in force at scoring time</div>
      </div>`;
    }
    html += `<div class="ev-grid">`;
    if (d.alert) html += `<div class="ev-card"><h6>Alert (scored by rule)</h6>${evKv(d.alert, ["tenant_id"])}</div>`;
    if (d.transaction) html += `<div class="ev-card"><h6>Transaction</h6>${evKv(d.transaction, ["tenant_id"])}</div>`;
    if (d.case) html += `<div class="ev-card"><h6>Case</h6>${evKv(d.case, ["tenant_id"])}</div>`;
    html += `</div>`;

    const list = d.sibling_alerts || d.alerts;
    if (list && list.length) {
      html += `<div class="ev-card" style="margin-top:14px"><h6>Linked alerts (${list.length})</h6>
        <table class="cfg-table"><thead><tr>${Object.keys(list[0]).map(k =>
          `<th>${k.replace(/_/g, " ")}</th>`).join("")}</tr></thead><tbody>${
          list.map(r => `<tr>${Object.values(r).map(v =>
            `<td class="mono-cell">${v ?? "—"}</td>`).join("")}</tr>`).join("")
        }</tbody></table></div>`;
    }
    if (d.value_check) {
      const ok = d.value_check.reconciled;
      html += `<div class="ev-check ${ok ? "ok" : "bad"}">
        ${ok ? "RECONCILED" : "MISMATCH"} — stated ${inr(d.value_check.stated_paise)}
        vs ${inr(d.value_check.derived_paise)} derived from linked transactions (invariant R-03).</div>`;
    }
    $("ev-body").innerHTML = html;
    // The lifecycle belongs above the evidence: the first question about a case is
    // "where is it and what happens next", not "what were the raw column values".
    const caseId = entity === "case" ? id : (d.case && d.case.case_id);
    if (caseId) {
      renderWorkflow(caseId); renderFilings(caseId);
      renderAccountability(caseId); renderRecovery(caseId);
    } else {
      $("wf-host").innerHTML = ""; $("filing-host").innerHTML = "";
      $("acc-host").innerHTML = ""; $("rec-host").innerHTML = "";
    }
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { $("ev-body").innerHTML = `<p class="error">${esc(err.message)}</p>`; }
}
$("ev-close").onclick = () => {
  $("evidence-panel").hidden = true;
  $("wf-host").innerHTML = ""; $("filing-host").innerHTML = "";
  $("acc-host").innerHTML = ""; $("rec-host").innerHTML = ""; wfCaseId = null;
  stageIndex = STAGES.length - 2; renderStageBar();
};

const ID_FIELD = { alert: "alert_id", case: "case_id", transaction: "txn_id" };

// Rule / typology / assignee values are data, not a fixed vocabulary - populate them
// from the tenant's own breakdowns so the lists never drift from reality.
async function populateDynamicFilters() {
  if (!selectedTenant) return;
  const fill = (id, rows) => {
    const el = $(id); if (!el) return;
    const chosen = new Set(multi(id));
    el.innerHTML = rows.map((r) => `<option${chosen.has(r.key) ? " selected" : ""}>${esc(r.key)}</option>`).join("");
  };
  const get = async (metric, dim) => {
    try {
      return (await api(`/analytics/${selectedTenant.id}/breakdown?metric=${metric}&dimension=${dim}`)).rows;
    } catch { return []; }
  };
  fill("f-rule", await get("alert_count", "rule"));
  fill("f-typology", await get("alert_count", "typology"));
  fill("f-assignee", (await get("case_count", "assignee")).filter((r) => r.key));
}

// Which metric best represents "the trend" for each dashboard.
const TREND_METRIC = {
  analyst: "alert_count", ews: "alert_count", investigator: "case_count",
  risk_manager: "alert_count", aml: "case_count", rfa: "case_count",
  board: "fraud_value_total", supervisor: "case_count", model: "alert_count",
  inspection: "fraud_value_total", realtime: "transaction_count",
  account360: "transaction_count", tenant_health: "transaction_count",
};
let trendChart = null;

async function loadTrend() {
  if (!selectedTenant) return;
  const metric = TREND_METRIC[currentDash] || "alert_count";
  const bucket = $("trend-bucket").value;
  const p = drillFilterParams(); p.set("metric", metric); p.set("bucket", bucket);
  try {
    const d = await api(`/analytics/${selectedTenant.id}/timeseries?${p}`);
    const money = metric.includes("value");
    $("trend-label").textContent =
      `· ${(REGISTRY_LABELS[metric] || metric)} by ${bucket}`;
    const el = $("trend-canvas");
    if (trendChart) { try { trendChart.dispose(); } catch {} }
    trendChart = echarts.init(el);
    const th = chartTheme();
    trendChart.setOption({
      color: [cssVar("--brand-primary") || "#0F6E7A"],
      tooltip: { trigger: "axis", valueFormatter: (v) => (money ? inr(v) : Number(v).toLocaleString("en-IN")) },
      grid: { left: 8, right: 18, top: 18, bottom: 8, containLabel: true },
      xAxis: { type: "category", data: d.rows.map((r) => r.key?.slice(0, 10)),
               axisLabel: { color: th.muted, fontSize: 10 },
               axisLine: { lineStyle: { color: th.split } } },
      yAxis: { type: "value", axisLabel: { color: th.muted, fontSize: 10,
                 formatter: (v) => (money ? inr(v) : v.toLocaleString("en-IN")) },
               splitLine: { lineStyle: { color: th.split } } },
      series: [{ type: "line", smooth: true, showSymbol: d.rows.length < 40,
                 areaStyle: { opacity: 0.12 }, lineStyle: { width: 2 },
                 data: d.rows.map((r) => r.value) }],
    });
  } catch { $("trend-label").textContent = "· unavailable"; }
}
const REGISTRY_LABELS = {
  alert_count: "Alerts", case_count: "Cases", fraud_value_total: "Fraud value",
  transaction_count: "Transactions",
};

// ---- model drift ----
async function loadDrift() {
  const panel = $("drift-panel");
  if (!selectedTenant || currentDash !== "model") { panel.hidden = true; return; }
  panel.hidden = false;
  setLoading("drift-panel", true, "Comparing windows…");
  try {
    const p = drillFilterParams();
    const d = await api(`/analytics/${selectedTenant.id}/drift?${p}`);
    const chip = $("drift-status");

    if (!d.available) {
      chip.textContent = "not enough data";
      chip.style.background = "var(--bg)"; chip.style.color = "var(--muted)";
      $("drift-body").innerHTML =
        `<p class="muted">${esc(d.reason || "Not enough alerts in one of the windows to compare.")}</p>`;
      $("drift-windows").textContent = "";
      return;
    }

    const band = d.band;
    chip.textContent = `PSI ${d.psi} — ${esc(band)}`;
    const tone = band === "stable" ? "var(--ok)" : band === "moderate" ? "#b4791a" : "var(--err)";
    chip.style.background = `color-mix(in srgb, ${tone} 15%, transparent)`;
    chip.style.color = tone;

    const w = d.windows;
    $("drift-windows").textContent =
      `· ${w.reference.alerts} alerts before vs ${w.current.alerts} now`;

    const moved = (d.mix_shift || []).filter((m) => Math.abs(m.delta_pct) >= 0.5);
    $("drift-body").innerHTML = `
      <div class="drift-heads">
        <div class="drift-stat"><div class="label">PSI</div>
          <div class="value band-${esc(band)}">${d.psi}</div>
          <div class="hint">stable &lt; ${d.thresholds.stable_below} ·
            significant &gt; ${d.thresholds.significant_above}</div></div>
        <div class="drift-stat"><div class="label">KS</div>
          <div class="value">${d.ks?.ks ?? "—"}</div>
          <div class="hint">largest gap${d.ks?.at_value != null
            ? ` at score ${Math.round(d.ks.at_value)}` : ""}</div></div>
      </div>
      <h5 class="chart-sub">Score distribution</h5>
      <table class="cfg-table"><thead><tr><th>Score band</th><th>Reference</th>
        <th>Current</th><th>Contribution</th></tr></thead><tbody>` +
      d.bins.map((b) => `<tr><td class="mono-cell">${esc(b.bin)}</td>
        <td>${b.reference_pct}%</td><td>${b.current_pct}%</td>
        <td class="mono-cell">${b.contribution}</td></tr>`).join("") +
      `</tbody></table>` +
      (moved.length ? `<h5 class="chart-sub">Where the mix moved</h5>
        <table class="cfg-table"><thead><tr><th>Rule</th><th>Reference</th>
          <th>Current</th><th>Change</th></tr></thead><tbody>` +
        moved.map((m) => `<tr><td class="mono-cell">${esc(m.rule_id)}</td>
          <td>${m.reference_pct}%</td><td>${m.current_pct}%</td>
          <td class="${m.delta_pct >= 0 ? "delta-up" : "delta-down"}">
            ${m.delta_pct >= 0 ? "+" : ""}${m.delta_pct}%</td></tr>`).join("") +
        `</tbody></table>` : "");
  } catch (err) {
    $("drift-body").innerHTML = `<p class="error">${esc(err.message)}</p>`;
  } finally {
    setLoading("drift-panel", false);
  }
}

// ---- dormant indicator register ----
function renderDormant(d) {
  const panel = $("dormant-panel");
  if (!d) { panel.hidden = true; return; }
  panel.hidden = false;
  const chip = $("dormant-status");

  if (!d.available) {
    chip.textContent = "catalogue unavailable";
    chip.style.background = "var(--bg)"; chip.style.color = "var(--muted)";
    $("dormant-body").innerHTML =
      `<p class="muted">The rule catalogue could not be read, so coverage is unknown.
       Reporting every rule as dormant here would be a false alarm.</p>`;
    return;
  }

  const pct = Math.round((d.coverage ?? 0) * 100);
  const good = pct >= 80;
  chip.textContent = `${pct}% firing`;
  chip.style.background = good ? "color-mix(in srgb, var(--ok) 15%, transparent)"
                               : "color-mix(in srgb, var(--err) 15%, transparent)";
  chip.style.color = good ? "var(--ok)" : "var(--err)";

  let html = `<div class="coverage-bar"><span style="width:${pct}%"></span></div>
    <p class="muted coverage-line">${d.fired_quantitative} of ${d.configured_quantitative}
     quantitative indicators fired in this window · ${d.configured} configured in total</p>`;

  if (d.dormant.length) {
    html += `<table class="cfg-table"><thead><tr><th>Rule</th><th>Family</th>
      <th>Would fire when</th><th>Threshold</th></tr></thead><tbody>` +
      d.dormant.map((r) => `<tr><td class="mono-cell">${r.rule_id}</td>
        <td><span class="badge">${r.family}</span></td>
        <td>${esc(r.reason)}</td>
        <td class="mono-cell">${r.threshold ?? "—"} ${r.unit || ""}</td></tr>`).join("") +
      `</tbody></table>`;
  } else {
    html += `<p class="ok">Every configured quantitative indicator produced at least one
      signal in this window.</p>`;
  }
  if (d.dormant_qualitative.length) {
    html += `<p class="muted coverage-line">Qualitative indicators silent:
      ${esc(d.dormant_qualitative.join(", "))} — expected, these are fed from CBS events and
      relationship-manager input rather than the payment stream.</p>`;
  }
  if (d.uncatalogued.length) {
    html += `<p class="error">Firing but not in the catalogue:
      ${esc(d.uncatalogued.join(", "))} — detection and configuration have drifted apart.</p>`;
  }
  $("dormant-body").innerHTML = html;
}

// ---- network / link analysis ----
// Only the dashboards whose job is structure get the graph.
const GRAPH_DASHBOARDS = new Set(["investigator", "account360", "aml"]);
let graphChart = null;
let selectedCaseId = null;
// Drill paging. Reset to the first page whenever the result set changes,
// otherwise a filter can leave you stranded past the end of it.
let pageOffset = 0;

async function loadGraph() {
  const panel = $("graph-panel");
  if (!selectedTenant || !GRAPH_DASHBOARDS.has(currentDash)) {
    panel.hidden = true;
    if (graphChart) { try { graphChart.dispose(); } catch {} graphChart = null; }
    return;
  }
  panel.hidden = false;
  setLoading("graph-panel", true, "Building network\u2026");
  const p = new URLSearchParams();
  const scope = $("graph-scope").value;
  if (scope === "case" && selectedCaseId) p.set("case_id", selectedCaseId);
  else if (currentDash === "account360" && $("f-account")?.value.trim())
    p.set("account", $("f-account").value.trim());
  if (piiReveal.on) { p.set("reveal", "true"); p.set("justification", piiReveal.justification); }

  try {
    const g = await api(`/analytics/${selectedTenant.id}/graph?${p}`);
    const th = chartTheme();
    $("graph-label").textContent =
      `· ${g.nodes.length} accounts, ${g.edges.length} links` +
      (g.scope.case_id ? ` · case ${g.scope.case_id.slice(0, 10)}` : "") +
      (g.pii_masked ? " · masked" : " · revealed");

    if (!g.nodes.length) {
      $("graph-canvas").innerHTML = '<p class="muted">No linked accounts for this scope.</p>';
      return;
    }
    $("graph-canvas").innerHTML = "";
    if (graphChart) { try { graphChart.dispose(); } catch {} }
    graphChart = echarts.init($("graph-canvas"));

    const maxDeg = Math.max(...g.nodes.map((n) => n.degree), 1);
    const ROLE_COLOR = { hub: "#C4453B", collector: "#C77F17", disburser: cssVar("--brand-primary") || "#0F6E7A" };
    const maxVal = Math.max(...g.edges.map((e) => e.value_paise), 1);

    graphChart.setOption({
      tooltip: {
        formatter: (p) => p.dataType === "edge"
          ? `${p.data.source} → ${esc(p.data.target)}<br/>${p.data.count} txns · ${inr(p.data.value_paise)}`
          : `${esc(p.data.name)}<br/>role: ${p.data.role}<br/>${p.data.degree} counterparties<br/>` +
            `in ${inr(p.data.in_paise)} · out ${inr(p.data.out_paise)}`,
      },
      legend: [{ data: ["hub", "collector", "disburser"], bottom: 0,
                 textStyle: { color: th.muted, fontSize: 11 } }],
      series: [{
        type: "graph", layout: "force", roam: true, draggable: true,
        force: { repulsion: 180, edgeLength: [40, 120], gravity: 0.06 },
        categories: [{ name: "hub" }, { name: "collector" }, { name: "disburser" }],
        label: { show: false },
        emphasis: { focus: "adjacency", label: { show: true, color: th.text, fontSize: 10 } },
        edgeSymbol: ["none", "arrow"], edgeSymbolSize: 6,
        data: g.nodes.map((n) => ({
          name: n.id, id: n.id, role: n.role, degree: n.degree,
          in_paise: n.in_paise, out_paise: n.out_paise,
          category: ["hub", "collector", "disburser"].indexOf(n.role),
          symbolSize: 8 + (n.degree / maxDeg) * 34,
          itemStyle: { color: ROLE_COLOR[n.role] },
        })),
        links: g.edges.map((e) => ({
          source: e.source, target: e.target, count: e.count, value_paise: e.value_paise,
          lineStyle: { width: 0.6 + (e.value_paise / maxVal) * 4, opacity: 0.55,
                       curveness: 0.12, color: th.muted },
        })),
      }],
    });

    // Clicking an account pins Account 360 to it - the graph is a navigation surface,
    // not just a picture.
    graphChart.on("click", (p) => {
      if (p.dataType !== "node") return;
      // A masked id is not a real account number, so it cannot be filtered on.
      if (g.pii_masked) {
        $("graph-label").textContent += " · reveal PII to pivot from a node";
        return;
      }
      const acctInput = $("f-account");
      if (!acctInput) return;
      acctInput.value = p.data.name;
      currentDash = "account360";
      document.querySelectorAll("[data-dash]").forEach((x) =>
        x.classList.toggle("active", x.dataset.dash === "account360"));
      resetDrill(); loadDashboard();
    });
  } catch (err) {
    $("graph-canvas").innerHTML = `<p class="error">${esc(err.message)}</p>`;
  } finally {
    setLoading("graph-panel", false);
  }
}

function renderPager(d) {
  const total = d.total ?? d.count ?? 0;
  const from = total === 0 ? 0 : d.offset + 1;
  const to = d.offset + d.count;
  $("page-info").textContent = total === 0
    ? "No matching rows"
    : `Showing ${from.toLocaleString("en-IN")}\u2013${to.toLocaleString("en-IN")} of ` +
      `${total.toLocaleString("en-IN")} ${currentDrill}s`;
  $("page-first").disabled = d.offset === 0;
  $("page-prev").disabled = d.offset === 0;
  $("page-next").disabled = !d.has_more;
}

async function loadDrill() {
  if (!selectedTenant) return;
  const pageSize = Number($("page-size").value) || 50;
  const p = drillFilterParams();
  p.append("limit", String(pageSize));
  p.append("offset", String(pageOffset));
  if (piiReveal.on) { p.append("reveal", "true"); p.append("justification", piiReveal.justification); }
  setLoading("drill-panel", true, "Loading records\u2026");
  try {
    const d = await api(`/analytics/${selectedTenant.id}/drill/${currentDrill}?${p}`);
    const chip = $("pii-state");
    chip.textContent = d.pii_masked ? "PII masked" : "PII revealed — audited";
    chip.classList.toggle("revealed", !d.pii_masked);
    renderPager(d);
    $("drill-label").textContent = `${currentDrill}s`;
    if (!d.rows.length) {
      $("drill-head").innerHTML = ""; $("drill-rows").innerHTML =
        `<tr><td class="muted">No rows for this filter</td></tr>`; return;
    }
    const cols = Object.keys(d.rows[0]);
    const idf = ID_FIELD[currentDrill];
    $("drill-head").innerHTML = `<tr>${cols.map((c) => `<th>${c.replace(/_/g, " ")}</th>`).join("")}</tr>`;
    $("drill-rows").innerHTML = d.rows.map((r) => {
      const cells = cols.map((c) => {
        let v = r[c];
        if (v === null || v === undefined) v = "—";
        else if (c.endsWith("_paise")) v = inr(v);
        else if (c.endsWith("_ts") || c === "ts") v = new Date(v).toLocaleString();
        const mono = /_id$|account|version|^ts$|_ts$/.test(c) ? "mono-cell" : "";
        const masked = typeof v === "string" && v.includes("•") ? " masked-cell" : "";
        return `<td class="${mono}${masked}">${esc(v)}</td>`;
      }).join("");
      return `<tr data-ev-id="${esc(r[idf] ?? "")}" title="Open evidence">${cells}</tr>`;
    }).join("");
    // Row click = stage 6.
    $("drill-rows").querySelectorAll("[data-ev-id]").forEach((tr) => {
      tr.onclick = () => {
        if (!tr.dataset.evId) return;
        if (currentDrill === "case") selectedCaseId = tr.dataset.evId;
        showEvidence(currentDrill, tr.dataset.evId);
      };
    });
  } catch {
    $("drill-rows").innerHTML = `<tr><td class="muted">—</td></tr>`;
  } finally {
    setLoading("drill-panel", false);
  }
}

document.querySelectorAll("#dash-tabs [data-dash]").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll("[data-dash]").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); currentDash = b.dataset.dash; resetDrill(); loadDashboard();
  };
});
document.querySelectorAll(".dash-tabs [data-drill]").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll("[data-drill]").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); currentDrill = b.dataset.drill;
    pageOffset = 0; loadDrill();
  };
});
$("export-rows").onclick = async () => {
  if (!selectedTenant) return;
  const btn = $("export-rows");
  const p = drillFilterParams();
  p.append("limit", "5000");
  // The export inherits the current masking state; unmasking it is the same gated,
  // audited decision as unmasking the table.
  if (piiReveal.on) { p.append("reveal", "true"); p.append("justification", piiReveal.justification); }
  busy(+1);
  btn.setAttribute("aria-busy", "true");
  const originalLabel = btn.textContent;
  btn.textContent = "Exporting\u2026";
  try {
    const res = await fetch(
      `${API}/analytics/${selectedTenant.id}/export/${currentDrill}?${p}`,
      { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error((await res.json())?.error?.message || `HTTP ${res.status}`);
    const blob = await res.blob();
    const name = (res.headers.get("content-disposition") || "")
      .match(/filename="([^"]+)"/)?.[1] || `frms_${currentDrill}.csv`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name; document.body.appendChild(a); a.click();
    a.remove(); URL.revokeObjectURL(url);
    btn.textContent = "Exported ✓";
  } catch (err) {
    btn.textContent = originalLabel;
    alert(err.message);
    return;
  } finally {
    busy(-1);
    btn.removeAttribute("aria-busy");
  }
  setTimeout(() => (btn.textContent = originalLabel), 1800);
};

$("page-size").onchange = () => { pageOffset = 0; loadDrill(); };
$("page-first").onclick = () => { pageOffset = 0; loadDrill(); };
$("page-prev").onclick = () => {
  pageOffset = Math.max(0, pageOffset - (Number($("page-size").value) || 50));
  loadDrill();
};
$("page-next").onclick = () => {
  pageOffset += Number($("page-size").value) || 50;
  loadDrill();
};

$("trend-bucket").onchange = loadTrend;
$("graph-scope").onchange = loadGraph;
$("graph-refresh").onclick = loadGraph;

$("reveal-pii").onclick = async () => {
  if (piiReveal.on) {
    piiReveal = { on: false, justification: "" };
    $("reveal-pii").textContent = "Reveal PII…";
    return loadDrill();
  }
  const why = prompt(
    "Unmasking customer data is recorded against your name.\n\nReason (min 8 characters):");
  if (!why || why.trim().length < 8) return;
  piiReveal = { on: true, justification: why.trim() };
  $("reveal-pii").textContent = "Re-mask";
  try { await loadDrill(); }
  catch (e) {
    piiReveal = { on: false, justification: "" };
    $("reveal-pii").textContent = "Reveal PII…";
    alert(e.message);
  }
};

$("apply-filters").onclick = () => { resetDrill(); loadDashboard(); };
$("clear-filters").onclick = () => {
  for (const [id, , kind] of FILTER_MAP) {
    const el = $(id); if (!el) continue;
    if (kind === "multi") Array.from(el.options).forEach((o) => (o.selected = false));
    else if (kind === "bool") el.checked = false;
    else el.value = "";
  }
  $("f-preset").value = ""; $("f-compare").checked = false;
  $("saved-views").value = ""; reflectViewSelection(null);
  resetDrill(); loadDashboard();
};
$("more-filters").onclick = () => {
  const panel = $("filter-more");
  panel.hidden = !panel.hidden;
  $("more-filters").textContent = panel.hidden ? "More filters" : "Fewer filters";
  if (!panel.hidden) populateDynamicFilters();
};
$("f-preset").onchange = () => { applyPreset($("f-preset").value); resetDrill(); loadDashboard(); };
$("f-compare").onchange = () => loadDashboard();

$("copy-link").onclick = async () => {
  const url = location.origin + location.pathname + "#" + filtersToQuery();
  try { await navigator.clipboard.writeText(url); $("copy-link").textContent = "Copied ✓"; }
  catch { prompt("Copy this link:", url); }
  setTimeout(() => ($("copy-link").textContent = "Copy link"), 1600);
};

$("save-view").onclick = async () => {
  if (!selectedTenant) return;
  // The sharing decision is made at save time, in a dialog that can express an
  // audience. A pre-set checkbox could only ever say all-or-nothing, and was missed.
  openShareDialog({ name: "", shared: false, shared_roles: [] });
};

$("delete-view").onclick = async () => {
  const id = $("saved-views").value;
  const view = savedViews.find((v) => v.id === id);
  if (!view || !confirm(`Delete the view "${esc(view.name)}"?`)) return;
  try {
    await api(`/analytics/${selectedTenant.id}/views/${id}`, { method: "DELETE" });
    await renderSavedViews();
  } catch (err) {
    alert(err.message);
  }
};

function reflectViewSelection(view) {
  const scope = $("view-scope");
  $("delete-view").hidden = !view || !view.mine;
  $("share-toggle").hidden = !view || !view.mine;
  if (!view) { scope.textContent = ""; scope.className = "view-scope"; return; }
  $("share-toggle").textContent = view.shared ? "Change sharing" : "Share";
  // Ownership is tested first: on a view someone else shared, who shared it is the
  // useful fact. Testing `shared` first made that branch unreachable.
  // `audience` is the server's own description, so the chip cannot claim a wider or
  // narrower reach than the query that actually enforces it.
  scope.textContent = !view.mine
    ? `Shared by ${esc(view.owner)}`
    : view.shared ? (view.audience || "Shared") : "Private to you";
  scope.className = "view-scope " + (view.shared ? "shared" : "private");
}

$("share-toggle").onclick = () => {
  const view = savedViews.find((v) => v.id === $("saved-views").value);
  if (view && view.mine) openShareDialog(view);
};

$("saved-views").onchange = (e) => {
  const view = savedViews.find((v) => v.id === e.target.value);
  reflectViewSelection(view);
  if (!view) return;
  const q = new URLSearchParams(view.query || "");
  applyFilterState(q);
  const dash = view.dashboard || q.get("dash");
  if (dash && (me?.dashboards || []).some((d) => d.key === dash)) {
    currentDash = dash;
    document.querySelectorAll("[data-dash]").forEach((x) =>
      x.classList.toggle("active", x.dataset.dash === dash));
  }
  resetDrill(); loadDashboard();
};

// Boot
if (token) enterApp();


// ---------------------------------------------------------------- share dialog
let audienceRoles = [];      // [{name, label}] from RBAC, not hardcoded here
let shareEditing = null;     // the view being re-shared, or null when saving a new one

async function loadAudienceRoles() {
  if (audienceRoles.length || !selectedTenant) return;
  try {
    audienceRoles = await api(`/analytics/${selectedTenant.id}/view-audiences`);
  } catch {
    audienceRoles = [];
  }
  $("share-roles").innerHTML = audienceRoles.map((r) =>
    `<label><input type="checkbox" class="share-role" value="${esc(r.name)}" /> ${esc(r.label)}</label>`
  ).join("");
}

function shareMode() {
  return document.querySelector('input[name="share-mode"]:checked')?.value || "private";
}

function syncShareRoles() {
  $("share-roles").hidden = shareMode() !== "roles";
}

async function openShareDialog(view) {
  if (!selectedTenant) return;
  await loadAudienceRoles();
  shareEditing = view.id ? view : null;
  $("share-dialog-title").textContent = view.id ? "Sharing" : "Save view";
  $("share-save").textContent = view.id ? "Update sharing" : "Save view";
  $("share-name").value = view.name || "";
  $("share-name").disabled = !!view.id;   // renaming here would fork a second view
  $("share-tenant-name").textContent = selectedTenant.display_name;
  $("share-error").hidden = true;

  const roles = view.shared_roles || [];
  const mode = !view.shared ? "private" : (roles.length ? "roles" : "tenant");
  document.querySelectorAll('input[name="share-mode"]').forEach((r) => {
    r.checked = r.value === mode;
  });
  document.querySelectorAll(".share-role").forEach((c) => {
    c.checked = roles.includes(c.value);
  });
  syncShareRoles();
  $("share-dialog").showModal();
  if (!view.id) $("share-name").focus();
}

document.querySelectorAll('input[name="share-mode"]').forEach((r) => {
  r.onchange = syncShareRoles;
});
$("share-cancel").onclick = () => $("share-dialog").close();

$("share-save").onclick = async () => {
  const name = ($("share-name").value || "").trim();
  const err = $("share-error");
  if (!name) {
    err.textContent = "Give the view a name.";
    err.hidden = false;
    return;
  }
  const mode = shareMode();
  const roles = mode === "roles"
    ? [...document.querySelectorAll(".share-role:checked")].map((c) => c.value)
    : [];
  if (mode === "roles" && !roles.length) {
    err.textContent = "Pick at least one role, or choose a different audience.";
    err.hidden = false;
    return;
  }
  err.hidden = true;
  try {
    const saved = await api(`/analytics/${selectedTenant.id}/views`, {
      method: "POST",
      body: {
        name,
        dashboard: shareEditing ? shareEditing.dashboard : (currentDash || ""),
        query: shareEditing ? shareEditing.query : filtersToQuery(),
        shared: mode !== "private",
        shared_roles: roles,
      },
    });
    $("share-dialog").close();
    await renderSavedViews();
    $("saved-views").value = saved.id;
    reflectViewSelection(saved);
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  }
};


// ------------------------------------------------------- case lifecycle (RFA workflow)
// The server owns the rules; this renders what it reports and never second-guesses it.
// In particular, an action the server marks unavailable is shown *disabled with its
// reason* rather than hidden: "the borrower's response window is open until 19 Aug" is
// the process explaining itself, where a missing button just looks like a broken page.

const WF_TRACK = [
  ["under_review", "Under review"],
  ["rfa_flagged", "Red-flagged"],
  ["natural_justice", "Natural justice"],
  ["response_evaluation", "Evaluating reply"],
  ["fraud_declared", "Fraud declared"],
  ["fmr_reported", "FMR filed"],
  ["closed_fraud", "Closed"],
];
// Actions that move a case forward get visual weight; irreversible ones are marked.
const WF_PRIMARY = new Set(["flag_rfa", "issue_show_cause", "record_response",
                            "close_window", "file_fmr", "close_case"]);
const WF_GRAVE = new Set(["declare_fraud", "close_no_fraud", "exonerate", "revoke_rfa"]);

let wfCaseId = null;

function wfDays(iso) {
  if (!iso) return null;
  return Math.round((new Date(iso) - Date.now()) / 86400000);
}

function wfDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined,
    { day: "2-digit", month: "short", year: "numeric" });
}

function wfClock(label, iso, { deadline = false, filedIso = null } = {}) {
  if (!iso) return "";
  const days = wfDays(iso);
  // A past event needs no second line; repeating its own date read as a rendering bug.
  let cls = "", sub = days === 0 ? "today"
    : days < 0 ? `${Math.abs(days)} day${Math.abs(days) === 1 ? "" : "s"} ago` : "";
  if (filedIso) {
    cls = "ok";
    sub = "filed " + wfDate(filedIso);
  } else if (deadline) {
    if (days < 0) { cls = "breach"; sub = `overdue by ${Math.abs(days)} day${Math.abs(days) === 1 ? "" : "s"}`; }
    else if (days <= 2) { cls = "soon"; sub = days === 0 ? "due today" : `${days} day${days === 1 ? "" : "s"} left`; }
    else { sub = `${days} days left`; }
  }
  return `<div class="wf-clock ${cls}"><dt>${esc(label)}</dt>
    <dd>${wfDate(iso)}<span class="wf-sub">${sub}</span></dd></div>`;
}

function wfTrack(state) {
  if (state === "exonerated") {
    // Exoneration is a terminal branch, not a step on the road to a fraud declaration.
    return `<ol class="wf-track">
      <li class="done">Under review</li><li class="done">Reviewed</li>
      <li class="branch">Exonerated<br><span class="muted">allegation not sustained</span></li>
    </ol>`;
  }
  const at = WF_TRACK.findIndex(([k]) => k === state);
  return `<ol class="wf-track">${WF_TRACK.map(([key, label], i) => {
    const cls = i < at ? "done" : i === at ? "current" : "";
    return `<li class="${cls}">${esc(label)}</li>`;
  }).join("")}</ol>`;
}

function wfActions(wf) {
  if (wf.terminal) return `<p class="wf-none">This case is closed. No further transitions are available.</p>`;
  if (!wf.actions.length) return `<p class="wf-none">No transitions are defined from this state.</p>`;
  return wf.actions.map((a) => {
    const tone = WF_PRIMARY.has(a.action) ? "primary" : WF_GRAVE.has(a.action) ? "grave" : "";
    let why = "";
    if (!a.allowed) {
      why = `<span class="wf-why">${esc(a.blocked_reason || "Unavailable.")}</span>`;
    } else if (a.requires_second_approval) {
      why = a.proposed_by
        ? `<span class="wf-why pending">Proposed by ${esc(a.proposed_by)} — your confirmation applies it.</span>`
        : `<span class="wf-why">Needs a second approver after you.</span>`;
    } else if (a.requires_document) {
      why = `<span class="wf-why">Requires a ${a.requires_document.replace(/_/g, " ")} on file.</span>`;
    }
    const label = a.proposed_by && a.allowed && a.requires_second_approval
      ? `Approve: ${a.label.toLowerCase()}` : a.label;
    return `<div class="wf-act ${tone}">
      <button data-wf-action="${a.action}" ${a.allowed ? "" : "disabled"}
              title="${esc(a.allowed ? "" : (a.blocked_reason || ""))}">${esc(label)}</button>
      ${why}</div>`;
  }).join("");
}

function wfHistory(rows) {
  if (!rows.length) return `<p class="wf-none">Nothing has happened to this case yet.</p>`;
  return `<ul class="wf-timeline">${rows.slice().reverse().map((r) => {
    const when = new Date(r.created_at).toLocaleString(undefined,
      { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
    const moved = r.to_state ? ` → ${r.to_state.replace(/_/g, " ")}` : "";
    return `<li class="${r.status}">
      <div class="wf-ev-head">
        <span class="wf-ev-label">${esc(r.label)}${moved}</span>
        <span class="wf-badge ${r.status}">${r.status}</span>
        ${r.breached_policy ? `<span class="wf-breach">outside the ${r.breach_days_allowed}-day window</span>` : ""}
      </div>
      <div class="wf-ev-meta">${esc(r.actor)} · ${esc(r.actor_role).replace(/_/g, " ")} · ${esc(when)}
        ${r.refusal_code ? ` · refused (${esc(r.refusal_code)})` : ""}</div>
      ${r.reason ? `<div class="wf-ev-reason">${esc(r.reason)}</div>` : ""}
    </li>`;
  }).join("")}</ul>`;
}

async function renderWorkflow(caseId) {
  wfCaseId = caseId;
  const host = $("wf-host");
  if (!host) return;
  host.innerHTML = `<div class="wf"><p class="wf-none" style="padding:16px 18px">Loading lifecycle…</p></div>`;
  try {
    const [wf, hist] = await Promise.all([
      api(`/analytics/${selectedTenant.id}/cases/${caseId}/workflow`),
      api(`/analytics/${selectedTenant.id}/cases/${caseId}/history`),
    ]);
    const pretty = wf.state.replace(/_/g, " ");
    host.innerHTML = `<div class="wf">
      <div class="wf-head">
        <h6>Case lifecycle</h6>
        <span class="wf-state ${wf.terminal || wf.state === "exonerated" ? "is-closed" : ""}">${pretty}</span>
        ${wf.rfa_flag ? `<span class="wf-rfa">RFA</span>` : ""}
        <span class="wf-assignee">
          <input id="wf-assignee" value="${esc(wf.assignee || "")}" placeholder="unassigned"
                 aria-label="Case assignee" />
          <button id="wf-assign" class="small-btn ghost">Assign</button>
        </span>
      </div>
      ${wfTrack(wf.state)}
      <div class="wf-clocks">
        ${wfClock("Show-cause issued", wf.clocks.show_cause_ts)}
        ${wfClock("Borrower response due", wf.clocks.response_due_ts, { deadline: true })}
        ${wfClock("Decision taken", wf.clocks.decision_ts)}
        ${wfClock("FMR due", wf.clocks.fmr_due_ts, { deadline: true, filedIso: wf.clocks.fmr_filed_ts })}
        ${wfClock("STR due", wf.clocks.str_due_ts, { deadline: true, filedIso: wf.clocks.str_filed_ts })}
      </div>
      <div class="wf-actions">${wfActions(wf)}</div>
      <details class="wf-history"><summary>History (${hist.rows.length})</summary>
        ${wfHistory(hist.rows)}</details>
    </div>`;

    host.querySelectorAll("[data-wf-action]").forEach((b) => {
      b.onclick = () => openWorkflowDialog(wf, b.dataset.wfAction);
    });
    $("wf-assign").onclick = async () => {
      try {
        await api(`/analytics/${selectedTenant.id}/cases/${caseId}/assign`,
                  { method: "POST", body: { assignee: $("wf-assignee").value.trim() } });
        await renderWorkflow(caseId);
      } catch (e) { alert(e.message); }
    };
  } catch (err) {
    host.innerHTML = `<div class="wf"><p class="error" style="padding:16px 18px">${esc(err.message)}</p></div>`;
  }
}

function openWorkflowDialog(wf, action) {
  const a = wf.actions.find((x) => x.action === action);
  if (!a) return;
  const dlg = $("wf-dialog");
  const grave = WF_GRAVE.has(action);
  $("wf-dialog-title").textContent = a.label;

  let effect = `This moves the case from <b>${wf.state.replace(/_/g, " ")}</b> to ` +
               `<b>${a.to_state.replace(/_/g, " ")}</b>.`;
  if (action === "issue_show_cause" && wf.policy.natural_justice_days) {
    effect += ` The borrower then has <b>${wf.policy.natural_justice_days} days</b> to reply — ` +
              `your bank's board-approved window — and the case cannot be moved on before it closes.`;
  }
  if (action === "declare_fraud") {
    effect += ` It also starts the <b>${wf.policy.fmr_filing_days}-day</b> FMR and ` +
              `<b>${wf.policy.str_filing_days}-day</b> STR clocks.`;
  }
  $("wf-dialog-effect").innerHTML = effect;
  $("wf-dialog-effect").className = "wf-effect" + (grave ? " grave" : "");

  const note = $("wf-dialog-note");
  if (a.requires_second_approval && !a.proposed_by) {
    note.textContent = "This is recorded as a proposal. A different authorised user must " +
                       "confirm it before it takes effect.";
    note.hidden = false;
  } else if (a.requires_second_approval && a.proposed_by) {
    note.textContent = `You are confirming a proposal made by ${esc(a.proposed_by)}.`;
    note.hidden = false;
  } else { note.hidden = true; }

  $("wf-reason-field").hidden = !a.requires_reason;
  $("wf-reason").value = "";
  $("wf-dialog-error").hidden = true;
  $("wf-confirm").textContent = a.requires_second_approval && !a.proposed_by
    ? "Propose" : a.label;
  dlg.dataset.action = action;
  dlg.dataset.needsReason = a.requires_reason ? "1" : "";
  dlg.showModal();
  if (a.requires_reason) $("wf-reason").focus();
}

$("wf-cancel").onclick = () => $("wf-dialog").close();

$("wf-confirm").onclick = async () => {
  const dlg = $("wf-dialog");
  const err = $("wf-dialog-error");
  const reason = $("wf-reason").value.trim();
  if (dlg.dataset.needsReason && !reason) {
    err.textContent = "A written reason is required — it is recorded on the case.";
    err.hidden = false;
    return;
  }
  try {
    const out = await api(
      `/analytics/${selectedTenant.id}/cases/${wfCaseId}/transition`,
      { method: "POST", body: { action: dlg.dataset.action, reason } });
    dlg.close();
    await renderWorkflow(wfCaseId);
    if (out.outcome === "proposed" || out.breached_policy) {
      alert(out.message);
    }
  } catch (e) {
    // A refusal is the lifecycle doing its job, so it is shown in place rather than
    // thrown away - the user needs to read why.
    err.textContent = e.message;
    err.hidden = false;
  }
};

// ------------------------------------------------------- federated sign-in (SSO)
// The console does not learn anything about a tenant's identity provider beyond its
// name. Everything else - issuer, client id, endpoints - stays server-side, because a
// public login page is exactly where that information should not be.
$("sso-find").onclick = async () => {
  const org = $("sso-org").value.trim().toLowerCase();
  if (!org) return;
  try {
    const d = await api(`/auth/sso/providers/${encodeURIComponent(org)}`,
                        { auth: false });
    const list = d.providers || [];
    if (!list.length) {
      $("login-error").textContent =
        `No organisation sign-in is configured for "${org}". Use your email and password.`;
      $("login-error").hidden = false;
      $("sso-block").hidden = true;
      return;
    }
    $("login-error").hidden = true;
    $("sso-buttons").innerHTML = list.map((p) =>
      `<button type="button" class="sso-btn" data-sso="${esc(d.tenant)}/${esc(p.slug)}">
         Continue with ${esc(p.display_name)}</button>`).join("");
    $("sso-buttons").querySelectorAll("[data-sso]").forEach((b) => {
      // A full page navigation, not fetch: the provider needs to own the browser for
      // its own login, and may legitimately require an interactive step of its own.
      b.onclick = () => { window.location.href = `${API}/auth/sso/${b.dataset.sso}/start`; };
    });
    $("sso-block").hidden = false;
  } catch (err) {
    $("login-error").textContent = err.message;
    $("login-error").hidden = false;
  }
};

// The provider sends the browser back here with a one-time handoff, never with a
// session token: URLs reach history, referrer headers and access logs.
(async function completeSso() {
  const q = new URLSearchParams(window.location.search);
  const handoff = q.get("sso_handoff");
  const ssoError = q.get("sso_error");
  if (!handoff && !ssoError) return;

  // Clear it from the address bar before doing anything else.
  window.history.replaceState({}, "", window.location.pathname);

  if (ssoError) {
    $("login-error").textContent = ssoError;
    $("login-error").hidden = false;
    showView("login-view");
    return;
  }
  try {
    const res = await fetch(API + "/auth/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: handoff }),
    });
    if (!res.ok) throw new Error("That sign-in link has already been used.");
    storeSession(await res.json());
    enterApp();
  } catch (err) {
    $("login-error").textContent = err.message;
    $("login-error").hidden = false;
    showView("login-view");
  }
})();

// ------------------------------------------------------------------ notifications
// Polled rather than pushed. A websocket would be nicer, but it is another thing to
// keep alive through a load balancer, and a compliance clock measured in days does not
// need sub-minute delivery to the screen.
const INBOX_POLL_MS = 60000;
let inboxTimer = null;

// Which notifications this session has already announced. Seeded silently on the first
// poll: arriving at a screen and being buried under twenty toasts for things that
// happened last week is worse than no toast at all.
let announced = null;
// Urgent items are held longer, because they are the ones worth reading twice.
const TOAST_MS = { info: 5000, warn: 7000, urgent: 10000 };
const MAX_TOASTS = 3;

function dismissToast(el) {
  if (!el || el.classList.contains("leaving")) return;
  el.classList.add("leaving");
  setTimeout(() => el.remove(), 200);
}

function showToast(n) {
  const stack = $("toasts");
  // A burst must not paper over the screen. Oldest goes first.
  while (stack.children.length >= MAX_TOASTS) dismissToast(stack.firstElementChild);

  const life = TOAST_MS[n.severity] || TOAST_MS.info;
  const el = document.createElement("div");
  el.className = `toast ${n.severity}`;
  el.innerHTML = `
    <div class="stripe"></div>
    <div>
      <div class="toast-subject">${esc(n.subject)}</div>
      <div class="toast-body">${esc(n.body)}</div>
    </div>
    <button class="toast-close" aria-label="Dismiss">&times;</button>
    <div class="toast-life"><span style="animation-duration:${life}ms"></span></div>`;

  const timer = { id: null, remaining: life, startedAt: Date.now() };
  const start = () => {
    timer.startedAt = Date.now();
    timer.id = setTimeout(() => dismissToast(el), timer.remaining);
  };
  // Hovering pauses the countdown - a notification that vanishes mid-sentence is a
  // notification nobody trusts. The progress bar pauses with it, in CSS.
  el.addEventListener("mouseenter", () => {
    clearTimeout(timer.id);
    timer.remaining -= Date.now() - timer.startedAt;
  });
  el.addEventListener("mouseleave", start);

  el.querySelector(".toast-close").onclick = (e) => {
    e.stopPropagation();
    clearTimeout(timer.id);
    dismissToast(el);
  };
  el.onclick = async () => {
    clearTimeout(timer.id);
    dismissToast(el);
    try {
      await api(`/notifications/${selectedTenant.id}/read`,
                { method: "POST", body: { ids: [n.id] } });
    } catch { /* opening the case matters more than marking it read */ }
    const caseId = n.link && new URLSearchParams(n.link.split("?")[1] || "").get("case");
    if (caseId) showEvidence("case", caseId);
    refreshInbox();
  };

  stack.appendChild(el);
  start();
}

function announce(items) {
  const unread = items.filter((n) => !n.read);
  if (announced === null) {
    // First poll of the session: remember what is already there, announce none of it.
    announced = new Set(items.map((n) => n.id));
    return;
  }
  const fresh = unread.filter((n) => !announced.has(n.id));
  items.forEach((n) => announced.add(n.id));
  // Most urgent first, so if the cap trims anything it trims the least important.
  const order = { urgent: 0, warn: 1, info: 2 };
  fresh.sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3));
  fresh.slice(0, MAX_TOASTS).forEach(showToast);
}

function relativeTime(iso) {
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

async function refreshInbox() {
  if (!selectedTenant || !token) return;
  try {
    const d = await api(`/notifications/${selectedTenant.id}`);
    const badge = $("bell-count");
    badge.textContent = d.unread > 99 ? "99+" : String(d.unread);
    badge.hidden = d.unread === 0;

    $("inbox-list").innerHTML = d.items.length ? d.items.map((n) => `
      <div class="note-row ${n.severity} ${n.read ? "" : "unread"}"
           data-id="${n.id}" data-link="${esc(n.link || "")}">
        <div class="stripe"></div>
        <div>
          <div class="note-subject">${esc(n.subject)}</div>
          <div class="note-body">${esc(n.body)}</div>
          <div class="note-when">${relativeTime(n.created_at)}</div>
        </div>
      </div>`).join("")
      : `<div class="inbox-empty">Nothing needs your attention.</div>`;

    announce(d.items);

    $("inbox-list").querySelectorAll(".note-row").forEach((row) => {
      row.onclick = async () => {
        try {
          await api(`/notifications/${selectedTenant.id}/read`,
                    { method: "POST", body: { ids: [row.dataset.id] } });
        } catch { /* reading is a convenience; never block the navigation */ }
        const link = row.dataset.link;
        const caseId = link && new URLSearchParams(link.split("?")[1] || "").get("case");
        $("inbox").hidden = true;
        if (caseId) showEvidence("case", caseId);
        refreshInbox();
      };
    });
  } catch { /* a failed poll must not disturb the page */ }
}

$("bell").onclick = () => {
  const panel = $("inbox");
  panel.hidden = !panel.hidden;
  if (!panel.hidden) refreshInbox();
};

$("inbox-read-all").onclick = async () => {
  await api(`/notifications/${selectedTenant.id}/read`,
            { method: "POST", body: { all: true } });
  refreshInbox();
};

// Clicking elsewhere closes it, but not a click inside it.
document.addEventListener("click", (e) => {
  const panel = $("inbox");
  if (panel.hidden) return;
  if (!panel.contains(e.target) && !$("bell").contains(e.target)) panel.hidden = true;
});

function startInboxPolling() {
  if (inboxTimer) clearInterval(inboxTimer);
  announced = null;
  refreshInbox();
  inboxTimer = setInterval(refreshInbox, INBOX_POLL_MS);
}

// ------------------------------------------- notification settings (per person)
$("inbox-prefs").onclick = async () => {
  $("inbox").hidden = true;
  try {
    const d = await api(`/notifications/${selectedTenant.id}/preferences`);
    $("pref-email").checked = d.email_enabled;
    const muted = new Set(d.muted_kinds || []);
    // Unmutable kinds are shown, disabled, with the reason. Hiding them would leave
    // someone wondering why notifications they never asked for keep arriving.
    $("pref-kinds").innerHTML = d.kinds.map((k) => `
      <label class="pref-kind ${k.mutable ? "" : "locked"}">
        <input type="checkbox" data-kind="${esc(k.key)}"
               ${k.mutable ? "" : "disabled"}
               ${k.mutable && !muted.has(k.key) ? "checked" : ""}
               ${k.mutable ? "" : "checked"} />
        <span>${esc(k.label)}
          ${k.mutable ? "" : `<span class="why">Required — your bank is accountable
             for acting on these</span>`}</span>
        <span class="pref-sev ${k.severity}">${k.severity}</span>
      </label>`).join("");
    $("prefs-error").hidden = true;
    $("prefs-dialog").showModal();
  } catch (err) {
    alert(err.message);
  }
};

$("prefs-cancel").onclick = () => $("prefs-dialog").close();

$("prefs-save").onclick = async () => {
  // The checkbox reads "notify me", so a muted kind is an *unticked* mutable one.
  const muted = [...document.querySelectorAll("#pref-kinds input[data-kind]")]
    .filter((c) => !c.disabled && !c.checked)
    .map((c) => c.dataset.kind);
  try {
    await api(`/notifications/${selectedTenant.id}/preferences`, {
      method: "PUT",
      body: { email_enabled: $("pref-email").checked, muted_kinds: muted },
    });
    $("prefs-dialog").close();
    refreshInbox();
  } catch (err) {
    $("prefs-error").textContent = err.message;
    $("prefs-error").hidden = false;
  }
};

// ------------------------------------ notification delivery (per tenant, admin)
async function loadDeliverySettings() {
  const host = $("nd-status");
  if (!selectedTenant || !me || !me.can_admin_tenant) return;
  try {
    const rows = await api(`/notifications/${selectedTenant.id}/channels`);
    const email = rows.find((r) => r.channel === "email") || {};
    const hook = rows.find((r) => r.channel === "webhook") || {};

    $("nd-email-on").checked = !!email.enabled;
    $("nd-email-host").value = (email.config || {}).host || "";
    $("nd-email-port").value = (email.config || {}).port || 25;
    $("nd-email-from").value = (email.config || {}).from || "";
    $("nd-email-user").value = (email.config || {}).username || "";
    // Never populated from the server, and left blank means "leave it as it is".
    $("nd-email-pass").value = "";
    $("nd-email-pass").placeholder = email.has_credentials ? "unchanged" : "";
    $("nd-email-tls").checked = (email.config || {}).starttls !== false;
    $("nd-email-sev").value = email.min_severity || "warn";
    $("nd-email-tries").value = (email.config || {}).max_attempts || 5;
    $("nd-email-backoff").value = ((email.config || {}).retry_backoff_minutes || [1, 5, 15, 60]).join(", ");

    $("nd-hook-on").checked = !!hook.enabled;
    $("nd-hook-url").value = (hook.config || {}).url || "";
    $("nd-hook-sev").value = hook.min_severity || "warn";
    $("nd-hook-tries").value = (hook.config || {}).max_attempts || 5;
    $("nd-hook-backoff").value = ((hook.config || {}).retry_backoff_minutes || [1, 5, 15, 60]).join(", ");

    const live = [email.enabled && "email", hook.enabled && "webhook"].filter(Boolean);
    host.textContent = live.length
      ? `Sending via ${live.join(" and ")}`
      : "Nothing leaves the platform — inbox only";
  } catch (err) {
    host.textContent = "";
  }
}

function retryPolicy(prefix) {
  const steps = $(`${prefix}-backoff`).value
    .split(/[,\s]+/).map((x) => parseInt(x, 10)).filter((n) => n > 0);
  return {
    max_attempts: Number($(`${prefix}-tries`).value) || 5,
    // Empty means "use the platform default" rather than "never wait".
    retry_backoff_minutes: steps.length ? steps : undefined,
  };
}

async function saveChannel(channel, body) {
  $("nd-error").hidden = true;
  try {
    await api(`/notifications/${selectedTenant.id}/channels`,
              { method: "PUT", body: { channel, ...body } });
    await loadDeliverySettings();
    loadDeliveryProblems();
  } catch (err) {
    $("nd-error").textContent = err.message;
    $("nd-error").hidden = false;
  }
}

$("nd-email-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const config = {
    host: $("nd-email-host").value.trim(),
    port: Number($("nd-email-port").value) || 25,
    from: $("nd-email-from").value.trim(),
    username: $("nd-email-user").value.trim(),
    starttls: $("nd-email-tls").checked,
    ...retryPolicy("nd-email"),
  };
  // Only send a password when one was typed. The field is never pre-filled, so
  // submitting it empty must not wipe a working credential.
  const pass = $("nd-email-pass").value;
  if (pass) config.password = pass;
  saveChannel("email", { enabled: $("nd-email-on").checked, config,
                         min_severity: $("nd-email-sev").value });
});

$("nd-hook-form").addEventListener("submit", (e) => {
  e.preventDefault();
  saveChannel("webhook", {
    enabled: $("nd-hook-on").checked,
    config: { url: $("nd-hook-url").value.trim(), ...retryPolicy("nd-hook") },
    min_severity: $("nd-hook-sev").value,
  });
});

// ------------------------------------------------- test sends and delivery problems
async function sendTest(channel, resultId) {
  const el = $(resultId);
  el.hidden = false;
  el.className = "small";
  el.textContent = "Sending…";
  try {
    const d = await api(`/notifications/${selectedTenant.id}/channels/test`,
                        { method: "POST", body: { channel } });
    // A failed test is a successful answer, so the real diagnostic is shown rather
    // than a generic "something went wrong".
    el.className = `small nd-result ${d.ok ? "ok" : "bad"}`;
    el.textContent = d.ok ? `Delivered to ${d.to} — ${d.detail}`
                          : `Not delivered: ${d.detail}`;
  } catch (err) {
    el.className = "small nd-result bad";
    el.textContent = err.message;
  }
  loadDeliveryProblems();
}

$("nd-email-test").onclick = () => sendTest("email", "nd-email-result");
$("nd-hook-test").onclick = () => sendTest("webhook", "nd-hook-result");

async function loadDeliveryProblems() {
  if (!selectedTenant || !me || !me.can_admin_tenant) return;
  try {
    const d = await api(`/notifications/${selectedTenant.id}/deliveries?status=problems`);
    const items = d.items || [];
    const retrying = d.counts.retrying || 0;
    const gaveUp = d.counts.abandoned || 0;

    $("nd-prob-count").textContent = items.length
      ? `(${retrying} retrying, ${gaveUp} given up)` : "";
    $("nd-retry-all").hidden = items.length === 0;

    $("nd-problems").innerHTML = items.length ? items.map((p) => `
      <div class="nd-prob">
        <div>
          <div class="subj">${esc(p.subject)}</div>
          <div class="meta">${p.channel} → ${esc(p.recipient || p.target || "—")} ·
            attempt ${p.attempts}</div>
          <div class="err">${esc(p.last_error || "no detail recorded")}</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">
          <span class="nd-state ${p.status}">${p.status === "pending" ? "retrying" : "given up"}</span>
          <button class="small-btn ghost" data-retry="${p.id}">Retry</button>
        </div>
      </div>`).join("")
      : `<div class="nd-empty">No delivery failures. Everything raised has either been
           sent or is waiting on a channel you have not enabled.</div>`;

    $("nd-problems").querySelectorAll("[data-retry]").forEach((b) => {
      b.onclick = async () => {
        b.disabled = true;
        try {
          await api(`/notifications/${selectedTenant.id}/deliveries/${b.dataset.retry}/retry`,
                    { method: "POST" });
        } catch (err) {
          $("nd-error").textContent = err.message;
          $("nd-error").hidden = false;
        }
        loadDeliveryProblems();
      };
    });
  } catch (err) {
    // Say so. An empty panel is indistinguishable from "no failures", which is the
    // most dangerous thing this particular view could claim while it is broken.
    $("nd-prob-count").textContent = "";
    $("nd-retry-all").hidden = true;
    $("nd-problems").innerHTML =
      `<div class="nd-empty">Could not load delivery history — ${esc(err.message)}.
         Failures may exist and not be shown here.</div>`;
  }
}

$("nd-retry-all").onclick = async () => {
  // The realistic sequence is: a setting was wrong, an hour of notifications backed up,
  // someone fixed it. Retrying them one at a time is not a recovery procedure.
  const btn = $("nd-retry-all");
  btn.disabled = true;
  btn.textContent = "Retrying…";
  try {
    const d = await api(`/notifications/${selectedTenant.id}/deliveries/retry-all`,
                        { method: "POST", body: { include_retrying: true } });
    $("nd-error").hidden = true;
    $("nd-prob-count").textContent =
      `(requeued ${d.requeued}, sent ${d.sent}, still failing ${d.retrying + d.abandoned})`;
  } catch (err) {
    $("nd-error").textContent = err.message;
    $("nd-error").hidden = false;
  }
  btn.disabled = false;
  btn.textContent = "Retry all";
  loadDeliveryProblems();
};

// ------------------------------------------------------------ regulatory filings
// The platform prepares and records; a named officer submits. That split is the whole
// design, so the UI never offers a button that says "file".
let filingCaseId = null;

function filingCard(kind, label, readiness, existing) {
  const done = existing && existing.status === "acknowledged";
  const state = done ? "acknowledged" : (readiness.ready ? "ready" : "blocked");
  const stateText = done ? "submitted" : (readiness.ready ? "ready to prepare" : "not fileable");

  const gaps = (readiness.blocking || []).length
    ? `<ul class="filing-gaps">${readiness.blocking.map((g) =>
        `<li><b>${esc(g.label)}</b> — ${esc(g.why || "required")}</li>`).join("")}</ul>`
    : "";

  // Polishing needs the same evidenced case readiness does (it reads the built
  // payload) and stops making sense once a return is submitted — the clock has
  // already stopped, and there is nothing left to prepare.
  const polishBtn = !done && readiness.ready
    ? `<button class="small-btn ghost" data-polish="${kind}">Polish narrative</button>`
    : "";

  const actions = done
    ? `<button class="small-btn ghost" data-dl="${existing.id}">Open pack</button>
       <button class="small-btn ghost" data-dl-xml="${existing.id}">XML</button>`
    : existing
      ? `<button class="small-btn ghost" data-dl="${existing.id}">Open pack</button>
         <button class="small-btn ghost" data-dl-xml="${existing.id}">XML</button>
         <button class="small-btn" data-ack="${existing.id}">Record submission</button>
         <button class="small-btn ghost" data-gen="${kind}">Regenerate</button>
         ${polishBtn}`
      : `<button class="small-btn" data-gen="${kind}" ${readiness.ready ? "" : "disabled"}>
           Prepare return</button>
         ${polishBtn}`;

  const rev = existing
    ? `<div class="filing-rev">Revision ${existing.revision} ·
         prepared by ${esc(existing.generated_by)} ·
         <span class="mono">${(existing.content_hash || "").slice(0, 16)}…</span>
         ${existing.reference_number
           ? ` · reference <b>${esc(existing.reference_number)}</b>` : ""}</div>`
    : "";

  return `<div class="filing-card">
    <div style="display:flex;justify-content:space-between;align-items:start;gap:8px">
      <div><h6>${esc(label)}</h6>
        <div class="filing-due">to ${kind === "fmr" ? "RBI" : "FIU-IND"}</div></div>
      <span class="filing-state ${state}">${esc(stateText)}</span>
    </div>
    ${gaps}
    <div class="filing-actions">${actions}</div>
    ${rev}
  </div>`;
}

async function renderFilings(caseId) {
  filingCaseId = caseId;
  const host = $("filing-host");
  if (!host || !selectedTenant) return;
  try {
    const [fmr, str, existing] = await Promise.all([
      api(`/analytics/${selectedTenant.id}/cases/${caseId}/filings/fmr/readiness`),
      api(`/analytics/${selectedTenant.id}/cases/${caseId}/filings/str/readiness`),
      api(`/analytics/${selectedTenant.id}/cases/${caseId}/filings`),
    ]);
    // Only the live revision of each kind; superseded drafts are history, not choices.
    const live = {};
    existing.forEach((f) => {
      if (f.status !== "superseded" && !live[f.kind]) live[f.kind] = f;
    });

    host.innerHTML = `<div class="filing">
      <div class="filing-head"><h6>Regulatory returns</h6></div>
      <div class="filing-body">
        ${filingCard("fmr", "Fraud Monitoring Return", fmr, live.fmr)}
        ${filingCard("str", "Suspicious Transaction Report", str, live.str)}
      </div>
      <div class="filing-note">The platform prepares the return and records the
        acknowledgement. It does not submit: that is a decision a named officer takes,
        and the pack is not the regulator's own file format.</div>
    </div>`;

    host.querySelectorAll("[data-gen]").forEach((b) => {
      b.onclick = async () => {
        b.disabled = true;
        try {
          await api(`/analytics/${selectedTenant.id}/cases/${caseId}/filings/${b.dataset.gen}/generate`,
                    { method: "POST" });
        } catch (err) { alert(err.message); }
        renderFilings(caseId);
      };
    });
    host.querySelectorAll("[data-dl]").forEach((b) => {
      b.onclick = () => openFiling(b.dataset.dl, "html");
    });
    host.querySelectorAll("[data-dl-xml]").forEach((b) => {
      b.onclick = () => openFiling(b.dataset.dlXml, "xml");
    });
    host.querySelectorAll("[data-ack]").forEach((b) => {
      b.onclick = () => {
        $("ack-ref").value = "";
        $("ack-note").value = "";
        $("ack-error").hidden = true;
        $("ack-dialog").dataset.filing = b.dataset.ack;
        $("ack-dialog").showModal();
        $("ack-ref").focus();
      };
    });
    host.querySelectorAll("[data-polish]").forEach((b) => {
      b.onclick = () => openPolishDialog(caseId, b.dataset.polish);
    });
  } catch (err) {
    host.innerHTML = `<div class="filing"><div class="filing-note">
      Could not load filing status — ${esc(err.message)}.</div></div>`;
  }
}

const FILING_FIELD_LABELS = {
  modus_operandi: "Modus operandi",
  suspicion_grounds: "Grounds for suspicion",
};

async function openPolishDialog(caseId, kind) {
  const dialog = $("polish-dialog");
  dialog.dataset.caseId = caseId;
  dialog.dataset.kind = kind;
  $("polish-error").hidden = true;
  $("polish-fields").innerHTML = "";
  $("polish-save").hidden = true;
  $("polish-loading").hidden = false;
  dialog.showModal();

  try {
    const res = await api(
      `/analytics/${selectedTenant.id}/cases/${caseId}/filings/${kind}/narrative/polish`,
      { method: "POST" });
    $("polish-loading").hidden = true;

    if (!res.results.length) {
      $("polish-fields").innerHTML =
        `<p class="share-note">No narrative fields to polish for this return yet —
         prepare readiness first.</p>`;
      return;
    }

    $("polish-fields").innerHTML = res.results.map((r) => `
      <div class="polish-field" data-field="${esc(r.field)}">
        <h6>${esc(FILING_FIELD_LABELS[r.field] || r.field)}
          <span class="polish-badge ${r.verified ? "verified" : "rejected"}">
            ${r.verified ? "Rewrite verified" : "Rewrite rejected"}</span>
        </h6>
        ${r.verified ? "" : `<p class="polish-reject-reason">${esc(r.rejection_reason || "")}
          — showing the original text below instead.</p>`}
        <div class="polish-original"><strong>Original (as built)</strong>${esc(r.original)}</div>
        <label>Text to use</label>
        <textarea data-text>${esc(r.verified ? r.polished : r.original)}</textarea>
      </div>`).join("");
    $("polish-save").hidden = false;
  } catch (err) {
    $("polish-loading").hidden = true;
    $("polish-error").textContent = err.message;
    $("polish-error").hidden = false;
  }
}

$("polish-cancel").onclick = () => $("polish-dialog").close();

$("polish-save").onclick = async () => {
  const dialog = $("polish-dialog");
  const caseId = dialog.dataset.caseId;
  const kind = dialog.dataset.kind;
  const overrides = {};
  dialog.querySelectorAll(".polish-field").forEach((el) => {
    overrides[el.dataset.field] = el.querySelector("[data-text]").value;
  });

  $("polish-save").disabled = true;
  try {
    await api(`/analytics/${selectedTenant.id}/cases/${caseId}/filings/${kind}/generate`,
             { method: "POST", body: overrides });
    dialog.close();
    renderFilings(caseId);
  } catch (err) {
    $("polish-error").textContent = err.message;
    $("polish-error").hidden = false;
  } finally {
    $("polish-save").disabled = false;
  }
};

async function openFiling(id, fmt) {
  // Fetched rather than linked: the download needs the bearer token, and the pack
  // carries unmasked customer identifiers so the access is audited server-side.
  try {
    const res = await fetch(
      `${API}/analytics/${selectedTenant.id}/filings/${id}/download?fmt=${fmt}`,
      { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = new Blob([await res.text()],
                          { type: fmt === "xml" ? "application/xml" : "text/html" });
    window.open(URL.createObjectURL(blob), "_blank");
  } catch (err) {
    alert(`Could not open the return: ${esc(err.message)}`);
  }
}

// ------------------------------ LEA referral and recovery (BR-415)
// The unevidenced figure is the thing this panel exists to show. A recovered amount with
// no entry behind it is not a rounding difference; it is a number the bank cannot
// substantiate, and it feeds the net-loss figure the board reads.
let recCaseId = null;

const recOpts = (obj, sel) => Object.entries(obj)
  .map(([k, v]) => `<option value="${esc(k)}"${k === sel ? " selected" : ""}>${esc(v)}</option>`)
  .join("");

async function renderRecovery(caseId) {
  recCaseId = caseId;
  const host = $("rec-host");
  if (!host || !selectedTenant) return;
  let d;
  try {
    d = await api(`/analytics/${selectedTenant.id}/cases/${caseId}/recovery`);
  } catch (err) {
    host.innerHTML = `<div class="acc"><div class="acc-note">
      Could not load referral and recovery — ${esc(err.message)}.</div></div>`;
    return;
  }
  // Only meaningful once fraud is declared; before that there is nothing to refer or
  // recover, and an empty panel would imply otherwise.
  if (!["fraud_declared", "fmr_reported", "closed_fraud"].includes(d.case_state)) {
    host.innerHTML = ""; return;
  }

  const r = d.referral;
  const refState = r ? (r.progressed ? "done" : "open")
                     : (d.referral_expected ? "overdue" : "none");
  const refText = r ? r.state_label
                    : (d.referral_expected ? "not referred" : "not required");

  const refBody = r
    ? `<div class="acc-f-where">${esc(r.agency_label)}${r.agency_office
         ? " · " + esc(r.agency_office) : ""}</div>
       <div class="acc-f-action">Complaint ${esc(r.complaint_ref || "—")}
         · referred ${new Date(r.referred_on).toLocaleDateString()}
         ${r.fir_number ? `· FIR <b>${esc(r.fir_number)}</b>` : ""}</div>
       ${r.last_update ? `<div class="acc-f-note">${esc(r.last_update)}</div>` : ""}
       ${d.may_record ? `<button class="small-btn ghost" id="rec-update">Update status</button>` : ""}`
    : `<div class="acc-empty">${d.referral_expected
         ? `This fraud is at or above the ${inr(d.referral_floor_paise)} referral floor
            and no complaint has been recorded.`
         : `Below the ${inr(d.referral_floor_paise)} referral floor, so a complaint is
            not required by policy.`}</div>
       ${d.may_record ? `<button class="small-btn" id="rec-refer">Record referral</button>` : ""}`;

  const gap = d.recovery_evidenced ? "" : `<div class="acc-systemic">
      <b>${inr(d.unevidenced_paise)} unevidenced.</b> The case carries a recovered figure
      that no entry substantiates. Record the entries behind it, or the amount cannot be
      evidenced to an inspection.</div>`;

  const entries = d.entries.length
    ? `<ul class="acc-findings">${d.entries.map((e) => `<li class="acc-finding clear">
         <div class="acc-f-head">
           <span class="acc-f-who"><b>${inr(e.amount_paise)}</b></span>
           <span class="acc-f-finding clear">${esc(e.mode_label)}</span></div>
         <div class="acc-f-where">${new Date(e.recovered_on).toLocaleDateString()}
           ${e.reference ? "· ref " + esc(e.reference) : ""}</div>
         ${e.note ? `<div class="acc-f-note">${esc(e.note)}</div>` : ""}
       </li>`).join("")}</ul>`
    : `<div class="acc-empty">No recovery has been recorded against this case.</div>`;

  host.innerHTML = `<div class="acc">
    <div class="acc-head">
      <div><h6>Law enforcement</h6>
        <div class="acc-clock">Referral floor ${inr(d.referral_floor_paise)}</div></div>
      <span class="acc-state ${refState}">${esc(refText)}</span>
    </div>
    <div class="acc-body">${refBody}</div>
    <div class="acc-head" style="border-top:1px solid var(--line)">
      <div><h6>Recovery</h6>
        <div class="acc-clock">${inr(d.recovered_paise)} recovered of
          ${inr(d.amount_paise)} · ${inr(d.outstanding_paise)} outstanding</div></div>
      <span class="acc-state ${d.recovery_evidenced ? "done" : "overdue"}"
        >${d.recovery_evidenced ? "evidenced" : "unevidenced"}</span>
    </div>
    <div class="acc-body">${gap}${entries}
      ${d.may_record ? `<div class="acc-actions">
        <button class="small-btn" id="rec-add">Record a recovery</button></div>` : ""}</div>
  </div>`;

  const reload = () => { renderRecovery(caseId); };

  if ($("rec-refer")) {
    $("rec-refer").onclick = () => {
      $("rec-refer-form").reset();
      $("rec-refer-error").hidden = true;
      $("rec-agency").innerHTML = recOpts(d.vocabulary.agencies);
      $("rec-referred-on").value = new Date().toISOString().slice(0, 10);
      $("rec-refer-dialog").showModal();
    };
  }
  if ($("rec-update")) {
    $("rec-update").onclick = () => {
      $("rec-update-form").reset();
      $("rec-update-error").hidden = true;
      $("rec-state").innerHTML = recOpts(d.vocabulary.states, r.state);
      $("rec-fir").value = r.fir_number || "";
      $("rec-update-dialog").showModal();
    };
  }
  if ($("rec-add")) {
    $("rec-add").onclick = () => {
      $("rec-entry-form").reset();
      $("rec-entry-error").hidden = true;
      $("rec-mode").innerHTML = recOpts(d.vocabulary.modes);
      $("rec-on").value = new Date().toISOString().slice(0, 10);
      $("rec-entry-dialog").showModal();
      $("rec-amount").focus();
    };
  }
  host._reload = reload;
}

function recFail(id, msg) { $(id).textContent = msg; $(id).hidden = false; }

$("rec-refer-cancel").onclick = () => $("rec-refer-dialog").close();
$("rec-refer-save").onclick = async () => {
  if (!$("rec-referred-on").value) {
    return recFail("rec-refer-error", "The referral date is required.");
  }
  try {
    await api(`/analytics/${selectedTenant.id}/cases/${recCaseId}/recovery/referral`,
              { method: "POST", body: {
                agency: $("rec-agency").value, agency_office: $("rec-office").value,
                complaint_ref: $("rec-complaint").value,
                referred_on: $("rec-referred-on").value,
                note: $("rec-refer-note").value } });
    $("rec-refer-dialog").close();
    renderRecovery(recCaseId);
  } catch (err) { recFail("rec-refer-error", err.message); }
};

$("rec-update-cancel").onclick = () => $("rec-update-dialog").close();
$("rec-update-save").onclick = async () => {
  try {
    await api(`/analytics/${selectedTenant.id}/cases/${recCaseId}/recovery/referral/update`,
              { method: "POST", body: {
                state: $("rec-state").value, fir_number: $("rec-fir").value,
                fir_date: $("rec-fir-date").value || null,
                note: $("rec-update-note").value } });
    $("rec-update-dialog").close();
    renderRecovery(recCaseId);
  } catch (err) { recFail("rec-update-error", err.message); }
};

$("rec-entry-cancel").onclick = () => $("rec-entry-dialog").close();
$("rec-entry-save").onclick = async () => {
  const rupees = parseFloat($("rec-amount").value);
  if (!(rupees > 0)) {
    return recFail("rec-entry-error", "Enter an amount greater than zero.");
  }
  if (!$("rec-on").value) {
    return recFail("rec-entry-error", "The recovery date is required.");
  }
  try {
    await api(`/analytics/${selectedTenant.id}/cases/${recCaseId}/recovery/entries`,
              { method: "POST", body: {
                // Rounded to whole paise here: the API takes integer paise, and a float
                // reaching the amount column is how money quietly goes wrong.
                amount_paise: Math.round(rupees * 100),
                mode: $("rec-mode").value, reference: $("rec-reference").value,
                recovered_on: $("rec-on").value, note: $("rec-entry-note").value } });
    $("rec-entry-dialog").close();
    renderRecovery(recCaseId);
    // The case header shows the recovered figure, which has just moved.
    renderWorkflow(recCaseId);
  } catch (err) { recFail("rec-entry-error", err.message); }
};

// ------------------------------------------------------ board / ACB packs (BR-507)
// Shown on the Board dashboard only. The schedule table is the point: a committee that
// was never given a pack for a quarter is the governance gap, and it is invisible in a
// list that only shows the packs that do exist.
let bpIssueId = null;

function bpRow(p, caps) {
  const badge = { issued: "done", draft: "open", superseded: "none",
                  missing: "overdue" }[p.status] || "none";
  const when = p.issued_at ? new Date(p.issued_at).toLocaleDateString() : "—";
  const actions = p.pack_id
    ? `<button class="small-btn ghost" data-bp-open="${esc(p.pack_id)}">Open</button>
       <button class="small-btn ghost" data-bp-json="${esc(p.pack_id)}">JSON</button>`
      + (p.status === "draft"
          ? ` <button class="small-btn" data-bp-issue="${esc(p.pack_id)}"
                ${caps.may_issue ? "" : `disabled title="${esc(caps.issue_requires)}"`}
                >Issue</button>`
          : "")
    : `<button class="small-btn" data-bp-make="${esc(p.period_start)}"
         ${caps.may_prepare ? "" : "disabled"}>Prepare</button>`;
  return `<tr>
    <td>${esc(p.period_label)}</td>
    <td><span class="acc-state ${badge}">${p.status === "missing" ? "no pack" : esc(p.status)}</span></td>
    <td>${esc(when)}</td>
    <td class="bp-actions">${actions}</td></tr>`;
}

async function loadBoardPacks() {
  const panel = $("bp-panel");
  if (!panel) return;
  if (!selectedTenant || currentDash !== "board") { panel.hidden = true; return; }
  panel.hidden = false;
  try {
    const [due, packs] = await Promise.all([
      api(`/analytics/${selectedTenant.id}/board-packs/schedule/due`),
      api(`/analytics/${selectedTenant.id}/board-packs`),
    ]);
    $("bp-cadence").textContent = `· ${due.cadence}`;
    bpApplyCaps(due);

    // A superseded draft is history, not a choice; show the live pack per period.
    const live = {};
    packs.forEach((p) => {
      const k = p.period_start;
      if (p.status === "superseded") return;
      if (!live[k] || p.status === "issued") live[k] = p;
    });
    const rows = due.periods.map((p) => {
      const l = live[p.period_start];
      return l ? { ...p, status: l.status, pack_id: l.id, issued_at: l.issued_at } : p;
    });

    const missing = rows.filter((r) => r.status === "missing").length;
    const unissued = rows.filter((r) => r.status === "draft").length;
    const summary = missing
      ? `<p class="error">${missing} completed ${missing === 1 ? "period" : "periods"}
           went by with no pack put in front of the committee.</p>`
      : `<p class="ok">Every completed period has a pack.</p>`;

    $("bp-body").innerHTML = summary
      + (unissued ? `<p class="muted row-hint">${unissued} prepared but not yet
           issued — a draft evidences nothing until it reaches the committee.</p>` : "")
      + (due.may_issue ? "" : `<p class="muted row-hint">${esc(due.issue_requires)}</p>`)
      + `<table class="cfg-table"><thead><tr><th>Period</th><th>Status</th>
           <th>Issued</th><th></th></tr></thead><tbody>${
             rows.map((r) => bpRow(r, due)).join("")}</tbody></table>`;

    $("bp-body").querySelectorAll("[data-bp-open]").forEach((b) => {
      b.onclick = () => openBoardPack(b.dataset.bpOpen, "html");
    });
    $("bp-body").querySelectorAll("[data-bp-json]").forEach((b) => {
      b.onclick = () => openBoardPack(b.dataset.bpJson, "json");
    });
    $("bp-body").querySelectorAll("[data-bp-make]").forEach((b) => {
      b.onclick = () => generateBoardPack(b.dataset.bpMake, b);
    });
    $("bp-body").querySelectorAll("[data-bp-issue]").forEach((b) => {
      b.onclick = () => {
        bpIssueId = b.dataset.bpIssue;
        $("bp-recipients").value = "";
        $("bp-note").value = "";
        $("bp-issue-error").hidden = true;
        $("bp-issue-dialog").showModal();
        $("bp-recipients").focus();
      };
    });
  } catch (err) {
    // Stated, never blank: an empty panel here reads as "no packs needed".
    $("bp-body").innerHTML =
      `<p class="error">Could not load board packs — ${esc(err.message)}.</p>`;
  }
}

async function generateBoardPack(asOf, btn) {
  if (btn) btn.disabled = true;
  try {
    await api(`/analytics/${selectedTenant.id}/board-packs/generate`,
              { method: "POST", body: asOf ? { as_of: asOf } : {} });
  } catch (err) { alert(err.message); }
  loadBoardPacks();
}

async function openBoardPack(id, fmt) {
  // Fetched rather than linked: the download needs the bearer token, and the pack names
  // individual customers and staff, so the access is audited server-side.
  try {
    const res = await fetch(
      `${API}/analytics/${selectedTenant.id}/board-packs/${id}/download?fmt=${fmt}`,
      { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (err) {
    alert(`Could not open the pack: ${esc(err.message)}`);
  }
}

$("bp-generate").onclick = () => generateBoardPack(null, $("bp-generate"));
// The top-level Prepare button follows the same capability as the per-row ones.
function bpApplyCaps(caps) {
  $("bp-generate").disabled = !caps.may_prepare;
  $("bp-generate").title = caps.may_prepare ? "" : "Your role may not prepare a board pack";
}
$("bp-issue-cancel").onclick = () => $("bp-issue-dialog").close();
$("bp-issue-save").onclick = async () => {
  const recipients = $("bp-recipients").value.split(/[\n,;]+/)
    .map((s) => s.trim()).filter(Boolean);
  if (!recipients.length) {
    $("bp-issue-error").textContent =
      "Name at least one recipient. A pack issued to nobody evidences nothing.";
    $("bp-issue-error").hidden = false;
    return;
  }
  try {
    await api(`/analytics/${selectedTenant.id}/board-packs/${bpIssueId}/issue`,
              { method: "POST", body: { recipients, note: $("bp-note").value } });
    $("bp-issue-dialog").close();
    loadBoardPacks();
  } catch (err) {
    $("bp-issue-error").textContent = err.message;
    $("bp-issue-error").hidden = false;
  }
};

// -------------------------------------------------- staff accountability (BR-414)
// Deliberately sits below the returns in the drawer. That is the order the obligation
// runs in: report first, then settle accountability - never the other way round.
let accCaseId = null;

function accFindingRow(f, frozen, canEdit) {
  // Escaped once, here. The div below interpolates this already-escaped string, so it
  // must not be escaped again - a branch called "Fort & Colaba" would otherwise read
  // "Fort &amp; Colaba" on screen.
  const where = [f.role_at_time, f.branch].filter(Boolean).map(esc).join(" · ");
  return `<li class="acc-finding ${f.adverse ? "adverse" : "clear"}">
    <div class="acc-f-head">
      <span class="acc-f-who"><b>${esc(f.staff_name)}</b>
        <span class="mono">${esc(f.staff_ref)}</span></span>
      <span class="acc-f-finding ${f.adverse ? "adverse" : "clear"}">${esc(f.finding_label)}</span>
    </div>
    ${where ? `<div class="acc-f-where">${where}</div>` : ""}
    <div class="acc-f-action">Action: ${esc(f.action_label)}</div>
    ${f.note ? `<div class="acc-f-note">${esc(f.note)}</div>` : ""}
    ${frozen || !canEdit ? "" : `<button class="small-btn ghost acc-del" data-del="${esc(f.id)}"
       title="Withdraw a finding entered in error">Withdraw</button>`}
  </li>`;
}

async function renderAccountability(caseId) {
  accCaseId = caseId;
  const host = $("acc-host");
  if (!host || !selectedTenant) return;
  let d;
  try {
    d = await api(`/analytics/${selectedTenant.id}/cases/${caseId}/accountability`);
  } catch (err) {
    host.innerHTML = `<div class="acc"><div class="acc-note">
      Could not load the staff accountability examination — ${esc(err.message)}.</div></div>`;
    return;
  }
  // Not applicable until fraud is declared; showing an empty panel on a live
  // investigation would imply the bank is already examining its own staff.
  if (!d.required) { host.innerHTML = ""; return; }

  const frozen = d.status === "concluded";
  const state = frozen ? (d.breached_policy ? "late" : "done")
                       : (d.overdue ? "overdue" : (d.status ? "open" : "none"));
  const stateText = frozen
    ? (d.breached_policy ? "concluded (late)" : "concluded")
    : (d.status ? (d.overdue ? "overdue" : "in progress") : "not started");

  const when = d.due_ts ? new Date(d.due_ts).toLocaleDateString() : "—";
  const clock = frozen
    ? `Concluded ${new Date(d.concluded_at).toLocaleDateString()} by ${esc(d.concluded_by)}`
    : `Due ${esc(when)} · ${d.window_days}-day board-approved window`
      + (d.days_remaining !== null && d.days_remaining !== undefined
         ? (d.days_remaining < 0 ? ` · <b>${Math.abs(d.days_remaining)} days overdue</b>`
                                 : ` · ${d.days_remaining} days left`) : "");

  const body = !d.status
    ? `<div class="acc-empty">No examination has been started. A declared fraud cannot be
         closed until staff accountability has been examined and concluded.</div>
       <button class="small-btn" id="acc-open" ${d.may_examine ? "" : "disabled"}
         >Start examination</button>`
    : `<ul class="acc-findings">${
         d.findings.length
           ? d.findings.map((f) => accFindingRow(f, frozen, d.may_examine)).join("")
           : `<li class="acc-empty">No findings recorded yet. If no one was at fault,
                record that against the person examined — an examination with no findings
                cannot be told apart from one that never happened.</li>`}</ul>
       ${frozen
         ? `<div class="acc-conclusion"><b>Conclusion.</b> ${esc(d.conclusion)}
              ${d.systemic_lapse
                ? `<div class="acc-systemic"><b>Systemic lapse recorded.</b>
                     ${esc(d.systemic_note)}</div>` : ""}</div>`
         : `<div class="acc-actions">
              <button class="small-btn ghost" id="acc-add" ${d.may_examine ? "" : "disabled"}
                >Record a finding</button>
              <button class="small-btn" id="acc-conclude" ${d.may_conclude ? "" : "disabled"}
                >Conclude examination</button>
            </div>
            ${d.may_conclude ? "" : `<div class="acc-blocked">${esc(d.conclude_requires)}</div>`}`}`;

  host.innerHTML = `<div class="acc">
    <div class="acc-head">
      <div><h6>Staff accountability</h6>
        <div class="acc-clock">${clock}</div></div>
      <span class="acc-state ${state}">${esc(stateText)}</span>
    </div>
    <div class="acc-body">${body}</div>
    <div class="acc-note">Examined separately from the return, and never before it:
      RBI requires reporting not to wait for the accountability exercise.
      ${d.examiner ? `Examining authority: ${esc(d.examiner)}.` : ""}</div>
  </div>`;

  const reload = () => { renderAccountability(caseId); renderWorkflow(caseId); };

  if ($("acc-open")) {
    $("acc-open").onclick = async () => {
      $("acc-open").disabled = true;
      try {
        await api(`/analytics/${selectedTenant.id}/cases/${caseId}/accountability/open`,
                  { method: "POST", body: { examiner: "" } });
      } catch (err) { alert(err.message); }
      reload();
    };
  }
  if ($("acc-add")) {
    $("acc-add").onclick = () => {
      $("accf-form").reset();
      $("accf-error").hidden = true;
      $("accf-vocab").innerHTML = Object.entries(d.vocabulary.findings)
        .map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
      $("accf-action").innerHTML = Object.entries(d.vocabulary.actions)
        .map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
      $("accf-dialog").showModal();
      $("accf-ref").focus();
    };
  }
  if ($("acc-conclude")) {
    $("acc-conclude").onclick = () => {
      $("accc-form").reset();
      $("accc-error").hidden = true;
      $("accc-dialog").showModal();
      $("accc-text").focus();
    };
  }
  host.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async () => {
      if (!confirm("Withdraw this finding? The withdrawal is recorded in the audit trail.")) return;
      try {
        await api(`/analytics/${selectedTenant.id}/cases/${caseId}`
                  + `/accountability/findings/${b.dataset.del}`, { method: "DELETE" });
      } catch (err) { alert(err.message); }
      reload();
    };
  });
}

$("accf-cancel").onclick = () => $("accf-dialog").close();
$("accf-save").onclick = async () => {
  const ref = $("accf-ref").value.trim(), name = $("accf-name").value.trim();
  if (!ref || !name) {
    $("accf-error").textContent = "The employee reference and name are both required — "
      + "a disciplinary process cannot act on 'the branch manager'.";
    $("accf-error").hidden = false;
    return;
  }
  try {
    await api(`/analytics/${selectedTenant.id}/cases/${accCaseId}/accountability/findings`,
              { method: "POST", body: {
                staff_ref: ref, staff_name: name,
                role_at_time: $("accf-role").value, branch: $("accf-branch").value,
                finding: $("accf-vocab").value, action_taken: $("accf-action").value,
                note: $("accf-note").value } });
    $("accf-dialog").close();
    renderAccountability(accCaseId);
  } catch (err) {
    $("accf-error").textContent = err.message;
    $("accf-error").hidden = false;
  }
};

$("accc-cancel").onclick = () => $("accc-dialog").close();
$("accc-save").onclick = async () => {
  const text = $("accc-text").value.trim();
  if (!text) {
    $("accc-error").textContent = "A written conclusion is required.";
    $("accc-error").hidden = false;
    return;
  }
  try {
    await api(`/analytics/${selectedTenant.id}/cases/${accCaseId}/accountability/conclude`,
              { method: "POST", body: {
                conclusion: text,
                systemic_lapse: $("accc-systemic").checked,
                systemic_note: $("accc-systemic-note").value } });
    $("accc-dialog").close();
    renderAccountability(accCaseId);
    renderWorkflow(accCaseId);
  } catch (err) {
    $("accc-error").textContent = err.message;
    $("accc-error").hidden = false;
  }
};

$("ack-cancel").onclick = () => $("ack-dialog").close();

$("ack-save").onclick = async () => {
  const ref = $("ack-ref").value.trim();
  if (!ref) {
    $("ack-error").textContent = "The regulator's reference is required — it is what "
      + "evidences the filing.";
    $("ack-error").hidden = false;
    return;
  }
  try {
    await api(`/analytics/${selectedTenant.id}/filings/${$("ack-dialog").dataset.filing}/acknowledge`,
              { method: "POST", body: { reference_number: ref, note: $("ack-note").value } });
    $("ack-dialog").close();
    renderFilings(filingCaseId);
    renderWorkflow(filingCaseId);
  } catch (err) {
    $("ack-error").textContent = err.message;
    $("ack-error").hidden = false;
  }
};

// ---- BR-611: say what the figures on screen are actually made of -------------------
async function refreshSourceBanner() {
  const el = $("source-banner");
  if (!el || !state.tenantId) return;
  try {
    const p = buildFilterParams();
    const d = await api(`/analytics/${state.tenantId}/data-sources?${p.toString()}`);
    if (!d.has_non_live) { el.hidden = true; el.innerHTML = ""; return; }
    const parts = d.sources.filter((r) => !r.live).map((r) =>
      `${esc(Number(r.transactions).toLocaleString())} txn / ${esc(Number(r.alerts).toLocaleString())} alert / ${esc(Number(r.cases).toLocaleString())} case`
      + ` rows of ${esc(r.label).toLowerCase()}`);
    const live = d.sources.find((r) => r.live);
    el.className = "source-banner" + (d.mixed ? " mixed" : "");
    el.innerHTML =
      `<strong>${d.mixed ? "These figures mix real and demonstration data."
                         : "These figures contain no live traffic."}</strong> `
      + `Included: ${esc(parts.join("; "))}`
      + (live ? `, alongside ${esc(Number(live.transactions).toLocaleString())} live transaction rows.` : ".")
      + ` <button type="button" class="link-btn js-only-live">Show live only</button>`;
    el.hidden = false;
    const btn = el.querySelector(".js-only-live");
    if (btn) btn.onclick = () => {
      const sel = $("f-source");
      if (sel) [...sel.options].forEach((o) => { o.selected = o.value === "live"; });
      resetDrill(); loadDashboard();
    };
  } catch (e) {
    // A failed disclosure call must not blank the dashboard behind it, but it must not
    // silently imply the data is clean either.
    el.className = "source-banner";
    el.innerHTML = "Could not determine whether these figures include demonstration data.";
    el.hidden = false;
  }
}

// ---- Lane A: what enforcing the inline lane would have cost ----------------
//
// Shadow mode records the decision it *would* have enforced and returns allow. That is
// only worth anything if somebody can look at the record, and until this panel existed
// nobody could: the data was written faithfully and had no reader.
//
// The panel's job is to be honest about three things a chart would happily obscure —
// whether the window was measured at all, whether the record is complete, and whether a
// rail is still in shadow. A rail with no traffic is not a rail with a 0% decline rate.

const laneSeverityClass = { critical: "bad", high: "warn", medium: "warn", low: "ok" };

function laneRupees(paise) {
  const r = Number(paise || 0) / 100;
  if (r >= 1e7) return "₹" + (r / 1e7).toFixed(2) + " Cr";
  if (r >= 1e5) return "₹" + (r / 1e5).toFixed(2) + " L";
  return "₹" + r.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

const laneNum = (v) => Number(v || 0).toLocaleString("en-IN");
const lanePct = (v) => (v === null || v === undefined ? "—" : v + "%");

function laneEmpty(rep) {
  // A rail the tenant routes to Lane B has no inline decisions *by design*. Saying
  // "nothing measured" there would read as a gap in coverage rather than a choice.
  const off = rep.rails_not_inline || [];
  if (off.length) {
    return `<div class="lane-empty">
      <div class="lane-empty-title">` + esc(off.join(", ")) + ` is not handled by the inline lane</div>
      <p class="muted">This rail settles in a window wide enough to score it with full
        account and network context after ingestion, which finds more than an 80&nbsp;ms
        check can. It is screened — by the near-real-time lane, not this one.</p>
    </div>`;
  }
  return `<div class="lane-empty">
    <div class="lane-empty-title">Nothing measured in this window</div>
    <p class="muted">${esc(rep.why_not || "No inline decisions were recorded.")}</p>
  </div>`;
}

function laneFilterNote(rep) {
  // Some of the selected rails are inline and some are not: report the mixture rather
  // than quietly dropping the ones that cannot appear.
  const off = rep.rails_not_inline || [];
  if (!off.length || !rep.measured) return "";
  return `<p class="muted row-hint">` + esc(off.join(", ")) +
    ` is filtered but routed to the near-real-time lane, so it contributes nothing here.
      The figures below cover the inline rails in your selection only.</p>`;
}

function laneHealthBanner(h) {
  if (!h) return "";
  // A report drawn from an incomplete record must say so before it shows a single figure.
  return `<div class="lane-alarm">
    <div class="lane-alarm-title">This report is incomplete</div>
    <p>${esc(h.meaning)}</p>
    <p class="muted">${esc(h.failed_writes_since_start)} failed write(s) since start ·
      last: ${esc(h.last_error)} · ${esc(h.scope)}</p>
  </div>`;
}

function laneKpis(rep) {
  const pi = rep.projected_impact || {};
  const lat = rep.latency_ms || {};
  const enf = rep.enforcement || {};
  const pct = pi.would_stop_pct;
  const ms = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(1) + " ms");
  const cells = [
    { l: "Decisions", v: laneNum(rep.total), k: "", sub: "" },
    { l: "Would have been stopped", k: pct > 5 ? "bad" : pct > 1 ? "warn" : "ok",
      v: lanePct(pct), sub: laneNum(pi.would_stop) + " payment(s)" },
    { l: "Still in shadow", k: enf.shadow_pct === 100 ? "ok" : "warn",
      v: lanePct(enf.shadow_pct), sub: laneNum(enf.enforced) + " enforced" },
    { l: "Decision latency p99", k: "", v: ms(lat.p99),
      sub: lat.p50 === null || lat.p50 === undefined ? "" : "p50 " + ms(lat.p50) },
  ];
  return `<div class="kpi-row lane-kpis">` + cells.map((c) =>
    `<div class="kpi ${esc(c.k)}"><div class="label">${esc(c.l)}</div>
       <div class="value">${esc(c.v)}</div>
       ${c.sub ? `<div class="sub">${esc(c.sub)}</div>` : ""}</div>`).join("") + `</div>`;
}

function laneRailTable(rows) {
  if (!rows.length) return "";
  // Constant markup chosen by a boolean - no interpolation, nothing user-supplied.
  const MODE_UNKNOWN = '<span class="muted">unknown</span>';
  const MODE_SHADOW = '<span class="lane-chip warn">shadow</span>';
  const MODE_ENFORCING = '<span class="lane-chip bad">enforcing</span>';
  const mode = (r) => (r.shadow === null || r.shadow === undefined ? MODE_UNKNOWN
    : r.shadow ? MODE_SHADOW : MODE_ENFORCING);
  return `<h5 class="lane-h">By rail</h5>
  <div class="tw"><table class="cfg-table">
    <thead><tr><th>Rail</th><th class="num">Decisions</th><th class="num">Would stop</th>
      <th class="num">Rate</th><th class="num">Value stopped</th><th>Mode</th>
      <th class="num">Budget</th></tr></thead>
    <tbody>` + rows.map((r) => `<tr>
      <td><strong>${esc(r.rail)}</strong></td>
      <td class="num">${esc(laneNum(r.total))}</td>
      <td class="num">${esc(laneNum(r.stopped))}</td>
      <td class="num">${esc(lanePct(r.stopped_pct))}</td>
      <td class="num">${esc(laneRupees(r.stopped_paise))}</td>
      <td>` + mode(r) + `</td>
      <td class="num">${r.budget_ms === null || r.budget_ms === undefined
          ? "—" : esc(r.budget_ms) + " ms"}</td>
    </tr>`).join("") + `</tbody></table></div>`;
}

function laneRuleTable(rows) {
  if (!rows.length) {
    return `<h5 class="lane-h">By rule</h5>
      <p class="muted">No rule matched in this window. That is a measured result, not an
      absence of screening — the decisions above did run the inline rule set.</p>`;
  }
  const top = rows.slice(0, 12);
  const max = Math.max.apply(null, top.map((r) => r.matches).concat([1]));
  return `<h5 class="lane-h">By rule <span class="muted">— what drives the declines</span></h5>
  <div class="tw"><table class="cfg-table">
    <thead><tr><th>Rule</th><th>Family</th><th class="num">Matches</th>
      <th class="num">Led to a stop</th><th>Share</th></tr></thead>
    <tbody>` + top.map((r) => `<tr>
      <td><strong>${esc(r.rule_id)}</strong></td>
      <td>${esc(r.family || "—")}</td>
      <td class="num">${esc(laneNum(r.matches))}</td>
      <td class="num">${esc(laneNum(r.led_to_stop))}</td>
      <td><span class="lane-bar" style="width:${esc(Math.round(100 * r.matches / max))}%"></span></td>
    </tr>`).join("") + `</tbody></table></div>`;
}

const LANE_OUTCOME_LABEL = {
  clean: "ran, nothing matched",
  matched: "at least one rule matched",
  budget_exceeded: "budget expired mid-evaluation",
  store_unavailable: "counters unreadable",
  no_policy: "no policy for the rail",
  not_screened: "nothing was screened",
};

function laneOutcomeStrip(rep) {
  const o = rep.by_outcome || {};
  // not_screened is called out separately: it is the one outcome meaning the control did
  // not run, and it must never sit alongside "clean" as though it were a pass.
  const unscreened = Number(o.not_screened || 0);
  const warn = unscreened
    ? `<div class="lane-alarm">
         <div class="lane-alarm-title">${esc(laneNum(unscreened))} decision(s) screened nothing</div>
         <p>No inline rule was evaluated for these payments — an empty or unloaded rule
            catalogue. They are not clean passes, and the rail's fail policy decided what
            the caller was told.</p>
       </div>` : "";
  const chips = Object.keys(o).map((k) =>
    `<span class="lane-chip${k === "not_screened" && o[k] ? " bad" : ""}">
       <b>${esc(laneNum(o[k]))}</b> ${esc(LANE_OUTCOME_LABEL[k] || k)}</span>`).join("");
  return warn + `<div class="lane-chips">` + chips + `</div>`;
}

function laneSeverityStrip(rep) {
  const by = rep.by_severity || {};
  const keys = Object.keys(by).filter((k) => by[k] > 0);
  if (!keys.length) return "";
  return `<div class="lane-chips">` + keys.map((k) =>
    `<span class="lane-chip ${esc(laneSeverityClass[k] || "")}">
       <b>${esc(laneNum(by[k]))}</b> ${esc(k)}</span>`).join("") + `</div>`;
}

async function loadLanePanel() {
  const panel = $("lane-panel");
  if (!panel) return;
  if (!selectedTenant || currentDash !== "realtime") { panel.hidden = true; return; }
  panel.hidden = false;
  const days = $("lane-window") ? $("lane-window").value : "7";
  // The rail filter above this panel governs the dashboard; it must govern the panel too.
  // Ignoring it would leave a control sitting over numbers it does not affect.
  const q = new URLSearchParams({ days });
  (drillFilterParams().getAll("rails") || []).forEach((r) => q.append("rails", r));
  const body = $("lane-body");
  body.innerHTML = `<p class="muted">Loading inline decisions…</p>`;
  try {
    const rep = await api(`/decisions/${selectedTenant.id}/shadow?${q}`);
    const rails = rep.by_rail || [];
    const inShadow = rails.filter((r) => r.shadow === true).length;
    $("lane-mode").textContent = rails.length
      ? `· ${inShadow} of ${rails.length} rail(s) in shadow` : "";
    if (!rep.measured) {
      body.innerHTML = laneHealthBanner(rep.record_health) + laneEmpty(rep);
      return;
    }
    body.innerHTML = laneHealthBanner(rep.record_health)
      + laneFilterNote(rep)
      + laneKpis(rep)
      + `<p class="muted row-hint">${esc((rep.projected_impact || {}).note || "")}</p>`
      + laneOutcomeStrip(rep)
      + laneSeverityStrip(rep)
      + laneRailTable(rails)
      + laneRuleTable(rep.by_rule || []);
  } catch (err) {
    body.innerHTML = `<div class="lane-alarm"><div class="lane-alarm-title">
      Could not load inline decisions</div><p>${esc(err.message)}</p></div>`;
  }
}

document.getElementById("lane-window")?.addEventListener("change", loadLanePanel);
