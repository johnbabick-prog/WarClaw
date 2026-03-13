/**
 * WarClaw — Traffic Monitor Module
 * Real-time LAN traffic capture, conversation tracking, and AI analysis.
 */

let trafficWs = null;
let trafficRefreshInterval = null;

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

  return `
    <div class="traffic-frame ${typeCls}">
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

  return `
    <div class="conv-card">
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
    </div>
  `;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

async function startTrafficCapture() {
  const startBtn = document.getElementById('traffic-start-btn');
  const stopBtn = document.getElementById('traffic-stop-btn');

  try {
    await API.post('/api/traffic/start', {});
    startBtn.style.display = 'none';
    stopBtn.style.display = '';
    document.getElementById('traffic-status').textContent = 'CAPTURING';
    document.getElementById('traffic-status').style.color = 'var(--accent-green)';
    document.getElementById('traffic-empty').style.display = 'none';
    toast('Traffic capture started', 'success');

    // Connect WebSocket for live feed
    connectTrafficWs();

    // Start polling conversations
    trafficRefreshInterval = setInterval(refreshConversations, 3000);
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
  document.getElementById('traffic-pkt-count').textContent = stats.packets_captured || 0;
  document.getElementById('traffic-byte-count').textContent = fmtBytes(stats.bytes_captured || 0);
  document.getElementById('traffic-conv-count').textContent = stats.conversations || 0;

  const badge = document.getElementById('traffic-badge');
  if (badge && stats.conversations > 0) {
    badge.textContent = stats.conversations;
    badge.style.display = '';
  }
}

async function refreshConversations() {
  try {
    const data = await API.get('/api/traffic/conversations');
    updateTrafficStats(data);

    const container = document.getElementById('traffic-conversations');
    if (data.conversations && data.conversations.length > 0) {
      container.innerHTML = data.conversations.map(renderConversation).join('');
    } else {
      container.innerHTML = '<div class="text-muted" style="font-size:11px;padding:16px;text-align:center;">Waiting for traffic...</div>';
    }
  } catch (e) {
    // silent — capture may not be running
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
  // Check if capture is already running on page load
  API.get('/api/traffic/stats').then(stats => {
    if (stats.running) {
      document.getElementById('traffic-start-btn').style.display = 'none';
      document.getElementById('traffic-stop-btn').style.display = '';
      document.getElementById('traffic-status').textContent = 'CAPTURING';
      document.getElementById('traffic-status').style.color = 'var(--accent-green)';
      document.getElementById('traffic-empty').style.display = 'none';
      connectTrafficWs();
      trafficRefreshInterval = setInterval(refreshConversations, 3000);
      refreshConversations();
    }
  }).catch(() => {});
}

document.addEventListener('DOMContentLoaded', initTraffic);
