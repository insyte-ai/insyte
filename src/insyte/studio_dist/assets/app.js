/* Insyte Studio — self-contained SPA (no build step, no external dependencies).
 *
 * Talks to the FastAPI backend. Streams analysis progress over SSE. Renders result cards with
 * Overview / Chart / Data / SQL / Method / Warnings tabs and inline SVG charts. This ships in
 * the wheel so end users need no Node.js; contributors can replace it with a Vite/React build.
 */
(function () {
  "use strict";

  const API = "/api";
  const state = {
    status: null,
    metrics: null,
    conversations: [],
    conversationId: null,
    detailed: localStorage.getItem("insyte-detailed") === "1",
    busy: false,
    activeStream: null,
    activeLoader: null,
    activeAnalysisId: null,
  };

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
    const input = el("input", { id: "composer-input", class: "composer-input", placeholder: "Ask an analytics question…", autocomplete: "off" });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !state.busy) submitQuestion(input.value); });
    const plus = el("button", {
      class: "composer-plus" + (state.detailed ? " active" : ""), id: "composer-plus",
      title: "Options", onClick: togglePlusMenu,
    }, "+");
    const menu = el("div", { class: "plus-menu hidden", id: "plus-menu" },
      el("button", { class: "plus-item" + (state.detailed ? " on" : ""), id: "plus-detailed", onClick: selectDetailed },
        el("span", { class: "pi-check" }, state.detailed ? "✓" : ""),
        el("span", { class: "pi-body" },
          el("span", { class: "pi-title" }, "Detailed report"),
          el("span", { class: "pi-sub" }, "In-depth analyst write-up with charts")
        )
      )
    );
    // The active tool shows as a removable chip inside the input pill (like ChatGPT).
    const chip = el("span", { class: "tool-chip" + (state.detailed ? "" : " hidden"), id: "detailed-chip" },
      el("span", { class: "tc-ico" }, "◍"),
      "Detailed report",
      el("button", { class: "tc-x", title: "Remove", onClick: (e) => { e.stopPropagation(); setDetailed(false); } }, "✕")
    );
    const field = el("div", { class: "composer-field" },
      el("div", { class: "composer-plus-wrap" }, plus, menu),
      chip,
      input
    );
    const composer = el("div", { class: "composer" },
      field,
      el("button", { class: "composer-send", id: "composer-send", title: "Ask", onClick: onSendClick }, sendIcon())
    );
    const composerWrap = el("div", { class: "composer-wrap" }, composer);
    const samples = el("div", { class: "suggestions", id: "chat-samples" },
      ...suggestions().slice(0, 4).map((q) => el("button", { class: "suggestion", onClick: () => submitQuestion(q) }, q))
    );

    // Empty state (heading + composer + samples, centered). Once there are messages the
    // container loses .empty: the log fills the space and the composer sticks to the bottom.
    const chat = el("div", { class: "chat empty", id: "chat" }, hero, log, composerWrap, samples);
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

  // ---- send / stop control ---------------------------------------------------------------
  function iconSvg(inner) {
    const svg = svgNode("svg", {
      viewBox: "0 0 24 24", width: 19, height: 19, fill: "none", stroke: "currentColor",
      "stroke-width": 2.2, "stroke-linecap": "round", "stroke-linejoin": "round",
    });
    svg.innerHTML = inner;
    return svg;
  }
  function sendIcon() { return iconSvg('<path d="M12 20V5"/><path d="M5 12l7-7 7 7"/>'); }
  function stopIcon() { return iconSvg('<rect x="6.5" y="6.5" width="11" height="11" rx="2.5" fill="currentColor" stroke="none"/>'); }

  function onSendClick() {
    if (state.busy) stopAnalysis();
    else submitQuestion(($("#composer-input") || {}).value);
  }
  function setComposerBusy(busy) {
    state.busy = busy;
    const btn = $("#composer-send");
    if (!btn) return;
    btn.classList.toggle("busy", busy);
    btn.title = busy ? "Stop" : "Ask";
    btn.innerHTML = "";
    btn.appendChild(busy ? stopIcon() : sendIcon());
  }
  function finishStream() {
    if (state.activeStream) { try { state.activeStream.close(); } catch (e) {} }
    state.activeStream = null;
    state.activeLoader = null;
    state.activeAnalysisId = null;
    setComposerBusy(false);
  }
  function stopAnalysis() {
    const loader = state.activeLoader, aid = state.activeAnalysisId;
    finishStream();
    if (loader && loader.parentNode) {
      loader.classList.remove("loader");
      loader.innerHTML = "";
      loader.appendChild(el("div", { class: "muted" }, "Stopped."));
    }
    if (aid) postJSON("/analyses/" + aid + "/cancel", {}).catch(() => {});
  }

  function submitQuestion(text) {
    if (state.busy) return;
    text = (text || "").trim();
    if (!text) return;
    setChatActive();
    const log = $("#chat-log");
    appendUser(log, text);
    const input = $("#composer-input");
    if (input) input.value = "";

    ensureConversation()
      .then((cid) => postJSON("/conversations/" + cid + "/messages", { content: text, detailed: !!state.detailed }))
      .then((job) => streamAnalysis(log, job))
      .catch(showError);
  }

  function togglePlusMenu(e) {
    if (e) e.stopPropagation();
    const menu = $("#plus-menu");
    if (!menu) return;
    const willOpen = menu.classList.contains("hidden");
    menu.classList.toggle("hidden");
    if (willOpen) setTimeout(() => document.addEventListener("click", closePlusMenuOnce), 0);
  }
  function closePlusMenuOnce(e) {
    const menu = $("#plus-menu"), plus = $("#composer-plus");
    if (menu && !menu.contains(e.target) && e.target !== plus) {
      menu.classList.add("hidden");
      document.removeEventListener("click", closePlusMenuOnce);
    }
  }
  function setDetailed(on) {
    state.detailed = on;
    localStorage.setItem("insyte-detailed", on ? "1" : "0");
    const item = $("#plus-detailed");
    if (item) {
      item.classList.toggle("on", on);
      const chk = item.querySelector(".pi-check");
      if (chk) chk.textContent = on ? "✓" : "";
    }
    const plus = $("#composer-plus");
    if (plus) plus.classList.toggle("active", on);
    const chip = $("#detailed-chip");
    if (chip) chip.classList.toggle("hidden", !on);
    if (on) maybeShowReportNotice();
  }
  function selectDetailed() {
    setDetailed(!state.detailed);
    const menu = $("#plus-menu");
    if (menu) menu.classList.add("hidden");
    document.removeEventListener("click", closePlusMenuOnce);
  }

  // Shown once, the first time a user turns detailed reports on: what leaves the machine.
  function maybeShowReportNotice() {
    if (localStorage.getItem("insyte-report-notice") === "1") return;
    const notice = el("div", { class: "report-notice" },
      el("div", { class: "rn-body" },
        el("strong", {}, "Detailed reports use your AI CLI. "),
        "They send your aggregated results — not raw rows or credentials — to your local codex/claude to write analyst commentary. Turn it off anytime."
      ),
      el("button", { class: "rn-dismiss", onClick: (e) => {
        localStorage.setItem("insyte-report-notice", "1");
        e.target.closest(".report-notice").remove();
      } }, "Got it")
    );
    document.body.appendChild(notice);
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
    investigation_planned: "Planning your investigation",
    investigation_step_started: "Running an investigation step",
    investigation_step_completed: "Reviewing the finding",
    investigation_report_ready: "Preparing the investigation summary",
    query_started: "Running the query",
    query_completed: "Crunching the numbers",
    chart_prepared: "Preparing your answer",
  };

  function readableEventName(name) {
    return String(name || "")
      .split("_")
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  }

  function streamAnalysis(log, job) {
    const text = el("span", { class: "loader-text" }, "Reading your question…");
    const loader = el("div", { class: "msg loader" }, el("span", { class: "spinner" }), text);
    log.append(loader);
    loader.scrollIntoView({ behavior: "smooth", block: "end" });

    const source = new EventSource(job.stream_url);
    state.activeStream = source;
    state.activeLoader = loader;
    state.activeAnalysisId = job.analysis_id;
    setComposerBusy(true);

    Object.keys(PHASES).forEach((ev) =>
      source.addEventListener(ev, () => { text.textContent = (PHASES[ev] || readableEventName(ev)) + "…"; })
    );
    source.addEventListener("query_blocked", () => {});
    source.addEventListener("report_generating", () => { text.textContent = "Writing your detailed report…"; });
    source.addEventListener("report_skipped", () => {});
    source.addEventListener("report_failed", () => {});
    source.addEventListener("response_completed", (e) => {
      finishStream();
      loader.remove();
      const result = JSON.parse(e.data).result;
      log.append(renderResult(result));
      log.lastChild.scrollIntoView({ behavior: "smooth", block: "end" });
      refreshConversations();
    });
    source.onerror = () => {
      // Fires on our own close too; only act if this stream is still the active one.
      if (state.activeStream !== source) return;
      finishStream();
      text.textContent = "Connection lost.";
      loader.classList.remove("loader");
    };
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
    if (r.investigation) card.append(renderInvestigation(r.investigation));
    if (r.limitations && r.limitations.length) {
      card.append(el("div", { class: "tab-body" }, el("div", { class: "warn-box" }, r.limitations.join("; "))));
    }
    if (!r.report && showMetrics && r.metrics && r.metrics.length) {
      card.append(el("div", { class: "metric-row" }, ...r.metrics.map(metricCard)));
    }

    // A detailed report replaces the basic tab strip with the full analyst dashboard.
    if (r.report) {
      card.append(renderReport(r));
      if (r.suggested_questions && r.suggested_questions.length) card.append(followups(r.suggested_questions));
      return card;
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

  function renderInvestigation(inv) {
    const steps = inv.plan && inv.plan.steps ? inv.plan.steps : [];
    const wrap = el("div", { class: "investigation" },
      el("div", { class: "investigation-head" },
        el("span", { class: "investigation-title" }, "Investigation timeline"),
        inv.plan && inv.plan.period ? el("span", { class: "investigation-period" }, inv.plan.period) : null
      )
    );
    if (steps.length) {
      wrap.append(el("div", { class: "investigation-steps" }, ...steps.map((step) =>
        el("div", { class: "investigation-step " + (step.status || "pending") },
          el("span", { class: "step-dot" }),
          el("div", { class: "step-body" },
            el("div", { class: "step-top" },
              el("span", { class: "step-title" }, step.title || readableEventName(step.kind)),
              el("span", { class: "step-status" }, readableEventName(step.status || "pending"))
            ),
            step.key_finding ? el("div", { class: "step-finding" }, step.key_finding) : null,
            step.limitation ? el("div", { class: "step-limitation" }, step.limitation) : null
          )
        )
      )));
    }
    if (inv.findings && inv.findings.length) {
      wrap.append(el("div", { class: "investigation-findings" },
        ...inv.findings.slice(0, 3).map((finding) => el("div", {}, finding))
      ));
    }
    return wrap;
  }

  // ---- detailed report dashboard ---------------------------------------------------------
  function reportSection(title) {
    return el("div", { class: "report-section" }, title);
  }

  function renderReport(r) {
    const rep = r.report;
    const wrap = el("div", { class: "report" });
    wrap.append(el("div", { class: "report-head" },
      el("span", { class: "report-title" }, "◍ Detailed report"),
      el("span", { class: "conf-chip " + (rep.confidence_overall || "medium") }, (rep.confidence_overall || "medium") + " confidence")
    ));
    if (rep.tl_dr) wrap.append(el("div", { class: "tl-dr" }, rep.tl_dr));
    if (rep.decision) wrap.append(el("div", { class: "decision" }, el("b", {}, "Decision: "), rep.decision));
    if (rep.executive_summary) wrap.append(el("div", { class: "exec" }, rep.executive_summary));
    if (r.metrics && r.metrics.length) wrap.append(el("div", { class: "metric-row" }, ...r.metrics.map(metricCard)));

    const charts = reportCharts(r);
    if (charts.length) wrap.append(el("div", { class: "chart-grid" }, ...charts));

    if (rep.key_insights && rep.key_insights.length) {
      wrap.append(reportSection("Key insights"));
      wrap.append(el("div", { class: "insights" }, ...rep.key_insights.map(insightCard)));
    }
    if ((rep.evidence && rep.evidence.length) || (rep.counter_evidence && rep.counter_evidence.length)) {
      wrap.append(reportSection("Evidence"));
      wrap.append(evidenceGrid(rep));
    }
    if (rep.confidence_reasons && rep.confidence_reasons.length) {
      wrap.append(reportSection("Confidence"));
      wrap.append(bulletPanel(rep.confidence_reasons));
    }
    if (rep.data_quality && rep.data_quality.length) {
      wrap.append(reportSection("Data quality"));
      wrap.append(el("div", { class: "dq-strip" }, ...rep.data_quality.map(dqChip)));
    }
    const rc = rep.root_cause;
    if (rc && (rc.likely_cause || rc.what_changed)) {
      wrap.append(reportSection("Root cause"));
      wrap.append(rootCauseBox(rc));
    }
    const bi = rep.business_impact;
    if (bi && (bi.narrative || bi.financial_note)) {
      wrap.append(reportSection("Business impact"));
      wrap.append(el("div", { class: "impact" },
        bi.narrative ? el("div", {}, bi.narrative) : null,
        bi.financial_note ? el("div", { class: "impact-fin" }, bi.financial_note) : null
      ));
    }
    const fc = rep.forecast;
    if (fc && (fc.expected || fc.best_case || fc.worst_case)) {
      wrap.append(reportSection("Forecast"));
      wrap.append(forecastPanel(fc));
    }
    if (rep.risks && rep.risks.length) {
      wrap.append(reportSection("Risks"));
      wrap.append(el("div", { class: "risks" }, ...rep.risks.map(riskRow)));
    }
    if (rep.recommendations && rep.recommendations.length) {
      wrap.append(reportSection("Recommendations"));
      wrap.append(el("div", { class: "recs" }, ...rep.recommendations.map(recCard)));
    }
    if (rep.metrics_to_track && rep.metrics_to_track.length) {
      wrap.append(reportSection("Metrics to track"));
      wrap.append(el("div", { class: "metric-tags" }, ...rep.metrics_to_track.map((m) => el("span", {}, m))));
    }
    if (rep.next_best_questions && rep.next_best_questions.length) {
      wrap.append(reportSection("Next best questions"));
      wrap.append(followups(rep.next_best_questions));
    }
    if (rep.caveats && rep.caveats.length) {
      wrap.append(el("details", { class: "caveats" },
        el("summary", {}, "Caveats & limitations"),
        el("ul", {}, ...rep.caveats.map((c) => el("li", {}, c)))
      ));
    }
    if (r.context) wrap.append(contextBox(r.context));
    wrap.append(el("div", { class: "report-foot" },
      "Generated by " + (rep.generated_by || "your AI CLI") + " · commentary over Insyte-computed figures"));
    return wrap;
  }

  function evidenceGrid(rep) {
    return el("div", { class: "evidence-grid" },
      rep.evidence && rep.evidence.length ? el("div", { class: "ev-panel" },
        el("div", { class: "ev-title" }, "Supports"),
        el("ul", {}, ...rep.evidence.map((e) => el("li", {}, e)))
      ) : null,
      rep.counter_evidence && rep.counter_evidence.length ? el("div", { class: "ev-panel counter" },
        el("div", { class: "ev-title" }, "Complicates"),
        el("ul", {}, ...rep.counter_evidence.map((e) => el("li", {}, e)))
      ) : null
    );
  }

  function bulletPanel(items) {
    return el("ul", { class: "bullet-panel" }, ...items.map((item) => el("li", {}, item)));
  }

  function contextBox(ctx) {
    const items = [];
    if (ctx.active_metric) items.push(["Metric", ctx.active_metric]);
    if (ctx.active_dimension) items.push(["Dimension", ctx.active_dimension]);
    if (ctx.active_period) items.push(["Period", ctx.active_period]);
    if (ctx.active_report_mode) items.push(["Mode", ctx.active_report_mode]);
    if (!items.length && !(ctx.unresolved_assumptions && ctx.unresolved_assumptions.length)) return null;
    return el("details", { class: "context-box" },
      el("summary", {}, "Context used for follow-ups"),
      el("div", { class: "ctx-grid" }, ...items.map(([k, v]) =>
        el("div", { class: "ctx-item" }, el("span", {}, k), el("b", {}, v))
      )),
      ctx.unresolved_assumptions && ctx.unresolved_assumptions.length
        ? el("ul", { class: "ctx-assumptions" }, ...ctx.unresolved_assumptions.map((a) => el("li", {}, a)))
        : null
    );
  }

  // Charts are derived only from the real result — never from anything the model returned.
  function reportCharts(r) {
    const cards = [];
    if (r.charts && r.charts.length) {
      cards.push(chartCard(r.charts[0].title || "Overview", chartTab(r.charts[0])));
    }
    if (r.contributors && r.contributors.length > 1) {
      cards.push(chartCard(
        "Contribution share",
        chartFrame("Contribution share", () => shareChart(r.contributors))
      ));
    }
    const spec = r.charts && r.charts[0];
    if (spec && spec.type === "line" && spec.series && spec.series[0]) {
      const key = spec.series[0].key;
      const labels = (spec.data || []).map((d) => String(d[spec.x_key]));
      const values = (spec.data || []).map((d) => Number(d[key]) || 0);
      if (values.length > 1) {
        cards.push(chartCard(
          "Period-over-period growth",
          chartFrame("Period-over-period growth", () => growthBars(labels, values))
        ));
      }
    }
    return cards;
  }

  function chartCard(title, body) {
    return el("div", { class: "chart-card", "aria-label": title }, body);
  }

  function shareChart(contributors) {
    return el("div", { class: "share" },
      ...contributors.slice(0, 8).map((c) => {
        const pct = c.contribution_percent != null ? c.contribution_percent : 0;
        return el("div", { class: "share-row" },
          el("div", { class: "share-label", title: c.label }, c.label),
          el("div", { class: "share-track" }, el("div", { class: "share-fill", style: "width:" + Math.max(2, Math.min(100, pct)) + "%" })),
          el("div", { class: "share-pct" }, pct.toFixed(0) + "%")
        );
      })
    );
  }

  function growthBars(labels, values) {
    const rows = [];
    for (let i = 1; i < values.length; i++) {
      const prev = values[i - 1];
      const pct = prev ? ((values[i] - prev) / prev) * 100 : 0;
      const dir = pct >= 0 ? "up" : "down";
      rows.push(el("div", { class: "g-row" },
        el("div", { class: "g-label", title: labels[i] }, labels[i]),
        el("div", { class: "g-track" }, el("div", { class: "g-fill " + dir, style: "width:" + Math.max(3, Math.min(100, Math.abs(pct))) + "%" })),
        el("div", { class: "g-pct " + dir }, (pct >= 0 ? "+" : "") + pct.toFixed(0) + "%")
      ));
    }
    return el("div", { class: "growth" }, ...rows);
  }

  function insightCard(ins) {
    return el("details", { class: "insight" },
      el("summary", {},
        el("span", { class: "ins-title" }, ins.title || "Insight"),
        el("span", { class: "conf-chip " + (ins.confidence || "medium") }, ins.confidence || "")
      ),
      ins.detail ? el("div", { class: "ins-detail" }, ins.detail) : null,
      ins.evidence ? el("div", { class: "ins-line" }, el("b", {}, "Evidence: "), ins.evidence) : null,
      ins.limitations ? el("div", { class: "ins-line" }, el("b", {}, "Caveat: "), ins.limitations) : null,
      ins.alternative_explanation ? el("div", { class: "ins-line" }, el("b", {}, "Alternative: "), ins.alternative_explanation) : null
    );
  }

  function dqChip(f) {
    return el("div", { class: "dq " + (f.severity || "info"), title: f.impact || "" },
      el("span", { class: "dq-dot" }),
      f.issue + (f.affected ? " · " + f.affected : "")
    );
  }

  function rootCauseBox(rc) {
    const meta = [];
    if (rc.what_changed) meta.push(el("span", {}, "Changed: " + rc.what_changed));
    if (rc.when) meta.push(el("span", {}, "When: " + rc.when));
    if (rc.dimension) meta.push(el("span", {}, "Along: " + rc.dimension));
    if (rc.confidence) meta.push(el("span", { class: "conf-chip " + rc.confidence }, rc.confidence));
    return el("div", { class: "rootcause" },
      rc.likely_cause ? el("div", { class: "rc-main" }, rc.likely_cause) : null,
      meta.length ? el("div", { class: "rc-meta" }, ...meta) : null,
      rc.evidence ? el("div", { class: "rc-ev" }, el("b", {}, "Evidence: "), rc.evidence) : null
    );
  }

  function forecastPanel(fc) {
    const card = (label, val, cls) => el("div", { class: "fc " + cls },
      el("div", { class: "fc-label" }, label), el("div", { class: "fc-val" }, val || "—"));
    return el("div", { class: "forecast" },
      el("div", { class: "fc-cards" },
        card("Worst case", fc.worst_case, "down"),
        card("Expected", fc.expected, "mid"),
        card("Best case", fc.best_case, "up")
      ),
      fc.assumptions ? el("div", { class: "fc-note" }, fc.assumptions) : null
    );
  }

  function riskRow(rk) {
    return el("div", { class: "risk" },
      el("span", { class: "risk-like " + (rk.likelihood || "medium") }, rk.likelihood || "—"),
      el("span", { class: "risk-text" }, rk.risk + (rk.mitigation ? " — " + rk.mitigation : ""))
    );
  }

  function recCard(rc) {
    const meta = [rc.expected_impact, rc.est_roi ? "ROI: " + rc.est_roi : null].filter(Boolean).join(" · ");
    return el("div", { class: "rec " + (rc.priority || "medium") },
      el("div", { class: "rec-head" },
        el("span", { class: "rec-horizon" }, rc.horizon || "short"),
        el("span", { class: "rec-prio " + (rc.priority || "medium") }, (rc.priority || "medium") + " priority")
      ),
      el("div", { class: "rec-action" }, rc.action),
      meta ? el("div", { class: "rec-meta" }, meta) : null
    );
  }

  // ---- inline SVG chart ------------------------------------------------------------------
  function chartTab(spec) {
    const data = spec.data || [];
    const key = spec.series && spec.series[0] ? spec.series[0].key : null;
    if (!key || !data.length) return el("div", { class: "muted" }, "No chart.");
    const labels = data.map((d) => formatChartLabel(d[spec.x_key]));
    const values = data.map((d) => Number(d[key]) || 0);
    const seriesLabel = spec.series[0].label || key;
    return chartFrame(
      spec.title || "Chart",
      () => spec.type === "line"
        ? lineChart(labels, values, seriesLabel)
        : barChart(labels, values, seriesLabel)
    );
  }

  function chartFrame(title, render, options) {
    const body = el("div", { class: "chart-wrap" }, render());
    const btn = options && options.modal ? null : el("button", { class: "chart-expand", title: "Expand chart", "aria-label": "Expand chart" }, "⛶");
    if (btn) btn.addEventListener("click", () => openChartFullscreen(title, render));
    return el("div", { class: "chart-shell" },
      el("div", { class: "chart-toolbar" },
        el("span", { class: "chart-title", title }, title),
        btn
      ),
      body
    );
  }

  function openChartFullscreen(title, render) {
    const close = el("button", { class: "chart-close", title: "Close chart", "aria-label": "Close chart" }, "×");
    const overlay = el("div", { class: "chart-modal" },
      el("div", { class: "chart-modal-panel" },
        el("div", { class: "chart-modal-head" },
          el("div", { class: "chart-modal-title" }, title),
          close
        ),
        el("div", { class: "chart-modal-body" }, chartFrame(title, render, { modal: true }))
      )
    );
    const done = () => {
      document.removeEventListener("keydown", onKey);
      overlay.remove();
    };
    const onKey = (e) => { if (e.key === "Escape") done(); };
    close.addEventListener("click", done);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) done(); });
    document.addEventListener("keydown", onKey);
    document.body.append(overlay);
  }

  const W = 760, H = 260, PAD = 42;
  function svgNode(tag, attrs) {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  }
  function scaleY(v, min, max) {
    const range = max - min || 1;
    return H - PAD - ((v - min) / range) * (H - 2 * PAD);
  }
  function chartBounds(values) {
    const rawMin = Math.min(...values, 0);
    const rawMax = Math.max(...values, 1);
    const pad = Math.max((rawMax - rawMin) * 0.12, rawMax === rawMin ? Math.abs(rawMax) * 0.08 : 0);
    return { min: Math.min(0, rawMin - pad), max: rawMax + pad };
  }
  function grid(svg, min, max) {
    [0, 0.5, 1].forEach((ratio) => {
      const y = PAD + (H - 2 * PAD) * ratio;
      svg.append(svgNode("line", { class: "grid-line", x1: PAD, y1: y, x2: W - 16, y2: y }));
    });
    const maxLabel = svgNode("text", { class: "axis-label", x: PAD, y: PAD - 10 });
    maxLabel.textContent = compact(max);
    svg.append(maxLabel);
    const minLabel = svgNode("text", { class: "axis-label", x: PAD, y: H - 10 });
    minLabel.textContent = compact(min);
    svg.append(minLabel);
  }
  function formatChartLabel(value) {
    const text = String(value ?? "");
    const date = new Date(text);
    if (!Number.isNaN(date.getTime()) && /^\d{4}-\d{2}-\d{2}/.test(text)) {
      return date.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
    }
    return text;
  }
  function tooltip(svg, label, value, x, y, key) {
    const group = svgNode("g", { class: "chart-tip", opacity: "0", "pointer-events": "none" });
    const text = svgNode("text", { x: 0, y: 0 });
    text.append(svgText(label, 0, 0, "tip-label"));
    text.append(svgText((key ? key + ": " : "") + compact(value), 0, 15, "tip-value"));
    const valueText = (key ? key + ": " : "") + compact(value);
    const width = Math.max(88, Math.min(170, Math.max(label.length, valueText.length) * 6.4 + 22));
    const tx = Math.min(W - width - 10, Math.max(10, x - width / 2));
    const ty = y < 82 ? y + 42 : y - 44;
    group.setAttribute("transform", "translate(" + tx + "," + ty + ")");
    group.append(svgNode("rect", { class: "tip-bg", x: 0, y: -14, width, height: 36, rx: 6 }));
    group.append(text);
    svg.append(group);
    return group;
  }
  function svgText(text, x, y, cls) {
    const t = svgNode("tspan", { x, y, class: cls });
    t.textContent = text;
    return t;
  }
  function hoverTarget(node, tip) {
    node.addEventListener("mouseenter", () => tip.setAttribute("opacity", "1"));
    node.addEventListener("mouseleave", () => tip.setAttribute("opacity", "0"));
    node.addEventListener("focus", () => tip.setAttribute("opacity", "1"));
    node.addEventListener("blur", () => tip.setAttribute("opacity", "0"));
  }

  function barChart(labels, values, key) {
    const { min, max } = chartBounds(values);
    const svg = svgNode("svg", { class: "insyte-chart", viewBox: "0 0 " + W + " " + H, width: "100%", height: H });
    grid(svg, min, max);
    svg.append(svgNode("line", { class: "axis", x1: PAD, y1: H - PAD, x2: W - 16, y2: H - PAD }));
    const bw = (W - PAD - 12) / labels.length;
    labels.forEach((lab, i) => {
      const x = PAD + i * bw + bw * 0.15;
      const y = scaleY(values[i], min, max);
      const bar = svgNode("rect", {
        class: "bar",
        x,
        y,
        width: bw * 0.7,
        height: H - PAD - y,
        rx: 5,
        tabindex: 0,
      });
      const tip = tooltip(svg, lab, values[i], x + bw * 0.35, y, key);
      hoverTarget(bar, tip);
      svg.append(bar);
      if (labels.length <= 12 || i % Math.ceil(labels.length / 8) === 0) {
        const t = svgNode("text", { x: x + bw * 0.35, y: H - PAD + 16, "text-anchor": "middle" });
        t.textContent = lab.length > 8 ? lab.slice(0, 7) + "…" : lab;
        svg.append(t);
      }
      svg.append(tip);
    });
    return svg;
  }

  function lineChart(labels, values, key) {
    const { min, max } = chartBounds(values);
    const svg = svgNode("svg", { class: "insyte-chart", viewBox: "0 0 " + W + " " + H, width: "100%", height: H });
    grid(svg, min, max);
    svg.append(svgNode("line", { class: "axis", x1: PAD, y1: H - PAD, x2: W - 16, y2: H - PAD }));
    const step = labels.length > 1 ? (W - PAD - 12) / (labels.length - 1) : 0;
    const points = values.map((v, i) => [PAD + i * step, scaleY(v, min, max)]);
    const linePath = smoothPath(points);
    const areaPath = linePath + " L " + points[points.length - 1][0] + " " + (H - PAD) +
      " L " + points[0][0] + " " + (H - PAD) + " Z";
    svg.append(svgNode("path", { class: "area", d: areaPath }));
    svg.append(svgNode("path", { class: "line", d: linePath }));
    values.forEach((v, i) => {
      const x = points[i][0], y = points[i][1];
      if (labels.length <= 12 || i % Math.ceil(labels.length / 8) === 0) {
        const t = svgNode("text", { x, y: H - PAD + 16, "text-anchor": "middle" });
        t.textContent = labels[i].length > 8 ? labels[i].slice(0, 7) + "…" : labels[i];
        svg.append(t);
      }
      const dot = svgNode("circle", { class: "dot", cx: x, cy: y, r: 4, tabindex: 0 });
      const hit = svgNode("circle", { class: "dot-hit", cx: x, cy: y, r: 13, tabindex: 0 });
      const tip = tooltip(svg, labels[i], v, x, y, key);
      hoverTarget(dot, tip);
      hoverTarget(hit, tip);
      svg.append(dot, hit);
      svg.append(tip);
    });
    return svg;
  }

  function smoothPath(points) {
    if (!points.length) return "";
    if (points.length === 1) return "M " + points[0][0] + " " + points[0][1];
    let d = "M " + points[0][0] + " " + points[0][1];
    for (let i = 0; i < points.length - 1; i++) {
      const p0 = points[Math.max(0, i - 1)];
      const p1 = points[i];
      const p2 = points[i + 1];
      const p3 = points[Math.min(points.length - 1, i + 2)];
      const cp1x = p1[0] + (p2[0] - p0[0]) / 6;
      const cp1y = p1[1] + (p2[1] - p0[1]) / 6;
      const cp2x = p2[0] - (p3[0] - p1[0]) / 6;
      const cp2y = p2[1] - (p3[1] - p1[1]) / 6;
      d += " C " + cp1x + " " + cp1y + ", " + cp2x + " " + cp2y + ", " + p2[0] + " " + p2[1];
    }
    return d;
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

  // ---- backdrop -------------------------------------------------------------------------
  function initBackground() {
    if ($("#bg-scene")) return;
    const backdrop = el("div", { id: "bg-scene", "aria-hidden": "true" },
      el("div", { class: "bg-glow bg-glow-a" }),
      el("div", { class: "bg-glow bg-glow-b" }),
      el("div", { class: "bg-grid" })
    );
    document.body.appendChild(backdrop);
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
