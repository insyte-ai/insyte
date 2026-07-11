/* Insyte Studio — self-contained SPA (no build step, no external dependencies).
 *
 * Talks to the FastAPI backend. Streams analysis progress over SSE. Renders result cards with
 * Overview / Chart / Data / SQL / Method / Warnings tabs and inline SVG charts. This ships in
 * the wheel so end users need no Node.js; contributors can replace it with a Vite/React build.
 */
(function () {
  "use strict";

  const API = "/api";
  const state = { status: null, metrics: null, conversations: [], conversationId: null };

  // ---- helpers ---------------------------------------------------------------------------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null) continue;
        if (k === "class") node.className = v;
        else if (k === "html") node.innerHTML = v;
        else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
        else node.setAttribute(k, v);
      }
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      node.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  const $ = (sel) => document.querySelector(sel);
  async function getJSON(p) {
    const r = await fetch(API + p);
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }
  async function postJSON(p, body) {
    const r = await fetch(API + p, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }
  function compact(n) {
    if (n == null || isNaN(n)) return "—";
    const a = Math.abs(n);
    if (a >= 1e7) return (n / 1e7).toFixed(2) + " Cr";   // crore
    if (a >= 1e5) return (n / 1e5).toFixed(2) + " L";    // lakh
    if (a >= 1e3) return (n / 1e3).toFixed(1) + " K";    // thousand
    return Number.isInteger(n) ? String(n) : n.toFixed(2);
  }
  function fmtValue(v, format) {
    if (v == null) return "—";
    if (format === "percent") return (v * 100).toFixed(1) + "%";
    if (format === "currency") return "₹" + compact(v);
    return compact(v);
  }

  // ---- theme -----------------------------------------------------------------------------
  function initTheme() {
    const saved = localStorage.getItem("insyte-theme");
    const theme = saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.setAttribute("data-theme", theme);
  }
  function toggleTheme() {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("insyte-theme", next);
    const btn = $("#theme-btn");
    if (btn) btn.textContent = next === "dark" ? "☀" : "☾";
  }

  // ---- shell -----------------------------------------------------------------------------
  function renderShell() {
    const app = $("#app");
    app.innerHTML = "";
    app.append(renderHeader(), renderBody(), renderStatusBar());
  }

  function renderHeader() {
    const s = state.status;
    const connected = s && s.database && s.database.url_configured;
    return el("div", { class: "header" },
      el("button", { class: "icon-btn", title: "Toggle sidebar", onClick: () => $(".body").classList.toggle("collapsed") }, "☰"),
      el("div", { class: "brand-wrap" },
        el("img", { class: "brand-logo logo-dark", src: "/assets/logo-dark.png", alt: "Insyte" }),
        el("img", { class: "brand-logo logo-light", src: "/assets/logo-light.png", alt: "Insyte" })
      ),
      el("span", { class: "project" }, s ? s.project : "…"),
      el("span", { class: "spacer" }),
      el("span", { class: "badge" + (connected ? "" : " warn") },
        el("span", { class: "status-dot" }),
        s ? (s.database.type + (connected ? " · Connected" : " · No URL")) : "…"
      ),
      el("button", { id: "theme-btn", class: "icon-btn", title: "Theme", onClick: toggleTheme },
        document.documentElement.getAttribute("data-theme") === "dark" ? "☀" : "☾")
    );
  }

  function navButton(label, icon, route) {
    const active = currentRoute() === route;
    return el("button", { class: "nav-item" + (active ? " active" : ""), onClick: () => (location.hash = "#/" + route) }, icon + "  " + label);
  }

  function renderBody() {
    const body = el("div", { class: "body" });
    body.append(renderSidebar(), el("div", { class: "main" }, el("div", { class: "main-inner", id: "view" })));
    return body;
  }

  function renderSidebar() {
    const recent = el("div", {},
      ...state.conversations.slice(0, 12).map((c) =>
        el("button", { class: "recent-item", title: c.title, onClick: () => openConversation(c.id) }, c.title)
      )
    );
    return el("div", { class: "sidebar" },
      el("button", { class: "new-chat", onClick: newChat }, "+  New analysis"),
      el("div", { class: "nav-heading" }, "Workspace"),
      navButton("Chat", "💬", "chat"),
      el("div", { class: "nav-heading" }, "Recent"),
      recent
    );
  }

  function renderStatusBar() {
    const s = state.status;
    if (!s) return el("div", { class: "statusbar" }, "Loading…");
    const scan = s.schema.last_scan ? new Date(s.schema.last_scan).toLocaleString() : "not scanned";
    return el("div", { class: "statusbar" },
      el("span", {}, s.analytics_mode + " mode"),
      el("span", {}, s.schema.tables + " tables"),
      el("span", {}, "Last scan: " + scan),
      el("span", {}, "Read-only")
    );
  }

  // ---- router ----------------------------------------------------------------------------
  function currentRoute() {
    const r = (location.hash || "#/chat").replace(/^#\//, "");
    return ["chat", "schema", "metrics", "history", "settings"].includes(r) ? r : "chat";
  }
  function route() {
    renderShell();
    const view = $("#view");
    view.innerHTML = "";
    const r = currentRoute();
    if (r === "chat") renderChat(view);
    else if (r === "schema") renderSchemaPage(view);
    else if (r === "metrics") renderMetricsPage(view);
    else if (r === "history") renderHistoryPage(view);
    else if (r === "settings") renderSettingsPage(view);
  }

  // ---- chat ------------------------------------------------------------------------------
  function suggestions() {
    const m = state.metrics;
    if (!m || !m.metrics || !m.metrics.length) return [];
    const pick = (arr, prefs) => {
      for (const p of prefs) { const h = arr.find((x) => (x.name || "").toLowerCase().includes(p)); if (h) return h; }
      return arr[0];
    };
    const metric = pick(m.metrics, ["grand_total", "total_amount", "revenue", "sales", "order_count", "amount"]);
    const ml = (metric.label || metric.name.replace(/_/g, " ")).toLowerCase();
    const out = ["What is the " + ml + " last month?", "Monthly " + ml + " trend"];
    const dims = m.dimensions || [];
    if (dims.length) {
      const d = pick(dims, ["city", "category", "payment_method", "brand", "type", "status"]);
      const dl = (d.label || d.name.replace(/_/g, " ")).toLowerCase();
      out.splice(1, 0, ml + " by " + dl);
      out.push("What is the expected " + ml + " this year?");
    }
    return out;
  }

  function renderChat(view) {
    const project = state.status ? state.status.project : "your data";
    const hero = el("div", { class: "hero", id: "chat-hero" },
      el("h1", {}, "What would you like to know?"),
      el("p", {}, "Ask anything about your " + project + " data.")
    );
    const log = el("div", { id: "chat-log" });
    const input = el("input", { id: "composer-input", placeholder: "Ask an analytics question…", autocomplete: "off" });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") submitQuestion(input.value); });
    const composer = el("div", { class: "composer" },
      input,
      el("button", { onClick: () => submitQuestion(input.value) }, "Ask")
    );
    const samples = el("div", { class: "suggestions", id: "chat-samples" },
      ...suggestions().slice(0, 4).map((q) => el("button", { class: "suggestion", onClick: () => submitQuestion(q) }, q))
    );

    // Empty state (heading + composer + samples, centered). Once there are messages the
    // container loses .empty: the log fills the space and the composer sticks to the bottom.
    const chat = el("div", { class: "chat empty", id: "chat" }, hero, log, composer, samples);
    view.append(chat);
    if (state.conversationId) loadMessages(state.conversationId, log);
    else setTimeout(() => input.focus(), 0);
  }

  function loadMessages(id, log) {
    getJSON("/conversations/" + id).then((data) => {
      log.innerHTML = "";
      if (data.messages && data.messages.length) setChatActive();
      for (const m of data.messages) {
        if (m.role === "user") appendUser(log, m.content);
        else appendAssistantText(log, m.content);
      }
    });
  }

  function setChatActive() {
    const chat = $("#chat");
    if (chat) chat.classList.remove("empty");
  }

  function newChat() {
    postJSON("/conversations", { title: "New analysis" })
      .then((c) => {
        state.conversationId = c.id;
        refreshConversations();
        location.hash = "#/chat";
        route();
        $("#composer-input") && $("#composer-input").focus();
      })
      .catch(showError);
  }

  function openConversation(id) {
    state.conversationId = id;
    if (currentRoute() !== "chat") { location.hash = "#/chat"; }
    else { route(); }
  }

  function ensureConversation() {
    if (state.conversationId) return Promise.resolve(state.conversationId);
    return postJSON("/conversations", { title: "New analysis" }).then((c) => {
      state.conversationId = c.id;
      refreshConversations();
      return c.id;
    });
  }

  function submitQuestion(text) {
    text = (text || "").trim();
    if (!text) return;
    setChatActive();
    const log = $("#chat-log");
    appendUser(log, text);
    const input = $("#composer-input");
    if (input) input.value = "";

    ensureConversation()
      .then((cid) => postJSON("/conversations/" + cid + "/messages", { content: text }))
      .then((job) => streamAnalysis(log, job))
      .catch(showError);
  }

  function appendUser(log, text) {
    log.append(el("div", { class: "msg user" }, el("div", { class: "bubble" }, text)));
    log.lastChild.scrollIntoView({ behavior: "smooth", block: "end" });
  }
  function appendAssistantText(log, text) {
    log.append(el("div", { class: "msg" }, el("div", { class: "muted" }, text)));
  }

  // A single loader with friendly, rotating status text (no step checklist).
  const PHASES = {
    question_received: "Reading your question",
    ai_resolving: "Thinking",
    metric_resolved: "Finding the right data",
    analysis_planned: "Planning the analysis",
    query_started: "Running the query",
    query_completed: "Crunching the numbers",
    chart_prepared: "Preparing your answer",
  };

  function streamAnalysis(log, job) {
    const text = el("span", { class: "loader-text" }, "Reading your question…");
    const loader = el("div", { class: "msg loader" }, el("span", { class: "spinner" }), text);
    log.append(loader);
    loader.scrollIntoView({ behavior: "smooth", block: "end" });

    const source = new EventSource(job.stream_url);
    Object.keys(PHASES).forEach((ev) =>
      source.addEventListener(ev, () => { text.textContent = PHASES[ev] + "…"; })
    );
    source.addEventListener("query_blocked", () => {});
    source.addEventListener("response_completed", (e) => {
      source.close();
      loader.remove();
      const result = JSON.parse(e.data).result;
      log.append(renderResult(result));
      log.lastChild.scrollIntoView({ behavior: "smooth", block: "end" });
      refreshConversations();
    });
    source.onerror = () => { source.close(); };
  }

  // ---- result card -----------------------------------------------------------------------
  function renderResult(r) {
    if (r.status === "blocked" || r.status === "unrecognized" || r.status === "error" || r.status === "message") {
      const label = r.status === "message" ? "Insyte" : r.status;
      const card = el("div", { class: "card" + (r.status === "blocked" ? " blocked" : "") },
        el("div", { class: "summary" },
          el("div", { class: "label" }, label),
          el("div", { class: "text" }, r.summary)
        )
      );
      if (r.warnings && r.warnings.length) {
        card.append(el("div", { class: "tab-body" }, el("div", { class: "warn-box" }, r.warnings.join("; "))));
      }
      if (r.suggested_questions && r.suggested_questions.length) {
        card.append(followups(r.suggested_questions));
      }
      return card;
    }

    // Decide how much to show. A plain single-value answer stays minimal; charts/data/metrics
    // only appear when they add something (a trend, segments, or a forecast).
    const hasChart = !!(r.charts && r.charts.length);
    const multiRow = !!(r.table && r.table.columns && r.table.columns.length && r.table.row_count > 1);
    const showMetrics = !!(r.projection || (r.metrics && r.metrics.length > 1));
    const rich = hasChart || multiRow || !!r.projection;

    const card = el("div", { class: "card" });
    card.append(el("div", { class: "summary" },
      el("div", { class: "label" }, r.projection ? "Projection" : "Answer"),
      el("div", { class: "text" }, r.summary)
    ));
    if (r.limitations && r.limitations.length) {
      card.append(el("div", { class: "tab-body" }, el("div", { class: "warn-box" }, r.limitations.join("; "))));
    }
    if (showMetrics && r.metrics && r.metrics.length) {
      card.append(el("div", { class: "metric-row" }, ...r.metrics.map(metricCard)));
    }

    if (!rich) {
      // Minimal answer: the sentence says it all — just follow-ups (no SQL, no tabs).
      if (r.warnings && r.warnings.length) {
        card.append(el("div", { class: "tab-body" }, el("div", { class: "warn-box" }, r.warnings.join("; "))));
      }
      if (r.suggested_questions && r.suggested_questions.length) card.append(followups(r.suggested_questions));
      return card;
    }

    const tabs = [["Overview", overviewTab(r)]];
    if (hasChart) tabs.push(["Chart", chartTab(r.charts[0])]);
    if (multiRow) tabs.push(["Data", dataTab(r.table)]);
    if (r.warnings && r.warnings.length) tabs.push(["Warnings", el("div", {}, el("div", { class: "warn-box" }, r.warnings.join("; ")))]);

    const tabBar = el("div", { class: "tabs" });
    const bodies = [];
    tabs.forEach(([name, body], i) => {
      const btn = el("button", { class: "tab" + (i === 0 ? " active" : "") }, name);
      const wrap = el("div", { class: "tab-body" + (i === 0 ? "" : " hidden") }, body);
      btn.addEventListener("click", () => {
        tabBar.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        bodies.forEach((b) => b.classList.add("hidden"));
        btn.classList.add("active");
        wrap.classList.remove("hidden");
      });
      tabBar.append(btn);
      bodies.push(wrap);
    });
    card.append(tabBar, ...bodies);
    if (r.suggested_questions && r.suggested_questions.length) card.append(followups(r.suggested_questions));
    return card;
  }

  function sqlDisclosure(q) {
    return el("details", { class: "sql-disclosure" },
      el("summary", {}, "View SQL"),
      el("pre", { class: "sql" }, q.sql)
    );
  }

  function metricCard(m) {
    const change = m.change_percent;
    return el("div", { class: "metric" },
      el("div", { class: "m-label" }, m.label),
      el("div", { class: "m-value" }, fmtValue(m.value, m.format)),
      change != null ? el("div", { class: "m-change " + (change >= 0 ? "up" : "down") }, (change >= 0 ? "▲ " : "▼ ") + Math.abs(change).toFixed(1) + "%") : null
    );
  }

  function overviewTab(r) {
    const parts = [el("div", {}, r.summary)];
    if (r.contributors && r.contributors.length) {
      parts.push(el("div", { class: "nav-heading" }, "Top contributors"));
      parts.push(el("div", {}, ...r.contributors.slice(0, 5).map((c) =>
        el("div", {}, "• " + c.label + " — " + (c.contribution_percent != null ? c.contribution_percent.toFixed(0) + "%" : ""))
      )));
    }
    if (r.freshness) parts.push(el("div", { class: "meta-line" }, el("span", {}, "Mode: " + r.freshness.mode)));
    return el("div", {}, ...parts);
  }

  function dataTab(table) {
    const thead = el("tr", {}, ...table.columns.map((c) => el("th", {}, c)));
    const rows = table.rows.slice(0, 200).map((row) => el("tr", {}, ...row.map((cell) => el("td", {}, cell == null ? "" : String(cell)))));
    return el("div", { class: "chart-wrap" },
      el("table", { class: "data" }, el("thead", {}, thead), el("tbody", {}, ...rows)),
      table.row_count > 200 ? el("div", { class: "muted" }, "Showing first 200 of " + table.row_count + " rows") : null
    );
  }

  function sqlTab(q) {
    return el("div", {},
      el("pre", { class: "sql" }, q.sql),
      el("div", { class: "meta-line" },
        el("span", { class: "ok" }, "✓ Read-only"),
        el("span", { class: "ok" }, "✓ " + q.validation_status),
        el("span", {}, q.rows_returned + " rows"),
        el("span", {}, q.duration_ms + " ms"),
        q.applied_limit != null ? el("span", {}, "limit " + q.applied_limit) : null
      )
    );
  }

  function methodTab(r) {
    return el("div", {},
      el("div", {}, "Result: " + (r.metrics.length ? "metric analysis" : "narrative")),
      r.freshness ? el("div", {}, "Analytics mode: " + r.freshness.mode) : null,
      r.confidence != null ? el("div", {}, "Confidence: " + (r.confidence * 100).toFixed(0) + "%") : null,
      r.limitations && r.limitations.length ? el("div", { class: "warn-box" }, r.limitations.join("; ")) : null
    );
  }

  function followups(questions) {
    return el("div", { class: "followups" },
      ...questions.map((q) => el("button", { class: "followup", onClick: () => submitQuestion(q) }, q))
    );
  }

  // ---- inline SVG chart ------------------------------------------------------------------
  function chartTab(spec) {
    const wrap = el("div", { class: "chart-wrap" });
    const data = spec.data || [];
    const key = spec.series && spec.series[0] ? spec.series[0].key : null;
    if (!key || !data.length) return el("div", { class: "muted" }, "No chart.");
    const labels = data.map((d) => String(d[spec.x_key]));
    const values = data.map((d) => Number(d[key]) || 0);
    wrap.append(spec.type === "line" ? lineChart(labels, values) : barChart(labels, values));
    return wrap;
  }

  const W = 760, H = 240, PAD = 34;
  function svgNode(tag, attrs) {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  }
  function scaleY(v, max) { return H - PAD - (max ? (v / max) * (H - 2 * PAD) : 0); }

  function barChart(labels, values) {
    const max = Math.max(...values, 1);
    const svg = svgNode("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H });
    svg.append(svgNode("line", { class: "axis", x1: PAD, y1: H - PAD, x2: W - 8, y2: H - PAD }));
    const bw = (W - PAD - 12) / labels.length;
    labels.forEach((lab, i) => {
      const x = PAD + i * bw + bw * 0.15;
      const y = scaleY(values[i], max);
      svg.append(svgNode("rect", { class: "bar", x, y, width: bw * 0.7, height: H - PAD - y, rx: 3 }));
      const t = svgNode("text", { x: x + bw * 0.35, y: H - PAD + 12, "text-anchor": "middle" });
      t.textContent = lab.length > 10 ? lab.slice(0, 9) + "…" : lab;
      svg.append(t);
    });
    return svg;
  }

  function lineChart(labels, values) {
    const max = Math.max(...values, 1);
    const svg = svgNode("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H });
    svg.append(svgNode("line", { class: "axis", x1: PAD, y1: H - PAD, x2: W - 8, y2: H - PAD }));
    const step = labels.length > 1 ? (W - PAD - 12) / (labels.length - 1) : 0;
    const pts = values.map((v, i) => (PAD + i * step) + "," + scaleY(v, max));
    svg.append(svgNode("polyline", { class: "line", points: pts.join(" ") }));
    values.forEach((v, i) => svg.append(svgNode("circle", { class: "dot", cx: PAD + i * step, cy: scaleY(v, max), r: 3 })));
    return svg;
  }

  // ---- other pages -----------------------------------------------------------------------
  function renderSchemaPage(view) {
    view.append(el("div", { class: "page" }, el("h2", {}, "Schema"), el("div", { id: "schema-body" }, el("div", { class: "muted" }, "Loading…"))));
    getJSON("/schema").then((s) => {
      const body = $("#schema-body");
      body.innerHTML = "";
      if (!s.scanned) { body.append(el("div", { class: "muted" }, "No metadata yet. Run 'insyte scan'.")); return; }
      const rows = s.tables.map((t) =>
        el("tr", {},
          el("td", {}, el("button", { class: "row-link", onClick: () => showTable(t.schema, t.name) }, t.qualified_name)),
          el("td", {}, t.category),
          el("td", {}, t.row_estimate == null ? "—" : compact(t.row_estimate)),
          el("td", {}, t.column_count)
        )
      );
      body.append(el("table", { class: "list-table" },
        el("thead", {}, el("tr", {}, el("th", {}, "Table"), el("th", {}, "Category"), el("th", {}, "Rows"), el("th", {}, "Cols"))),
        el("tbody", {}, ...rows)
      ), el("div", { id: "table-detail" }));
    });
  }
  function showTable(schema, name) {
    getJSON("/schema/tables/" + schema + "/" + name).then((d) => {
      const box = $("#table-detail");
      box.innerHTML = "";
      box.append(el("h2", {}, d.summary.qualified_name),
        el("table", { class: "list-table" },
          el("thead", {}, el("tr", {}, el("th", {}, "Column"), el("th", {}, "Type"), el("th", {}, "Null"), el("th", {}, "Key"))),
          el("tbody", {}, ...d.columns.map((c) =>
            el("tr", {}, el("td", {}, c.name), el("td", {}, c.type), el("td", {}, c.nullable ? "" : "not null"), el("td", {}, c.primary_key ? "PK" : c.unique ? "UQ" : ""))
          ))
        ));
    });
  }

  function renderMetricsPage(view) {
    view.append(el("div", { class: "page" }, el("h2", {}, "Metrics"), el("div", { id: "metrics-body" }, el("div", { class: "muted" }, "Loading…"))));
    getJSON("/metrics").then((m) => {
      const body = $("#metrics-body");
      body.innerHTML = "";
      if (!m.metrics.length) { body.append(el("div", { class: "muted" }, "No metrics. Run 'insyte semantic generate'.")); return; }
      body.append(el("table", { class: "list-table" },
        el("thead", {}, el("tr", {}, el("th", {}, "Name"), el("th", {}, "Label"), el("th", {}, "Status"), el("th", {}, "Expression"))),
        el("tbody", {}, ...m.metrics.map((x) =>
          el("tr", {}, el("td", {}, x.name), el("td", {}, x.label),
            el("td", {}, el("span", { class: "chip " + x.status }, x.status)), el("td", {}, x.expression))
        ))
      ));
    });
  }

  function renderHistoryPage(view) {
    view.append(el("div", { class: "page" }, el("h2", {}, "History"), el("div", { id: "history-body" }, el("div", { class: "muted" }, "Loading…"))));
    getJSON("/history").then((h) => {
      const body = $("#history-body");
      body.innerHTML = "";
      if (!h.queries.length) { body.append(el("div", { class: "muted" }, "No queries yet.")); return; }
      body.append(el("table", { class: "list-table" },
        el("thead", {}, el("tr", {}, el("th", {}, "When"), el("th", {}, "Status"), el("th", {}, "Source"), el("th", {}, "SQL"))),
        el("tbody", {}, ...h.queries.map((q) =>
          el("tr", {}, el("td", {}, q.created_at ? new Date(q.created_at).toLocaleTimeString() : ""),
            el("td", {}, el("span", { class: "chip" }, q.status)), el("td", {}, q.source),
            el("td", {}, (q.sql || "").slice(0, 80)))
        ))
      ));
    });
  }

  function renderSettingsPage(view) {
    const page = el("div", { class: "page" }, el("h2", {}, "Settings"));
    page.append(el("div", { class: "setting-row" },
      el("span", {}, "Theme"),
      el("button", { class: "icon-btn", onClick: toggleTheme }, document.documentElement.getAttribute("data-theme") === "dark" ? "☀ Light" : "☾ Dark")
    ));
    const cfg = el("pre", { class: "sql" }, "Loading config…");
    page.append(el("div", { class: "nav-heading" }, "Public configuration"), cfg);
    view.append(page);
    getJSON("/config/public").then((c) => { cfg.textContent = JSON.stringify(c, null, 2); });
  }

  function refreshConversations() {
    // Update only the sidebar so an in-progress chat view (and its results) is never wiped.
    return getJSON("/conversations")
      .then((d) => {
        state.conversations = d.conversations || [];
        const sb = document.querySelector(".sidebar");
        if (sb) sb.replaceWith(renderSidebar());
      })
      .catch(() => {});
  }

  function showError(err) {
    const view = $("#view");
    if (view) view.append(el("div", { class: "warn-box" }, "Something went wrong: " + err.message));
  }

  // ---- animated dotted background --------------------------------------------------------
  function initBackground() {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const canvas = el("canvas", { id: "bg-dots" });
    document.body.appendChild(canvas);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let w = 0, h = 0;
    const GAP = 32;
    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth; h = window.innerHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    window.addEventListener("resize", resize);
    let t = 0;
    function frame() {
      ctx.clearRect(0, 0, w, h);
      const light = document.documentElement.getAttribute("data-theme") === "light";
      const base = light ? "60, 80, 140" : "150, 165, 220";
      for (let x = 0; x <= w + GAP; x += GAP) {
        for (let y = 0; y <= h + GAP; y += GAP) {
          // A slow sine/cosine wave gives the dots a gentle rolling, pseudo-3D motion.
          const wave = Math.sin(x * 0.012 + t) * Math.cos(y * 0.012 + t * 0.7);
          const depth = (wave + 1) * 0.5;            // 0..1
          const r = 0.8 + depth * 1.6;
          const alpha = 0.12 + depth * 0.24;         // visible but still subtle behind the chat
          ctx.beginPath();
          ctx.fillStyle = "rgba(" + base + "," + alpha.toFixed(3) + ")";
          ctx.arc(x, y + wave * 5, r, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      t += 0.006;
      requestAnimationFrame(frame);
    }
    frame();
  }

  // ---- boot ------------------------------------------------------------------------------
  async function boot() {
    initTheme();
    initBackground();
    try { state.status = await getJSON("/status"); } catch (e) { /* DB may be down */ }
    try { state.metrics = await getJSON("/metrics"); } catch (e) {}
    try { const d = await getJSON("/conversations"); state.conversations = d.conversations || []; } catch (e) {}
    window.addEventListener("hashchange", route);
    route();
  }

  boot();
})();
