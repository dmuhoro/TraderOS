"use strict";

const $ = (id) => document.getElementById(id);

const STEP_ORDER = [
  "start",
  "preflight",
  "broker_check",
  "market_data_check",
  "paper_trading",
  "performance_review",
  "strategy_promotion",
  "controlled_live",
  "shutdown",
  "session_report",
];

const state = {
  apiKey: null,
  sessionToken: null,
  role: null,
  authRequired: false,
  sseMode: false,
  refreshTimer: null,
};

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmt(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "-";
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function fmtMoney(n) {
  const v = Number(n);
  if (Number.isNaN(v)) return "-";
  const cls = v > 0 ? "pos" : v < 0 ? "neg" : "";
  return `<span class="${cls}">$${fmt(v)}</span>`;
}

async function api(path, opts = {}) {
  const headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
  if (state.sessionToken) headers["X-Session-Token"] = state.sessionToken;
  else if (state.apiKey) headers["X-API-Key"] = state.apiKey;
  if (opts.body) headers["Content-Type"] = "application/json";
  const resp = await fetch(path, {
    method: opts.method || "GET",
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (resp.status === 401) {
    logout();
    throw new Error("unauthorized");
  }
  let data = null;
  try {
    data = await resp.json();
  } catch (_) {
    data = null;
  }
  if (!resp.ok) {
    const msg = (data && data.error && data.error.message) || `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return data;
}

function loadAuthState() {
  // WP8: the dashboard keeps a short-lived server-side session token in the
  // closing page session (sessionStorage), never a static API key. logout()
  // clears it and the server revokes it, so nothing roamable persists.
  try {
    state.apiKey = null;
    state.sessionToken = sessionStorage.getItem("traderos.session");
  } catch (_) {
    state.sessionToken = null;
  }
}

function saveSession(token) {
  state.sessionToken = token;
  state.apiKey = null;
  try {
    sessionStorage.setItem("traderos.session", token);
  } catch (_) {
    /* non-persistent storage unavailable: keep in-memory only */
  }
}

function logout() {
  const revoked = state.sessionToken;
  state.sessionToken = null;
  state.apiKey = null;
  try {
    sessionStorage.removeItem("traderos.session");
  } catch (_) {
    /* no-op */
  }
  state.role = null;
  state.authRequired = false;
  if (revoked) {
    api("/v1/auth/logout", { method: "POST" }).catch(() => {});
  }
  renderAuth();
  clearPanels();
}

async function refreshAuth() {
  try {
    const info = await api("/v1/auth/me");
    state.role = info.authenticated ? info.role : null;
    state.authRequired = Boolean(info.required);
  } catch (_) {
    state.role = null;
  }
  renderAuth();
  return state.role;
}

function renderAuth() {
  $("key-input").classList.toggle("hidden", state.authRequired && state.role !== null);
  $("pw-input").classList.toggle("hidden", state.authRequired && state.role !== null);
  $("login-btn").classList.toggle("hidden", state.role !== null || !state.authRequired);
  $("logout-btn").classList.toggle("hidden", state.role === null);
  const roleBadge = $("role-badge");
  if (state.role) {
    roleBadge.textContent = `role: ${state.role}`;
    roleBadge.classList.remove("badge-hidden");
  } else {
    roleBadge.textContent = "role: -";
    roleBadge.classList.add("badge-hidden");
  }
  const canWrite = !state.authRequired || ["operate", "admin"].includes(state.role);
  const isAdmin = !state.authRequired || state.role === "admin";
  $("wf-advance").disabled = !canWrite;
  $("strat-create").disabled = !canWrite;
  $("ks-engage").disabled = !isAdmin;
  $("ks-disengage").disabled = !isAdmin;
}

function setBadge(id, text, kind) {
  const el = $(id);
  el.textContent = text;
  el.className = "badge" + (kind ? ` badge-${kind}` : "");
}

function renderWorkflow(wf) {
  $("wf-status").textContent = wf.status || "idle";
  $("wf-session").textContent = wf.session_id || "-";
  const current = wf.current_step;
  $("wf-steps").innerHTML = STEP_ORDER.map((step) => {
    let cls = "step";
    if (STEP_ORDER.indexOf(current) > STEP_ORDER.indexOf(step)) cls += " done";
    if (step === current) cls += " current";
    return `<span class="${cls}">${step}</span>`;
  }).join("");
  const history = wf.history || [];
  $("wf-history").innerHTML = history
    .slice()
    .reverse()
    .map(
      (h) =>
        `<li>${esc(h.step)} → ${esc(h.result)} (${esc(h.ok ? "ok" : "fail")}) by ${esc(h.actor)} — ${esc(h.timestamp || "")}</li>`
    )
    .join("");
  $("wf-result").textContent = "";
}

function renderPortfolio(p) {
  $("pf-equity").innerHTML = fmtMoney(p.total_equity);
  $("pf-cash").innerHTML = fmtMoney(p.cash);
  $("pf-posval").innerHTML = fmtMoney(p.positions_value);
  $("pf-pnl").innerHTML = fmtMoney(p.total_pnl);
}

function renderEquityCurve(points) {
  const canvas = $("equity-chart");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 400;
  const h = 140;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  const vals = (points || []).map((p) => Number(p.equity));
  if (vals.length < 2) {
    ctx.fillStyle = "#8b949e";
    ctx.font = "12px monospace";
    ctx.fillText("not enough history", 8, 20);
    return;
  }
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  ctx.strokeStyle = "#2f81f7";
  ctx.lineWidth = 2;
  ctx.beginPath();
  vals.forEach((v, i) => {
    const x = (i / (vals.length - 1)) * w;
    const y = h - 8 - ((v - min) / span) * (h - 24);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function renderRisk(ks) {
  const engaged = Boolean(ks.engaged);
  $("ks-state").textContent = engaged ? "ENGAGED" : "closed";
  $("ks-state").style.color = engaged ? "var(--red)" : "var(--green)";
  $("ks-circuit").textContent = String(ks.circuit_open);
  $("ks-failures").textContent = String(ks.consecutive_failures);
  setBadge("ks-badge", engaged ? "kill switch: ENGAGED" : "kill switch: closed", engaged ? "danger" : "ok");
  if (ks.reason) $("ks-state").title = ks.reason;
}

function renderReadiness(rd) {
  $("rd-ready").textContent = String(Boolean(rd.ready));
  $("rd-checks").textContent = JSON.stringify(rd.checks || {}, null, 2);
}

function renderPreflight(pf) {
  $("pf-check").textContent = pf.passed ? "passed" : `failed: ${(pf.failures || []).join(", ")}`;
  $("pf-check").style.color = pf.passed ? "var(--green)" : "var(--red)";
}

function renderOperationalHealth(orch) {
  const ha = (orch.operational && orch.operational.ha) || { configured: false };
  $("ops-ha").textContent = ha.configured
    ? (ha.leading ? "leader" : "standby")
    : "not configured";
  $("ops-ha").style.color = ha.configured && ha.leading ? "var(--green)" : ha.configured ? "var(--amber)" : "var(--muted)";
  const lease = ha.last_lease;
  $("ops-lease").textContent = lease ? `${lease.action} ${lease.ts}` : (ha.configured ? "no lease" : "-");
  $("ops-lease").title = ha.configured ? `owner: ${ha.owner}` : "";
  const oc = (orch.operational && orch.operational.oncall) || { configured: false };
  $("ops-oncall").textContent = oc.configured ? `routing ≥ ${oc.min_severity}` : "not configured";
  $("ops-oncall").style.color = oc.configured ? "var(--green)" : "var(--muted)";
  $("ops-oncall-del").textContent = `${oc.delivered} / ${oc.delivery_failed}`;
  $("ops-user").textContent = orch.operational && orch.operational.trading_user_id
    ? orch.operational.trading_user_id
    : "unattributed";
  const sec = orch.secret_rotation;
  const metrics = orch.metrics || {};
  const accessCount =
    Number(metrics["secret.accessed.read.cached"] || 0) + Number(metrics["secret.accessed.read.provider"] || 0);
  $("ops-secrets").textContent = sec ? `rotator: ${sec.total_secrets} secret(s)` : "not configured";
  $("ops-versions").textContent = sec ? String(Object.keys(sec.versions || {}).length) : "-";
  $("ops-access").textContent = accessCount ? `${accessCount} access(es)` : "-";
}

function renderPositions(data) {
  const user = data.trading_user_id || "-";
  const rows = data.positions || [];
  $("positions-body").innerHTML = rows.length
    ? rows
        .map(
          (p) =>
            `<tr><td>${esc(user)}</td><td>${esc(p.market_id)}</td><td>${esc(fmt(p.quantity))}</td><td>${esc(fmt(p.entry_price))}</td>` +
            `<td>${esc(fmt(p.current_price))}</td><td>${fmtMoney(p.pnl)}</td><td>${fmtMoney(p.realized_pnl)}</td></tr>`
        )
        .join("")
    : `<tr><td colspan="7" class="empty">no positions</td></tr>`;
}

function renderOrders(data) {
  const rows = data.orders || [];
  $("orders-body").innerHTML = rows.length
    ? rows
        .map(
          (o) =>
            `<tr><td>${esc(o.market_id || o.symbol || "-")}</td><td>${esc(o.side || "-")}</td>` +
            `<td>${esc(fmt(o.quantity))}</td><td>${esc(o.order_type || o.type || "-")}</td><td>${esc(o.status || "-")}</td></tr>`
        )
        .join("")
    : `<tr><td colspan="5" class="empty">no open orders</td></tr>`;
}

function renderTrades(data) {
  const user = data.trading_user_id || "-";
  const rows = data.trades || [];
  $("trades-body").innerHTML = rows.length
    ? rows
        .map(
          (t) =>
            `<tr><td>${esc(t.filled_at || t.created_at || "")}</td><td>${esc(user)}</td><td>${esc(t.market_id)}</td><td>${esc(t.side)}</td>` +
            `<td>${esc(fmt(t.quantity))}</td><td>${esc(fmt(t.filled_price || t.price))}</td><td>${esc(t.status)}</td></tr>`
        )
        .join("")
    : `<tr><td colspan="7" class="empty">no trades</td></tr>`;
}

function renderStrategies(data) {
  const rows = data.strategies || [];
  const select = $("research-strategy");
  const active = rows.filter((s) => s.status === "active").map((s) => s.name);
  const current = select.value;
  const choices = active.length ? active : rows.map((s) => s.name);
  if (choices.length) {
    select.innerHTML = choices.map((n) => `<option value="${esc(String(n))}">${esc(String(n))}</option>`).join("");
    if (choices.includes(current)) select.value = current;
    if (select.selectedIndex === -1) select.selectedIndex = 0;
  }
  const canWrite = !state.authRequired || ["operate", "admin"].includes(state.role);
  $("strategies-body").innerHTML = rows.length
    ? rows
        .map(
          (s) =>
            `<tr><td>${esc(s.name)}</td><td>${esc(s.template)}</td><td>${esc(s.status)}</td><td>${esc(s.version)}</td><td>` +
            (canWrite
              ? [
                  ["enable", s.status === "active" ? "disabled" : ""],
                  ["disable", s.status === "disabled" ? "disabled" : ""],
                  ["promote", ""],
                  ["archive", ""],
                ]
                  .map(
                    ([action, disabled]) =>
                      `<button class="btn" data-strat-action="${action}" data-strat-name="${esc(s.name)}" ${disabled}>${action}</button>`
                  )
                  .join("")
              : `<span class="empty">read-only</span>`) +
            `</td></tr>`
        )
        .join("")
    : `<tr><td colspan="5" class="empty">no strategies</td></tr>`;
}

function renderReport(text) {
  $("report-body").textContent = text || "no session report yet";
}

function renderMarketOverview(data) {
  const rows = (data && data.markets) || [];
  $("market-body").innerHTML = rows.length
    ? rows
        .map(
          (m) =>
            `<tr>
              <td>${esc(m.symbol)}</td>
              <td>${fmt(m.last)}</td>
              <td class="${Number(m.change_pct) >= 0 ? "pos" : "neg"}">${m.change_pct != null ? Number(m.change_pct).toFixed(2) + "%" : "-"}</td>
              <td>${fmt(m.volume)}</td>
              <td>${m.sma20 != null ? fmt(m.sma20) : "-"}</td>
              <td>${m.sma50 != null ? fmt(m.sma50) : "-"}</td>
              <td>${m.rsi != null ? fmt(m.rsi) : "-"}</td>
              <td>${m.atr != null ? fmt(m.atr) : "-"}</td>
              <td>
                <span class="badge ${m.state === "uptrend" ? "badge-ok" : m.state === "downtrend" ? "badge-danger" : "badge-idle"}">${esc(m.state)}</span>
              </td>
            </tr>`
        )
        .join("")
    : `<tr><td colspan="9" class="empty">no market data</td></tr>`;
}

function researchSymbolSelect() {
  return $("research-symbol");
}

function populateResearchSymbols(symbols) {
  const sel = researchSymbolSelect();
  const current = sel.value;
  sel.innerHTML = symbols.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
  if (symbols.includes(current)) sel.value = current;
  if (sel.selectedIndex === -1 && symbols.length) sel.selectedIndex = 0;
}

function renderResearchBacktest(data) {
  const m = data || {};
  const rows = [
    ["Strategy", m.strategy],
    ["Symbol", m.symbol],
    ["Candles", m.candles],
    ["Total return", m.total_return != null ? Number(m.total_return).toFixed(4) : "-"],
    ["Sharpe", m.sharpe_ratio != null ? Number(m.sharpe_ratio).toFixed(4) : "-"],
    ["Sortino", m.sortino_ratio != null ? Number(m.sortino_ratio).toFixed(4) : "-"],
    ["Calmar", m.calmar_ratio != null ? Number(m.calmar_ratio).toFixed(4) : "-"],
    ["Max drawdown", m.max_drawdown != null ? Number(m.max_drawdown).toFixed(4) : "-"],
    ["Win rate", m.win_rate != null ? Number(m.win_rate).toFixed(4) : "-"],
    ["Profit factor", m.profit_factor != null ? Number(m.profit_factor).toFixed(4) : "-"],
    ["Expectancy", m.expectancy != null ? Number(m.expectancy).toFixed(4) : "-"],
  ];
  $("research-metrics").innerHTML = `<table>
    <thead><tr><th>Metric</th><th>Value</th></tr></thead>
    <tbody>${rows.map(([k, v]) => `<tr><td>${esc(String(k))}</td><td>${esc(String(v))}</td></tr>`).join("")}</tbody>
  </table>`;
  $("research-result").textContent = data && data.strategy ? `backtest complete` : "";
}

function renderResearchObservations(data) {
  const obs = (data && data.observations) || [];
  $("research-body").innerHTML = obs.length
    ? obs
        .map(
          (o) =>
            `<tr>
              <td>${esc((o.timestamp || "").slice(0, 19).replace("T", " "))}</td>
              <td>${esc(o.symbol)}</td>
              <td>${esc(o.content)}</td>
              <td>${esc((o.tags || []).join(", "))}</td>
            </tr>`
        )
        .join("")
    : `<tr><td colspan="4" class="empty">no observations yet</td></tr>`;
}

function renderResearch(data) {
  let markets = data && data.markets;
  if (markets) renderMarketOverview(data);
  const symbols = data && data.symbols;
  if (symbols) {
    populateResearchSymbols(symbols);
  }
}

function appendEvent(evt) {
  const type = evt.type || "state";
  const data = typeof evt.data === "string" ? evt.data : JSON.stringify(evt.data || {});
  const div = document.createElement("div");
  div.innerHTML = `<span class="ty">[${esc(type)}]</span> <span class="ts">${esc(evt.timestamp || "")}</span> ${esc(data)}`;
  const log = $("event-log");
  log.prepend(div);
  while (log.children.length > 200) log.lastChild.remove();
}

async function refreshPanels() {
  if (state.authRequired && !state.role) return;
  const calls = [
    ["/v1/workflow", renderWorkflow],
    ["/v1/portfolio", renderPortfolio],
    ["/v1/equity-curve", (d) => renderEquityCurve(d.points)],
    ["/v1/kill-switch", renderRisk],
    ["/v1/readiness", renderReadiness],
    ["/v1/preflight", renderPreflight],
    ["/v1/positions", renderPositions],
    ["/v1/orders", renderOrders],
    ["/v1/trades", renderTrades],
    ["/v1/strategies", renderStrategies],
    ["/v1/market/overview", renderMarketOverview],
    ["/v1/market/symbols", (d) => populateResearchSymbols(d.symbols)],
    ["/v1/research/observations", renderResearchObservations],
  ];
  const results = await Promise.allSettled(
    calls.map(async ([path, fn]) => {
      const data = await api(path);
      fn(data);
    })
  );
  for (const r of results) {
    if (r.status === "rejected") {
      appendEvent({ type: "api_error", data: String(r.reason) });
    }
  }
  const orch = await api("/v1/orchestrator/status").catch(() => null);
  if (orch) {
    setBadge("orch-badge", `orchestrator: ${orch.running ? "running" : "idle"}`, orch.running ? "ok" : "idle");
    $("mode-badge").textContent = `mode: ${orch.mode || "-"}`;
    renderOperationalHealth(orch);
  }
  $("last-refresh").textContent = `last refresh: ${new Date().toLocaleTimeString()}`;
}

function clearPanels() {
  const panels = [
    ["positions-body", "no positions"],
    ["orders-body", "no open orders"],
    ["trades-body", "no trades"],
    ["strategies-body", "no strategies"],
  ];
  for (const [id, text] of panels) $(id).innerHTML = `<tr><td colspan="5" class="empty">${text}</td></tr>`;
  $("pf-equity").textContent = "-";
  $("pf-cash").textContent = "-";
  $("pf-posval").textContent = "-";
  $("pf-pnl").textContent = "-";
}

async function connectSse() {
  // EventSource cannot send the X-API-Key header, so when auth is required we
  // mint a short-lived single-purpose token (via the header-authenticated
  // fetch seam) and subscribe with it as a query param. Without a token the
  // stream stays open-endpoint, matching the open-auth deployment.
  if (state.authRequired || state.role) {
    try {
      const sub = await api("/v1/events/token");
      if (sub && sub.token) {
        connectSseWithUrl(`/v1/events?token=${encodeURIComponent(sub.token)}`);
      } else {
        fallbackToPolling();
      }
      return;
    } catch (_) {
      fallbackToPolling();
      return;
    }
  }
  connectSseWithUrl("/v1/events");
}

function connectSseWithUrl(url) {
  let failures = 0;
  const source = new EventSource(url);
  source.onopen = () => {
    failures = 0;
    state.sseMode = true;
    $("conn-state").textContent = "SSE: connected";
    $("conn-state").style.color = "var(--green)";
  };
  source.onmessage = (event) => {
    try {
      appendEvent(JSON.parse(event.data));
    } catch (_) {
      appendEvent({ type: "message", data: event.data });
    }
    refreshPanels();
  };
  source.onerror = () => {
    failures += 1;
    fallbackToPolling();
    if (failures > 0 && !state.refreshTimer) {
      state.refreshTimer = setInterval(refreshPanels, 5000);
    }
    if (failures >= 3) {
      source.close();
    }
  };
}

function fallbackToPolling() {
  $("conn-state").textContent = "SSE: disconnected (polling)";
  $("conn-state").style.color = "var(--amber)";
  state.sseMode = false;
  if (!state.refreshTimer) {
    state.refreshTimer = setInterval(refreshPanels, 5000);
  }
}

async function advanceWorkflow() {
  const wf = await api("/v1/workflow");
  const next = wf.next_step;
  if (!next) {
    $("wf-result").textContent = "workflow complete or not started";
    return;
  }
  const body = { step: next, actor: $("wf-actor").value || "operator" };
  if (next === "strategy_promotion") {
    const name = prompt("Strategy to promote:");
    if (!name) return;
    body.strategy = name;
  }
  try {
    const out = await api("/v1/workflow/advance", { method: "POST", body });
    $("wf-result").textContent = out.ok ? `advanced to ${out.step}` : `failed: ${out.detail}`;
    $("wf-result").style.color = out.ok ? "var(--green)" : "var(--red)";
    refreshPanels();
  } catch (err) {
    $("wf-result").textContent = String(err.message);
    $("wf-result").style.color = "var(--red)";
  }
}

function wireEvents() {
  $("login-btn").addEventListener("click", async () => {
    // WP8: authenticate with username/password against /v1/auth/login and
    // hold the short-lived server-side session token. No static API key is
    // ever persisted in localStorage.
    const username = $("key-input").value.trim();
    const password = $("pw-input").value;
    if (!username || !password) return;
    try {
      const out = await api("/v1/auth/login", {
        method: "POST",
        body: { username, password },
      });
      saveSession(out.token);
      const info = await api("/v1/auth/me");
      state.role = info.authenticated ? info.role : null;
      state.authRequired = Boolean(info.required);
      renderAuth();
      $("pw-input").value = "";
      refreshPanels();
} catch (err) {
      logout();
      appendEvent({ type: "login_error", data: String(err.message) });
      $("pw-input").value = "";
    }
  });
  $("logout-btn").addEventListener("click", () => {
    logout();
  });
  $("wf-advance").addEventListener("click", advanceWorkflow);
  $("ks-engage").addEventListener("click", () => {
    api("/v1/kill-switch/engage", { method: "POST" })
      .then(refreshPanels)
      .catch((err) => appendEvent({ type: "error", data: err.message }));
  });
  $("ks-disengage").addEventListener("click", () => {
    api("/v1/kill-switch/disengage", { method: "POST" })
      .then(refreshPanels)
      .catch((err) => appendEvent({ type: "error", data: err.message }));
  });
  $("strat-create").addEventListener("click", () => {
    api("/v1/strategies", {
      method: "POST",
      body: { name: $("strat-name").value.trim(), template: $("strat-template").value },
    })
      .then(() => {
        $("strat-name").value = "";
        refreshPanels();
      })
      .catch((err) => appendEvent({ type: "error", data: err.message }));
  });
  $("strategies-body").addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-strat-action]");
    if (!btn) return;
    api(`/v1/strategies/${encodeURIComponent(btn.dataset.stratName)}/${btn.dataset.stratAction}`, {
      method: "POST",
      body: {},
    })
      .then(refreshPanels)
      .catch((err) => appendEvent({ type: "error", data: err.message }));
  });
  $("report-load").addEventListener("click", async () => {
    const fmt = $("report-fmt").value;
    const url = fmt === "markdown" ? "/v1/reports/session?fmt=markdown" : "/v1/reports/session";
    const headers = {};
    if (state.sessionToken) headers["X-Session-Token"] = state.sessionToken;
    else if (state.apiKey) headers["X-API-Key"] = state.apiKey;
    const resp = await fetch(url, { headers });
    const text = await resp.text();
    renderReport(text);
  });
  $("research-run").addEventListener("click", () => {
    const symbol = $("research-symbol").value;
    const strategy = $("research-strategy").value;
    if (!symbol || !strategy) return;
    $("research-result").textContent = "running ...";
    api("/v1/research/backtest", {
      method: "POST",
      body: { symbol, strategy },
    })
      .then((data) => {
        renderResearchBacktest(data);
        $("research-result").textContent = `backtest complete on ${data.symbol}`;
      })
      .catch((err) => {
        $("research-result").textContent = `backtest failed: ${err.message}`;
        $("research-result").style.color = "var(--red)";
        appendEvent({ type: "error", data: err.message });
      });
  });
  $("research-log").addEventListener("click", async () => {
    const symbol = $("research-symbol-new").value.trim() || $("research-symbol").value;
    const content = $("research-content").value.trim();
    if (!symbol || !content) return;
    try {
      await api("/v1/research/observations", { method: "POST", body: { symbol, content } });
      $("research-content").value = "";
      const obs = await api("/v1/research/observations");
      renderResearchObservations(obs);
    } catch (err) {
      appendEvent({ type: "error", data: err.message });
    }
  });
}

async function init() {
  loadAuthState();
  wireEvents();
  await refreshAuth();
  if (!state.authRequired || state.role) {
    await refreshPanels();
  }
  connectSse();
}

document.addEventListener("DOMContentLoaded", init);
