/**
 * WarClaw — Mission Log (Events) View
 * Connects to /api/events/stream (SSE) and renders a live event feed.
 */

const LEVEL_COLOR = {
  info:    'var(--text-secondary)',
  warn:    'var(--accent-amber)',
  alert:   'var(--accent-red)',
  success: 'var(--accent-green)',
};

const LEVEL_ICON = {
  info:    '◈',
  warn:    '▲',
  alert:   '✗',
  success: '✓',
};

const CAT_COLOR = {
  lan:      'var(--accent-cyan)',
  ai:       'var(--accent-blue)',
  system:   'var(--text-muted)',
  protocol: 'var(--accent-green)',
  app:      'var(--accent-amber)',
};

let _evtSource = null;
let _eventCount = 0;

function _renderEvent(evt) {
  const feed = document.getElementById('events-feed');
  if (!feed) return;

  // Remove placeholder text on first real event
  if (_eventCount === 0) feed.innerHTML = '';

  _eventCount++;

  const color = LEVEL_COLOR[evt.level] || 'var(--text-secondary)';
  const icon  = LEVEL_ICON[evt.level]  || '◈';
  const catColor = CAT_COLOR[evt.category] || 'var(--text-muted)';

  const row = document.createElement('div');
  row.className = 'event-row';
  row.style.cssText = `
    display:flex; gap:10px; align-items:baseline; padding:5px 0;
    border-bottom:1px solid rgba(26,74,122,0.4);
  `;

  row.innerHTML = `
    <span style="color:var(--text-muted);flex-shrink:0;width:80px;">${evt.utc ? evt.utc.slice(11) : '--:--:--'}</span>
    <span style="color:${color};flex-shrink:0;width:12px;">${icon}</span>
    <span style="color:${catColor};flex-shrink:0;width:60px;text-transform:uppercase;font-size:10px;letter-spacing:1px;">${evt.category}</span>
    <span style="color:${color};flex:1;">${_esc(evt.message)}</span>
  `;

  feed.appendChild(row);

  // Auto-scroll to bottom
  feed.scrollTop = feed.scrollHeight;

  // Update badge if not on events view
  if (!document.getElementById('view-events')?.classList.contains('active')) {
    const badge = document.getElementById('events-badge');
    if (badge) {
      badge.style.display = '';
      badge.textContent = Math.min(parseInt(badge.textContent || '0') + 1, 99);
    }
  }
}

function _esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function _connectSSE() {
  if (_evtSource) {
    _evtSource.close();
  }

  _evtSource = new EventSource('/api/events/stream');

  _evtSource.onmessage = (e) => {
    try {
      const evt = JSON.parse(e.data);
      _renderEvent(evt);
    } catch (_) {}
  };

  _evtSource.onerror = () => {
    // Reconnect after 5s on error
    _evtSource.close();
    setTimeout(_connectSSE, 5000);
  };
}

// ── Clear button ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const clearBtn = document.getElementById('events-clear-btn');
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      const feed = document.getElementById('events-feed');
      if (feed) {
        feed.innerHTML = '<div style="color:var(--text-muted);text-align:center;margin-top:40px;">Log view cleared — stream still active.</div>';
        _eventCount = 0;
      }
    });
  }

  const exportBtn = document.getElementById('events-export-btn');
  if (exportBtn) {
    exportBtn.addEventListener('click', async () => {
      try {
        const data = await API.get('/api/events/?limit=1000');
        const blob = new Blob([JSON.stringify(data.events, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `edgerunner-warclaw-mission-log-${Date.now()}.json`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (e) {
        toast('Export failed: ' + e.message, 'error');
      }
    });
  }
});

window.addEventListener('viewchange', (e) => {
  if (e.detail === 'events') {
    const badge = document.getElementById('events-badge');
    if (badge) badge.style.display = 'none';
  }
});

// ── Start SSE connection ──────────────────────────────────────────
_connectSSE();
