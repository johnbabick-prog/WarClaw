/**
 * WarClaw — App Factory Module
 * Generate, manage, and edit applications.
 */

const APP_ICONS = ['⚓', '⬡', '◈', '⚙', '◉', '⊕', '⊗', '⬢'];
function randomIcon() { return APP_ICONS[Math.floor(Math.random() * APP_ICONS.length)]; }
let editingAppSlug = '';
let selectedAppSlug = '';
let appStudioFocused = false;
let appLibraryDetailsSlug = '';

// ── Code Editor State ────────────────────────────────────────────
let _editorSlug = '';
let _editorFiles = [];
let _editorActiveFile = '';
let _editorDirty = false;
let _pendingDeleteAppSlug = '';

function escapeAppHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function inferAppBlueprint(text, fallbackName = 'Ship App') {
  const raw = String(text || '').trim();
  const lowered = raw.toLowerCase();
  if (lowered.includes('ship systems overview') || lowered.includes('host density')) {
    return {
      name: 'Ship Systems Overview',
      description: 'Consolidate discovered hosts, services, and integration priorities into one operational dashboard.',
    };
  }
  if (lowered.includes('navigation') || lowered.includes('nmea') || lowered.includes('bridge')) {
    return {
      name: 'Navigation Dashboard',
      description: 'Track vessel position, heading, speed, depth, and live bridge data from discovered navigation feeds.',
    };
  }
  if (lowered.includes('modbus') || lowered.includes('engineering') || lowered.includes('plant')) {
    return {
      name: 'Engineering Plant Monitor',
      description: 'Monitor engineering telemetry, register values, and alerts from MODBUS-enabled ship systems.',
    };
  }
  if (lowered.includes('report') || lowered.includes('opord') || lowered.includes('brief')) {
    return {
      name: 'Report Generator',
      description: 'Turn uploaded notes, logs, and watch records into structured operational summaries and briefs.',
    };
  }
  if (lowered.includes('lan') || lowered.includes('network') || lowered.includes('scan')) {
    return {
      name: 'LAN Investigator',
      description: 'Scan the local network, inspect hosts and services, and surface follow-on operator actions.',
    };
  }
  return { name: fallbackName, description: raw || fallbackName };
}

window.inferAppBlueprint = inferAppBlueprint;

function resetAppEditor() {
  editingAppSlug = '';
  document.getElementById('app-name-input').value = '';
  document.getElementById('app-desc-input').value = '';
  document.getElementById('app-context-input').value = '';
  document.getElementById('gen-app-btn').innerHTML = 'Create Starting Version';
  document.getElementById('app-edit-cancel-btn').style.display = 'none';
  document.getElementById('app-builder-drawer-title').textContent = 'Start With A Brief';
}

function openAppBuilderDrawer() {
  const drawer = document.getElementById('app-builder-drawer');
  if (drawer) drawer.classList.add('visible');
}
window.openAppBuilderDrawer = openAppBuilderDrawer;

function closeAppBuilderDrawer() {
  const drawer = document.getElementById('app-builder-drawer');
  if (drawer) drawer.classList.remove('visible');
}
window.closeAppBuilderDrawer = closeAppBuilderDrawer;

async function editApp(slug) {
  const app = await API.get(`/api/apps/${slug}`);
  selectApp(slug, app);
  editingAppSlug = slug;
  document.getElementById('app-builder-drawer-title').textContent = 'Edit App Brief';
  document.getElementById('app-name-input').value = app.name || '';
  document.getElementById('app-desc-input').value = app.description || '';
  document.getElementById('app-context-input').value = app.context || '';
  document.getElementById('gen-app-btn').innerHTML = 'Save App Changes';
  document.getElementById('app-edit-cancel-btn').style.display = '';
  openAppBuilderDrawer();
}
window.editApp = editApp;
window.loadAppList = loadAppList;

async function loadAppList() {
  const panel = document.getElementById('app-list-panel');
  try {
    const data = await API.get('/api/apps/');
    State.generatedApps = data.apps;
    document.getElementById('stat-apps').textContent = data.apps.length;
    if (typeof syncLiveUi === 'function') syncLiveUi();

    if (!data.apps.length) {
      selectedAppSlug = '';
      panel.innerHTML = `
        <div id="lan-empty" style="height:200px;">
          <div class="empty-icon">&#x2295;</div>
          <div>No apps generated yet. Use the brief panel to create a starting version.</div>
        </div>
      `;
      renderAppStudio(null);
      return;
    }

    panel.innerHTML = data.apps.map(app => `
      <div class="app-card ${app.slug === selectedAppSlug ? 'selected' : ''}" id="app-card-${app.slug}" onclick="selectApp('${app.slug}')">
        <div class="app-icon">${randomIcon()}</div>
        <div class="app-info">
          <div class="app-title-row">
            <div class="app-name">${app.name}</div>
            <button class="btn app-details-btn" onclick="event.stopPropagation();toggleAppLibraryDetails('${app.slug}')">${appLibraryDetailsSlug === app.slug ? 'Hide Details' : 'Details'}</button>
          </div>
        </div>
        <div class="app-actions">
          <button class="btn btn-primary" ${app.has_frontend ? '' : 'disabled'}
            onclick="event.stopPropagation();launchApp('${app.slug}', ${app.has_frontend ? 'true' : 'false'})">&#x25B6; Open</button>
          <button class="btn" onclick="event.stopPropagation();openCodeEditor('${app.slug}')">&#x2630; Code</button>
          <button class="btn" onclick="event.stopPropagation();editApp('${app.slug}')">&#x270E; Brief</button>
          <button class="btn btn-danger" onclick="event.stopPropagation();deleteApp('${app.slug}')">&#x2715;</button>
        </div>
        ${appLibraryDetailsSlug === app.slug ? `
          <div class="app-card-details">
            <div class="app-desc">${app.description || ''}</div>
            <div class="app-meta">
              ${app.slug} &nbsp;&#xB7;&nbsp;
              ${new Date(app.created_at * 1000).toLocaleDateString()}
              ${app.generation_time_s ? `&nbsp;&#xB7;&nbsp; generated in ${app.generation_time_s}s` : ''}
            </div>
            ${app.status !== 'success' ? `
              <div class="text-amber" style="font-size:11px;margin-top:6px;">
                Partial generation: ${(app.errors || []).join(', ')}
              </div>
            ` : ''}
            <div class="app-card-brief">
              <div class="app-card-brief-label">Current Brief</div>
              <div class="app-card-brief-copy">${escapeAppHtml(app.operator_summary || app.description || 'No summary available.')}</div>
            </div>
          </div>
        ` : ''}
      </div>
    `).join('');

    const selectedExists = data.apps.some(app => app.slug === selectedAppSlug);
    if (!selectedExists) {
      selectedAppSlug = data.apps[0].slug;
    }
    await selectApp(selectedAppSlug);
  } catch (e) {
    panel.innerHTML = `<div class="text-red">Failed to load apps: ${e.message}</div>`;
    renderAppStudio(null, e.message);
  }
}

async function selectApp(slug, providedApp = null) {
  if (!slug) {
    selectedAppSlug = '';
    renderAppStudio(null);
    return;
  }
  selectedAppSlug = slug;
  document.querySelectorAll('#app-list-panel .app-card').forEach(card => {
    card.classList.toggle('selected', card.id === `app-card-${slug}`);
  });
  try {
    const app = providedApp || await API.get(`/api/apps/${slug}`);
    renderAppStudio(app);
  } catch (e) {
    renderAppStudio(null, e.message);
  }
}
window.selectApp = selectApp;

function toggleAppLibraryDetails(slug) {
  appLibraryDetailsSlug = appLibraryDetailsSlug === slug ? '' : slug;
  loadAppList();
}
window.toggleAppLibraryDetails = toggleAppLibraryDetails;

function renderAppStudio(app, error = '') {
  const subtitle = document.getElementById('app-studio-subtitle');
  const openBtn = document.getElementById('app-studio-open-btn');
  const codeBtn = document.getElementById('app-studio-code-btn');
  const thread = document.getElementById('app-iteration-thread');

  if (!app) {
    if (subtitle) subtitle.textContent = error ? `Could not load selected app: ${error}` : 'Select an app to review and refine it with the AI.';
    if (openBtn) openBtn.disabled = true;
    if (codeBtn) codeBtn.disabled = true;
    if (thread) {
      thread.innerHTML = `
        <div class="app-iteration-empty">
          <div class="empty-icon">◈</div>
          <div>Iteration history will appear here after you refine an app.</div>
        </div>
      `;
    }
    return;
  }

  if (subtitle) subtitle.textContent = app.app_kind ? `${app.app_kind.replace(/_/g, ' ')} · ${app.slug}` : app.slug;
  if (openBtn) openBtn.disabled = !app.has_frontend;
  if (codeBtn) codeBtn.disabled = false;
  renderIterationThread(app.iteration_history || []);
  syncAppStudioLayout();
}

function renderIterationThread(history) {
  const thread = document.getElementById('app-iteration-thread');
  if (!thread) return;
  if (!history.length) {
    thread.innerHTML = `
      <div class="app-iteration-empty">
        <div class="empty-icon">⬡</div>
        <div>No refinements yet. Ask for a different layout, workflow, or visual direction.</div>
      </div>
    `;
    return;
  }
  thread.innerHTML = history.map(item => `
    <div class="iteration-msg ${item.role === 'user' ? 'user' : 'assistant'}">
      <div class="iteration-role">${item.role === 'user' ? 'You' : 'WarClaw AI'}</div>
      <div class="iteration-content">${escapeAppHtml(item.content || '')}</div>
    </div>
  `).join('');
  thread.scrollTop = thread.scrollHeight;
}

function syncAppStudioLayout() {
  const layout = document.getElementById('app-factory-layout');
  const studio = document.getElementById('app-studio-panel');
  const focusBtn = document.getElementById('app-studio-focus-btn');
  if (layout) layout.classList.toggle('chat-focus', appStudioFocused);
  if (focusBtn) focusBtn.textContent = appStudioFocused ? 'Exit Focus' : 'Focus Chat';
}

function toggleAppStudioFocus() {
  appStudioFocused = !appStudioFocused;
  syncAppStudioLayout();
}
window.toggleAppStudioFocus = toggleAppStudioFocus;

async function generateApp() {
  const name = document.getElementById('app-name-input').value.trim();
  const desc = document.getElementById('app-desc-input').value.trim();
  const context = document.getElementById('app-context-input').value.trim();
  const btn = document.getElementById('gen-app-btn');
  const wasEditing = !!editingAppSlug;

  if (!name || !desc) {
    toast('App name and description are required', 'error');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = wasEditing ? '<span class="spinner"></span> SAVING...' : '<span class="spinner"></span> GENERATING...';
  const progress = createProgressController('gen-progress-fill', 'gen-status', 'gen-eta', {
    message: wasEditing
      ? 'Rebuilding app metadata and workspace...'
      : (State.modelReady ? 'AI is assembling the app plan and UI...' : 'Generating app from local templates and heuristics...'),
    etaSeconds: wasEditing ? 18 : (State.modelReady ? 75 : 20),
    intervalMs: 850,
    step: wasEditing ? 4 : 2.5,
  });

  try {
    const path = editingAppSlug ? `/api/apps/${editingAppSlug}/update` : '/api/apps/generate';
    const result = await API.post(path, { name, description: desc, context });

    if (result.status === 'success') {
      const verb = wasEditing ? 'updated' : 'generated';
      progress.complete(`App "${result.name}" ${verb} in ${result.generation_time_s}s`);
      toast(`App "${result.name}" ${verb}!`, 'success');
    } else {
      progress.set(`Partial generation: ${result.errors.join(', ')}`, 100);
      toast('App generated with warnings', 'info');
    }

    await loadAppList();
    await selectApp(result.slug);
    resetAppEditor();
    closeAppBuilderDrawer();

  } catch (e) {
    progress.fail('Generation failed: ' + e.message);
    toast('App generation failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = editingAppSlug ? 'Save App Changes' : 'Create Starting Version';
    progress.reset();
  }
}

function launchApp(slug, hasFrontend = true) {
  if (!hasFrontend) {
    toast('This app was only partially generated and has no frontend to open yet.', 'error');
    return;
  }
  const app = State.generatedApps.find(item => item.slug === slug);
  const version = app?.updated_at || app?.created_at || Date.now() / 1000;
  const url = `/api/apps/${slug}/ui?v=${encodeURIComponent(version)}`;
  window.open(url, '_blank', 'noopener');
}
window.launchApp = launchApp;

async function deleteApp(slug) {
  _pendingDeleteAppSlug = slug;
  const modal = document.getElementById('app-delete-modal');
  const target = document.getElementById('app-delete-modal-target');
  const confirmBtn = document.getElementById('app-delete-confirm-btn');
  if (target) target.textContent = slug;
  if (confirmBtn) {
    confirmBtn.disabled = false;
    confirmBtn.textContent = 'Delete App';
  }
  if (modal) modal.classList.add('visible');
}
window.deleteApp = deleteApp;

function closeDeleteAppModal() {
  _pendingDeleteAppSlug = '';
  const modal = document.getElementById('app-delete-modal');
  const target = document.getElementById('app-delete-modal-target');
  const confirmBtn = document.getElementById('app-delete-confirm-btn');
  if (target) target.textContent = '';
  if (confirmBtn) {
    confirmBtn.disabled = false;
    confirmBtn.textContent = 'Delete App';
  }
  if (modal) modal.classList.remove('visible');
}
window.closeDeleteAppModal = closeDeleteAppModal;

async function confirmDeleteApp() {
  const slug = _pendingDeleteAppSlug;
  if (!slug) return;
  const confirmBtn = document.getElementById('app-delete-confirm-btn');
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = 'Deleting...';
  }
  try {
    await API.del(`/api/apps/${slug}`);
    toast(`App ${slug} deleted`, 'info');
    if (selectedAppSlug === slug) selectedAppSlug = '';
    closeDeleteAppModal();
    await loadAppList();
  } catch (e) {
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = 'Delete App';
    }
    toast('Delete failed: ' + e.message, 'error');
  }
}
window.confirmDeleteApp = confirmDeleteApp;

function launchSelectedApp() {
  const app = State.generatedApps.find(item => item.slug === selectedAppSlug);
  if (app) launchApp(app.slug, app.has_frontend !== false);
}
window.launchSelectedApp = launchSelectedApp;

function openSelectedAppCode() {
  if (selectedAppSlug) openCodeEditor(selectedAppSlug);
}
window.openSelectedAppCode = openSelectedAppCode;

function loadSelectedIntoBrief() {
  if (!selectedAppSlug) return;
  editApp(selectedAppSlug);
}
window.loadSelectedIntoBrief = loadSelectedIntoBrief;

async function iterateSelectedApp() {
  if (!selectedAppSlug) {
    toast('Select an app first', 'error');
    return;
  }
  const input = document.getElementById('app-iteration-input');
  const instruction = input.value.trim();
  if (!instruction) {
    toast('Describe what you want to change', 'error');
    return;
  }
  const btn = document.getElementById('app-iterate-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> REFINING...';
  renderIterationThread([...(State.generatedApps.find(app => app.slug === selectedAppSlug)?.iteration_history || []), { role: 'user', content: instruction }]);

  try {
    const result = await API.post(`/api/apps/${selectedAppSlug}/iterate`, { instruction });
    input.value = '';
    toast(`App "${result.name}" refined: ${result.reply || 'new build ready'}`, 'success', 5000);
    await loadAppList();
    await selectApp(result.slug, result);
    if (result.has_frontend) {
      const refreshPreview = confirm('Refinement completed. Open the refreshed app build now?');
      if (refreshPreview) {
        const version = result.updated_at || Date.now() / 1000;
        window.open(`/api/apps/${result.slug}/ui?v=${encodeURIComponent(version)}`, '_blank', 'noopener');
      }
    }
  } catch (e) {
    toast('Refinement failed: ' + e.message, 'error');
    await selectApp(selectedAppSlug);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Refine Selected App';
  }
}

// ── Code Editor ─────────────────────────────────────────────────
async function openCodeEditor(slug) {
  _editorSlug = slug;
  _editorDirty = false;

  const modal = document.getElementById('code-editor-modal');
  if (!modal) return;

  document.getElementById('code-editor-title').textContent = `Code Editor: ${slug}`;

  try {
    const data = await API.get(`/api/apps/${slug}/source`);
    _editorFiles = data.files;

    _renderEditorTabs();
    if (_editorFiles.length > 0) {
      await _loadEditorFile(_editorFiles[0].name);
    }

    modal.classList.add('visible');
  } catch (e) {
    toast('Failed to open code editor: ' + e.message, 'error');
  }
}
window.openCodeEditor = openCodeEditor;

function closeCodeEditor() {
  if (_editorDirty) {
    if (!confirm('You have unsaved changes. Close anyway?')) return;
  }
  const modal = document.getElementById('code-editor-modal');
  if (modal) modal.classList.remove('visible');
  _editorSlug = '';
  _editorDirty = false;
}

function _renderEditorTabs() {
  const tabs = document.getElementById('code-editor-tabs');
  tabs.innerHTML = _editorFiles.map(f => {
    const active = f.name === _editorActiveFile;
    const icon = _fileIcon(f.name);
    return `<button class="code-tab ${active ? 'active' : ''}" onclick="_loadEditorFile('${f.name}')">${icon} ${f.name}</button>`;
  }).join('');
}

function _fileIcon(name) {
  if (name.endsWith('.html')) return '&#x2630;';
  if (name.endsWith('.py')) return '&#x2699;';
  if (name.endsWith('.json')) return '&#x25A6;';
  return '&#x25A1;';
}

async function _loadEditorFile(filename) {
  if (_editorDirty) {
    if (!confirm('Discard unsaved changes to current file?')) return;
  }

  _editorActiveFile = filename;
  _editorDirty = false;
  _renderEditorTabs();

  const textarea = document.getElementById('code-editor-textarea');
  const lineNums = document.getElementById('code-editor-lines');
  const statusEl = document.getElementById('code-editor-file-status');

  textarea.value = 'Loading...';
  textarea.disabled = true;

  try {
    const data = await API.get(`/api/apps/${_editorSlug}/source/${filename}`);
    textarea.value = data.content;
    textarea.disabled = false;
    _updateLineNumbers();
    statusEl.textContent = `${filename} - ${_formatBytes(data.content.length)}`;
  } catch (e) {
    textarea.value = `Error loading file: ${e.message}`;
    statusEl.textContent = `Error loading ${filename}`;
  }
}

function _updateLineNumbers() {
  const textarea = document.getElementById('code-editor-textarea');
  const lineNums = document.getElementById('code-editor-lines');
  if (!textarea || !lineNums) return;

  const lines = textarea.value.split('\n').length;
  lineNums.textContent = Array.from({ length: lines }, (_, i) => i + 1).join('\n');
}

function _onEditorInput() {
  _editorDirty = true;
  _updateLineNumbers();
  const saveBtn = document.getElementById('code-editor-save-btn');
  if (saveBtn) saveBtn.classList.add('has-changes');
}

async function saveEditorFile() {
  const textarea = document.getElementById('code-editor-textarea');
  const content = textarea.value;
  const saveBtn = document.getElementById('code-editor-save-btn');

  saveBtn.disabled = true;
  saveBtn.textContent = 'Saving...';

  try {
    // Use fetch directly for PUT
    const r = await fetch(`${API.base}/api/apps/${_editorSlug}/source/${_editorActiveFile}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }

    _editorDirty = false;
    saveBtn.classList.remove('has-changes');
    toast(`${_editorActiveFile} saved`, 'success');

    // Update file status
    const statusEl = document.getElementById('code-editor-file-status');
    statusEl.textContent = `${_editorActiveFile} - ${_formatBytes(content.length)} - saved`;
  } catch (e) {
    toast('Save failed: ' + e.message, 'error');
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = 'Save';
  }
}

function previewEditorApp() {
  if (_editorSlug) {
    const url = `/api/apps/${_editorSlug}/ui`;
    window.open(url, `preview-${_editorSlug}`, 'noopener');
  }
}

function _formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Handle keyboard shortcuts in editor
function _onEditorKeydown(e) {
  // Ctrl/Cmd+S to save
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    saveEditorFile();
    return;
  }

  // Tab key inserts spaces
  if (e.key === 'Tab') {
    e.preventDefault();
    const ta = e.target;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    ta.value = ta.value.substring(0, start) + '  ' + ta.value.substring(end);
    ta.selectionStart = ta.selectionEnd = start + 2;
    _onEditorInput();
  }
}

// Template quick-fills
const APP_TEMPLATES = [
  {
    label: 'Nav Dashboard',
    name: 'Navigation Dashboard',
    desc: 'Real-time navigation display showing vessel position, heading, speed, and depth from NMEA sensors.',
    context: 'NMEA 0183 data source on LAN.',
  },
  {
    label: 'Plant Monitor',
    name: 'Engineering Plant Monitor',
    desc: 'Monitor ship engineering plant parameters from MODBUS sensors including temperatures, pressures, and RPMs.',
    context: 'MODBUS TCP device on LAN port 502.',
  },
  {
    label: 'LAN Overview',
    name: 'Ship Systems Overview',
    desc: 'Master dashboard displaying all detected ship systems, their status, and live data feeds.',
    context: 'Multiple systems detected on ship LAN.',
  },
  {
    label: 'AIS Tracker',
    name: 'AIS Traffic Display',
    desc: 'Parse and display AIS vessel tracking data from the ship LAN AIS receiver.',
    context: 'AIS receiver broadcasting NMEA VDM sentences.',
  },
];

function initApps() {
  document.getElementById('gen-app-btn').onclick = generateApp;
  document.getElementById('app-iterate-btn').onclick = iterateSelectedApp;
  document.getElementById('app-use-selected-btn').onclick = loadSelectedIntoBrief;

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

  document.getElementById('app-edit-cancel-btn').onclick = resetAppEditor;
  document.getElementById('app-iteration-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      iterateSelectedApp();
    }
  });

  // Code editor events
  const editorTextarea = document.getElementById('code-editor-textarea');
  if (editorTextarea) {
    editorTextarea.addEventListener('input', _onEditorInput);
    editorTextarea.addEventListener('keydown', _onEditorKeydown);
    editorTextarea.addEventListener('scroll', () => {
      const lineNums = document.getElementById('code-editor-lines');
      if (lineNums) lineNums.scrollTop = editorTextarea.scrollTop;
    });
  }

  loadAppList();
}

/**
 * Generate all recommended apps from LAN scan results with one click.
 */
async function generateAllRecommended() {
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
    const blueprint = inferAppBlueprint(rec, 'Ship App');
    const name = blueprint.name;
    try {
      await API.post('/api/apps/generate', {
        name: name,
        description: blueprint.description,
        context: context,
      });
      success++;
      toast(`Generated: ${name}`, 'success');
    } catch (e) {
      failed++;
      toast(`Failed: ${name} -- ${e.message}`, 'error');
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
