async ({ dv, app, base }) => {
  const loadJson = async (path, fallback = null) => {
    const raw = await dv.io.load(path);
    if (raw == null) return fallback;
    return JSON.parse(raw);
  };
  const digest = async (text) => {
    const bytes = new TextEncoder().encode(text);
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, "0")).join("");
  };
  const root = dv.container;
  root.classList.add("parsing-data-explorer");
  const css = await dv.io.load(`${base}/app/view.css`);
  if (css) {
    const style = document.createElement("style");
    style.textContent = css;
    root.appendChild(style);
  }

  let dashboard;
  let build;
  let reviewState;
  try {
    dashboard = await loadJson(`${base}/cache/dashboard.json`);
    build = await loadJson(`${base}/cache/build-manifest.json`);
    reviewState = await loadJson(`${base}/state/review-state.json`, { version: "parsing-data-explorer-review-v1", records: {} });
  } catch (error) {
    root.createEl("div", { cls: "pde-alert pde-error", text: `Cache illisible : ${error.message}` });
    return;
  }
  if (!dashboard || !build) {
    root.createEl("div", { cls: "pde-alert", text: "Cache absent. Exécutez analytics refresh." });
    return;
  }
  const manifestPath = `${dashboard.source.pack_vault_path}/90_Evidence/pack_manifest.json`;
  const liveManifest = await dv.io.load(manifestPath);
  if (!liveManifest || await digest(liveManifest) !== dashboard.source.manifest_sha256) {
    root.createEl("div", { cls: "pde-alert pde-error", text: "Cache obsolète : le pack source a changé. Dashboard masqué jusqu’au refresh." });
    return;
  }

  let records = reviewState.records || {};
  const rowState = (row) => {
    const record = records[row.conversation_id];
    const status = record ? record.status : (row.needs_review ? "todo" : "not_required");
    const reviewStale = Boolean(record && record.status === "reviewed" && record.source_signature !== row.source_signature);
    return { status, reviewStale };
  };
  let stateWriteQueue = Promise.resolve();
  const updateStatus = (row, status) => {
    stateWriteQueue = stateWriteQueue.then(() => app.vault.adapter.process(`${base}/state/review-state.json`, raw => {
      const current = JSON.parse(raw);
      if (current.version !== "parsing-data-explorer-review-v1" || typeof current.records !== "object") {
        throw new Error("Sidecar incompatible");
      }
      current.records[row.conversation_id] = {
        status,
        status_updated_at: new Date().toISOString(),
        source_signature: row.source_signature,
      };
      reviewState = current;
      records = current.records;
      return `${JSON.stringify(current, null, 2)}\n`;
    })).then(render).catch(error => {
      root.createEl("div", { cls: "pde-alert pde-error", text: `Écriture refusée : ${error.message}` });
    });
  };

  const values = (key) => [...new Set(dashboard.conversations.flatMap(row => row[key] || []))].sort();
  const modelValues = () => [...new Set(dashboard.conversations.flatMap(row => [row.default_model, ...(row.models_seen || [])]).filter(Boolean))].sort();
  const latestMonth = dashboard.monthly.length ? dashboard.monthly[dashboard.monthly.length - 1].month_key : "";
  const recentMonths = new Set(dashboard.monthly.slice(-12).map(row => row.month_key));
  const filters = {
    period: "12",
    status: "open",
    model: "",
    project: "",
    gpt: "",
    knowledge: "",
    archive: "",
    files: "",
    sources: "",
    tools: "",
    minMessages: 0,
    minUnresolved: 0,
    search: "",
  };

  const selectControl = (parent, label, options, key) => {
    const wrap = parent.createEl("label", { cls: "pde-control" });
    wrap.createEl("span", { text: label });
    const select = wrap.createEl("select");
    for (const [value, text] of options) select.createEl("option", { value, text });
    select.value = filters[key];
    select.addEventListener("change", () => { filters[key] = select.value; render(); });
  };
  const numberControl = (parent, label, key) => {
    const wrap = parent.createEl("label", { cls: "pde-control" });
    wrap.createEl("span", { text: label });
    const input = wrap.createEl("input", { type: "number" });
    input.min = "0";
    input.value = String(filters[key]);
    input.addEventListener("input", () => { filters[key] = Number(input.value || 0); render(); });
  };
  const matching = (row) => {
    const state = rowState(row);
    if (filters.period === "12" && !recentMonths.has(row.month_key)) return false;
    if (filters.status === "open" && !(["todo", "in_progress"].includes(state.status) || state.reviewStale)) return false;
    if (filters.status === "stale" && !state.reviewStale) return false;
    if (filters.status && !["open", "stale"].includes(filters.status) && state.status !== filters.status) return false;
    if (filters.model && ![row.default_model, ...(row.models_seen || [])].includes(filters.model)) return false;
    if (filters.project && !(row.space_projects || []).includes(filters.project)) return false;
    if (filters.gpt && !(row.gpts || []).includes(filters.gpt)) return false;
    if (filters.knowledge && !(row.knowledge_stores || []).includes(filters.knowledge)) return false;
    if (filters.archive && String(row.is_archived) !== filters.archive) return false;
    if (filters.files === "yes" && row.file_reference_count === 0) return false;
    if (filters.files === "no" && row.file_reference_count > 0) return false;
    if (filters.sources === "yes" && row.source_evidence_count === 0) return false;
    if (filters.sources === "no" && row.source_evidence_count > 0) return false;
    if (filters.tools === "yes" && row.tool_evidence_count === 0) return false;
    if (filters.tools === "no" && row.tool_evidence_count > 0) return false;
    if (row.message_count < filters.minMessages || row.unresolved_file_count < filters.minUnresolved) return false;
    const haystack = [row.conversation_id, row.note_path, row.default_model, ...(row.models_seen || []), ...(row.gpts || []), ...(row.space_projects || []), ...(row.knowledge_stores || [])].filter(Boolean).join(" ").toLowerCase();
    return !filters.search || haystack.includes(filters.search.toLowerCase());
  };
  const sortRows = rows => [...rows].sort((a, b) => {
    const sa = rowState(a), sb = rowState(b);
    const priority = state => state.reviewStale ? 0 : state.status === "todo" ? 1 : state.status === "in_progress" ? 2 : 3;
    return priority(sa) - priority(sb)
      || b.unresolved_file_count - a.unresolved_file_count
      || Number(a.context_evidence !== "unknown") - Number(b.context_evidence !== "unknown")
      || String(b.created_at || "").localeCompare(String(a.created_at || ""));
  });

  const render = async () => {
    [...root.children].filter(node => node.tagName !== "STYLE").forEach(node => node.remove());
    root.createEl("h2", { text: "Parsing Data Explorer" });
    root.createEl("div", { cls: "pde-muted", text: `Source ${dashboard.source.pack_vault_path} · ${dashboard.source.conversation_count} conversations · dernier mois ${latestMonth || "—"}` });

    const controls = root.createEl("div", { cls: "pde-controls" });
    selectControl(controls, "Période", [["12", "12 derniers mois"], ["all", "Tout le corpus"]], "period");
    selectControl(controls, "État", [["open", "Priorités ouvertes"], ["", "Tous"], ["todo", "Todo"], ["in_progress", "En cours"], ["reviewed", "Revu"], ["ignored", "Ignoré"], ["not_required", "Non requis"], ["stale", "Review stale"]], "status");
    selectControl(controls, "Modèle", [["", "Tous"], ...modelValues().map(v => [v, v])], "model");
    selectControl(controls, "Projet", [["", "Tous"], ...values("space_projects").map(v => [v, v])], "project");
    selectControl(controls, "GPT", [["", "Tous"], ...values("gpts").map(v => [v, v])], "gpt");
    selectControl(controls, "Knowledge", [["", "Tous"], ...values("knowledge_stores").map(v => [v, v])], "knowledge");
    selectControl(controls, "Archive", [["", "Tous"], ["true", "Archivé"], ["false", "Actif"], ["null", "Inconnu"]], "archive");
    for (const [label, key] of [["Fichiers", "files"], ["Sources", "sources"], ["Outils", "tools"]]) selectControl(controls, label, [["", "Tous"], ["yes", "Présent"], ["no", "Absent"]], key);
    numberControl(controls, "Messages min.", "minMessages");
    numberControl(controls, "Non résolus min.", "minUnresolved");
    const searchWrap = controls.createEl("label", { cls: "pde-control pde-search" });
    searchWrap.createEl("span", { text: "Recherche locale" });
    const search = searchWrap.createEl("input", { type: "search", value: filters.search });
    search.addEventListener("input", () => { filters.search = search.value; render(); });
    const reset = controls.createEl("button", { text: "Reset" });
    reset.addEventListener("click", () => { Object.assign(filters, { period: "12", status: "open", model: "", project: "", gpt: "", knowledge: "", archive: "", files: "", sources: "", tools: "", minMessages: 0, minUnresolved: 0, search: "" }); render(); });

    const rows = sortRows(dashboard.conversations.filter(matching));
    const kpis = root.createEl("div", { cls: "pde-kpis" });
    for (const [label, value] of [["Conversations", rows.length], ["Messages", rows.reduce((n, r) => n + r.message_count, 0)], ["À revoir", rows.filter(r => rowState(r).status === "todo").length], ["Review stale", rows.filter(r => rowState(r).reviewStale).length], ["Fichiers non résolus", rows.reduce((n, r) => n + r.unresolved_file_count, 0)]]) {
      const card = kpis.createEl("div", { cls: "pde-kpi" }); card.createEl("strong", { text: String(value) }); card.createEl("span", { text: label });
    }

    root.createEl("h3", { text: "Conversations et messages mensuels" });
    const visibleMonthly = dashboard.monthly.filter(row => filters.period === "all" || recentMonths.has(row.month_key));
    const maxMessages = Math.max(1, ...visibleMonthly.map(row => row.message_count));
    const chart = root.createEl("div", { cls: "pde-chart" });
    visibleMonthly.forEach(row => {
      const bar = chart.createEl("div", { cls: "pde-bar" });
      bar.style.height = `${Math.max(4, row.message_count / maxMessages * 110)}px`;
      bar.title = `${row.month_key}: ${row.conversation_count} conversations, ${row.message_count} messages`;
      bar.createEl("span", { text: row.month_key.slice(5) });
    });

    root.createEl("h3", { text: "Couverture et distributions" });
    const summary = root.createEl("div", { cls: "pde-summary" });
    summary.createEl("div", { text: `Contexte connu ${(dashboard.coverage.context_known_ratio * 100).toFixed(1)}% · fichiers résolus ${(dashboard.coverage.files_resolved_ratio * 100).toFixed(1)}% · locator ${(dashboard.coverage.locator_ratio * 100).toFixed(1)}%` });
    summary.createEl("div", { text: `Densité moyenne — fichiers ${dashboard.quality.file_density}, sources ${dashboard.quality.source_density}, outils ${dashboard.quality.tool_density}` });
    summary.createEl("div", { text: `Médianes — messages ${dashboard.distributions.message_count.p50}, fichiers ${dashboard.distributions.file_reference_count.p50}, sources ${dashboard.distributions.source_evidence_count.p50}, outils ${dashboard.distributions.tool_evidence_count.p50}` });
    summary.createEl("div", { text: `P95 — messages ${dashboard.distributions.message_count.p95}, fichiers ${dashboard.distributions.file_reference_count.p95}, non résolus ${dashboard.distributions.unresolved_file_count.p95}` });

    root.createEl("h3", { text: `Conversations filtrées (${rows.length})` });
    const table = root.createEl("div", { cls: "pde-table-wrap" }).createEl("table", { cls: "pde-table" });
    const head = table.createEl("thead").createEl("tr");
    ["Conversation", "Date", "État", "Messages", "Fichiers", "Sources", "Outils", "Contexte"].forEach(text => head.createEl("th", { text }));
    const body = table.createEl("tbody");
    for (const row of rows) {
      const tr = body.createEl("tr");
      const conversationCell = tr.createEl("td");
      const fullPath = row.note_path ? `${dashboard.source.pack_vault_path}/${row.note_path}` : null;
      if (fullPath && app.vault.getAbstractFileByPath(fullPath)) {
        const link = conversationCell.createEl("a", { cls: "internal-link", text: row.conversation_id, href: fullPath });
        link.dataset.href = fullPath;
      } else {
        conversationCell.createEl("span", { text: row.conversation_id });
        conversationCell.createEl("small", { cls: "pde-warning", text: fullPath ? " cible absente" : " locator absent" });
      }
      tr.createEl("td", { text: (row.created_at || "—").slice(0, 10) });
      const stateCell = tr.createEl("td");
      const state = rowState(row);
      const stateSelect = stateCell.createEl("select", { cls: state.reviewStale ? "pde-stale" : "" });
      if (state.status === "not_required") stateSelect.createEl("option", { value: "", text: "Non requis" });
      for (const [value, text] of [["todo", "Todo"], ["in_progress", "En cours"], ["reviewed", "Revu"], ["ignored", "Ignoré"]]) stateSelect.createEl("option", { value, text });
      stateSelect.value = state.status === "not_required" ? "" : state.status;
      stateSelect.addEventListener("change", () => { if (stateSelect.value) updateStatus(row, stateSelect.value); });
      if (state.reviewStale) stateCell.createEl("small", { cls: "pde-warning", text: "review stale" });
      for (const value of [row.message_count, `${row.unresolved_file_count}/${row.file_reference_count}`, row.source_evidence_count, row.tool_evidence_count, row.context_evidence]) tr.createEl("td", { text: String(value) });
    }

    root.createEl("h3", { text: "Diagnostics" });
    const diagnostics = root.createEl("ul", { cls: "pde-diagnostics" });
    diagnostics.createEl("li", { text: `${dashboard.quality.has_warnings_count} conversations avec avertissements` });
    diagnostics.createEl("li", { text: `${dashboard.quality.unknown_context_count} contextes inconnus` });
    diagnostics.createEl("li", { text: `${dashboard.quality.locator_unavailable_count} conversations sans locator` });
    const currentOrphanCount = Object.keys(records).filter(id => !dashboard.conversations.some(row => row.conversation_id === id)).length;
    diagnostics.createEl("li", { text: `${currentOrphanCount} états humains orphelins conservés` });
    dashboard.quality.diagnostics.forEach(text => diagnostics.createEl("li", { text }));
  };
  await render();
}
