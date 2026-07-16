(() => {
  "use strict";

  const POLL_INTERVAL = 2000;
  const state = {
    snapshot: null,
    loading: false,
    connected: false,
    chartPoints: [],
    chartGeometry: null,
    pollTimer: null,
    openGuidance: new Set(),
    openNotes: new Set(),
    guidanceInitialized: false,
    notesInitialized: false,
  };

  const $ = (id) => document.getElementById(id);
  const elements = {
    runId: $("run-id"),
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
    bestContent: $("best-content"),
    agentsCount: $("agents-count"),
    agentsList: $("agents-list"),
    activityList: $("activity-list"),
    guidanceCount: $("guidance-count"),
    guidanceList: $("guidance-list"),
    noteCounts: $("note-counts"),
    notesList: $("notes-list"),
    footerTime: $("footer-time"),
    agentTemplate: $("agent-template"),
  };

  async function refresh() {
    if (state.loading) return;
    state.loading = true;
    elements.refresh.classList.add("loading");
    try {
      const response = await fetch("/api/snapshot", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const snapshot = await response.json();
      state.snapshot = snapshot;
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

  function setConnected(connected) {
    state.connected = connected;
    elements.connectionPill.classList.toggle("connected", connected);
    elements.connectionPill.classList.toggle("disconnected", !connected);
    elements.connectionLabel.textContent = connected ? "Live" : "Reconnecting";
  }

  function render(snapshot) {
    renderRun(snapshot);
    renderChart(snapshot.progress || []);
    renderBest(snapshot.best);
    renderAgents(snapshot.agents || []);
    renderActivity(snapshot.activity || []);
    renderGuidance(snapshot.guidance || []);
    renderNotes(snapshot.notes || []);
  }

  function renderRun(snapshot) {
    const run = snapshot.run || {};
    const diagnostics = snapshot.diagnostics || {};
    const best = snapshot.best;
    const max = run.max_total_tasks;
    const completed = run.completed_tasks || 0;
    const progressPct = max ? Math.min((completed / max) * 100, 100) : 0;
    const displayState = humanizeState(run.state);

    document.title = `${run.id || "Agent Evolve"} · Run Observatory`;
    elements.runId.textContent = run.id || "Unknown run";
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

  function renderGuidance(guidance) {
    elements.guidanceCount.textContent = guidance.length;
    if (!guidance.length) {
      elements.guidanceList.innerHTML = '<div class="empty-panel">No meta-guidance generated yet. It will appear when the search needs a strategic intervention.</div>';
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

  function renderNotes(notes) {
    const counts = { positive: 0, negative: 0, error: 0 };
    notes.forEach((note) => { if (counts[note.note_type] != null) counts[note.note_type] += 1; });
    elements.noteCounts.innerHTML = Object.entries(counts)
      .filter(([, count]) => count > 0)
      .map(([type, count]) => `<span>${type[0].toUpperCase()} ${count}</span>`)
      .join("");
    if (!notes.length) {
      elements.notesList.innerHTML = '<div class="empty-panel">No notes recorded yet. Distilled successes, regressions, and repairs will appear here.</div>';
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
    const min = Math.min(...points.map((item) => item.fitness));
    const max = Math.max(...points.map((item) => item.fitness));
    elements.chartRange.textContent = `${points.length} scored tasks · ${formatCompact(min)}–${formatCompact(max)}`;
    elements.chartUpdated.textContent = `Updated ${formatTime(state.snapshot.generated_at, true)}`;
    drawChart(points);
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
    const values = points.flatMap((point) => [point.fitness, point.best_so_far].filter(Number.isFinite));
    let yMin = Math.min(...values);
    let yMax = Math.max(...values);
    const span = yMax - yMin;
    const margin = span > 0 ? span * 0.14 : Math.max(Math.abs(yMax) * 0.08, 0.05);
    yMin -= margin;
    yMax += margin;

    const x = (index) => pad.left + (points.length === 1 ? plotW / 2 : (index / (points.length - 1)) * plotW);
    const y = (value) => pad.top + (1 - (value - yMin) / (yMax - yMin)) * plotH;
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

    state.chartGeometry = { x, y, pad, width, height, points };
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
    elements.chartTooltip.innerHTML = `
      <strong>${escapeHtml(point.task_id)}</strong>
      fitness&nbsp; ${formatFitness(point.fitness)}<br>
      best&nbsp;&nbsp;&nbsp;&nbsp; ${formatFitness(point.best_so_far)}
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
    }, 120);
  });
  elements.chart.addEventListener("pointermove", handleChartPointer);
  elements.chart.addEventListener("pointerleave", hideChartTooltip);
  elements.refresh.addEventListener("click", refresh);
  elements.guidanceList.addEventListener("toggle", (event) => trackDetailsToggle(event, state.openGuidance), true);
  elements.notesList.addEventListener("toggle", (event) => trackDetailsToggle(event, state.openNotes), true);

  refresh();
  state.pollTimer = window.setInterval(refresh, POLL_INTERVAL);
})();
