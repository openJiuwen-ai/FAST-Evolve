(() => {
  "use strict";

  const POLL_INTERVAL = 2000;
  const initialRunId = new URLSearchParams(window.location.search).get("run");
  const state = {
    snapshot: null,
    loading: false,
    connected: false,
    selectedRunId: initialRunId,
    launchRunId: null,
    runHistory: [],
    runHistoryLoading: false,
    runMenuOpen: false,
    chartPoints: [],
    chartGeometry: null,
    chartScaleMode: "focus",
    lineageData: { nodes: [], parent_edges: [], inspiration_edges: [], roots: [] },
    lineageSignature: "",
    lineageLayout: null,
    lineageScale: 1,
    lineagePan: { x: 0, y: 0 },
    lineagePanStart: null,
    lineageSuppressClick: false,
    lineageUserAdjusted: false,
    lineageHoveredId: null,
    pollTimer: null,
    openGuidance: new Set(),
    openNotes: new Set(),
    guidanceInitialized: false,
    notesInitialized: false,
  };

  const $ = (id) => document.getElementById(id);
  const elements = {
    runId: $("run-id"),
    runSelector: $("run-selector"),
    runSelectorTrigger: $("run-selector-trigger"),
    runSelectorMeta: $("run-selector-meta"),
    runMenu: $("run-menu"),
    runHistoryCount: $("run-history-count"),
    runHistoryList: $("run-history-list"),
    connectionPill: $("connection-pill"),
    connectionLabel: $("connection-label"),
    refresh: $("refresh-button"),
    statusTitle: $("status-title"),
    stateBadge: $("state-badge"),
    taskDescription: $("task-description"),
    statBest: $("stat-best"),
    statBestTask: $("stat-best-task"),
    statProgress: $("stat-progress"),
    progressFill: $("progress-fill"),
    statAgents: $("stat-agents"),
    statSuccess: $("stat-success"),
    statPlateau: $("stat-plateau"),
    statDiversity: $("stat-diversity"),
    chart: $("progress-chart"),
    chartWrap: $("chart-wrap"),
    chartEmpty: $("chart-empty"),
    chartTooltip: $("chart-tooltip"),
    chartRange: $("chart-range"),
    chartUpdated: $("chart-updated"),
    chartScaleFocus: $("chart-scale-focus"),
    chartScaleFull: $("chart-scale-full"),
    lineageCard: $("lineage-card"),
    lineageViewport: $("lineage-viewport"),
    lineageStage: $("lineage-stage"),
    lineageEmpty: $("lineage-empty"),
    lineageTooltip: $("lineage-tooltip"),
    lineageCount: $("lineage-count"),
    lineageSummary: $("lineage-summary"),
    lineageScale: $("lineage-scale"),
    lineageFullscreen: $("lineage-fullscreen"),
    lineageZoomIn: $("lineage-zoom-in"),
    lineageZoomOut: $("lineage-zoom-out"),
    lineageFit: $("lineage-fit"),
    bestContent: $("best-content"),
    agentsCount: $("agents-count"),
    agentsList: $("agents-list"),
    activityList: $("activity-list"),
    guidanceCount: $("guidance-count"),
    guidanceStatus: $("guidance-status"),
    guidanceList: $("guidance-list"),
    noteCounts: $("note-counts"),
    notesStatus: $("notes-status"),
    notesList: $("notes-list"),
    footerTime: $("footer-time"),
    agentTemplate: $("agent-template"),
  };

  async function refresh() {
    if (state.loading) return;
    state.loading = true;
    elements.refresh.classList.add("loading");
    try {
      const query = state.selectedRunId
        ? `?run_id=${encodeURIComponent(state.selectedRunId)}`
        : "";
      const response = await fetch(`/api/snapshot${query}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const snapshot = await response.json();
      state.snapshot = snapshot;
      state.selectedRunId = snapshot.run?.id || state.selectedRunId;
      setConnected(true);
      render(snapshot);
    } catch (error) {
      setConnected(false);
      elements.chartUpdated.textContent = "Connection interrupted — retrying";
      console.warn("Dashboard refresh failed", error);
    } finally {
      state.loading = false;
      elements.refresh.classList.remove("loading");
    }
  }

  async function refreshRunHistory() {
    if (state.runHistoryLoading) return;
    state.runHistoryLoading = true;
    try {
      const response = await fetch("/api/runs", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      state.runHistory = Array.isArray(data.runs) ? data.runs : [];
      state.launchRunId = data.current || state.launchRunId;
      if (!state.selectedRunId) state.selectedRunId = data.current || null;
      renderRunHistory();
    } catch (error) {
      if (!state.runHistory.length) {
        elements.runHistoryList.innerHTML = '<div class="run-history-empty">Unable to load persisted runs.</div>';
      }
      console.warn("Run history refresh failed", error);
    } finally {
      state.runHistoryLoading = false;
    }
  }

  function setConnected(connected) {
    state.connected = connected;
    elements.connectionPill.classList.toggle("connected", connected);
    elements.connectionPill.classList.toggle("disconnected", !connected);
    elements.connectionLabel.textContent = connected ? "Live" : "Reconnecting";
  }

  function render(snapshot) {
    const features = snapshot.features || {};
    renderRun(snapshot);
    renderChart(snapshot.progress || []);
    renderLineage(snapshot.lineage || {});
    renderBest(snapshot.best);
    renderAgents(snapshot.agents || []);
    renderActivity(snapshot.activity || []);
    renderFeatureStatus(elements.guidanceStatus, features.meta_guidance);
    renderFeatureStatus(elements.notesStatus, features.notes);
    renderGuidance(snapshot.guidance || [], features.meta_guidance);
    renderNotes(snapshot.notes || [], features.notes);
  }

  function renderRun(snapshot) {
    const run = snapshot.run || {};
    const diagnostics = snapshot.diagnostics || {};
    const best = snapshot.best;
    const max = run.max_total_tasks;
    const completed = run.completed_tasks || 0;
    const progressPct = max ? Math.min((completed / max) * 100, 100) : 0;
    const displayState = humanizeState(run.state);

    document.title = `${run.id || "EvoTrias"} · EvoTrias Evolution Dashboard`;
    elements.runId.textContent = run.id || "Unknown run";
    const historyItem = state.runHistory.find((item) => item.id === run.id);
    const timestamp = formatRunTimestamp(historyItem || { id: run.id, started_at: run.started_at });
    elements.runSelectorMeta.textContent = [
      timestamp,
      `${completed} completed`,
    ].filter(Boolean).join(" · ");
    elements.statusTitle.textContent = statusHeadline(run.state, completed);
    elements.taskDescription.textContent = run.task_description || "No task description recorded.";
    elements.stateBadge.className = `state-badge ${run.state || "initializing"}`;
    elements.stateBadge.querySelector("span").textContent = displayState;
    elements.statBest.textContent = best ? formatFitness(best.fitness) : "—";
    elements.statBest.title = best ? String(best.fitness) : "";
    elements.statBestTask.textContent = best ? `${best.task_id} · ${best.attempt_id || "candidate"}` : "Awaiting first result";
    elements.statProgress.innerHTML = `${completed} <em>/ ${max ?? "—"}</em>`;
    elements.progressFill.style.width = `${progressPct}%`;
    elements.statAgents.textContent = run.active_agents || 0;
    elements.statSuccess.textContent = `${run.successful_tasks || 0} successful · ${run.failed_tasks || 0} failed`;
    elements.statPlateau.textContent = diagnostics.plateau_count ?? "—";
    elements.statDiversity.textContent = diagnostics.diversity == null
      ? "Diversity —"
      : `Diversity ${formatCompact(diagnostics.diversity)}`;
    elements.footerTime.textContent = `Last sync ${formatTime(snapshot.generated_at, true)}`;
  }

  function renderRunHistory() {
    elements.runHistoryCount.textContent = String(state.runHistory.length);
    if (!state.runHistory.length) {
      elements.runHistoryList.innerHTML = '<div class="run-history-empty">No persisted runs found.</div>';
      return;
    }
    elements.runHistoryList.innerHTML = state.runHistory.map((run) => {
      const current = run.id === state.selectedRunId;
      const legacy = run.label === "run";
      const label = legacy ? run.id : run.label || run.id;
      const subtitle = legacy
        ? run.task_description || "Original timestamp-only ID"
        : run.id;
      const best = run.best_fitness == null ? "" : `best ${formatCompact(run.best_fitness)}`;
      return `
        <button class="run-history-item ${current ? "current" : ""}" type="button" data-run-id="${escapeHtml(run.id)}" title="Open ${escapeHtml(run.id)}">
          <span class="run-history-status ${run.status === "running" ? "running" : ""}" title="${run.status === "running" ? "Running" : "Persisted"}"></span>
          <span class="run-history-main">
            <strong class="run-history-title">${escapeHtml(label)}</strong>
            <span class="run-history-id">${escapeHtml(subtitle)}</span>
          </span>
          <span class="run-history-side">
            <span>${escapeHtml(formatRunTimestamp(run))}</span>
            <span class="run-history-meta">
              <span>${Number(run.attempts) || Number(run.completed_tasks) || 0} att${best ? ` · ${escapeHtml(best)}` : ""}</span>
              ${current ? '<span class="run-current-chip">current</span>' : ""}
            </span>
          </span>
        </button>`;
    }).join("");
  }

  function toggleRunMenu(force = null) {
    const open = force == null ? !state.runMenuOpen : Boolean(force);
    state.runMenuOpen = open;
    elements.runSelector.classList.toggle("open", open);
    elements.runSelectorTrigger.setAttribute("aria-expanded", String(open));
    elements.runMenu.hidden = !open;
    if (open) refreshRunHistory();
  }

  function switchRun(runId, { pushHistory = true } = {}) {
    const nextRunId = String(runId || "");
    if (!nextRunId || nextRunId === state.selectedRunId) {
      toggleRunMenu(false);
      return;
    }
    state.selectedRunId = nextRunId;
    state.snapshot = null;
    state.lineageSignature = "";
    state.lineageLayout = null;
    state.lineageUserAdjusted = false;
    state.openGuidance.clear();
    state.openNotes.clear();
    state.guidanceInitialized = false;
    state.notesInitialized = false;
    const url = new URL(window.location.href);
    url.searchParams.set("run", nextRunId);
    if (pushHistory) window.history.pushState({ run: nextRunId }, "", url);
    renderRunHistory();
    toggleRunMenu(false);
    setConnected(false);
    elements.connectionLabel.textContent = "Loading";
    refresh();
  }

  function formatRunTimestamp(run) {
    const raw = String(run?.timestamp || run?.id || "");
    const match = raw.match(/(?:^|_)(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/);
    if (match) {
      const [, year, month, day, hour, minute] = match;
      const date = new Date(
        Number(year),
        Number(month) - 1,
        Number(day),
        Number(hour),
        Number(minute),
      );
      return date.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    }
    const started = new Date(run?.started_at || run?.updated_at || "");
    if (!Number.isNaN(started.getTime())) {
      return started.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    }
    return "Unknown time";
  }

  function renderBest(best) {
    if (!best) {
      elements.bestContent.className = "best-content empty-copy";
      elements.bestContent.innerHTML = "<p>No successful candidate has been recorded yet.</p>";
      return;
    }
    const metrics = Object.entries(best.metrics || {})
      .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
      .filter(([key]) => !["overall_score", "fitness", "target_ratio"].includes(key))
      .sort((a, b) => metricPriority(a[0]) - metricPriority(b[0]))
      .slice(0, 6);
    elements.bestContent.className = "best-content";
    elements.bestContent.innerHTML = `
      <div class="best-score">
        <small>Highest observed fitness</small>
        <strong title="${escapeHtml(String(best.fitness))}">${formatFitness(best.fitness)}</strong>
      </div>
      <div class="best-meta">
        <div><span>Task</span><strong>${escapeHtml(best.task_id || "—")}</strong></div>
        <div><span>Attempt</span><strong>${escapeHtml(best.attempt_id || "—")}</strong></div>
        <div><span>Generation</span><strong>${best.generation ?? "—"}</strong></div>
        <div><span>Parent</span><strong>${escapeHtml(best.parent_id || "root")}</strong></div>
      </div>
      <p class="artifact-path" title="${escapeHtml(best.artifact_ref || "")}">${escapeHtml(best.artifact_ref || "No artifact reference")}</p>
      ${metrics.length ? `
        <div class="metrics-title"><span>Core metrics</span><span>${metrics.length} shown</span></div>
        <div class="metrics-grid">
          ${metrics.map(([key, value]) => `
            <div class="metric-pill"><span>${escapeHtml(prettyKey(key))}</span><strong title="${value}">${formatCompact(value)}</strong></div>
          `).join("")}
        </div>` : ""}
    `;
  }

  function renderAgents(agents) {
    elements.agentsCount.textContent = agents.length;
    if (!agents.length) {
      elements.agentsList.innerHTML = '<div class="empty-panel">No active subagents. The run may be complete or waiting to dispatch.</div>';
      return;
    }
    const fragment = document.createDocumentFragment();
    agents.forEach((agent) => {
      const node = elements.agentTemplate.content.cloneNode(true);
      node.querySelector('[data-field="task"]').textContent = agent.task_id;
      node.querySelector('[data-field="agent"]').textContent = agent.agent_id || "pending-id";
      node.querySelector('[data-field="agent"]').title = agent.agent_id || "";
      node.querySelector('[data-field="activity"]').textContent = agent.activity;
      node.querySelector('[data-field="detail"]').textContent = agent.detail;
      node.querySelector('[data-field="attempt"]').textContent = agent.latest_attempt || "starting";
      fragment.appendChild(node);
    });
    elements.agentsList.replaceChildren(fragment);
  }

  function renderActivity(activity) {
    if (!activity.length) {
      elements.activityList.innerHTML = '<div class="empty-panel">Activity will appear as the run advances.</div>';
      return;
    }
    elements.activityList.innerHTML = activity.slice(0, 24).map((item) => {
      const failure = item.status === "failure" ? " failure" : "";
      return `
        <div class="activity-item">
          <time class="activity-time" datetime="${escapeHtml(item.timestamp || "")}">${formatTime(item.timestamp)}</time>
          <span class="activity-rail"><i class="activity-dot ${escapeHtml(item.kind || "system")}${failure}"></i></span>
          <div class="activity-copy">
            <strong>${escapeHtml(item.title || "Run activity")}</strong>
            <p>${escapeHtml(item.detail || "")}</p>
          </div>
        </div>
      `;
    }).join("");
  }

  function renderGuidance(guidance, enabled) {
    elements.guidanceCount.textContent = guidance.length;
    if (!guidance.length) {
      const message = enabled === false
        ? "Meta-guidance is disabled for this run."
        : "No meta-guidance generated yet. It will appear when the search needs a strategic intervention.";
      elements.guidanceList.innerHTML = `<div class="empty-panel">${message}</div>`;
      return;
    }
    const visibleGuidance = guidance.slice(0, 18);
    const keys = visibleGuidance.map((item, index) => guidanceKey(item, index));
    pruneOpenState(state.openGuidance, keys);
    if (!state.guidanceInitialized) {
      const defaultIndex = visibleGuidance.findIndex((item) => (item.lifecycle || item.status) !== "tried");
      if (defaultIndex >= 0) state.openGuidance.add(keys[defaultIndex]);
      state.guidanceInitialized = true;
    }
    elements.guidanceList.innerHTML = visibleGuidance.map((item, index) => {
      const status = item.lifecycle || item.status || "tried";
      const uses = item.max_uses == null ? `${item.uses || 0} uses` : `${item.uses || 0}/${item.max_uses} uses`;
      const relationship = item.relationship_to_current_best || item.approach_type || "strategy";
      const outcome = item.outcome && item.outcome !== "unknown" ? item.outcome : null;
      const key = keys[index];
      return `
        <details class="knowledge-item" data-panel-key="${escapeHtml(key)}" ${state.openGuidance.has(key) ? "open" : ""}>
          <summary>
            <span class="type-mark ${status}">${guidanceMark(status)}</span>
            <span class="knowledge-heading">
              <strong>${escapeHtml(item.idea || item.guidance_id || "Meta-guidance")}</strong>
              <small>${escapeHtml(item.guidance_id || "pending")} · ${escapeHtml(status)} · ${escapeHtml(uses)}</small>
            </span>
            <span class="expand-glyph">+</span>
          </summary>
          <div class="knowledge-body">
            <p>${escapeHtml(item.description || "A new strategic direction has been requested.")}</p>
            ${item.current_best_assessment ? `<p><strong>Assessment.</strong> ${escapeHtml(item.current_best_assessment)}</p>` : ""}
            ${item.novelty_claim ? `<p><strong>Novelty.</strong> ${escapeHtml(item.novelty_claim)}</p>` : ""}
            <div class="tag-row">
              <span class="tag">${escapeHtml(prettyKey(relationship))}</span>
              ${outcome ? `<span class="tag">outcome: ${escapeHtml(prettyKey(outcome))}</span>` : ""}
              ${item.best_fitness_at_creation != null ? `<span class="tag">anchor ${formatCompact(item.best_fitness_at_creation)}</span>` : ""}
            </div>
          </div>
        </details>
      `;
    }).join("");
  }

  function renderNotes(notes, enabled) {
    const counts = { positive: 0, negative: 0, error: 0 };
    notes.forEach((note) => { if (counts[note.note_type] != null) counts[note.note_type] += 1; });
    elements.noteCounts.innerHTML = Object.entries(counts)
      .filter(([, count]) => count > 0)
      .map(([type, count]) => `<span>${type[0].toUpperCase()} ${count}</span>`)
      .join("");
    if (!notes.length) {
      const message = enabled === false
        ? "Run-local Notes are disabled for this run."
        : "No notes recorded yet. Distilled successes, regressions, and repairs will appear here.";
      elements.notesList.innerHTML = `<div class="empty-panel">${message}</div>`;
      return;
    }
    const visibleNotes = notes.slice(0, 22);
    const keys = visibleNotes.map((note, index) => noteKey(note, index));
    pruneOpenState(state.openNotes, keys);
    if (!state.notesInitialized) {
      state.openNotes.add(keys[0]);
      state.notesInitialized = true;
    }
    elements.notesList.innerHTML = visibleNotes.map((note, index) => {
      const type = note.note_type || "error";
      const key = keys[index];
      return `
        <details class="knowledge-item" data-panel-key="${escapeHtml(key)}" ${state.openNotes.has(key) ? "open" : ""}>
          <summary>
            <span class="type-mark ${escapeHtml(type)}">${noteMark(type)}</span>
            <span class="knowledge-heading">
              <strong>${escapeHtml(note.title || "Run-local note")}</strong>
              <small>${escapeHtml(type)} · ${escapeHtml(note.confidence || "medium")} confidence · ${formatTime(note.created_at)}</small>
            </span>
            <span class="expand-glyph">+</span>
          </summary>
          <div class="knowledge-body">
            ${note.lesson ? `<p><strong>Lesson.</strong> ${escapeHtml(note.lesson)}</p>` : ""}
            ${note.actionable_guidance ? `<p><strong>Next action.</strong> ${escapeHtml(note.actionable_guidance)}</p>` : ""}
            ${note.evidence_summary ? `<p><strong>Evidence.</strong> ${escapeHtml(note.evidence_summary)}</p>` : ""}
          </div>
        </details>
      `;
    }).join("");
  }

  const LINEAGE_NODE_W = 176;
  const LINEAGE_NODE_H = 64;
  const LINEAGE_COL_STEP = 265;
  const LINEAGE_ROW_STEP = 86;
  const LINEAGE_PAD = 34;
  const LINEAGE_MIN_SCALE = 0.14;
  const LINEAGE_FOCUS_SCALE = 0.68;
  const LINEAGE_MAX_SCALE = 2.4;
  const LINEAGE_COLORS = ["#167c68", "#527a91", "#a27645", "#75658b", "#4f8176", "#8a665f"];

  function renderLineage(rawLineage) {
    const lineage = {
      nodes: Array.isArray(rawLineage.nodes) ? rawLineage.nodes : [],
      parent_edges: Array.isArray(rawLineage.parent_edges) ? rawLineage.parent_edges : [],
      inspiration_edges: Array.isArray(rawLineage.inspiration_edges) ? rawLineage.inspiration_edges : [],
      roots: Array.isArray(rawLineage.roots) ? rawLineage.roots : [],
      best_id: rawLineage.best_id || null,
    };
    state.lineageData = lineage;
    elements.lineageCount.textContent = String(lineage.nodes.length);
    elements.lineageEmpty.hidden = lineage.nodes.length > 0;
    elements.lineageSummary.textContent = lineage.nodes.length
      ? `${lineage.nodes.length} nodes · ${lineage.roots.length} roots · ${lineage.inspiration_edges.length} inspiration links`
      : "No lineage nodes yet";

    const signature = JSON.stringify(lineage);
    if (signature === state.lineageSignature) return;
    state.lineageSignature = signature;
    hideLineageHover();

    if (!lineage.nodes.length) {
      elements.lineageStage.innerHTML = "";
      state.lineageLayout = null;
      return;
    }

    const layout = computeLineageLayout(lineage);
    state.lineageLayout = layout;
    elements.lineageStage.style.width = `${layout.width}px`;
    elements.lineageStage.style.height = `${layout.height}px`;
    elements.lineageStage.innerHTML = lineageSvg(lineage, layout);
    applyLineageTransform();

    if (!state.lineageUserAdjusted) {
      requestAnimationFrame(() => focusLineage(false));
    }
  }

  function computeLineageLayout(lineage) {
    const byId = new Map(lineage.nodes.map((node) => [String(node.id), node]));
    const children = new Map();
    lineage.parent_edges.forEach((edge) => {
      const source = String(edge.source);
      const target = String(edge.target);
      if (!byId.has(source) || !byId.has(target)) return;
      if (!children.has(source)) children.set(source, []);
      children.get(source).push(target);
    });
    children.forEach((items) => items.sort((left, right) => {
      const a = byId.get(left) || {};
      const b = byId.get(right) || {};
      return String(a.created_at || a.id).localeCompare(String(b.created_at || b.id));
    }));

    const positions = new Map();
    const visiting = new Set();
    let nextLeaf = 0;
    let maxGeneration = 0;

    function walk(id) {
      if (positions.has(id)) return positions.get(id).y;
      const node = byId.get(id);
      if (!node) return LINEAGE_PAD + nextLeaf++ * LINEAGE_ROW_STEP;
      if (visiting.has(id)) {
        const y = LINEAGE_PAD + nextLeaf++ * LINEAGE_ROW_STEP;
        positions.set(id, { x: LINEAGE_PAD, y, node });
        return y;
      }
      visiting.add(id);
      const childIds = (children.get(id) || []).filter((childId) => !visiting.has(childId));
      const childYs = childIds.map(walk);
      const y = childYs.length
        ? (childYs[0] + childYs[childYs.length - 1]) / 2
        : LINEAGE_PAD + nextLeaf++ * LINEAGE_ROW_STEP;
      const generation = Math.max(0, Number(node.generation) || 0);
      maxGeneration = Math.max(maxGeneration, generation);
      positions.set(id, {
        x: LINEAGE_PAD + generation * LINEAGE_COL_STEP,
        y,
        node,
      });
      visiting.delete(id);
      return y;
    }

    const roots = lineage.roots.filter((id) => byId.has(String(id))).map(String);
    roots.forEach((rootId, index) => {
      if (index > 0 && nextLeaf > 0) nextLeaf += 0.65;
      walk(rootId);
    });
    lineage.nodes.forEach((node) => {
      const id = String(node.id);
      if (!positions.has(id)) {
        if (nextLeaf > 0) nextLeaf += 0.35;
        walk(id);
      }
    });

    const maxY = Math.max(...[...positions.values()].map((position) => position.y));
    return {
      positions,
      width: LINEAGE_PAD * 2 + maxGeneration * LINEAGE_COL_STEP + LINEAGE_NODE_W,
      height: Math.max(LINEAGE_PAD * 2 + LINEAGE_NODE_H, maxY + LINEAGE_NODE_H + LINEAGE_PAD),
    };
  }

  function lineageSvg(lineage, layout) {
    const rootIds = lineage.roots.map(String);
    const rootIndex = new Map(rootIds.map((id, index) => [id, index]));
    const rootColor = (node) => LINEAGE_COLORS[(rootIndex.get(String(node.root_id)) || 0) % LINEAGE_COLORS.length];

    const parentEdges = lineage.parent_edges.map((edge) => {
      const source = layout.positions.get(String(edge.source));
      const target = layout.positions.get(String(edge.target));
      if (!source || !target) return "";
      const color = rootColor(target.node);
      const path = lineageEdgePath(source, target, false);
      const endX = target.x;
      const endY = target.y + LINEAGE_NODE_H / 2;
      return `<g style="--edge-color:${color}88"><path class="lineage-parent-edge" d="${path}"></path><circle class="lineage-parent-end" cx="${endX}" cy="${endY}" r="2.5"></circle></g>`;
    }).join("");

    const inspirationEdges = lineage.inspiration_edges.map((edge) => {
      const source = layout.positions.get(String(edge.source));
      const target = layout.positions.get(String(edge.target));
      if (!source || !target) return "";
      return `<path class="lineage-inspiration-edge" data-inspiration-target="${escapeHtml(String(edge.target))}" data-inspiration-source="${escapeHtml(String(edge.source))}" d="${lineageEdgePath(source, target, true)}"></path>`;
    }).join("");

    const nodes = [...layout.positions.values()].map((position) => {
      const node = position.node;
      const id = String(node.id);
      const color = rootColor(node);
      const status = String(node.status || "pending").toLowerCase();
      const statusColor = status === "running"
        ? "#527a91"
        : status === "success"
          ? "#22a889"
          : status === "failure"
            ? "#a7524d"
            : "#9aa39f";
      const classes = [
        "lineage-node",
        status === "failure" ? "is-failure" : "",
        node.is_best ? "is-best" : "",
      ].filter(Boolean).join(" ");
      const fitness = node.fitness == null
        ? status === "running" ? "exploring" : "no score"
        : formatCompact(node.fitness);
      const bestMark = node.is_best
        ? `<circle class="lineage-best-trophy-ring" cx="156" cy="16" r="10"></circle><text class="lineage-best-trophy" x="156" y="20" text-anchor="middle">🏆</text>`
        : "";
      const generationX = node.is_best ? 136 : 161;
      const title = [
        id,
        `fitness ${node.fitness == null ? "—" : node.fitness}`,
        `generation ${node.generation ?? 0}`,
        `parent ${node.parent_id || "root"}`,
      ].join(" · ");
      return `
        <g class="${classes}" data-node-id="${escapeHtml(id)}" tabindex="0" role="img" aria-label="${escapeHtml(title)}" transform="translate(${position.x} ${position.y})" style="--node-color:${color};--status-color:${statusColor}">
          <rect class="lineage-node-shell" width="${LINEAGE_NODE_W}" height="${LINEAGE_NODE_H}" rx="11"></rect>
          <rect class="lineage-node-accent" x="1.5" y="9" width="3.5" height="46" rx="1.75"></rect>
          <text class="lineage-node-id" x="15" y="20">${escapeHtml(id)}</text>
          <text class="lineage-node-generation" x="${generationX}" y="19" text-anchor="end">G${Number(node.generation) || 0}</text>
          <text class="lineage-node-fitness" x="15" y="47">${escapeHtml(fitness)}</text>
          <circle class="lineage-status-dot ${status === "running" ? "running" : ""}" cx="121" cy="44" r="3"></circle>
          <text class="lineage-node-status" x="129" y="47">${escapeHtml(status)}</text>
          ${bestMark}
        </g>`;
    }).join("");

    // Keep inspiration edges in the DOM but invisible until their target node is hovered.
    return `
      <svg width="${layout.width}" height="${layout.height}" viewBox="0 0 ${layout.width} ${layout.height}" role="img" aria-label="Evolution lineage with ${lineage.nodes.length} nodes">
        <g class="lineage-parent-layer">${parentEdges}</g>
        <g class="lineage-inspiration-layer">${inspirationEdges}</g>
        <g class="lineage-node-layer">${nodes}</g>
      </svg>`;
  }

  function lineageEdgePath(source, target, inspiration) {
    const x1 = source.x + LINEAGE_NODE_W;
    const y1 = source.y + (inspiration ? LINEAGE_NODE_H * 0.72 : LINEAGE_NODE_H / 2);
    const x2 = target.x;
    const y2 = target.y + (inspiration ? LINEAGE_NODE_H * 0.28 : LINEAGE_NODE_H / 2);
    const distance = x2 - x1;
    if (distance > 30) {
      const curve = Math.max(42, distance * 0.5);
      return `M ${x1} ${y1} C ${x1 + curve} ${y1}, ${x2 - curve} ${y2}, ${x2} ${y2}`;
    }
    // Backward and same-generation inspiration links take a soft outer loop.
    // Both cubic segments meet with the same vertical tangent, avoiding a cusp.
    const loopX = Math.max(x1, x2) + Math.min(140, 68 + Math.abs(y2 - y1) * 0.12);
    const middleY = (y1 + y2) / 2;
    return `M ${x1} ${y1} C ${x1 + 38} ${y1}, ${loopX} ${y1}, ${loopX} ${middleY} C ${loopX} ${y2}, ${x2 - 38} ${y2}, ${x2} ${y2}`;
  }

  function applyLineageTransform() {
    elements.lineageStage.style.transform = `translate(${state.lineagePan.x}px, ${state.lineagePan.y}px) scale(${state.lineageScale})`;
    elements.lineageScale.textContent = `${Math.round(state.lineageScale * 100)}%`;
  }

  function fitLineage(markAdjusted = true) {
    if (!state.lineageLayout) return;
    const rect = elements.lineageViewport.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const scale = Math.min(
      1,
      (rect.width - 42) / state.lineageLayout.width,
      (rect.height - 42) / state.lineageLayout.height,
    );
    state.lineageScale = Math.max(LINEAGE_MIN_SCALE, Math.min(LINEAGE_MAX_SCALE, scale));
    state.lineagePan = {
      x: Math.max(21, (rect.width - state.lineageLayout.width * state.lineageScale) / 2),
      y: Math.max(21, (rect.height - state.lineageLayout.height * state.lineageScale) / 2),
    };
    if (markAdjusted) state.lineageUserAdjusted = true;
    applyLineageTransform();
  }

  function focusLineage(markAdjusted = false) {
    if (!state.lineageLayout) return;
    const rect = elements.lineageViewport.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const focusId = String(state.lineageData.best_id || state.lineageData.roots[0] || "");
    const focus = state.lineageLayout.positions.get(focusId)
      || state.lineageLayout.positions.values().next().value;
    if (!focus) return;

    state.lineageScale = Math.max(
      LINEAGE_MIN_SCALE,
      Math.min(LINEAGE_MAX_SCALE, LINEAGE_FOCUS_SCALE),
    );
    const scaledWidth = state.lineageLayout.width * state.lineageScale;
    const scaledHeight = state.lineageLayout.height * state.lineageScale;
    const centeredX = rect.width * 0.72 - (focus.x + LINEAGE_NODE_W / 2) * state.lineageScale;
    const centeredY = rect.height / 2 - (focus.y + LINEAGE_NODE_H / 2) * state.lineageScale;
    state.lineagePan = {
      x: scaledWidth <= rect.width - 42
        ? (rect.width - scaledWidth) / 2
        : Math.min(21, Math.max(rect.width - scaledWidth - 21, centeredX)),
      y: scaledHeight <= rect.height - 42
        ? (rect.height - scaledHeight) / 2
        : Math.min(21, Math.max(rect.height - scaledHeight - 21, centeredY)),
    };
    if (markAdjusted) state.lineageUserAdjusted = true;
    applyLineageTransform();
  }

  function zoomLineage(factor, clientX = null, clientY = null) {
    if (!state.lineageLayout) return;
    const rect = elements.lineageViewport.getBoundingClientRect();
    const anchorX = clientX == null ? rect.width / 2 : clientX - rect.left;
    const anchorY = clientY == null ? rect.height / 2 : clientY - rect.top;
    const previous = state.lineageScale;
    const next = Math.max(LINEAGE_MIN_SCALE, Math.min(LINEAGE_MAX_SCALE, previous * factor));
    if (next === previous) return;
    const worldX = (anchorX - state.lineagePan.x) / previous;
    const worldY = (anchorY - state.lineagePan.y) / previous;
    state.lineageScale = next;
    state.lineagePan = {
      x: anchorX - worldX * next,
      y: anchorY - worldY * next,
    };
    state.lineageUserAdjusted = true;
    applyLineageTransform();
  }

  function showLineageHover(nodeId, event = null) {
    const id = String(nodeId);
    if (state.lineageHoveredId === id && event) {
      positionLineageTooltip(event);
      return;
    }
    hideLineageHover();
    state.lineageHoveredId = id;
    const sourceIds = new Set();
    elements.lineageStage.querySelectorAll(".lineage-inspiration-edge").forEach((edge) => {
      const visible = edge.dataset.inspirationTarget === id;
      edge.classList.toggle("visible", visible);
      if (visible) sourceIds.add(edge.dataset.inspirationSource);
    });
    elements.lineageStage.querySelectorAll(".lineage-node").forEach((node) => {
      node.classList.toggle("hovered", node.dataset.nodeId === id);
      node.classList.toggle("inspiration-source", sourceIds.has(node.dataset.nodeId));
    });

    const node = state.lineageData.nodes.find((item) => String(item.id) === id);
    if (!node) return;
    const inspirations = node.inspiration_ids || [];
    elements.lineageTooltip.innerHTML = `
      <strong>${escapeHtml(id)}</strong>
      <div class="lineage-tooltip-grid">
        <span>fitness</span><span>${node.fitness == null ? "—" : escapeHtml(formatFitness(node.fitness))}</span>
        <span>generation</span><span>G${Number(node.generation) || 0}</span>
        <span>parent</span><span>${escapeHtml(node.parent_id || "root")}</span>
        <span>status</span><span>${escapeHtml(prettyKey(node.status || "pending"))}</span>
        <span>inspirations</span><span>${inspirations.length ? escapeHtml(inspirations.join(", ")) : "none"}</span>
      </div>
      <div class="lineage-tooltip-note">${inspirations.length ? "Dashed links show the inspirations used by this task." : "This task did not use additional inspirations."}</div>`;
    elements.lineageTooltip.hidden = false;
    if (event) positionLineageTooltip(event);
  }

  function hideLineageHover() {
    state.lineageHoveredId = null;
    elements.lineageTooltip.hidden = true;
    elements.lineageStage.querySelectorAll(".lineage-inspiration-edge.visible").forEach((edge) => edge.classList.remove("visible"));
    elements.lineageStage.querySelectorAll(".lineage-node.hovered, .lineage-node.inspiration-source").forEach((node) => {
      node.classList.remove("hovered", "inspiration-source");
    });
  }

  function positionLineageTooltip(event) {
    if (elements.lineageTooltip.hidden) return;
    const rect = elements.lineageViewport.getBoundingClientRect();
    const width = 224;
    const height = elements.lineageTooltip.offsetHeight || 150;
    const left = Math.min(Math.max(event.clientX - rect.left + 14, 8), rect.width - width - 8);
    const top = Math.min(Math.max(event.clientY - rect.top + 14, 8), rect.height - height - 8);
    elements.lineageTooltip.style.left = `${left}px`;
    elements.lineageTooltip.style.top = `${top}px`;
  }

  async function toggleLineageFullscreen() {
    try {
      if (document.fullscreenElement === elements.lineageCard) {
        await document.exitFullscreen();
      } else {
        await elements.lineageCard.requestFullscreen();
      }
    } catch (error) {
      console.warn("Unable to toggle lineage fullscreen", error);
    }
  }

  function updateLineageFullscreenControl() {
    const active = document.fullscreenElement === elements.lineageCard;
    elements.lineageFullscreen.classList.toggle("active", active);
    elements.lineageFullscreen.setAttribute("aria-label", active ? "Exit lineage fullscreen" : "Show lineage fullscreen");
    elements.lineageFullscreen.title = active ? "Exit fullscreen" : "Show lineage fullscreen";
    const path = elements.lineageFullscreen.querySelector("path");
    if (path) {
      path.setAttribute("d", active
        ? "M9 3v6H3M15 3v6h6M9 21v-6H3M15 21v-6h6"
        : "M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5");
    }
    requestAnimationFrame(() => focusLineage(false));
  }

  function renderChart(progress) {
    const points = progress.filter((item) => item.fitness != null && Number.isFinite(item.fitness));
    state.chartPoints = points;
    elements.chartEmpty.hidden = points.length > 0;
    elements.chart.style.visibility = points.length ? "visible" : "hidden";
    if (!points.length) {
      elements.chartRange.textContent = "No observations yet";
      state.chartGeometry = null;
      return;
    }
    elements.chartUpdated.textContent = `Updated ${formatTime(state.snapshot.generated_at, true)}`;
    drawChart(points);
  }

  function percentile(sortedValues, position) {
    if (!sortedValues.length) return 0;
    const index = (sortedValues.length - 1) * position;
    const lower = Math.floor(index);
    const upper = Math.ceil(index);
    if (lower === upper) return sortedValues[lower];
    const weight = index - lower;
    return sortedValues[lower] * (1 - weight) + sortedValues[upper] * weight;
  }

  function paddedDomain(min, max) {
    const span = max - min;
    const margin = span > 0 ? span * 0.12 : Math.max(Math.abs(max) * 0.08, 0.05);
    return { min: min - margin, max: max + margin };
  }

  function computeChartDomain(points, requestedMode) {
    const values = points.map((point) => point.fitness).filter(Number.isFinite);
    const sorted = [...values].sort((left, right) => left - right);
    const rawMin = sorted[0];
    const rawMax = sorted[sorted.length - 1];
    const full = paddedDomain(rawMin, rawMax);
    const fullResult = {
      ...full,
      mode: "full",
      rawMin,
      rawMax,
      lowOutliers: 0,
      highOutliers: 0,
    };

    if (requestedMode === "full" || sorted.length < 8) return fullResult;

    const q1 = percentile(sorted, 0.25);
    const q3 = percentile(sorted, 0.75);
    const iqr = q3 - q1;
    if (!(iqr > 0)) return fullResult;

    const lowerFence = q1 - 1.5 * iqr;
    const upperFence = q3 + 1.5 * iqr;
    const inliers = sorted.filter((value) => value >= lowerFence && value <= upperFence);
    if (inliers.length < 3 || inliers.length === sorted.length) return fullResult;

    // The global best is always visible, even when it is statistically unusual.
    const focusMin = Math.min(...inliers);
    const focusMax = Math.max(...inliers, rawMax);
    const focusSpan = focusMax - focusMin;
    const rawSpan = rawMax - rawMin;
    if (!(focusSpan > 0) || rawSpan < focusSpan * 2.4) return fullResult;

    const focus = paddedDomain(focusMin, focusMax);
    return {
      ...focus,
      mode: "focus",
      rawMin,
      rawMax,
      lowOutliers: values.filter((value) => value < focus.min).length,
      highOutliers: values.filter((value) => value > focus.max).length,
    };
  }

  function updateChartScaleControls() {
    const focus = state.chartScaleMode === "focus";
    elements.chartScaleFocus.classList.toggle("active", focus);
    elements.chartScaleFull.classList.toggle("active", !focus);
    elements.chartScaleFocus.setAttribute("aria-pressed", String(focus));
    elements.chartScaleFull.setAttribute("aria-pressed", String(!focus));
  }

  function drawChart(points) {
    const canvas = elements.chart;
    const rect = elements.chartWrap.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(Math.floor(rect.width * dpr), 1);
    canvas.height = Math.max(Math.floor(rect.height * dpr), 1);
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    const pad = { left: 54, right: 25, top: 25, bottom: 36 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const domain = computeChartDomain(points, state.chartScaleMode);
    const yMin = domain.min;
    const yMax = domain.max;
    const outlierCount = domain.lowOutliers + domain.highOutliers;
    const outlierParts = [
      domain.lowOutliers ? `${domain.lowOutliers} low` : "",
      domain.highOutliers ? `${domain.highOutliers} high` : "",
    ].filter(Boolean);
    const rangeLabel = domain.mode === "focus"
      ? `focus ${formatCompact(yMin)}–${formatCompact(yMax)} · ${outlierParts.join(" / ")} ${outlierCount === 1 ? "outlier" : "outliers"}`
      : state.chartScaleMode === "focus"
        ? `focus · no compression needed · ${formatCompact(domain.rawMin)}–${formatCompact(domain.rawMax)}`
        : `full ${formatCompact(domain.rawMin)}–${formatCompact(domain.rawMax)}`;
    elements.chartRange.textContent = `${points.length} scored tasks · ${rangeLabel}`;

    const x = (index) => pad.left + (points.length === 1 ? plotW / 2 : (index / (points.length - 1)) * plotW);
    const y = (value) => {
      const clamped = Math.min(Math.max(value, yMin), yMax);
      return pad.top + (1 - (clamped - yMin) / (yMax - yMin)) * plotH;
    };
    const styles = getComputedStyle(document.documentElement);
    const line = styles.getPropertyValue("--line").trim();
    const muted = styles.getPropertyValue("--muted").trim();
    const accent = styles.getPropertyValue("--accent").trim();
    const accentBright = styles.getPropertyValue("--accent-bright").trim();
    const blue = styles.getPropertyValue("--blue").trim();

    ctx.clearRect(0, 0, width, height);
    ctx.font = '9px "SFMono-Regular", Consolas, monospace';
    ctx.textBaseline = "middle";

    for (let i = 0; i <= 4; i += 1) {
      const yy = pad.top + (plotH / 4) * i;
      const value = yMax - ((yMax - yMin) / 4) * i;
      ctx.beginPath();
      ctx.strokeStyle = line;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 5]);
      ctx.moveTo(pad.left, yy);
      ctx.lineTo(width - pad.right, yy);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = muted;
      ctx.textAlign = "right";
      ctx.fillText(formatAxis(value), pad.left - 10, yy);
    }

    const xTickCount = Math.min(points.length, 6);
    for (let i = 0; i < xTickCount; i += 1) {
      const pointIndex = xTickCount === 1 ? 0 : Math.round((i / (xTickCount - 1)) * (points.length - 1));
      ctx.fillStyle = muted;
      ctx.textAlign = "center";
      ctx.fillText(String(points[pointIndex].index), x(pointIndex), height - 15);
    }

    // Raw task fitness — intentionally quiet so the best-so-far signal leads.
    ctx.beginPath();
    points.forEach((point, index) => {
      const command = index === 0 ? "moveTo" : "lineTo";
      ctx[command](x(index), y(point.fitness));
    });
    ctx.strokeStyle = `${blue}66`;
    ctx.lineWidth = 1.25;
    ctx.stroke();

    points.forEach((point, index) => {
      const belowRange = point.fitness < yMin;
      const aboveRange = point.fitness > yMax;
      if (belowRange || aboveRange) {
        drawBoundaryMarker(
          ctx,
          x(index),
          belowRange ? height - pad.bottom - 1 : pad.top + 1,
          belowRange ? "low" : "high",
          `${blue}aa`,
        );
        return;
      }
      ctx.beginPath();
      ctx.arc(x(index), y(point.fitness), point.is_new_best ? 3.2 : 2.2, 0, Math.PI * 2);
      ctx.fillStyle = point.is_new_best ? accentBright : `${blue}88`;
      ctx.fill();
      ctx.strokeStyle = "rgba(250,251,247,.9)";
      ctx.lineWidth = 1;
      ctx.stroke();
    });

    // Subtle area below best-so-far.
    const gradient = ctx.createLinearGradient(0, pad.top, 0, height - pad.bottom);
    gradient.addColorStop(0, "rgba(22, 124, 104, 0.16)");
    gradient.addColorStop(1, "rgba(22, 124, 104, 0.01)");
    ctx.beginPath();
    points.forEach((point, index) => {
      const command = index === 0 ? "moveTo" : "lineTo";
      ctx[command](x(index), y(point.best_so_far));
    });
    ctx.lineTo(x(points.length - 1), height - pad.bottom);
    ctx.lineTo(x(0), height - pad.bottom);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.beginPath();
    points.forEach((point, index) => {
      const command = index === 0 ? "moveTo" : "lineTo";
      ctx[command](x(index), y(point.best_so_far));
    });
    ctx.strokeStyle = accent;
    ctx.lineWidth = 2.2;
    ctx.lineJoin = "round";
    ctx.stroke();

    const bestValue = Math.max(...points.map((point) => point.fitness));
    const bestIndex = points.findIndex((point) => point.fitness === bestValue);
    const bx = x(bestIndex);
    const by = y(bestValue);
    ctx.beginPath();
    ctx.arc(bx, by, 6, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(250,251,247,.94)";
    ctx.fill();
    ctx.strokeStyle = accent;
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(bx, by, 2.3, 0, Math.PI * 2);
    ctx.fillStyle = accentBright;
    ctx.fill();

    const label = `BEST  ${formatCompact(bestValue)}`;
    ctx.font = '600 9px "SFMono-Regular", Consolas, monospace';
    const labelW = ctx.measureText(label).width + 14;
    const labelX = Math.min(Math.max(bx - labelW / 2, pad.left), width - pad.right - labelW);
    const labelY = Math.max(by - 31, 5);
    roundRect(ctx, labelX, labelY, labelW, 20, 6);
    ctx.fillStyle = "rgba(23,32,30,.92)";
    ctx.fill();
    ctx.fillStyle = "#b7efdf";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, labelX + labelW / 2, labelY + 10.5);

    state.chartGeometry = { x, y, pad, width, height, points, domain };
  }

  function drawBoundaryMarker(ctx, x, y, direction, color) {
    const base = direction === "low" ? y - 7 : y + 7;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x - 4.5, base);
    ctx.lineTo(x + 4.5, base);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "rgba(250,251,247,.92)";
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function handleChartPointer(event) {
    if (!state.chartGeometry) return;
    const rect = elements.chart.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const points = state.chartGeometry.points;
    const index = points.length === 1
      ? 0
      : Math.max(0, Math.min(points.length - 1, Math.round(((px - state.chartGeometry.pad.left) / (rect.width - state.chartGeometry.pad.left - state.chartGeometry.pad.right)) * (points.length - 1))));
    const point = points[index];
    const tx = state.chartGeometry.x(index);
    const ty = state.chartGeometry.y(point.fitness);
    const outsideFocus = state.chartGeometry.domain.mode === "focus"
      && (point.fitness < state.chartGeometry.domain.min || point.fitness > state.chartGeometry.domain.max);
    elements.chartTooltip.innerHTML = `
      <strong>${escapeHtml(point.task_id)}</strong>
      fitness&nbsp; ${formatFitness(point.fitness)}<br>
      best&nbsp;&nbsp;&nbsp;&nbsp; ${formatFitness(point.best_so_far)}
      ${outsideFocus ? "<br>outside focused range" : ""}
    `;
    elements.chartTooltip.hidden = false;
    const tooltipWidth = 155;
    elements.chartTooltip.style.left = `${Math.min(Math.max(tx + 12, 6), rect.width - tooltipWidth)}px`;
    elements.chartTooltip.style.top = `${Math.max(ty - 36, 5)}px`;
  }

  function hideChartTooltip() {
    elements.chartTooltip.hidden = true;
  }

  function roundRect(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + width, y, x + width, y + height, r);
    ctx.arcTo(x + width, y + height, x, y + height, r);
    ctx.arcTo(x, y + height, x, y, r);
    ctx.arcTo(x, y, x + width, y, r);
    ctx.closePath();
  }

  function formatFitness(value) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    const number = Number(value);
    if (Math.abs(number) >= 10000 || (Math.abs(number) > 0 && Math.abs(number) < 0.0001)) {
      return number.toExponential(4);
    }
    return number.toPrecision(7).replace(/(?:\.0+|(?:(\.\d*?)0+))$/, "$1");
  }

  function formatCompact(value) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    const number = Number(value);
    if (Math.abs(number) >= 1000) return number.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return number.toPrecision(5).replace(/(?:\.0+|(?:(\.\d*?)0+))$/, "$1");
  }

  function formatAxis(value) {
    const abs = Math.abs(value);
    if (abs >= 1000 || (abs > 0 && abs < 0.001)) return value.toExponential(1);
    return Number(value.toPrecision(4)).toString();
  }

  function formatTime(value, withSeconds = false) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: withSeconds ? "2-digit" : undefined,
    });
  }

  function humanizeState(value) {
    return prettyKey(value || "initializing").replace(/^./, (char) => char.toUpperCase());
  }

  function renderFeatureStatus(element, enabled) {
    const known = typeof enabled === "boolean";
    element.className = `feature-status ${known ? (enabled ? "enabled" : "disabled") : ""}`;
    element.querySelector("span").textContent = known ? (enabled ? "Enabled" : "Disabled") : "Unknown";
    element.title = known
      ? `Feature is ${enabled ? "enabled" : "disabled"} for this run`
      : "Feature status was not recorded for this run";
  }

  function statusHeadline(runState, completed) {
    if (runState === "stopped") return "Evolution complete";
    if (runState === "watching") return "Watching for a breakthrough";
    if (runState === "plateau") return "Reframing the search";
    if (completed > 0) return "Evolution in progress";
    return "Preparing the run";
  }

  function prettyKey(key) {
    return String(key || "").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function metricPriority(key) {
    const order = ["accuracy_score", "composite_score", "success_rate", "validity", "eval_time"];
    const index = order.indexOf(key);
    return index === -1 ? 100 : index;
  }

  function guidanceMark(status) {
    if (status === "active") return "↗";
    if (status === "pending") return "…";
    return "✓";
  }

  function noteMark(type) {
    if (type === "positive") return "+";
    if (type === "negative") return "−";
    return "!";
  }

  function guidanceKey(item, index) {
    return String(item.guidance_id || `${item.created_at || "guidance"}-${index}`);
  }

  function noteKey(note, index) {
    return String(note.note_id || `${note.created_at || "note"}-${index}`);
  }

  function pruneOpenState(openState, validKeys) {
    const valid = new Set(validKeys);
    for (const key of openState) {
      if (!valid.has(key)) openState.delete(key);
    }
  }

  function trackDetailsToggle(event, openState) {
    const details = event.target;
    if (!details.matches?.("details[data-panel-key]")) return;
    const key = details.dataset.panelKey;
    if (!key) return;
    if (details.open) openState.add(key);
    else openState.delete(key);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (state.chartPoints.length) drawChart(state.chartPoints);
      if (state.lineageLayout && !state.lineageUserAdjusted) focusLineage(false);
    }, 120);
  });
  elements.chart.addEventListener("pointermove", handleChartPointer);
  elements.chart.addEventListener("pointerleave", hideChartTooltip);
  elements.chartScaleFocus.addEventListener("click", () => {
    state.chartScaleMode = "focus";
    updateChartScaleControls();
    if (state.chartPoints.length) drawChart(state.chartPoints);
  });
  elements.chartScaleFull.addEventListener("click", () => {
    state.chartScaleMode = "full";
    updateChartScaleControls();
    if (state.chartPoints.length) drawChart(state.chartPoints);
  });
  elements.lineageViewport.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button, .lineage-tooltip")) return;
    event.preventDefault();
    state.lineageSuppressClick = false;
    state.lineagePanStart = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      panX: state.lineagePan.x,
      panY: state.lineagePan.y,
      moved: false,
    };
    elements.lineageViewport.classList.add("panning");
    elements.lineageViewport.setPointerCapture(event.pointerId);
  });
  elements.lineageViewport.addEventListener("pointermove", (event) => {
    if (state.lineagePanStart?.pointerId === event.pointerId) {
      const deltaX = event.clientX - state.lineagePanStart.clientX;
      const deltaY = event.clientY - state.lineagePanStart.clientY;
      if (!state.lineagePanStart.moved && Math.hypot(deltaX, deltaY) > 3) {
        state.lineagePanStart.moved = true;
        state.lineageSuppressClick = true;
        hideLineageHover();
      }
      state.lineagePan = {
        x: state.lineagePanStart.panX + deltaX,
        y: state.lineagePanStart.panY + deltaY,
      };
      state.lineageUserAdjusted = true;
      applyLineageTransform();
    } else if (state.lineageHoveredId) {
      positionLineageTooltip(event);
    }
  });
  const finishLineagePan = (event) => {
    if (state.lineagePanStart?.pointerId !== event.pointerId) return;
    const moved = state.lineagePanStart.moved;
    state.lineagePanStart = null;
    elements.lineageViewport.classList.remove("panning");
    if (elements.lineageViewport.hasPointerCapture(event.pointerId)) {
      elements.lineageViewport.releasePointerCapture(event.pointerId);
    }
    if (moved) window.setTimeout(() => { state.lineageSuppressClick = false; }, 0);
  };
  elements.lineageViewport.addEventListener("pointerup", finishLineagePan);
  elements.lineageViewport.addEventListener("pointercancel", finishLineagePan);
  elements.lineageViewport.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomLineage(event.deltaY > 0 ? 0.9 : 1.1, event.clientX, event.clientY);
  }, { passive: false });
  elements.lineageViewport.addEventListener("dblclick", (event) => {
    if (!event.target.closest(".lineage-node")) fitLineage();
  });
  elements.lineageViewport.addEventListener("keydown", (event) => {
    if (event.key === "+" || event.key === "=") zoomLineage(1.18);
    else if (event.key === "-") zoomLineage(1 / 1.18);
    else if (event.key === "0") fitLineage();
    else return;
    event.preventDefault();
  });
  elements.lineageStage.addEventListener("pointerover", (event) => {
    const node = event.target.closest(".lineage-node");
    if (node) showLineageHover(node.dataset.nodeId, event);
  });
  elements.lineageStage.addEventListener("pointerout", (event) => {
    const from = event.target.closest(".lineage-node");
    const to = event.relatedTarget?.closest?.(".lineage-node");
    if (from && from !== to) hideLineageHover();
  });
  elements.lineageStage.addEventListener("click", (event) => {
    if (state.lineageSuppressClick) return;
    const node = event.target.closest(".lineage-node");
    if (!node) return;
    showLineageHover(node.dataset.nodeId, event);
  });
  elements.lineageViewport.addEventListener("dragstart", (event) => event.preventDefault());
  elements.lineageViewport.addEventListener("selectstart", (event) => event.preventDefault());
  elements.lineageStage.addEventListener("focusin", (event) => {
    const node = event.target.closest(".lineage-node");
    if (!node) return;
    const rect = node.getBoundingClientRect();
    showLineageHover(node.dataset.nodeId, {
      clientX: rect.right,
      clientY: rect.top + rect.height / 2,
    });
  });
  elements.lineageStage.addEventListener("focusout", (event) => {
    if (event.target.closest(".lineage-node")) hideLineageHover();
  });
  elements.lineageZoomIn.addEventListener("click", () => zoomLineage(1.18));
  elements.lineageZoomOut.addEventListener("click", () => zoomLineage(1 / 1.18));
  elements.lineageFit.addEventListener("click", () => fitLineage());
  elements.lineageFullscreen.addEventListener("click", toggleLineageFullscreen);
  document.addEventListener("fullscreenchange", updateLineageFullscreenControl);
  elements.runSelectorTrigger.addEventListener("click", () => toggleRunMenu());
  elements.runHistoryList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-run-id]");
    if (item) switchRun(item.dataset.runId);
  });
  document.addEventListener("pointerdown", (event) => {
    if (state.runMenuOpen && !elements.runSelector.contains(event.target)) {
      toggleRunMenu(false);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.runMenuOpen) toggleRunMenu(false);
  });
  window.addEventListener("popstate", () => {
    const requested = new URLSearchParams(window.location.search).get("run");
    const nextRunId = requested || state.launchRunId;
    if (nextRunId && nextRunId !== state.selectedRunId) {
      switchRun(nextRunId, { pushHistory: false });
    }
  });
  elements.refresh.addEventListener("click", () => {
    refresh();
    refreshRunHistory();
  });
  elements.guidanceList.addEventListener("toggle", (event) => trackDetailsToggle(event, state.openGuidance), true);
  elements.notesList.addEventListener("toggle", (event) => trackDetailsToggle(event, state.openNotes), true);

  updateChartScaleControls();
  refreshRunHistory();
  refresh();
  state.pollTimer = window.setInterval(refresh, POLL_INTERVAL);
})();
