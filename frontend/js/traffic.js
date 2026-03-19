/**
 * WarClaw — Traffic Monitor Module
 * Real-time LAN traffic capture, conversation tracking, and AI analysis.
 */

let trafficWs = null;
let trafficRefreshInterval = null;
let trafficAdvisorInterval = null;
const trafficState = {
  selectedConversationKey: '',
  selectedConversation: null,
  selectedNodeId: '',
  selectedNode: null,
  conversations: [],
  activeSnapshotId: '',
  topology: { nodes: [], edges: [], source: 'none' },
  frames: [],
  nodePositions: {},
  viewport: { scale: 1, x: 0, y: 0 },
  draggingNodeId: '',
};

function parsePortList(text) {
  const raw = String(text || '').trim();
  if (!raw) return [];
  return raw.split(',').map(part => part.trim()).filter(Boolean).map(part => {
    const port = Number(part);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw new Error(`Invalid port: ${part}`);
    }
    return port;
  }).filter((port, index, arr) => arr.indexOf(port) === index);
}

function formatPortList(ports) {
  return (ports || []).map(port => String(port)).join(', ');
}

function updateTrafficNetworkLabel() {
  const label = document.getElementById('traffic-network-name');
  if (!label) return;
  const scan = State?.lanScanResult || null;
  label.textContent = scan?.network_name || scan?.interface || scan?.network || 'Unknown';
}

function openTrafficHelp() {
  const modal = document.getElementById('traffic-help-modal');
  if (modal) modal.classList.add('visible');
}
window.openTrafficHelp = openTrafficHelp;

function closeTrafficHelp() {
  const modal = document.getElementById('traffic-help-modal');
  if (modal) modal.classList.remove('visible');
}
window.closeTrafficHelp = closeTrafficHelp;

function fmtBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1048576).toFixed(1) + ' MB';
}

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString('en-US', { hour12: false });
}

function contentTypeClass(ct) {
  if (ct === 'nmea') return 'nmea';
  if (ct === 'json') return 'json';
  if (ct === 'http') return 'http';
  if (ct === 'modbus') return 'modbus';
  return '';
}

function contentTypeLabel(ct) {
  const labels = {
    nmea: 'NMEA',
    json: 'JSON',
    http: 'HTTP',
    modbus: 'MODBUS',
    text: 'TEXT',
    binary: 'BIN',
    empty: '',
    unknown: '',
  };
  return labels[ct] || ct.toUpperCase();
}

function renderTrafficFrame(frame) {
  if (frame.heartbeat) return '';

  const typeLabel = contentTypeLabel(frame.content_type);
  const typeCls = contentTypeClass(frame.content_type);
  const typeBadge = typeLabel ? `<span class="traffic-type-badge ${typeCls}">${typeLabel}</span>` : '';
  const time = fmtTime(frame.timestamp);

  let payload = '';
  if (frame.payload_decoded) {
    payload = `<div class="traffic-decoded">${escapeHtml(JSON.stringify(frame.payload_decoded, null, 0).slice(0, 200))}</div>`;
  } else if (frame.payload_preview) {
    payload = `<div class="traffic-preview">${escapeHtml(frame.payload_preview.slice(0, 150))}</div>`;
  }

  const convKey = `${frame.src}->${frame.dst}`;
  const isSelected = trafficState.selectedConversationKey && trafficState.selectedConversationKey === convKey;
  const isMuted = trafficState.selectedConversationKey && trafficState.selectedConversationKey !== convKey;

  return `
    <div class="traffic-frame ${typeCls} ${isSelected ? 'selected' : ''} ${isMuted ? 'muted' : ''}" data-conv-key="${escapeHtml(convKey)}">
      <span class="traffic-time">${time}</span>
      <span class="traffic-src">${frame.src}</span>
      <span class="traffic-arrow">→</span>
      <span class="traffic-dst">${frame.dst}</span>
      ${typeBadge}
      <span class="traffic-len">${frame.length}B</span>
      ${payload}
    </div>
  `;
}

function renderConversation(conv) {
  const duration = conv.duration_s > 0 ? `${conv.duration_s}s` : 'just now';
  const samples = conv.sample_payloads || [];
  const lastSample = samples.length ? samples[samples.length - 1] : null;
  const preview = lastSample ? (lastSample.preview || '').slice(0, 80) : '';

  const selected = trafficState.selectedConversationKey === conv.key ? 'selected' : '';
  return `
    <button class="conv-card ${selected}" data-conv-key="${escapeHtml(conv.key)}" onclick="selectTrafficConversation('${escapeHtml(conv.key)}')">
      <div class="conv-header">
        <span class="conv-proto ${contentTypeClass(conv.protocol.toLowerCase())}">${conv.protocol}</span>
        <span class="conv-count">${conv.packet_count} pkts</span>
      </div>
      <div class="conv-flow">
        <div class="conv-endpoint">${conv.src}</div>
        <div class="conv-arrow">→</div>
        <div class="conv-endpoint">${conv.dst}</div>
      </div>
      <div class="conv-meta">
        ${fmtBytes(conv.byte_count)} · ${duration}
      </div>
      ${preview ? `<div class="conv-preview">${escapeHtml(preview)}</div>` : ''}
    </button>
  `;
}

function nodeKind(node) {
  return node?.kind || (node?.port ? 'service' : 'host');
}

function trafficKeyForNode(nodeId) {
  return String(nodeId || '').trim();
}

function ensureNodePositions(nodes, width, height) {
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.max(70, Math.min(width, height) / 2 - 48);
  nodes.forEach((node, index, arr) => {
    if (trafficState.nodePositions[node.id]) return;
    const angle = (Math.PI * 2 * index) / Math.max(1, arr.length) - Math.PI / 2;
    trafficState.nodePositions[node.id] = {
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
    };
  });
}

function zoomTrafficDiagram(delta) {
  const next = Math.max(0.55, Math.min(2.8, (trafficState.viewport.scale || 1) + delta));
  trafficState.viewport.scale = next;
  renderTopology(trafficState.topology);
}

function resetTrafficDiagramView() {
  trafficState.viewport = { scale: 1, x: 0, y: 0 };
  renderTopology(trafficState.topology);
}

function renderNodeTrafficPanel() {
  const label = document.getElementById('traffic-node-label');
  const meta = document.getElementById('traffic-node-meta');
  const body = document.getElementById('traffic-node-traffic');
  if (!label || !meta || !body) return;
  const node = trafficState.selectedNode;
  if (!node) {
    label.textContent = 'No node selected.';
    meta.textContent = 'Click a node to inspect related traffic, service roles, and decoded payloads.';
    body.innerHTML = '';
    return;
  }

  const nodeId = trafficKeyForNode(node.id);
  const relatedConversations = trafficState.conversations.filter(conv =>
    conv.src === nodeId || conv.dst === nodeId || conv.src.startsWith(`${node.host}:`) || conv.dst.startsWith(`${node.host}:`)
  );
  const relatedFrames = (trafficState.frames || []).filter(frame =>
    frame.src === nodeId || frame.dst === nodeId || frame.src.startsWith(`${node.host}:`) || frame.dst.startsWith(`${node.host}:`)
  ).slice(-8).reverse();
  const protocols = (node.protocols || []).join(', ') || 'Unknown';

  label.textContent = node.label || node.id;
  meta.textContent = `${nodeKind(node).toUpperCase()} · ${protocols} · ${relatedConversations.length} related path(s) · ${relatedFrames.length} recent frame(s)`;

  const metrics = `
    <div class="topology-node-meta">
      <div class="topology-node-metric">
        <div class="topology-node-metric-label">Host</div>
        <div class="topology-node-metric-value">${escapeHtml(node.host || node.id)}</div>
      </div>
      <div class="topology-node-metric">
        <div class="topology-node-metric-label">Port / Role</div>
        <div class="topology-node-metric-value">${escapeHtml(node.port || 'Host node')}</div>
      </div>
      <div class="topology-node-metric">
        <div class="topology-node-metric-label">Traffic</div>
        <div class="topology-node-metric-value">${escapeHtml(`${node.packet_count || 0} pkts / ${fmtBytes(node.byte_count || 0)}`)}</div>
      </div>
    </div>
  `;

  const conversationSummary = relatedConversations.length
    ? relatedConversations.slice(0, 5).map(conv => `
      <div class="topology-traffic-card">
        <div class="topology-traffic-card-header">
          <strong>${escapeHtml(conv.protocol)}</strong>
          <span>${escapeHtml(`${conv.packet_count} pkts · ${fmtBytes(conv.byte_count)}`)}</span>
        </div>
        <div class="topology-traffic-readable">${escapeHtml(`${conv.src} -> ${conv.dst}`)}</div>
        ${conv.sample_payloads?.length ? `<div class="topology-traffic-raw">${escapeHtml(conv.sample_payloads[conv.sample_payloads.length - 1].preview || '')}</div>` : ''}
      </div>
    `).join('')
    : '<div class="text-muted" style="font-size:11px;">No related live conversations yet. The node may only be present from the LAN scan.</div>';

  const frameSummary = relatedFrames.length
    ? relatedFrames.map(frame => `
      <div class="topology-traffic-card">
        <div class="topology-traffic-card-header">
          <strong>${escapeHtml(contentTypeLabel(frame.content_type || frame.protocol || 'unknown'))}</strong>
          <span>${escapeHtml(`${fmtTime(frame.timestamp)} · ${frame.length || 0}B`)}</span>
        </div>
        <div class="topology-traffic-raw">${escapeHtml(frame.payload_preview || '[no raw preview]')}</div>
        ${frame.payload_decoded ? `<div class="topology-traffic-readable">${escapeHtml(JSON.stringify(frame.payload_decoded, null, 2))}</div>` : ''}
      </div>
    `).join('')
    : '<div class="text-muted" style="font-size:11px;">No recent frames captured for this node yet.</div>';

  body.innerHTML = `
    <div class="topology-node-details">
      ${metrics}
      <div>
        <div class="card-title">Related Paths</div>
        <div class="topology-traffic-list">${conversationSummary}</div>
      </div>
      <div>
        <div class="card-title">Recent Raw + Human Readable Traffic</div>
        <div class="topology-traffic-list">${frameSummary}</div>
      </div>
    </div>
  `;
}

function renderTopology(topology) {
  const container = document.getElementById('traffic-topology');
  const subtitle = document.getElementById('traffic-diagram-subtitle');
  if (!container) return;
  trafficState.topology = topology || { nodes: [], edges: [], source: 'none' };
  const nodes = topology?.nodes || [];
  const edges = topology?.edges || [];
  if (!nodes.length) {
    if (subtitle) subtitle.textContent = 'Service-to-service paths derived from observed traffic and the last LAN scan';
    container.innerHTML = '<div class="text-muted" style="font-size:11px;padding:16px;text-align:center;">No topology yet. Run a LAN scan or start capture and wait for traffic.</div>';
    return;
  }
  if (subtitle) {
    const source = topology?.source || 'traffic';
    subtitle.textContent = source === 'scan'
      ? 'Hardware and service map derived from the last LAN scan'
      : source === 'combined'
        ? 'Live conversations overlaid on the discovered hardware and service map'
        : 'Service-to-service paths derived from observed traffic';
  }

  const width = container.clientWidth || 640;
  const height = 220;
  ensureNodePositions(nodes.slice(0, 18), width, height);
  const positioned = nodes.slice(0, 18).map(node => ({ ...node, ...(trafficState.nodePositions[node.id] || { x: width / 2, y: height / 2 }) }));
  const nodeById = Object.fromEntries(positioned.map(node => [node.id, node]));
  const edgeSvg = edges
    .filter(edge => nodeById[edge.source] && nodeById[edge.target])
    .slice(0, 28)
    .map(edge => {
      const src = nodeById[edge.source];
      const dst = nodeById[edge.target];
      const stroke = edge.protocols.includes('NMEA') ? '#65d8ff'
        : edge.protocols.includes('MODBUS') ? '#ffc857'
        : '#ff8a5d';
      const selected = trafficState.selectedConversationKey === edge.id;
      return `<line x1="${src.x}" y1="${src.y}" x2="${dst.x}" y2="${dst.y}" stroke="${stroke}" stroke-width="${selected ? Math.min(8, 2 + edge.packet_count / 20) : Math.min(6, 1 + edge.packet_count / 25)}" stroke-opacity="${selected ? '1' : '0.65'}" data-edge-key="${escapeHtml(edge.id)}" class="topology-edge ${selected ? 'selected' : ''}" />`;
    }).join('');
  const nodeSvg = positioned.map(node => `
    <g class="topology-node ${nodeKind(node)}-node ${trafficState.selectedNodeId === node.id ? 'selected' : ''}" data-node-id="${escapeHtml(node.id)}" transform="translate(${node.x}, ${node.y})">
      <circle r="${nodeKind(node) === 'host' ? 20 : 17}" stroke="#65d8ff" stroke-width="1.4"></circle>
      <text y="4" fill="#ecf4f7" font-size="9" text-anchor="middle">${escapeHtml((node.port || '').slice(0, 5) || 'host')}</text>
      <text y="34" fill="#8ca7b6" font-size="9" text-anchor="middle">${escapeHtml((node.host || '').slice(0, 18))}</text>
    </g>
  `).join('');
  container.innerHTML = `<svg class="traffic-topology-svg" viewBox="0 0 ${width} ${height}" width="100%" height="${height}"><g id="traffic-topology-viewport" transform="translate(${trafficState.viewport.x} ${trafficState.viewport.y}) scale(${trafficState.viewport.scale})">${edgeSvg}${nodeSvg}</g></svg>`;

  const svg = container.querySelector('.traffic-topology-svg');
  const viewport = container.querySelector('#traffic-topology-viewport');
  if (svg && viewport) {
    let panState = null;
    svg.addEventListener('wheel', event => {
      event.preventDefault();
      zoomTrafficDiagram(event.deltaY < 0 ? 0.12 : -0.12);
    });
    svg.addEventListener('pointerdown', event => {
      const nodeEl = event.target.closest('.topology-node');
      if (nodeEl) return;
      panState = { startX: event.clientX, startY: event.clientY, x: trafficState.viewport.x, y: trafficState.viewport.y };
      svg.classList.add('dragging');
    });
    svg.addEventListener('pointermove', event => {
      if (trafficState.draggingNodeId) {
        const pt = svg.createSVGPoint();
        pt.x = event.clientX;
        pt.y = event.clientY;
        const inverse = viewport.getScreenCTM().inverse();
        const coord = pt.matrixTransform(inverse);
        trafficState.nodePositions[trafficState.draggingNodeId] = { x: coord.x, y: coord.y };
        renderTopology(trafficState.topology);
        return;
      }
      if (!panState) return;
      trafficState.viewport.x = panState.x + (event.clientX - panState.startX);
      trafficState.viewport.y = panState.y + (event.clientY - panState.startY);
      renderTopology(trafficState.topology);
    });
    svg.addEventListener('pointerup', () => {
      panState = null;
      trafficState.draggingNodeId = '';
      svg.classList.remove('dragging');
    });
    svg.addEventListener('pointerleave', () => {
      panState = null;
      trafficState.draggingNodeId = '';
      svg.classList.remove('dragging');
    });
    container.querySelectorAll('.topology-edge').forEach(edgeEl => {
      edgeEl.addEventListener('click', event => {
        event.stopPropagation();
        selectTrafficConversation(edgeEl.getAttribute('data-edge-key') || '');
      });
    });
    container.querySelectorAll('.topology-node').forEach(nodeEl => {
      nodeEl.addEventListener('click', event => {
        event.stopPropagation();
        selectTrafficNode(nodeEl.getAttribute('data-node-id') || '');
      });
      nodeEl.addEventListener('pointerdown', event => {
        event.stopPropagation();
        trafficState.draggingNodeId = nodeEl.getAttribute('data-node-id') || '';
      });
    });
  }
}

function renderTrafficRecommendations(items) {
  const el = document.getElementById('traffic-recommendations');
  if (!el) return;
  if (!items || !items.length) {
    el.innerHTML = '<div class="text-muted" style="font-size:11px;">No recommendations yet. Capture traffic or deploy the advisor agent.</div>';
    return;
  }
  el.innerHTML = items.map(item => `
    <div style="padding:10px 0;border-bottom:1px solid var(--border);">
      <div style="display:flex;justify-content:space-between;gap:12px;">
        <strong style="color:var(--text-primary);font-size:12px;">${escapeHtml(item.title)}</strong>
        <span style="font-size:10px;text-transform:uppercase;color:${item.severity === 'critical' ? 'var(--accent-red)' : item.severity === 'high' ? 'var(--accent-amber)' : 'var(--accent-blue)'};">${escapeHtml(item.severity)}</span>
      </div>
      <div style="font-size:11px;color:var(--text-secondary);margin-top:6px;">${escapeHtml(item.rationale)}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;">Action: ${escapeHtml(item.action)}</div>
    </div>
  `).join('');
}

function renderTrafficSnapshots(items) {
  const el = document.getElementById('traffic-snapshots');
  if (!el) return;
  if (!items || !items.length) {
    el.innerHTML = '<div class="text-muted" style="font-size:11px;">No saved snapshots yet.</div>';
    return;
  }
  el.innerHTML = items.map(item => `
    <div class="conv-card ${trafficState.activeSnapshotId === item.id ? 'selected' : ''}" style="margin-bottom:6px;border:1px solid var(--border);border-radius:8px;border-left-width:2px;">
      <div class="conv-header">
        <span class="conv-proto">${escapeHtml(String(item.conversation_count))} PATHS</span>
        <span class="conv-count">${escapeHtml(String(item.recommendation_count))} recs</span>
      </div>
      <div class="conv-flow">
        <div class="conv-endpoint">${escapeHtml(item.title)}</div>
      </div>
      <div class="conv-meta">${new Date((item.exported_at || 0) * 1000).toLocaleString()}</div>
      <div style="display:flex;gap:8px;margin-top:8px;">
        <button class="btn" style="font-size:10px;padding:4px 10px;" onclick="loadTrafficSnapshot('${escapeHtml(item.id)}')">Load</button>
        <button class="btn" style="font-size:10px;padding:4px 10px;" onclick="exportSavedTrafficBrief('${escapeHtml(item.id)}')">Brief</button>
        <button class="btn btn-danger" style="font-size:10px;padding:4px 10px;" onclick="deleteTrafficSnapshot('${escapeHtml(item.id)}')">Delete</button>
      </div>
    </div>
  `).join('');
}

function updateActiveSnapshotLabel() {
  const el = document.getElementById('traffic-active-snapshot');
  if (!el) return;
  el.textContent = trafficState.activeSnapshotId || 'Live';
}

function updateTrafficFocusPanel() {
  const label = document.getElementById('traffic-focus-label');
  const meta = document.getElementById('traffic-focus-meta');
  if (!label || !meta) return;
  const conv = trafficState.selectedConversation;
  if (!conv) {
    label.textContent = 'No path selected.';
    meta.textContent = 'Select a conversation to isolate its traffic in the live feed.';
    return;
  }
  label.textContent = `${conv.src} -> ${conv.dst}`;
  meta.textContent = `${conv.protocol} · ${conv.packet_count} packets · ${fmtBytes(conv.byte_count)} · active for ${conv.duration_s > 0 ? `${conv.duration_s}s` : 'just now'}`;
}

function applyTrafficFocus() {
  const frames = document.querySelectorAll('#traffic-feed .traffic-frame');
  frames.forEach(frame => {
    const key = frame.getAttribute('data-conv-key') || '';
    const selected = trafficState.selectedConversationKey && key === trafficState.selectedConversationKey;
    const muted = trafficState.selectedConversationKey && key !== trafficState.selectedConversationKey;
    frame.classList.toggle('selected', !!selected);
    frame.classList.toggle('muted', !!muted);
  });
  updateTrafficFocusPanel();
  renderNodeTrafficPanel();
}

function selectTrafficConversation(key) {
  if (!key) return;
  trafficState.selectedConversationKey = key;
  trafficState.selectedConversation = trafficState.conversations.find(conv => conv.key === key) || null;
  const container = document.getElementById('traffic-conversations');
  if (container) container.innerHTML = trafficState.conversations.map(renderConversation).join('');
  renderTopology(trafficState.topology);
  applyTrafficFocus();
}

function clearTrafficFocus() {
  trafficState.selectedConversationKey = '';
  trafficState.selectedConversation = null;
  const container = document.getElementById('traffic-conversations');
  if (container) container.innerHTML = trafficState.conversations.map(renderConversation).join('');
  renderTopology(trafficState.topology);
  applyTrafficFocus();
}

function selectTrafficNode(nodeId) {
  if (!nodeId) return;
  trafficState.selectedNodeId = nodeId;
  trafficState.selectedNode = (trafficState.topology?.nodes || []).find(node => node.id === nodeId) || null;
  const match = trafficState.conversations.find(conv => conv.src === nodeId || conv.dst === nodeId || conv.key.startsWith(`${nodeId}->`) || conv.key.endsWith(`->${nodeId}`));
  if (match) {
    selectTrafficConversation(match.key);
  } else {
    renderTopology(trafficState.topology);
    renderNodeTrafficPanel();
  }
}

function clearTrafficNodeSelection() {
  trafficState.selectedNodeId = '';
  trafficState.selectedNode = null;
  renderTopology(trafficState.topology);
  renderNodeTrafficPanel();
}

function exportTrafficSnapshot() {
  window.open('/api/traffic/snapshot/export', '_blank', 'noopener');
}

function exportTrafficBrief() {
  window.open('/api/traffic/snapshot/brief.md', '_blank', 'noopener');
}

function exportSavedTrafficBrief(snapshotId) {
  window.open(`/api/traffic/snapshots/${encodeURIComponent(snapshotId)}/brief.md`, '_blank', 'noopener');
}

async function refreshTrafficSnapshots() {
  try {
    const data = await API.get('/api/traffic/snapshots');
    renderTrafficSnapshots(data.snapshots || []);
  } catch (e) {
    renderTrafficSnapshots([]);
  }
}

function applyTrafficPayload(payload) {
  const topology = payload.topology || { nodes: [], edges: [] };
  const recs = payload.recommendations || [];
  const conversations = payload.conversations || [];
  trafficState.frames = payload.recent_frames || [];
  trafficState.conversations = conversations;
  trafficState.selectedConversation = conversations.find(conv => conv.key === trafficState.selectedConversationKey) || null;
  if (!trafficState.selectedConversation) trafficState.selectedConversationKey = '';
  renderTopology(topology);
  renderTrafficRecommendations(recs);

  const container = document.getElementById('traffic-conversations');
  if (container) {
    container.innerHTML = conversations.length
      ? conversations.map(renderConversation).join('')
      : '<div class="text-muted" style="font-size:11px;padding:16px;text-align:center;">No conversations in this snapshot.</div>';
  }

  const feed = document.getElementById('traffic-feed');
  if (feed) {
    const frames = payload.recent_frames || [];
    feed.innerHTML = frames.length
      ? frames.map(renderTrafficFrame).join('')
      : '<div class="text-muted" style="font-size:11px;padding:16px;text-align:center;">No frames in this snapshot.</div>';
  }
  applyTrafficFocus();
}

async function saveTrafficSnapshot() {
  try {
    const payload = await API.post('/api/traffic/snapshots?limit=120', {});
    trafficState.activeSnapshotId = payload.id || '';
    updateActiveSnapshotLabel();
    toast('Traffic snapshot saved', 'success');
    await refreshTrafficSnapshots();
  } catch (e) {
    toast('Snapshot save failed: ' + e.message, 'error');
  }
}

async function loadTrafficSnapshot(snapshotId) {
  try {
    const payload = await API.get(`/api/traffic/snapshots/${encodeURIComponent(snapshotId)}`);
    trafficState.activeSnapshotId = snapshotId;
    updateActiveSnapshotLabel();
    applyTrafficPayload(payload);
    await refreshTrafficSnapshots();
    toast('Traffic snapshot loaded', 'info');
  } catch (e) {
    toast('Snapshot load failed: ' + e.message, 'error');
  }
}

async function deleteTrafficSnapshot(snapshotId) {
  try {
    await API.del(`/api/traffic/snapshots/${encodeURIComponent(snapshotId)}`);
    if (trafficState.activeSnapshotId === snapshotId) {
      trafficState.activeSnapshotId = '';
      updateActiveSnapshotLabel();
    }
    await refreshTrafficSnapshots();
    toast('Traffic snapshot deleted', 'info');
  } catch (e) {
    toast('Snapshot delete failed: ' + e.message, 'error');
  }
}

async function returnToLiveTraffic() {
  trafficState.activeSnapshotId = '';
  updateActiveSnapshotLabel();
  await refreshConversations();
  toast('Returned to live traffic view', 'info');
}

function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

async function startTrafficCapture() {
  const startBtn = document.getElementById('traffic-start-btn');
  const stopBtn = document.getElementById('traffic-stop-btn');
  const includeInput = document.getElementById('traffic-include-ports-input');
  const excludeInput = document.getElementById('traffic-exclude-ports-input');

  try {
    const includePorts = parsePortList(includeInput?.value || '');
    const excludePorts = parsePortList(excludeInput?.value || '');
    const result = await API.post('/api/traffic/start', {
      include_ports: includePorts,
      exclude_ports: excludePorts,
    });
    startBtn.style.display = 'none';
    stopBtn.style.display = '';
    document.getElementById('traffic-status').textContent = 'CAPTURING';
    document.getElementById('traffic-status').style.color = 'var(--accent-green)';
    document.getElementById('traffic-empty').style.display = 'none';
    updateTrafficNetworkLabel();
    updateTrafficStats(result);
    toast('Traffic capture started', 'success');

    // Connect WebSocket for live feed
    connectTrafficWs();

    // Start polling conversations
    trafficRefreshInterval = setInterval(refreshConversations, 3000);
    trafficAdvisorInterval = setInterval(refreshTrafficRecommendations, 5000);
    await refreshConversations();
    await refreshTrafficRecommendations();
  } catch (e) {
    toast('Failed to start capture: ' + e.message, 'error');
  }
}

async function stopTrafficCapture() {
  const startBtn = document.getElementById('traffic-start-btn');
  const stopBtn = document.getElementById('traffic-stop-btn');

  try {
    await API.post('/api/traffic/stop', {});
    startBtn.style.display = '';
    stopBtn.style.display = 'none';
    document.getElementById('traffic-status').textContent = 'STOPPED';
    document.getElementById('traffic-status').style.color = 'var(--text-muted)';
    toast('Traffic capture stopped', 'info');

    if (trafficWs) {
      trafficWs.close();
      trafficWs = null;
    }
    if (trafficRefreshInterval) {
      clearInterval(trafficRefreshInterval);
      trafficRefreshInterval = null;
    }
    if (trafficAdvisorInterval) {
      clearInterval(trafficAdvisorInterval);
      trafficAdvisorInterval = null;
    }
  } catch (e) {
    toast('Failed to stop capture: ' + e.message, 'error');
  }
}

function connectTrafficWs() {
  if (trafficWs && trafficWs.readyState === WebSocket.OPEN) return;
  trafficWs = API.ws('/api/traffic/stream');

  trafficWs.onmessage = (event) => {
    const data = JSON.parse(event.data);

    if (data.heartbeat) {
      updateTrafficStats(data.stats);
      return;
    }

    const feed = document.getElementById('traffic-feed');
    const html = renderTrafficFrame(data);
    if (html) {
      const div = document.createElement('div');
      div.innerHTML = html;
      feed.appendChild(div.firstElementChild);

      // Keep last 300 entries
      while (feed.children.length > 300) {
        feed.removeChild(feed.firstChild);
      }
      feed.scrollTop = feed.scrollHeight;
    }

    // Update packet counter
    const countEl = document.getElementById('traffic-pkt-count');
    if (countEl) {
      const current = parseInt(countEl.textContent) || 0;
      countEl.textContent = current + 1;
    }
  };

  trafficWs.onclose = () => {
    trafficWs = null;
  };
}

function updateTrafficStats(stats) {
  if (!stats) return;
  updateTrafficNetworkLabel();
  document.getElementById('traffic-pkt-count').textContent = stats.packets_captured || 0;
  document.getElementById('traffic-byte-count').textContent = fmtBytes(stats.bytes_captured || 0);
  document.getElementById('traffic-conv-count').textContent = stats.conversations || 0;
  const includeInput = document.getElementById('traffic-include-ports-input');
  const excludeInput = document.getElementById('traffic-exclude-ports-input');
  if (includeInput && document.activeElement !== includeInput) {
    includeInput.value = formatPortList(stats.include_ports || []);
  }
  if (excludeInput && document.activeElement !== excludeInput) {
    excludeInput.value = formatPortList(stats.exclude_ports || []);
  }
  const statusDetail = document.getElementById('traffic-status-detail');
  if (statusDetail) {
    const mode = stats.capture_mode || 'idle';
    const lastError = stats.last_error || '';
    const includeText = formatPortList(stats.include_ports || []);
    const excludeText = formatPortList(stats.exclude_ports || []);
    const filterText = [
      includeText ? `Include: ${includeText}` : '',
      excludeText ? `Exclude: ${excludeText}` : '',
    ].filter(Boolean).join(' | ');
    if (lastError) {
      statusDetail.textContent = filterText ? `${lastError} | ${filterText}` : lastError;
    } else if (mode === 'socket-fallback') {
      statusDetail.textContent = `Using limited socket capture. Live topology is augmented with the last LAN scan.${filterText ? ` ${filterText}` : ''}`;
    } else if ((stats.conversations || 0) === 0 && (stats.packets_captured || 0) === 0) {
      statusDetail.textContent = `No live packets yet. Run a LAN scan to populate the network diagram while capture warms up.${filterText ? ` ${filterText}` : ''}`;
    } else {
      statusDetail.textContent = `Capture mode: ${mode.replace(/-/g, ' ')}. Last packet ${stats.last_packet_time ? new Date(stats.last_packet_time * 1000).toLocaleTimeString() : 'not observed yet'}.${filterText ? ` ${filterText}` : ''}`;
    }
  }

  const badge = document.getElementById('traffic-badge');
  if (badge && stats.conversations > 0) {
    badge.textContent = stats.conversations;
    badge.style.display = '';
  }
}

async function refreshConversations() {
  try {
    const [data, topology, recs, recent] = await Promise.all([
      API.get('/api/traffic/conversations'),
      API.get('/api/traffic/topology'),
      API.get('/api/traffic/recommendations'),
      API.get('/api/traffic/recent?limit=120'),
    ]);
    updateTrafficStats(data);
    trafficState.frames = recent.frames || [];
    renderTopology(topology);
    renderTrafficRecommendations(recs.recommendations || []);
    if (!trafficState.activeSnapshotId) updateActiveSnapshotLabel();
    trafficState.conversations = data.conversations || [];
    trafficState.selectedConversation = trafficState.conversations.find(conv => conv.key === trafficState.selectedConversationKey) || null;
    if (!trafficState.selectedConversation) {
      trafficState.selectedConversationKey = '';
    }

    const container = document.getElementById('traffic-conversations');
    if (data.conversations && data.conversations.length > 0) {
      container.innerHTML = data.conversations.map(renderConversation).join('');
      applyTrafficFocus();
    } else {
      container.innerHTML = '<div class="text-muted" style="font-size:11px;padding:16px;text-align:center;">Waiting for traffic...</div>';
      clearTrafficFocus();
    }
    await refreshTrafficSnapshots();
  } catch (e) {
    // silent — capture may not be running
  }
}

async function refreshTrafficRecommendations() {
  try {
    const [agentState, recs] = await Promise.all([
      API.get('/api/agents/'),
      API.get('/api/traffic/recommendations'),
    ]);
    const advisor = (agentState.agents || []).find(a => a.agent_type === 'traffic_advisor');
    const button = document.getElementById('traffic-advisor-btn');
    if (button) {
      if (advisor) {
        button.textContent = advisor.status === 'running' ? '⬡ Advisor Running' : '⬡ Start Advisor';
        button.disabled = advisor.status === 'running';
      } else {
        button.textContent = '⬡ Deploy Advisor';
        button.disabled = false;
      }
    }
    renderTrafficRecommendations((advisor && advisor.recommendations && advisor.recommendations.length)
      ? advisor.recommendations
      : (recs.recommendations || []));
  } catch (e) {
    renderTrafficRecommendations([]);
  }
}

async function deployTrafficAdvisor() {
  try {
    await API.post('/api/agents/deploy', {
      agent_type: 'traffic_advisor',
      target_host: '0.0.0.0',
      target_port: 0,
      auto_start: true,
    });
    toast('Traffic advisor deployed', 'success');
    await refreshTrafficRecommendations();
  } catch (e) {
    toast('Advisor deploy failed: ' + e.message, 'error');
  }
}

async function analyzeTraffic() {
  const panel = document.getElementById('traffic-ai-panel');
  const result = document.getElementById('traffic-ai-result');

  if (!State.modelReady) {
    toast('No AI model loaded. The model loads automatically on startup — check Hardware tab.', 'error');
    return;
  }

  panel.style.display = '';
  result.innerHTML = '<div class="text-muted">Analyzing traffic patterns...</div>';

  try {
    const data = await API.post('/api/traffic/analyze', {});
    let html = `<div style="white-space:pre-wrap;line-height:1.8;">${escapeHtml(data.analysis)}</div>`;

    if (data.recommendations && data.recommendations.length > 0) {
      html += '<div style="margin-top:12px;border-top:1px solid var(--border);padding-top:12px;">';
      html += '<div class="card-title">Recommended Apps</div>';
      data.recommendations.forEach(rec => {
        html += `
          <div style="margin:8px 0;padding:8px;background:var(--bg-card);border-radius:6px;">
            <div style="font-size:12px;color:var(--text-primary);">${escapeHtml(rec.title)}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">${escapeHtml(rec.description)}</div>
            <button class="btn btn-primary" style="font-size:10px;padding:3px 10px;margin-top:6px;"
              onclick="generateAppFromTraffic('${escapeHtml(rec.title)}', '${escapeHtml(rec.description)}', '${escapeHtml(rec.context || '')}')">
              Generate This App
            </button>
          </div>
        `;
      });
      html += '</div>';
    }

    result.innerHTML = html;
    toast('Traffic analysis complete', 'success');
  } catch (e) {
    result.innerHTML = `<div class="text-red">${escapeHtml(e.message)}</div>`;
    toast('Analysis failed: ' + e.message, 'error');
  }
}

function generateAppFromTraffic(name, desc, context) {
  showView('apps');
  document.getElementById('app-name-input').value = name;
  document.getElementById('app-desc-input').value = desc;
  document.getElementById('app-context-input').value = context;
  toast('App form pre-filled from traffic analysis', 'info');
}

function initTraffic() {
  document.getElementById('traffic-advisor-btn')?.addEventListener('click', deployTrafficAdvisor);
  document.getElementById('traffic-recommend-btn')?.addEventListener('click', refreshTrafficRecommendations);
  document.getElementById('traffic-clear-focus-btn')?.addEventListener('click', clearTrafficFocus);
  document.getElementById('traffic-export-btn')?.addEventListener('click', exportTrafficSnapshot);
  document.getElementById('traffic-brief-btn')?.addEventListener('click', exportTrafficBrief);
  document.getElementById('traffic-save-snapshot-btn')?.addEventListener('click', saveTrafficSnapshot);
  document.getElementById('traffic-live-btn')?.addEventListener('click', returnToLiveTraffic);
  document.getElementById('traffic-refresh-snapshots-btn')?.addEventListener('click', refreshTrafficSnapshots);
  document.getElementById('traffic-zoom-in-btn')?.addEventListener('click', () => zoomTrafficDiagram(0.15));
  document.getElementById('traffic-zoom-out-btn')?.addEventListener('click', () => zoomTrafficDiagram(-0.15));
  document.getElementById('traffic-reset-view-btn')?.addEventListener('click', resetTrafficDiagramView);
  document.getElementById('traffic-clear-node-btn')?.addEventListener('click', clearTrafficNodeSelection);
  document.getElementById('traffic-include-ports-input')?.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      startTrafficCapture();
    }
  });
  document.getElementById('traffic-exclude-ports-input')?.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      startTrafficCapture();
    }
  });
  window.addEventListener('warclaw:lan-scan-updated', updateTrafficNetworkLabel);
  updateTrafficNetworkLabel();

  // Check if capture is already running on page load
  API.get('/api/traffic/stats').then(stats => {
    updateTrafficStats(stats);
    if (stats.running) {
      document.getElementById('traffic-start-btn').style.display = 'none';
      document.getElementById('traffic-stop-btn').style.display = '';
      document.getElementById('traffic-status').textContent = 'CAPTURING';
      document.getElementById('traffic-status').style.color = 'var(--accent-green)';
      document.getElementById('traffic-empty').style.display = 'none';
      connectTrafficWs();
      trafficRefreshInterval = setInterval(refreshConversations, 3000);
      trafficAdvisorInterval = setInterval(refreshTrafficRecommendations, 5000);
      refreshConversations();
      refreshTrafficRecommendations();
    }
  }).catch(() => {}).finally(() => {
    updateActiveSnapshotLabel();
    refreshTrafficSnapshots();
    refreshConversations();
    refreshTrafficRecommendations();
    renderNodeTrafficPanel();
  });
}

document.addEventListener('DOMContentLoaded', initTraffic);
