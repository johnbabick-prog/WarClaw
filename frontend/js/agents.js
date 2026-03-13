/**
 * WarClaw — Agent Management View
 * Handles auto-recommendation, deploy, start, stop, and monitoring of agents.
 */

let _agentPollInterval = null;

// ── Load agent list ──────────────────────────────────────────────
async function loadAgents() {
  const panel = document.getElementById('agents-list');
  if (!panel) return;

  try {
    const data = await API.get('/api/agents/');

    if (data.agents.length === 0) {
      panel.innerHTML = `
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:200px;gap:12px;color:var(--text-muted);">
          <div class="empty-icon">⬡</div>
          <div>No agents deployed yet</div>
          <div style="font-size:11px;">Click "Auto-Recommend" to scan the LAN and get agent suggestions</div>
        </div>`;
      return;
    }

    panel.innerHTML = data.agents.map(a => _renderAgentCard(a)).join('');

    // Update dashboard badge
    const badge = document.getElementById('agents-running-badge');
    if (badge) {
      badge.textContent = data.running;
      badge.style.display = data.running > 0 ? '' : 'none';
    }

  } catch (e) {
    panel.innerHTML = `<div class="text-muted">Failed to load agents: ${_esc(e.message)}</div>`;
  }
}

function _renderAgentCard(a) {
  const statusColors = {
    running: 'var(--accent-green)',
    ready: 'var(--accent-blue)',
    stopped: 'var(--text-muted)',
    error: 'var(--accent-red)',
  };
  const statusColor = statusColors[a.status] || 'var(--text-muted)';
  const uptime = a.uptime_s != null ? _fmtUptime(a.uptime_s) : '—';

  return `
    <div class="agent-card" data-agent-id="${a.id}">
      <div class="agent-header">
        <span class="agent-icon">${a.icon}</span>
        <div class="agent-info">
          <div class="agent-name">${_esc(a.name)}</div>
          <div class="agent-target">${_esc(a.target_host)}:${a.target_port}</div>
        </div>
        <div style="margin-left:auto;display:flex;align-items:center;gap:8px;">
          <span class="agent-status-badge" style="color:${statusColor};border-color:${statusColor};">
            ${a.status.toUpperCase()}
          </span>
        </div>
      </div>
      <div class="agent-stats">
        <div class="agent-stat">
          <span class="agent-stat-label">FRAMES</span>
          <span class="agent-stat-value">${a.frames_processed.toLocaleString()}</span>
        </div>
        <div class="agent-stat">
          <span class="agent-stat-label">ALERTS</span>
          <span class="agent-stat-value" style="color:${a.alerts_fired > 0 ? 'var(--accent-red)' : 'var(--text-muted)'};">${a.alerts_fired}</span>
        </div>
        <div class="agent-stat">
          <span class="agent-stat-label">UPTIME</span>
          <span class="agent-stat-value">${uptime}</span>
        </div>
        <div class="agent-stat">
          <span class="agent-stat-label">TYPE</span>
          <span class="agent-stat-value" style="font-size:10px;text-transform:uppercase;">${a.category}</span>
        </div>
      </div>
      ${a.last_alert ? `<div class="agent-last-alert">⚠ ${_esc(a.last_alert)}</div>` : ''}
      <div class="agent-actions">
        ${a.status === 'running'
          ? `<button class="btn btn-danger" onclick="stopAgent('${a.id}')">⊗ Stop</button>`
          : a.status !== 'error'
            ? `<button class="btn btn-success" onclick="startAgent('${a.id}')">▶ Start</button>`
            : `<button class="btn" onclick="startAgent('${a.id}')">↺ Retry</button>`
        }
        <button class="btn btn-danger" onclick="removeAgent('${a.id}')" style="font-size:10px;">✕ Remove</button>
      </div>
    </div>`;
}

// ── Auto-recommend ───────────────────────────────────────────────
async function autoRecommend() {
  const panel = document.getElementById('agents-recommendations');
  const btn = document.getElementById('agents-recommend-btn');
  if (!panel) return;

  btn.disabled = true;
  btn.textContent = '⟳ Scanning...';
  panel.innerHTML = '<div class="text-muted" style="padding:10px;">Scanning LAN and analyzing services...</div>';

  try {
    const data = await API.get('/api/agents/recommend/auto');

    if (data.recommendations.length === 0) {
      panel.innerHTML = `
        <div class="text-muted" style="padding:10px;">
          No agents recommended — ${data.scan_summary.hosts_up} host(s) found but no matching protocols.
        </div>`;
      return;
    }

    panel.innerHTML = `
      <div class="text-muted" style="padding:8px 0 4px;font-size:10px;letter-spacing:1px;text-transform:uppercase;">
        ${data.count} RECOMMENDATION(S) FROM ${data.scan_summary.hosts_up} HOST(S)
      </div>
      ${data.recommendations.map(r => _renderRecommendation(r)).join('')}
    `;

  } catch (e) {
    panel.innerHTML = `<div class="text-red" style="padding:10px;">Scan failed: ${_esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '⬡ Auto-Recommend';
  }
}

function _renderRecommendation(r) {
  const priorityColors = { critical: 'var(--accent-red)', high: 'var(--accent-amber)', medium: 'var(--accent-blue)', low: 'var(--text-muted)' };
  const pColor = priorityColors[r.priority] || 'var(--text-muted)';
  const deployed = r.already_deployed;

  return `
    <div class="recommendation-card">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
        <span style="font-size:20px;">${r.icon}</span>
        <div style="flex:1;">
          <div style="font-weight:600;color:var(--text-primary);font-size:13px;">${_esc(r.name)}</div>
          <div style="font-size:10px;color:var(--text-muted);">
            ${_esc(r.target_host)}:${r.target_port} · <span style="color:${pColor};text-transform:uppercase;">${r.priority}</span>
          </div>
        </div>
      </div>
      <div style="font-size:11px;color:var(--text-secondary);line-height:1.5;margin-bottom:8px;">
        ${_esc(r.reason)}
      </div>
      ${deployed
        ? '<div style="font-size:11px;color:var(--accent-green);">✓ Already deployed</div>'
        : `<button class="btn btn-primary" style="font-size:11px;padding:4px 12px;"
             onclick="deployFromRecommendation('${r.agent_type}', '${r.target_host}', ${r.target_port})">
             ⚡ Deploy &amp; Start
           </button>`
      }
    </div>`;
}

// ── Agent actions ────────────────────────────────────────────────
async function deployFromRecommendation(agentType, host, port) {
  try {
    await API.post('/api/agents/deploy', {
      agent_type: agentType,
      target_host: host,
      target_port: port,
      auto_start: true,
    });
    toast('Agent deployed and started', 'success');
    loadAgents();
    autoRecommend();  // Refresh to show "already deployed"
  } catch (e) {
    toast('Deploy failed: ' + e.message, 'error');
  }
}

async function startAgent(id) {
  try {
    await API.post(`/api/agents/${id}/start`, {});
    toast('Agent started', 'success');
    loadAgents();
  } catch (e) {
    toast('Start failed: ' + e.message, 'error');
  }
}

async function stopAgent(id) {
  try {
    await API.post(`/api/agents/${id}/stop`, {});
    toast('Agent stopped', 'info');
    loadAgents();
  } catch (e) {
    toast('Stop failed: ' + e.message, 'error');
  }
}

async function removeAgent(id) {
  try {
    await API.del(`/api/agents/${id}`);
    toast('Agent removed', 'info');
    loadAgents();
  } catch (e) {
    toast('Remove failed: ' + e.message, 'error');
  }
}

// ── Helpers ──────────────────────────────────────────────────────
function _fmtUptime(s) {
  if (s == null) return '—';
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function _esc(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Polling ──────────────────────────────────────────────────────
function startAgentPolling() {
  if (_agentPollInterval) return;
  loadAgents();
  _agentPollInterval = setInterval(loadAgents, 5000);
}

function stopAgentPolling() {
  if (_agentPollInterval) {
    clearInterval(_agentPollInterval);
    _agentPollInterval = null;
  }
}

// Start/stop polling when view is shown/hidden
window.addEventListener('viewchange', (e) => {
  if (e.detail === 'agents') {
    startAgentPolling();
  } else {
    stopAgentPolling();
  }
});

// ── Init buttons ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const recBtn = document.getElementById('agents-recommend-btn');
  if (recBtn) recBtn.addEventListener('click', autoRecommend);
});
