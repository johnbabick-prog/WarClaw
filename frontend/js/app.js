/**
 * WarClaw — Main App Controller
 * Handles routing, status polling, and global state.
 */

const API = {
  base: window.location.origin,

  async get(path) {
    const r = await fetch(this.base + path);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  },

  async post(path, body) {
    const r = await fetch(this.base + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },

  async del(path) {
    const r = await fetch(this.base + path, { method: 'DELETE' });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  },

  async patch(path, body) {
    const r = await fetch(this.base + path, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },

  async put(path, body) {
    const r = await fetch(this.base + path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },

  ws(path) {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return new WebSocket(`${proto}//${location.host}${path}`);
  },
};

// ── Global state ─────────────────────────────────────────────────
const State = {
  modelReady: false,
  currentView: 'dashboard',
  lanScanResult: null,
  generatedApps: [],
  hwProfile: null,
  lastStatus: null,
  chatSessions: [],
  currentChatSessionId: null,
  reportTemplates: [],
};

// ── Toast notifications ──────────────────────────────────────────
function toast(message, type = 'info', duration = 4000) {
  const icons = { success: '✓', error: '✗', info: '◈' };
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${icons[type] || '●'}</span><span>${message}</span>`;
  container.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ── View routing ─────────────────────────────────────────────────
function showView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

  const view = document.getElementById(`view-${name}`);
  if (view) view.classList.add('active');

  const nav = document.querySelector(`[data-view="${name}"]`);
  if (nav) nav.classList.add('active');

  State.currentView = name;
  sessionStorage.setItem('warclaw.currentView', name);
  window.dispatchEvent(new CustomEvent('viewchange', { detail: name }));
}

// ── System time ──────────────────────────────────────────────────
function updateClock() {
  const el = document.getElementById('system-time');
  if (!el) return;
  const now = new Date();
  el.textContent = now.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
}

// ── Format uptime seconds → human readable ───────────────────────
function fmtUptime(s) {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

function fmtEtaSeconds(s) {
  const value = Math.max(0, Math.round(s || 0));
  if (value < 60) return `~${value}s remaining`;
  const m = Math.floor(value / 60);
  const rem = value % 60;
  return `~${m}m ${rem}s remaining`;
}

function createProgressController(fillId, statusId, etaId, options = {}) {
  const fill = document.getElementById(fillId);
  const status = document.getElementById(statusId);
  const eta = etaId ? document.getElementById(etaId) : null;
  const {
    start = 6,
    cap = 92,
    step = 3,
    intervalMs = 900,
    etaSeconds = 45,
    message = 'Working...',
  } = options;

  if (fill) fill.style.width = `${start}%`;
  if (status) {
    status.textContent = message;
    status.style.color = 'var(--accent-amber)';
  }
  if (eta) eta.textContent = fmtEtaSeconds(etaSeconds);

  let pct = start;
  let remaining = etaSeconds;
  const timer = setInterval(() => {
    pct = Math.min(cap, pct + step);
    remaining = Math.max(0, remaining - Math.max(1, Math.round(intervalMs / 1000)));
    if (fill) fill.style.width = `${pct}%`;
    if (eta) eta.textContent = fmtEtaSeconds(remaining);
  }, intervalMs);

  return {
    set(messageText, pctValue = null) {
      if (status) status.textContent = messageText;
      if (pctValue != null && fill) fill.style.width = `${pctValue}%`;
    },
    complete(messageText) {
      clearInterval(timer);
      if (fill) fill.style.width = '100%';
      if (status) {
        status.textContent = messageText;
        status.style.color = 'var(--accent-green)';
      }
      if (eta) eta.textContent = 'Completed';
    },
    fail(messageText) {
      clearInterval(timer);
      if (status) {
        status.textContent = messageText;
        status.style.color = 'var(--accent-red)';
      }
      if (fill) fill.style.width = '100%';
      if (eta) eta.textContent = 'Failed';
    },
    reset(delayMs = 2000) {
      clearInterval(timer);
      setTimeout(() => {
        if (fill) fill.style.width = '0%';
        if (eta) eta.textContent = '';
      }, delayMs);
    },
  };
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function renderAssistantIntel() {
  const recList = document.getElementById('assistant-rec-list');
  if (!recList) return;

  const items = [];
  if (!State.modelReady) {
    items.push({
      kicker: 'AI Core',
      title: 'Load a local model first',
      copy: 'The assistant, app factory, and recommendation engine become substantially more useful once a GGUF or Ollama model is online.',
    });
  }

  if (!State.lanScanResult) {
    items.push({
      kicker: 'Discovery',
      title: 'Run a LAN scan',
      copy: 'WarClaw can recommend concrete integrations after it sees actual NMEA, MODBUS, IEC 61162, or HTTP services on the network.',
    });
  } else {
    const hosts = State.lanScanResult.hosts_up || 0;
    items.push({
      kicker: 'Network',
      title: `${hosts} host${hosts === 1 ? '' : 's'} discovered`,
      copy: hosts > 0
        ? 'Use the assistant to summarize discovered services, rank integrations, or generate a dashboard around the live network footprint.'
        : 'No hosts were discovered on the last scan. Re-run against the correct subnet or inspect the scan timeout and network segment.',
    });

    if ((State.lanScanResult.recommendations || []).length) {
      items.push({
        kicker: 'Integrations',
        title: 'AI integrations available',
        copy: State.lanScanResult.recommendations[0],
      });
    }
  }

  if (State.generatedApps.length) {
    items.push({
      kicker: 'Generated Apps',
      title: `${State.generatedApps.length} app${State.generatedApps.length === 1 ? '' : 's'} ready`,
      copy: 'Open an existing generated route or ask the assistant to extend the current app set with a new operational workflow.',
    });
  }

  recList.innerHTML = items.map(item => `
    <div class="intel-card">
      <div class="intel-kicker">${item.kicker}</div>
      <div class="intel-title">${item.title}</div>
      <div class="intel-copy">${item.copy}</div>
    </div>
  `).join('');
}

function syncLiveUi() {
  const status = State.lastStatus;
  const hosts = State.lanScanResult?.hosts_up || 0;
  const modelSummary = status?.model_path
    ? ((status.model_provider === 'gguf') ? status.model_path.split('/').pop() : status.model_path)
    : 'No model loaded';

  setText('presence-value', State.modelReady ? 'AI Advisor Online' : 'AI Advisor Standing By');
  setText('dash-ai-status', State.modelReady ? 'ONLINE' : 'OFFLINE');
  setText('hero-ai-summary', State.modelReady ? 'Local AI model online' : 'No local model loaded');
  setText('hero-model-summary', modelSummary);
  setText('assistant-core-status', State.modelReady ? 'Online' : 'Offline');
  setText('assistant-lan-summary', hosts ? `${hosts} host${hosts === 1 ? '' : 's'} visible` : 'No scan yet');
  setText('assistant-app-summary', `${State.generatedApps.length || status?.generated_apps || 0} generated`);

  if (State.modelReady && hosts) {
    setText('hero-next-step', 'Ask for integration recommendations');
    setText('hero-next-step-sub', 'WarClaw can now map discovered systems to apps, agents, and live workflows.');
  } else if (State.modelReady) {
    setText('hero-next-step', 'Scan the LAN to ground the assistant');
    setText('hero-next-step-sub', 'Discovery data lets the AI recommend concrete protocol integrations instead of generic ideas.');
  } else {
    setText('hero-next-step', 'Load a model, then scan the LAN');
    setText('hero-next-step-sub', 'That unlocks the live assistant, agent recommendations, and app generation path.');
  }

  if (typeof updateSetupBanner === 'function') updateSetupBanner(State.modelReady);
  renderAssistantIntel();
}

// ── Status polling ───────────────────────────────────────────────
async function pollStatus() {
  try {
    const status = await API.get('/api/status');
    State.lastStatus = status;
    State.modelReady = status.model_ready;

    const dot = document.getElementById('ai-status-dot');
    const label = document.getElementById('ai-status-label');
    if (dot && label) {
      if (status.model_ready) {
        dot.className = 'status-dot online';
        label.textContent = 'AI READY';
      } else {
        dot.className = 'status-dot offline';
        label.textContent = 'NO MODEL';
      }
    }

    // Dashboard stats
    const appsEl = document.getElementById('stat-apps');
    if (appsEl) appsEl.textContent = status.generated_apps;
    const modelEl = document.getElementById('stat-model');
    if (modelEl) modelEl.textContent =
      status.model_path
        ? ((status.model_provider === 'gguf') ? status.model_path.split('/').pop() : status.model_path).slice(0, 28)
        : '— no model loaded';

    // Uptime + version
    const uptimeEl = document.getElementById('dash-uptime');
    if (uptimeEl && status.uptime_s != null) uptimeEl.textContent = fmtUptime(status.uptime_s);
    const verEl = document.getElementById('dash-version');
    if (verEl && status.version) verEl.textContent = `WarClaw v${status.version}`;

    // CPU / RAM
    const cpuEl = document.getElementById('dash-cpu');
    if (cpuEl && status.cpu_percent != null) cpuEl.textContent = `${status.cpu_percent.toFixed(1)}%`;
    const ramEl = document.getElementById('dash-ram');
    if (ramEl && status.ram_used_gb != null)
      ramEl.textContent = `${status.ram_used_gb} / ${status.ram_total_gb} GB (${status.ram_percent}%)`;

    // LAN status pill
    const lanDot = document.getElementById('lan-status-dot');
    const lanLabel = document.getElementById('lan-status-label');
    if (lanDot && lanLabel) {
      if (State.lanScanResult) {
        lanDot.className = 'status-dot online';
        const n = State.lanScanResult.hosts_up;
        lanLabel.textContent = `LAN ${n} HOST${n !== 1 ? 'S' : ''}`;
      } else {
        lanDot.className = 'status-dot';
        lanLabel.textContent = 'LAN —';
      }
    }

    // Agent stats
    const agentsRunEl = document.getElementById('dash-agents-running');
    if (agentsRunEl && status.agents_running != null) agentsRunEl.textContent = status.agents_running;
    const agentsTotEl = document.getElementById('dash-agents-total');
    if (agentsTotEl && status.agents_total != null) agentsTotEl.textContent = `${status.agents_total} deployed`;

    // Alerts + anomalies
    const alertsEl = document.getElementById('dash-alerts');
    if (alertsEl && status.agents_alerts != null) alertsEl.textContent = status.agents_alerts;
    const anomEl = document.getElementById('dash-anomalies');
    if (anomEl && status.anomalies_detected != null)
      anomEl.textContent = `${status.anomalies_detected} anomalies · ${status.data_bus_frames || 0} bus frames`;

    syncLiveUi();

  } catch (e) {
    const dot = document.getElementById('ai-status-dot');
    if (dot) dot.className = 'status-dot error';
    setText('presence-value', 'System Status Unreachable');
  }
}

// ── Nav click handlers ───────────────────────────────────────────
document.querySelectorAll('.nav-item[data-view]').forEach(item => {
  item.addEventListener('click', () => {
    const view = item.dataset.view;
    showView(view);
    if (view === 'hardware') loadHardwareView();
    if (view === 'apps') loadAppList();
    if (view === 'traffic') refreshConversations && refreshConversations();
    if (view === 'chat') refreshChatSessions && refreshChatSessions();
    if (view === 'reports') loadReportsView && loadReportsView();
  });
});

// ── Init ─────────────────────────────────────────────────────────
(async function init() {
  const params = new URLSearchParams(window.location.search);
  const requestedView = params.get('view');
  const requestedEdit = params.get('edit');
  updateClock();
  setInterval(updateClock, 1000);
  await pollStatus();
  setInterval(pollStatus, 8000);
  const initialView = requestedView || sessionStorage.getItem('warclaw.currentView') || 'dashboard';
  showView(initialView);
  if (initialView === 'apps' && typeof loadAppList === 'function') {
    await loadAppList();
    if (requestedEdit && typeof editApp === 'function') {
      await editApp(requestedEdit).catch(() => {});
    }
  }
})();
