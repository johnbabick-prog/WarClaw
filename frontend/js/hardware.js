/**
 * WarClaw — Hardware & Model Management Module
 */

async function loadHardwareView() {
  const hwPanel = document.getElementById('hw-profile');
  const modelsPanel = document.getElementById('hw-models');

  hwPanel.innerHTML = '<div class="text-muted">Detecting hardware...</div>';
  modelsPanel.innerHTML = '<div class="text-muted">Scanning models directory...</div>';

  try {
    const [profile, modelsData] = await Promise.all([
      API.get('/api/hardware/profile'),
      API.get('/api/hardware/models'),
    ]);
    State.hwProfile = profile;

    const tierHtml = `<span class="tier-badge ${profile.recommended_tier}">${profile.recommended_tier.toUpperCase()} TIER</span>`;
    const gpuHtml = profile.gpu_name
      ? `${profile.gpu_name} (${profile.gpu_vram_gb}GB VRAM) <span class="text-green">✓ CUDA</span>`
      : '<span class="text-amber">None detected — CPU only</span>';

    hwPanel.innerHTML = `
      <div class="card-title">Server Hardware</div>
      <div class="hw-info-row">
        <div class="hw-info-label">CPU</div>
        <div class="hw-info-value" style="font-size:11px;">${profile.cpu_model}</div>
      </div>
      <div class="hw-info-row">
        <div class="hw-info-label">Cores</div>
        <div class="hw-info-value">${profile.cpu_cores}</div>
      </div>
      <div class="hw-info-row">
        <div class="hw-info-label">RAM</div>
        <div class="hw-info-value">${profile.ram_gb} GB</div>
      </div>
      <div class="hw-info-row">
        <div class="hw-info-label">GPU</div>
        <div class="hw-info-value">${gpuHtml}</div>
      </div>
      <div class="hw-info-row">
        <div class="hw-info-label">Rec. Tier</div>
        <div class="hw-info-value">${tierHtml}</div>
      </div>
      <div class="hw-info-row">
        <div class="hw-info-label">Model Hint</div>
        <div class="hw-info-value mono" style="font-size:11px;">${profile.recommended_model}</div>
      </div>
      <div style="margin-top:12px;">
        ${profile.notes.map(n => `<div class="text-muted" style="font-size:11px;margin-bottom:4px;">◈ ${n}</div>`).join('')}
      </div>
    `;

    // Models list
    const currentModel = modelsData.current_model;
    const isReady = modelsData.model_ready;

    let modelsHtml = `<div class="card-title">Loaded Model</div>`;
    if (isReady) {
      const provider = modelsData.current_provider || 'gguf';
      const name = provider === 'gguf' ? currentModel.split('/').pop() : currentModel;
      const persisted = modelsData.saved_model && modelsData.saved_model === currentModel && (modelsData.saved_provider || provider) === provider;
      modelsHtml += `
        <div class="model-file-item" style="border-color:var(--accent-green);">
          <span class="text-green">●</span>
          <span class="model-file-name text-green">${name}</span>
          <span class="model-size">ACTIVE · ${provider.toUpperCase()}${persisted ? ' · DEFAULT' : ''}</span>
        </div>
      `;
    } else {
      modelsHtml += `<div class="text-amber" style="font-size:11px;margin-bottom:12px;">No model loaded</div>`;
    }

    modelsHtml += `<div class="card-title" style="margin-top:16px;">Local GGUF Models</div>`;

    if (modelsData.models.length === 0) {
      modelsHtml += `
        <div class="text-muted" style="font-size:11px;line-height:1.7;">
          No .gguf files found yet.<br><br>
          Models folder:<br>
          <span class="mono" style="color:var(--text-primary);">${modelsData.models_dir}</span>
        </div>
      `;
    } else {
      modelsHtml += modelsData.models.map(m => `
        <div class="model-file-item">
          <span style="color:var(--text-muted);">◈</span>
          <span class="model-file-name" title="${m.path}">${m.name}</span>
          <span class="model-size">${m.size_mb}MB</span>
          <button class="btn btn-primary" style="font-size:10px;padding:3px 10px;flex-shrink:0;"
            onclick="loadModel('${m.path}', 'gguf', ${profile.recommended_gpu_layers})">
            LOAD
          </button>
        </div>
      `).join('');
    }

    modelsHtml += `<div class="card-title" style="margin-top:16px;">Ollama Models</div>`;
    if ((modelsData.ollama_models || []).length === 0) {
      modelsHtml += `
        <div class="text-muted" style="font-size:11px;line-height:1.7;">
          No local Ollama models detected on <span class="mono" style="color:var(--text-primary);">http://127.0.0.1:11434</span>.
        </div>
      `;
    } else {
      modelsHtml += modelsData.ollama_models.map(m => `
        <div class="model-file-item">
          <span style="color:var(--text-muted);">◈</span>
          <span class="model-file-name" title="${m.name}">${m.name}</span>
          <span class="model-size">${m.size_mb ? `${m.size_mb}MB` : 'OLLAMA'}</span>
          <button class="btn btn-primary" style="font-size:10px;padding:3px 10px;flex-shrink:0;"
            onclick="loadModel('${m.name}', 'ollama', 0)">
            USE
          </button>
        </div>
      `).join('');
    }

    modelsHtml += `
      <div style="margin-top:20px;padding-top:18px;border-top:1px solid rgba(0,0,0,0.08);">
        <div class="card-title">Add Model From File Explorer</div>
        <div class="text-muted" style="font-size:11px;line-height:1.7;margin-bottom:12px;">
          Choose a local GGUF file. WarClaw will copy it into the models folder and can load it immediately.
        </div>
        <div id="hardware-model-status" class="hardware-status">No file selected yet.</div>
        <input type="file" id="manual-model-file" accept=".gguf" style="display:none;" />
        <div id="manual-model-selection" class="text-muted" style="font-size:11px;margin-bottom:10px;">No file selected</div>
        <div class="form-group" style="margin-bottom:10px;">
          <label>GPU Layers (0 = CPU only)</label>
          <input type="number" id="manual-gpu-layers" value="${profile.recommended_gpu_layers}" min="0" max="200" />
        </div>
        <div style="display:flex;gap:8px;">
          <button class="btn" style="flex:1;" onclick="openModelPicker()">Choose GGUF File</button>
          <button class="btn btn-primary" style="flex:1;" onclick="uploadAndLoadManualModel()">Upload & Load</button>
        </div>
      </div>
    `;

    modelsPanel.innerHTML = modelsHtml;
    bindModelPicker();

  } catch (e) {
    hwPanel.innerHTML = `<div class="text-red">Hardware detection failed: ${e.message}</div>`;
    modelsPanel.innerHTML = `<div class="text-red">${e.message}</div>`;
  }
}

function setHardwareStatus(message, type = 'info') {
  const el = document.getElementById('hardware-model-status');
  if (!el) return;
  el.className = `hardware-status ${type}`;
  el.textContent = message;
}

async function loadModel(path, provider = 'gguf', gpuLayers = 0) {
  const displayName = provider === 'gguf' ? path.split('/').pop() : path;
  setHardwareStatus(`Loading ${displayName}...`, 'info');
  toast(`Loading model — this may take 30-60 seconds...`, 'info', 30000);
  try {
    const result = await API.post('/api/hardware/models/load', {
      model_path: path,
      provider,
      n_gpu_layers: gpuLayers || 0,
    });
    toast(`Model loaded: ${displayName}`, 'success');
    setHardwareStatus(`Loaded ${displayName} successfully.`, 'success');
    State.modelReady = true;
    await loadHardwareView();
    await pollStatus();
  } catch (e) {
    setHardwareStatus(`Load failed: ${e.message}`, 'error');
    toast('Model load failed: ' + e.message, 'error');
  }
}

function openModelPicker() {
  document.getElementById('manual-model-file')?.click();
}

function bindModelPicker() {
  const input = document.getElementById('manual-model-file');
  if (!input) return;
  input.addEventListener('change', () => {
    const file = input.files?.[0];
    const label = document.getElementById('manual-model-selection');
    if (label) {
      label.textContent = file ? `${file.name} selected` : 'No file selected';
    }
    if (file) setHardwareStatus(`Selected ${file.name}. Click "Upload & Load" to continue.`, 'info');
  });
}

async function uploadAndLoadManualModel() {
  const file = document.getElementById('manual-model-file').files?.[0];
  const gpuLayers = parseInt(document.getElementById('manual-gpu-layers').value) || 0;
  if (!file) {
    setHardwareStatus('Choose a GGUF file first.', 'error');
    toast('Choose a GGUF file first', 'error');
    return;
  }

  const formData = new FormData();
  formData.append('file', file);

  setHardwareStatus(`Uploading ${file.name}...`, 'info');
  toast('Uploading model file...', 'info', 30000);
  try {
    const response = await fetch(`${API.base}/api/hardware/models/upload`, {
      method: 'POST',
      body: formData,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Upload failed');
    toast(`Uploaded ${result.name}`, 'success');
    setHardwareStatus(`Upload complete. Verifying ${result.name} in models directory...`, 'success');

    const modelsData = await API.get('/api/hardware/models');
    const uploadedModel = (modelsData.models || []).find(m => m.path === result.path || m.name === result.name);
    if (!uploadedModel) {
      throw new Error('Upload finished, but the model was not found in the models directory afterward');
    }

    setHardwareStatus(`Found ${uploadedModel.name}. Starting model load...`, 'info');
    await loadModel(uploadedModel.path, 'gguf', gpuLayers);
  } catch (e) {
    setHardwareStatus(`Upload failed: ${e.message}`, 'error');
    toast('Model upload failed: ' + e.message, 'error');
  }
}

function initHardware() {
  document.getElementById('hw-refresh-btn').onclick = loadHardwareView;
}

document.addEventListener('DOMContentLoaded', initHardware);
