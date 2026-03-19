/**
 * WarClaw — Agent Management View
 * Create, deploy, manage, configure, and track AI agents.
 */

let _agentPollInterval = null;
let _agentTemplates = [];
let _expandedAgentId = null;

// ── Load agent list ──────────────────────────────────────────────
async function loadAgents() {
  const panel = document.getElementById('agents-list');
  if (!panel) return;

  try {
    const data = await API.get('/api/agents/');

    if (data.agents.length === 0) {
      panel.innerHTML = `
        <div class="empty-state" style="height:240px;">
          <div class="empty-icon">&#x2B21;</div>
          <div class="empty-title">No agents deployed</div>
          <div class="empty-sub">Create a custom agent or click Auto-Recommend to scan the LAN and get suggestions matched to your ship's systems.</div>
          <div style="display:flex;gap:8px;margin-top:12px;">
            <button class="btn btn-primary" onclick="showAgentCreator()">+ Create Agent</button>
            <button class="btn" onclick="autoRecommend()">Auto-Recommend</button>
          </div>
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
  const uptime = a.uptime_s != null ? _fmtUptime(a.uptime_s) : '--';
  const isExpanded = _expandedAgentId === a.id;

  return `
    <div class="agent-card ${isExpanded ? 'agent-card-expanded' : ''}" data-agent-id="${a.id}">
      <div class="agent-header" onclick="toggleAgentDetail('${a.id}')">
        <span class="agent-icon">${a.icon}</span>
        <div class="agent-info">
          <div class="agent-name">${_esc(a.name)}</div>
          <div class="agent-target">${_esc(a.target_host)}${a.target_port ? ':' + a.target_port : ''}</div>
        </div>
        <div style="margin-left:auto;display:flex;align-items:center;gap:8px;">
          <span class="agent-status-badge" style="color:${statusColor};border-color:${statusColor};">
            ${a.status.toUpperCase()}
          </span>
          <span class="agent-expand-arrow">${isExpanded ? '&#x25B4;' : '&#x25BE;'}</span>
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
      ${a.last_alert ? `<div class="agent-last-alert">&#x26A0; ${_esc(a.last_alert)}</div>` : ''}
      ${isExpanded ? _renderAgentDetail(a) : ''}
      <div class="agent-actions">
        ${a.status === 'running'
          ? `<button class="btn btn-danger" onclick="event.stopPropagation();stopAgent('${a.id}')">&#x2297; Stop</button>`
          : a.status !== 'error'
            ? `<button class="btn btn-success" onclick="event.stopPropagation();startAgent('${a.id}')">&#x25B6; Start</button>`
            : `<button class="btn" onclick="event.stopPropagation();startAgent('${a.id}')">&#x21BA; Retry</button>`
        }
        <button class="btn" onclick="event.stopPropagation();showAgentConfig('${a.id}')" title="Configure">&#x2699; Config</button>
        <button class="btn btn-danger" onclick="event.stopPropagation();removeAgent('${a.id}')" style="font-size:10px;">&#x2715; Remove</button>
      </div>
    </div>`;
}

function _renderAgentDetail(a) {
  const created = new Date(a.created_at * 1000).toLocaleString();
  const started = a.started_at ? new Date(a.started_at * 1000).toLocaleString() : '--';
  const stopped = a.stopped_at ? new Date(a.stopped_at * 1000).toLocaleString() : '--';

  const configRows = Object.entries(a.config || {}).map(([key, val]) =>
    `<div class="agent-detail-row">
      <span class="agent-detail-label">${_esc(key.replace(/_/g, ' '))}</span>
      <span class="agent-detail-value">${_esc(String(val))}</span>
    </div>`
  ).join('');

  const recRows = (a.recommendations || []).slice(0, 4).map(r =>
    `<div class="agent-rec-item">
      <div style="font-weight:600;font-size:12px;color:var(--text-primary);">${_esc(r.title || '')}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">${_esc(r.rationale || r.action || '')}</div>
    </div>`
  ).join('');

  return `
    <div class="agent-detail-panel">
      <div class="agent-detail-section">
        <div class="agent-detail-title">Details</div>
        <div class="agent-detail-row">
          <span class="agent-detail-label">Agent ID</span>
          <span class="agent-detail-value" style="font-family:var(--font-mono);font-size:11px;">${_esc(a.id)}</span>
        </div>
        <div class="agent-detail-row">
          <span class="agent-detail-label">Description</span>
          <span class="agent-detail-value" style="max-width:260px;">${_esc(a.description)}</span>
        </div>
        <div class="agent-detail-row">
          <span class="agent-detail-label">Channel</span>
          <span class="agent-detail-value" style="font-family:var(--font-mono);font-size:11px;">${_esc(a.channel)}</span>
        </div>
        <div class="agent-detail-row">
          <span class="agent-detail-label">Priority</span>
          <span class="agent-detail-value" style="text-transform:uppercase;">${_esc(a.priority)}</span>
        </div>
        <div class="agent-detail-row">
          <span class="agent-detail-label">Created</span>
          <span class="agent-detail-value">${created}</span>
        </div>
        <div class="agent-detail-row">
          <span class="agent-detail-label">Started</span>
          <span class="agent-detail-value">${started}</span>
        </div>
        <div class="agent-detail-row">
          <span class="agent-detail-label">Stopped</span>
          <span class="agent-detail-value">${stopped}</span>
        </div>
        ${a.error_message ? `
        <div class="agent-detail-row">
          <span class="agent-detail-label">Error</span>
          <span class="agent-detail-value" style="color:var(--accent-red);">${_esc(a.error_message)}</span>
        </div>` : ''}
      </div>
      ${configRows ? `
      <div class="agent-detail-section">
        <div class="agent-detail-title">Configuration</div>
        ${configRows}
      </div>` : ''}
      ${recRows ? `
      <div class="agent-detail-section">
        <div class="agent-detail-title">Recommendations</div>
        ${recRows}
      </div>` : ''}
    </div>`;
}

function toggleAgentDetail(agentId) {
  _expandedAgentId = _expandedAgentId === agentId ? null : agentId;
  loadAgents();
}

// ── Agent Creator ───────────────────────────────────────────────
async function loadAgentTemplates() {
  if (_agentTemplates.length) return;
  try {
    const data = await API.get('/api/agents/templates');
    _agentTemplates = data.templates;
  } catch (e) {
    console.warn('Failed to load agent templates:', e);
  }
}

function showAgentCreator() {
  loadAgentTemplates().then(() => {
    const modal = document.getElementById('agent-creator-modal');
    if (!modal) return;

    // Populate template selector
    const sel = document.getElementById('agent-creator-type');
    sel.innerHTML = `
      <option value="custom">Custom Agent</option>
      ${_agentTemplates.map(t => `<option value="${t.agent_type}">${t.icon} ${t.name}</option>`).join('')}
    `;
    sel.onchange = () => _onAgentTypeChange(sel.value);

    // Reset form
    document.getElementById('agent-creator-name').value = '';
    document.getElementById('agent-creator-desc').value = '';
    document.getElementById('agent-creator-host').value = '';
    document.getElementById('agent-creator-port').value = '';
    document.getElementById('agent-creator-icon').value = '';
    document.getElementById('agent-creator-priority').value = 'medium';
    document.getElementById('agent-creator-category').value = '';
    document.getElementById('agent-creator-config').innerHTML = '';
    document.getElementById('agent-creator-custom-fields').style.display = '';

    modal.classList.add('visible');
  });
}

function hideAgentCreator() {
  const modal = document.getElementById('agent-creator-modal');
  if (modal) modal.classList.remove('visible');
}

function _onAgentTypeChange(agentType) {
  const customFields = document.getElementById('agent-creator-custom-fields');
  const configContainer = document.getElementById('agent-creator-config');

  if (agentType === 'custom') {
    customFields.style.display = '';
    configContainer.innerHTML = '';
    return;
  }

  const template = _agentTemplates.find(t => t.agent_type === agentType);
  if (!template) return;

  customFields.style.display = 'none';
  document.getElementById('agent-creator-name').value = template.name;
  document.getElementById('agent-creator-desc').value = template.description;

  // Render config schema fields
  const schema = template.config_schema || {};
  configContainer.innerHTML = Object.entries(schema).map(([key, spec]) => `
    <div class="form-group" style="margin-bottom:8px;">
      <label style="font-size:11px;">${_esc(spec.label || key)}</label>
      <input type="${spec.type === 'bool' ? 'checkbox' : spec.type === 'float' ? 'number' : 'text'}"
             class="agent-config-field"
             data-key="${key}" data-type="${spec.type}"
             ${spec.type === 'bool' ? (spec.default ? 'checked' : '') : `value="${spec.default ?? ''}"`}
             ${spec.type === 'float' ? 'step="0.1"' : ''}
             style="width:100%;padding:6px 8px;font-size:12px;" />
    </div>
  `).join('');
}

async function deployCustomAgent() {
  const agentType = document.getElementById('agent-creator-type').value;
  const name = document.getElementById('agent-creator-name').value.trim();
  const desc = document.getElementById('agent-creator-desc').value.trim();
  const host = document.getElementById('agent-creator-host').value.trim();
  const port = parseInt(document.getElementById('agent-creator-port').value) || 0;
  const icon = document.getElementById('agent-creator-icon').value.trim() || '';
  const priority = document.getElementById('agent-creator-priority').value;
  const category = document.getElementById('agent-creator-category').value.trim();

  if (!name) {
    toast('Agent name is required', 'error');
    return;
  }

  // Collect config from dynamic fields
  const config = {};
  document.querySelectorAll('.agent-config-field').forEach(field => {
    const key = field.dataset.key;
    const type = field.dataset.type;
    if (type === 'bool') config[key] = field.checked;
    else if (type === 'float') config[key] = parseFloat(field.value) || 0;
    else if (type === 'int') config[key] = parseInt(field.value) || 0;
    else config[key] = field.value;
  });

  const btn = document.getElementById('agent-creator-deploy-btn');
  btn.disabled = true;
  btn.textContent = 'Deploying...';

  try {
    await API.post('/api/agents/deploy', {
      agent_type: agentType,
      target_host: host || '0.0.0.0',
      target_port: port,
      config,
      name,
      description: desc,
      icon,
      category: category || (agentType === 'custom' ? 'custom' : ''),
      priority,
      auto_start: true,
    });
    toast(`Agent "${name}" deployed and started`, 'success');
    hideAgentCreator();
    loadAgents();
  } catch (e) {
    toast('Deploy failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Deploy & Start';
  }
}

// ── Agent Config Editor ─────────────────────────────────────────
async function showAgentConfig(agentId) {
  try {
    const agent = await API.get(`/api/agents/${agentId}`);
    const modal = document.getElementById('agent-config-modal');
    if (!modal) return;

    document.getElementById('agent-config-title').textContent = `Configure: ${agent.name}`;
    document.getElementById('agent-config-id').value = agentId;

    const container = document.getElementById('agent-config-fields');
    const config = agent.config || {};

    // Try to get schema from template
    const template = _agentTemplates.find(t => t.agent_type === agent.agent_type);
    const schema = template?.config_schema || {};

    // Merge: show schema fields with current values, plus any extra keys
    const allKeys = new Set([...Object.keys(schema), ...Object.keys(config)]);

    container.innerHTML = Array.from(allKeys).map(key => {
      const spec = schema[key] || {};
      const val = config[key] ?? spec.default ?? '';
      const label = spec.label || key.replace(/_/g, ' ');
      const type = spec.type || (typeof val === 'boolean' ? 'bool' : typeof val === 'number' ? 'float' : 'string');

      return `
        <div class="form-group" style="margin-bottom:10px;">
          <label style="font-size:11px;">${_esc(label)}</label>
          <input type="${type === 'bool' ? 'checkbox' : type === 'float' || type === 'int' ? 'number' : 'text'}"
                 class="agent-cfg-field"
                 data-key="${key}" data-type="${type}"
                 ${type === 'bool' ? (val ? 'checked' : '') : `value="${val}"`}
                 ${type === 'float' ? 'step="0.1"' : ''}
                 style="width:100%;padding:6px 8px;font-size:12px;" />
        </div>
      `;
    }).join('') || '<div class="text-muted">No configurable parameters.</div>';

    modal.classList.add('visible');
  } catch (e) {
    toast('Failed to load agent config: ' + e.message, 'error');
  }
}

function hideAgentConfig() {
  const modal = document.getElementById('agent-config-modal');
  if (modal) modal.classList.remove('visible');
}

async function saveAgentConfig() {
  const agentId = document.getElementById('agent-config-id').value;
  const config = {};

  document.querySelectorAll('.agent-cfg-field').forEach(field => {
    const key = field.dataset.key;
    const type = field.dataset.type;
    if (type === 'bool') config[key] = field.checked;
    else if (type === 'float') config[key] = parseFloat(field.value) || 0;
    else if (type === 'int') config[key] = parseInt(field.value) || 0;
    else config[key] = field.value;
  });

  try {
    await API.patch(`/api/agents/${agentId}/config`, { config });
    toast('Configuration saved', 'success');
    hideAgentConfig();
    loadAgents();
  } catch (e) {
    toast('Save failed: ' + e.message, 'error');
  }
}

// ── Auto-recommend ───────────────────────────────────────────────
async function autoRecommend() {
  const panel = document.getElementById('agents-recommendations');
  const btn = document.getElementById('agents-recommend-btn');
  if (!panel) return;

  btn.disabled = true;
  btn.textContent = 'Scanning...';
  panel.innerHTML = '<div class="text-muted" style="padding:10px;">Scanning LAN and analyzing services...</div>';

  try {
    const data = await API.get('/api/agents/recommend/auto');

    if (data.recommendations.length === 0) {
      panel.innerHTML = `
        <div class="text-muted" style="padding:10px;">
          No agents recommended -- ${data.scan_summary.hosts_up} host(s) found but no matching protocols.
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
    btn.textContent = 'Auto-Recommend';
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
            ${_esc(r.target_host)}:${r.target_port} &#xB7; <span style="color:${pColor};text-transform:uppercase;">${r.priority}</span>
          </div>
        </div>
      </div>
      <div style="font-size:11px;color:var(--text-secondary);line-height:1.5;margin-bottom:8px;">
        ${_esc(r.reason)}
      </div>
      ${deployed
        ? '<div style="font-size:11px;color:var(--accent-green);">&#x2713; Already deployed</div>'
        : `<button class="btn btn-primary" style="font-size:11px;padding:4px 12px;"
             onclick="deployFromRecommendation('${r.agent_type}', '${r.target_host}', ${r.target_port})">
             Deploy &amp; Start
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
    autoRecommend();
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
  if (s == null) return '--';
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
  loadAgentTemplates();
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

// ── Init ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const recBtn = document.getElementById('agents-recommend-btn');
  if (recBtn) recBtn.addEventListener('click', autoRecommend);
});
