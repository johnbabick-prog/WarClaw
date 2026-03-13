/**
 * WarClaw — App Factory Module
 */

const APP_ICONS = ['⚓', '⬡', '◈', '⚙', '◉', '⊕', '⊗', '⬢'];
function randomIcon() { return APP_ICONS[Math.floor(Math.random() * APP_ICONS.length)]; }

async function loadAppList() {
  const panel = document.getElementById('app-list-panel');
  try {
    const data = await API.get('/api/apps/');
    State.generatedApps = data.apps;
    document.getElementById('stat-apps').textContent = data.apps.length;
    if (typeof syncLiveUi === 'function') syncLiveUi();

    if (!data.apps.length) {
      panel.innerHTML = `
        <div id="lan-empty" style="height:200px;">
          <div class="empty-icon">⊕</div>
          <div>No apps generated yet. Use the panel on the left to create one.</div>
        </div>
      `;
      return;
    }

    panel.innerHTML = data.apps.map(app => `
      <div class="app-card" id="app-card-${app.slug}">
        <div class="app-icon">${randomIcon()}</div>
        <div class="app-info">
          <div class="app-name">${app.name}</div>
          <div class="app-desc">${app.description || ''}</div>
          <div class="app-meta">
            ${app.slug} &nbsp;·&nbsp;
            ${new Date(app.created_at * 1000).toLocaleDateString()}
            ${app.generation_time_s ? `&nbsp;·&nbsp; generated in ${app.generation_time_s}s` : ''}
          </div>
          ${app.status !== 'success' ? `
            <div class="text-amber" style="font-size:11px;margin-top:6px;">
              Partial generation: ${(app.errors || []).join(', ')}
            </div>
          ` : ''}
        </div>
        <div class="app-actions">
          <button class="btn btn-primary" ${app.has_frontend ? '' : 'disabled'}
            onclick="launchApp('${app.slug}', ${app.has_frontend ? 'true' : 'false'})">▶ Open</button>
          <button class="btn btn-danger" onclick="deleteApp('${app.slug}')">✕</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    panel.innerHTML = `<div class="text-red">Failed to load apps: ${e.message}</div>`;
  }
}

async function generateApp() {
  const name = document.getElementById('app-name-input').value.trim();
  const desc = document.getElementById('app-desc-input').value.trim();
  const context = document.getElementById('app-context-input').value.trim();
  const btn = document.getElementById('gen-app-btn');
  const statusEl = document.getElementById('gen-status');

  if (!name || !desc) {
    toast('App name and description are required', 'error');
    return;
  }

  if (!State.modelReady) {
    toast('No AI model loaded. Load a model in the Hardware tab first.', 'error');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> GENERATING...';
  statusEl.textContent = 'AI is writing your app — this may take 30–120 seconds...';
  statusEl.style.color = 'var(--accent-amber)';

  // Animate progress
  const progress = document.getElementById('gen-progress-fill');
  let pct = 0;
  const progTimer = setInterval(() => {
    pct = Math.min(pct + 0.8, 90);
    progress.style.width = pct + '%';
  }, 800);

  try {
    const result = await API.post('/api/apps/generate', { name, description: desc, context });
    clearInterval(progTimer);
    progress.style.width = '100%';

    if (result.status === 'success') {
      statusEl.textContent = `✓ App "${name}" generated in ${result.generation_time_s}s`;
      statusEl.style.color = 'var(--accent-green)';
      toast(`App "${name}" created!`, 'success');
    } else {
      statusEl.textContent = `⚠ Partial generation: ${result.errors.join(', ')}`;
      statusEl.style.color = 'var(--accent-amber)';
      toast('App generated with warnings', 'info');
    }

    await loadAppList();

    // Clear form
    document.getElementById('app-name-input').value = '';
    document.getElementById('app-desc-input').value = '';
    document.getElementById('app-context-input').value = '';

  } catch (e) {
    clearInterval(progTimer);
    statusEl.textContent = '✗ Generation failed: ' + e.message;
    statusEl.style.color = 'var(--accent-red)';
    toast('App generation failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '⚡ GENERATE APP';
    setTimeout(() => { progress.style.width = '0%'; }, 2000);
  }
}

function launchApp(slug, hasFrontend = true) {
  if (!hasFrontend) {
    toast('This app was only partially generated and has no frontend to open yet.', 'error');
    return;
  }
  const url = `/api/apps/${slug}/ui`;
  window.open(url, '_blank', 'noopener');
}

async function deleteApp(slug) {
  if (!confirm(`Delete app "${slug}"?`)) return;
  try {
    await API.del(`/api/apps/${slug}`);
    toast(`App ${slug} deleted`, 'info');
    await loadAppList();
  } catch (e) {
    toast('Delete failed: ' + e.message, 'error');
  }
}

// Template quick-fills
const APP_TEMPLATES = [
  {
    label: '⚓ Nav Dashboard',
    name: 'Navigation Dashboard',
    desc: 'Real-time navigation display showing vessel position, heading, speed, and depth from NMEA sensors.',
    context: 'NMEA 0183 data source on LAN.',
  },
  {
    label: '⚙ Plant Monitor',
    name: 'Engineering Plant Monitor',
    desc: 'Monitor ship engineering plant parameters from MODBUS sensors including temperatures, pressures, and RPMs.',
    context: 'MODBUS TCP device on LAN port 502.',
  },
  {
    label: '◈ LAN Overview',
    name: 'Ship Systems Overview',
    desc: 'Master dashboard displaying all detected ship systems, their status, and live data feeds.',
    context: 'Multiple systems detected on ship LAN.',
  },
  {
    label: '⬡ AIS Tracker',
    name: 'AIS Traffic Display',
    desc: 'Parse and display AIS vessel tracking data from the ship LAN AIS receiver.',
    context: 'AIS receiver broadcasting NMEA VDM sentences.',
  },
];

function initApps() {
  document.getElementById('gen-app-btn').onclick = generateApp;

  // Template buttons
  const templateContainer = document.getElementById('app-templates');
  APP_TEMPLATES.forEach(tpl => {
    const btn = document.createElement('button');
    btn.className = 'btn';
    btn.style.fontSize = '11px';
    btn.innerHTML = tpl.label;
    btn.onclick = () => {
      document.getElementById('app-name-input').value = tpl.name;
      document.getElementById('app-desc-input').value = tpl.desc;
      document.getElementById('app-context-input').value = tpl.context;
    };
    templateContainer.appendChild(btn);
  });

  loadAppList();
}

/**
 * Generate all recommended apps from LAN scan results with one click.
 */
async function generateAllRecommended() {
  if (!State.modelReady) {
    toast('No AI model loaded. Wait for model to finish loading.', 'error');
    return;
  }

  const recs = State.lanScanResult?.recommendations || [];
  if (!recs.length) {
    toast('No recommendations available. Run a LAN scan first.', 'error');
    return;
  }

  const btn = document.getElementById('gen-all-recs-btn');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Generating...';
  }

  toast(`Generating ${recs.length} recommended app(s)... This will take a few minutes.`, 'info', 8000);

  let success = 0;
  let failed = 0;

  // Build context from scan results
  const context = (State.lanScanResult?.hosts || []).map(h =>
    `${h.ip} (${(h.services || []).map(s => `${s.port}/${s.protocol}`).join(', ')})`
  ).join('; ');

  // Generate each app sequentially (LLM can only handle one at a time)
  for (const rec of recs) {
    const name = rec.split('—')[0].replace(/Create a/i, '').replace(/create a/i, '').trim().slice(0, 40) || 'Ship App';
    try {
      await API.post('/api/apps/generate', {
        name: name,
        description: rec,
        context: context,
      });
      success++;
      toast(`Generated: ${name}`, 'success');
    } catch (e) {
      failed++;
      toast(`Failed: ${name} — ${e.message}`, 'error');
    }
  }

  if (btn) {
    btn.disabled = false;
    btn.innerHTML = 'Generate All Recommended';
  }

  toast(`Done! ${success} app(s) generated, ${failed} failed.`, success > 0 ? 'success' : 'error', 6000);

  // Refresh the apps list
  await loadAppList();
}

document.addEventListener('DOMContentLoaded', initApps);
