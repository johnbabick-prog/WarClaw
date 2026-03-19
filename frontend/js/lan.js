/**
 * WarClaw — LAN Monitor Module
 */

let lanStreamWs = null;

function openLanHelp() {
  const modal = document.getElementById('lan-help-modal');
  if (modal) modal.classList.add('visible');
}
window.openLanHelp = openLanHelp;

function closeLanHelp() {
  const modal = document.getElementById('lan-help-modal');
  if (modal) modal.classList.remove('visible');
}
window.closeLanHelp = closeLanHelp;

function protocolClass(protocol) {
  if (protocol.includes('nmea') || protocol.includes('iec')) return 'nmea';
  if (protocol.includes('modbus')) return 'modbus';
  if (protocol.includes('http')) return 'http';
  return '';
}

function renderHostCard(host) {
  const portBadges = host.open_ports.map(p => {
    const svc = host.services.find(s => s.port === p);
    const cls = protocolClass(svc?.protocol || '');
    return `<span class="port-badge ${cls}">${p}</span>`;
  }).join('');

  const hints = host.integration_hints.map(h => `
    <div class="hint-row">
      <span class="hint-icon">◈</span>
      <span>${h}</span>
      <button class="hint-action-btn" onclick="promptCreateAppFromHint('${host.ip}', \`${h.replace(/`/g, '')}\`)">
        → Create App
      </button>
    </div>
  `).join('');

  const streamableService = host.services.find(s =>
    s.protocol.includes('nmea') || s.protocol.includes('iec')
  );

  const streamBtn = streamableService
    ? `<button class="btn btn-success" style="font-size:11px;padding:4px 10px;"
         onclick="startStream('${host.ip}', ${streamableService.port})">
         ▶ Stream
       </button>`
    : '';

  const modbusService = host.services.find(s => s.protocol.includes('modbus'));
  const modbusBtn = modbusService
    ? `<button class="btn" style="font-size:11px;padding:4px 10px;"
         onclick="probeModbus('${host.ip}', ${modbusService.port})">
         ⚙ MODBUS
       </button>`
    : '';

  return `
    <div class="host-card">
      <div class="host-header">
        <div>
          <div class="host-ip">${host.ip}</div>
          <div class="host-hostname">${host.hostname || 'hostname unknown'}</div>
        </div>
        <div class="host-ports">${portBadges}</div>
        <div style="display:flex;gap:6px;margin-left:12px;">
          ${streamBtn}${modbusBtn}
        </div>
      </div>
      ${host.integration_hints.length ? `<div class="host-hints">${hints}</div>` : ''}
    </div>
  `;
}

function renderRecommendations(recs) {
  if (!recs.length) return '';
  const items = recs.map(r => `
    <div class="recommendation-item">
      <span class="rec-icon">◈</span>
      <div>
        <div>${r}</div>
        <div class="rec-actions">
          <button class="btn btn-primary" style="font-size:10px;padding:3px 10px;"
            onclick="promptCreateAppFromRec(\`${r.replace(/`/g, '')}\`)">
            Generate This App
          </button>
        </div>
      </div>
    </div>
  `).join('');

  return `
    <div class="recommendations-section">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
        <div class="card-title">◈ AI INTEGRATION RECOMMENDATIONS</div>
        <button class="btn btn-primary" style="font-size:11px;padding:5px 14px;" id="gen-all-recs-btn"
          onclick="generateAllRecommended()">
          Generate All Recommended
        </button>
      </div>
      ${items}
    </div>
  `;
}

async function runLanScan() {
  const networkInput = document.getElementById('lan-network-input').value.trim();
  const portsInput = document.getElementById('lan-ports-input').value.trim();
  const btn = document.getElementById('lan-scan-btn');
  const hostList = document.getElementById('lan-host-list');
  const sidebar = document.getElementById('lan-scan-stats');
  const progress = createProgressController('lan-progress-fill', 'lan-status', 'lan-eta', {
    message: 'Preparing network discovery sweep...',
    etaSeconds: 30,
    intervalMs: 900,
    step: 3.2,
  });

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> SCANNING...';
  hostList.innerHTML = '<div id="lan-empty"><div class="empty-icon scan-animation">◎</div><div>Scanning LAN — this may take 30-60 seconds...</div></div>';
  document.getElementById('lan-recs').innerHTML = '';

  try {
    progress.set(networkInput ? `Scanning ${networkInput} for responsive hosts and services...` : 'Auto-detecting subnet and scanning for responsive hosts...', 12);
    const params = new URLSearchParams();
    if (networkInput) params.set('network', networkInput);
    if (portsInput) params.set('ports', portsInput);
    const url = params.toString() ? `/api/lan/scan?${params.toString()}` : '/api/lan/scan';
    const result = await API.get(url);
    State.lanScanResult = result;
    window.dispatchEvent(new CustomEvent('warclaw:lan-scan-updated', { detail: result }));

    // Stats
    sidebar.innerHTML = `
      <div class="card-title">SCAN RESULTS</div>
      <div class="hw-info-row">
        <div class="hw-info-label">Connected Via</div>
        <div class="hw-info-value">${result.network_name || result.interface || 'Unknown network'}</div>
      </div>
      <div class="hw-info-row">
        <div class="hw-info-label">Network</div>
        <div class="hw-info-value mono">${result.network}</div>
      </div>
      ${result.interface ? `
      <div class="hw-info-row">
        <div class="hw-info-label">Interface</div>
        <div class="hw-info-value mono">${result.interface}</div>
      </div>
      ` : ''}
      <div class="hw-info-row">
        <div class="hw-info-label">Scanned</div>
        <div class="hw-info-value">${result.hosts_scanned} hosts</div>
      </div>
      <div class="hw-info-row">
        <div class="hw-info-label">Active</div>
        <div class="hw-info-value text-green">${result.hosts_up} hosts</div>
      </div>
      <div class="hw-info-row">
        <div class="hw-info-label">Duration</div>
        <div class="hw-info-value">${result.scan_duration_s}s</div>
      </div>
      <div class="hw-info-row">
        <div class="hw-info-label">Probe Ports</div>
        <div class="hw-info-value mono" style="font-size:10px;">${(result.probe_ports || []).join(', ')}</div>
      </div>
    `;

    // Host cards
    if (result.hosts.length === 0) {
      hostList.innerHTML = '<div id="lan-empty"><div class="empty-icon">⊘</div><div>No active hosts found on this network segment.</div></div>';
    } else {
      hostList.innerHTML = result.hosts.map(renderHostCard).join('');
      document.getElementById('lan-host-count').textContent = result.hosts_up;
    }

    if (typeof updateLanBadge === 'function') updateLanBadge(result.hosts_up);
    if (typeof syncLiveUi === 'function') syncLiveUi();

    // Recommendations
    document.getElementById('lan-recs').innerHTML = renderRecommendations(result.recommendations);

    progress.complete(`Scan complete: ${result.hosts_up} host(s) on ${result.network} in ${result.scan_duration_s}s.`);
    toast(`Found ${result.hosts_up} hosts on ${result.network}`, 'success');
  } catch (e) {
    hostList.innerHTML = `<div id="lan-empty"><div class="empty-icon">✗</div><div class="text-red">${e.message}</div></div>`;
    progress.fail('Scan failed: ' + e.message);
    toast('Scan failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '⬡ SCAN LAN';
    progress.reset();
  }
}

function startStream(host, port) {
  const panel = document.getElementById('stream-panel');
  const streamInfo = document.getElementById('lan-stream-info');

  if (lanStreamWs) {
    lanStreamWs.close();
    lanStreamWs = null;
  }

  streamInfo.textContent = `Streaming ${host}:${port}`;
  panel.innerHTML = '';

  lanStreamWs = API.ws('/api/lan/stream');

  lanStreamWs.onopen = () => {
    lanStreamWs.send(JSON.stringify({ host, port, max_messages: 200 }));
    addStreamLine(`Connecting to ${host}:${port}...`, 'heartbeat');
  };

  lanStreamWs.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.status === 'connecting') {
      addStreamLine(`Connected — waiting for data...`, 'heartbeat');
    } else if (data.heartbeat) {
      addStreamLine(`[heartbeat]`, 'heartbeat');
    } else if (data.error) {
      addStreamLine(`ERROR: ${data.error}`, 'error');
    } else if (data.raw) {
      const decoded = data.decoded ? ` → ${data.decoded.type}: ${JSON.stringify(data.decoded).slice(0, 80)}` : '';
      addStreamLine(`${data.talker}${data.type}  ${data.raw.slice(0, 40)}${decoded}`, 'nmea');
    } else if (data.status === 'complete') {
      addStreamLine(`[stream complete]`, 'heartbeat');
    }
  };

  lanStreamWs.onclose = () => addStreamLine('[stream closed]', 'heartbeat');
  lanStreamWs.onerror = () => addStreamLine('[stream error]', 'error');
}

function addStreamLine(text, cls = '') {
  const panel = document.getElementById('stream-panel');
  const line = document.createElement('div');
  line.className = `stream-line ${cls}`;
  line.textContent = text;
  panel.appendChild(line);
  // Keep last 200 lines
  while (panel.children.length > 200) panel.removeChild(panel.firstChild);
  panel.scrollTop = panel.scrollHeight;
}

async function probeModbus(host, port = 502) {
  toast(`Probing MODBUS at ${host}:${port}...`, 'info');
  try {
    const result = await API.post('/api/lan/modbus/probe', { host, port, unit_id: 1 });
    const sidebar = document.getElementById('lan-scan-stats');
    sidebar.innerHTML += `
      <div style="margin-top:12px;">
        <div class="card-title">MODBUS — ${host}</div>
        <div class="hw-info-row"><div class="hw-info-label">Reachable</div>
          <div class="hw-info-value ${result.reachable ? 'text-green' : 'text-red'}">${result.reachable ? 'YES' : 'NO'}</div></div>
        ${result.holding_registers ? `
          <div class="hw-info-row"><div class="hw-info-label">Registers</div>
            <div class="hw-info-value mono" style="font-size:10px;">${result.holding_registers.slice(0,8).join(', ')}</div></div>
        ` : ''}
        ${result.error ? `<div class="text-red" style="margin-top:6px;font-size:11px;">${result.error}</div>` : ''}
      </div>
    `;
    toast(`MODBUS probe complete — ${result.reachable ? 'device online' : 'not reachable'}`, result.reachable ? 'success' : 'error');
  } catch (e) {
    toast('MODBUS probe failed: ' + e.message, 'error');
  }
}

function promptCreateAppFromHint(ip, hint) {
  // Switch to apps view and pre-fill the form
  showView('apps');
  document.getElementById('app-name-input').value = 'Ship System Monitor';
  document.getElementById('app-desc-input').value = hint;
  document.getElementById('app-context-input').value = `Host: ${ip}. ${hint}`;
  toast('App form pre-filled from LAN discovery', 'info');
}

function promptCreateAppFromRec(rec) {
  showView('apps');
  const blueprint = typeof inferAppBlueprint === 'function'
    ? inferAppBlueprint(rec, 'Ship App')
    : { name: 'Ship App', description: rec };
  document.getElementById('app-name-input').value = blueprint.name;
  document.getElementById('app-desc-input').value = blueprint.description;
  if (State.lanScanResult) {
    const context = State.lanScanResult.hosts.map(h =>
      `${h.ip} (${h.services.map(s => `${s.port}/${s.protocol}`).join(', ')})`
    ).join('; ');
    document.getElementById('app-context-input').value = context;
  }
  toast('App form pre-filled from AI recommendation', 'info');
}

function initLan() {
  document.getElementById('lan-scan-btn').onclick = runLanScan;

  document.getElementById('lan-stop-stream-btn').onclick = () => {
    if (lanStreamWs) {
      lanStreamWs.close();
      lanStreamWs = null;
      toast('Stream stopped', 'info');
    }
  };

  // Enter in network input triggers scan
  document.getElementById('lan-network-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') runLanScan();
  });
  document.getElementById('lan-ports-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') runLanScan();
  });
}

document.addEventListener('DOMContentLoaded', initLan);
