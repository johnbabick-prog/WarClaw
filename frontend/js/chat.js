/**
 * WarClaw — Chat Module
 * Session-based AI chat with persistent history.
 */

let chatWs = null;
let isStreaming = false;
let activeStream = null;

const HINTS = [
  'Summarize the operational picture from today.',
  'What systems are on this LAN?',
  'Recommend a daily report cadence for this ship.',
  'Create a navigation dashboard',
  'What should I monitor next watch?',
  'Explain NMEA 0183 sentences',
];

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function formatMessageContent(text) {
  text = escapeHtml(String(text || ''));
  text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => `<pre><code>${escapeHtml(code.trim())}</code></pre>`);
  text = text.replace(/\[([^\]]+)\]\((\/[^)\s]+)\)/g, (_, label, href) => `<a href="${href}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`);
  text = text.replace(/`([^`]+)`/g, (_, code) => `<code>${escapeHtml(code)}</code>`);
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  return text.replace(/\n/g, '<br>');
}

function appendMessage(role, content, streaming = false) {
  const container = document.getElementById('chat-messages');
  const id = `msg-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const isUser = role === 'user';
  const avatarChar = isUser ? '⬡' : '⚙';
  const roleLabel = isUser ? 'CREW' : 'WARCLAW AI';

  const el = document.createElement('div');
  el.className = `msg ${role}`;
  el.id = id;
  el.innerHTML = `
    <div class="msg-avatar">${avatarChar}</div>
    <div class="msg-body">
      <div class="msg-role">${roleLabel}</div>
      <div class="msg-content" id="${id}-content">
        ${streaming ? '<span class="typing-cursor"></span>' : formatMessageContent(content)}
      </div>
    </div>
  `;
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
  return id;
}

function renderChatMessages(messages) {
  const container = document.getElementById('chat-messages');
  container.innerHTML = '';

  if (!messages.length) {
    appendMessage(
      'assistant',
      'WarClaw AI standing by.\n\nCreate or select a session on the left. Your chats are now saved as reusable operator sessions.'
    );
    return;
  }

  messages.forEach(msg => appendMessage(msg.role, msg.content));
}

function updateStreamingMessage(msgId, fullText) {
  const el = document.getElementById(`${msgId}-content`);
  if (!el) return;
  el.innerHTML = formatMessageContent(fullText) + '<span class="typing-cursor"></span>';
  const container = document.getElementById('chat-messages');
  container.scrollTop = container.scrollHeight;
}

function finalizeStreamingMessage(msgId, fullText) {
  const el = document.getElementById(`${msgId}-content`);
  if (!el) return;
  el.innerHTML = formatMessageContent(fullText);
}

function connectChatWs() {
  if (chatWs && chatWs.readyState === WebSocket.OPEN) return chatWs;
  if (chatWs && chatWs.readyState === WebSocket.CONNECTING) return chatWs;

  chatWs = API.ws('/api/chat/ws');
  chatWs.onopen = () => {
    document.getElementById('ws-status').textContent = 'CONNECTED';
    document.getElementById('ws-status').style.color = 'var(--accent-green)';
  };
  chatWs.onclose = () => {
    document.getElementById('ws-status').textContent = 'DISCONNECTED';
    document.getElementById('ws-status').style.color = 'var(--accent-red)';
    chatWs = null;
  };
  chatWs.onerror = () => toast('Chat connection error', 'error');
  chatWs.onmessage = (event) => {
    if (!activeStream) return;
    const data = JSON.parse(event.data);
    if (data.token) {
      activeStream.responseText += data.token;
      updateStreamingMessage(activeStream.msgId, activeStream.responseText);
      return;
    }
    if (data.error) {
      finalizeStreamingMessage(activeStream.msgId, `[Error: ${data.error}]`);
      activeStream.reject(new Error(data.error));
      activeStream = null;
      isStreaming = false;
      document.getElementById('chat-send-btn').disabled = false;
      return;
    }
    if (data.done) {
      finalizeStreamingMessage(activeStream.msgId, activeStream.responseText);
      activeStream.resolve(activeStream.responseText);
      activeStream = null;
      isStreaming = false;
      document.getElementById('chat-send-btn').disabled = false;
    }
  };

  return chatWs;
}

function waitForSocketOpen(ws) {
  if (ws.readyState === WebSocket.OPEN) return Promise.resolve();
  if (ws.readyState !== WebSocket.CONNECTING) return Promise.reject(new Error('WS unavailable'));

  return new Promise((resolve, reject) => {
    const onOpen = () => {
      cleanup();
      resolve();
    };
    const onError = () => {
      cleanup();
      reject(new Error('WS error'));
    };
    const timeout = setTimeout(() => {
      cleanup();
      reject(new Error('WS timeout'));
    }, 5000);

    function cleanup() {
      clearTimeout(timeout);
      ws.removeEventListener('open', onOpen);
      ws.removeEventListener('error', onError);
    }

    ws.addEventListener('open', onOpen, { once: true });
    ws.addEventListener('error', onError, { once: true });
  });
}

async function refreshChatSessions() {
  const data = await API.get('/api/chat/sessions');
  State.chatSessions = data.sessions;

  if (!State.currentChatSessionId && data.sessions.length) {
    State.currentChatSessionId = data.sessions[0].id;
  }

  renderChatSessionList();
  if (State.currentChatSessionId) {
    await loadChatSession(State.currentChatSessionId);
  } else {
    renderChatMessages([]);
  }
}

function renderChatSessionList() {
  const list = document.getElementById('chat-session-list');
  list.innerHTML = '';

  if (!State.chatSessions.length) {
    list.innerHTML = '<div class="text-muted" style="font-size:12px;">No chat sessions yet.</div>';
    return;
  }

  State.chatSessions.forEach(session => {
    const row = document.createElement('button');
    row.className = `session-row ${session.id === State.currentChatSessionId ? 'active' : ''}`;
    row.innerHTML = `
      <div class="session-title">${escapeHtml(session.title)}</div>
      <div class="session-meta">${session.message_count} msgs</div>
    `;
    row.onclick = () => loadChatSession(session.id);
    list.appendChild(row);
  });
}

async function createChatSession(title = '') {
  const session = await API.post('/api/chat/sessions', { title });
  State.currentChatSessionId = session.id;
  await refreshChatSessions();
  showView('chat');
}

async function loadChatSession(sessionId) {
  const session = await API.get(`/api/chat/sessions/${sessionId}`);
  State.currentChatSessionId = session.id;
  renderChatSessionList();
  renderChatMessages(session.messages || []);
}

async function deleteCurrentChatSession() {
  if (!State.currentChatSessionId) return;
  await API.del(`/api/chat/sessions/${State.currentChatSessionId}`);
  State.currentChatSessionId = null;
  await refreshChatSessions();
}

async function sendChatMessage() {
  if (isStreaming) return;

  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;

  if (!State.currentChatSessionId) {
    await createChatSession(message);
  }

  input.value = '';
  input.style.height = 'auto';
  appendMessage('user', message);

  const ws = connectChatWs();
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    const connected = await waitForSocketOpen(ws).then(() => true).catch(e => {
      toast('Could not connect to AI: ' + e.message, 'error');
      return false;
    });
    if (!connected) return;
  }

  const msgId = appendMessage('assistant', '', true);
  isStreaming = true;
  document.getElementById('chat-send-btn').disabled = true;
  const progress = createProgressController('chat-progress-fill', 'chat-status', 'chat-eta', {
    message: 'Routing request through local actions and model context...',
    etaSeconds: 8,
    intervalMs: 600,
    step: 8,
  });

  const responseText = await new Promise((resolve, reject) => {
    activeStream = { msgId, responseText: '', resolve, reject };
    ws.send(JSON.stringify({
      session_id: State.currentChatSessionId,
      message,
      max_tokens: 2048,
      temperature: 0.7,
    }));
  }).catch(err => {
    toast(err.message, 'error');
    return null;
  });

  if (responseText !== null) {
    progress.complete('Assistant response ready.');
    await refreshChatSessions();
    await loadChatSession(State.currentChatSessionId);
  } else {
    progress.fail('Assistant request failed.');
  }
  progress.reset();
}

function initChat() {
  const hintContainer = document.querySelector('.chat-hints');
  HINTS.forEach(hint => {
    const chip = document.createElement('span');
    chip.className = 'hint-chip';
    chip.textContent = hint;
    chip.onclick = async () => {
      if (!State.currentChatSessionId) {
        await createChatSession(hint);
      }
      document.getElementById('chat-input').value = hint;
      sendChatMessage();
    };
    hintContainer.appendChild(chip);
  });

  document.getElementById('chat-send-btn').onclick = sendChatMessage;
  document.getElementById('chat-new-session-btn').onclick = () => createChatSession();
  document.getElementById('chat-delete-session-btn').onclick = deleteCurrentChatSession;

  const input = document.getElementById('chat-input');
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  });
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  });

  document.getElementById('chat-clear-btn').onclick = async () => {
    if (State.currentChatSessionId) {
      await API.del(`/api/chat/sessions/${State.currentChatSessionId}`);
      State.currentChatSessionId = null;
      await refreshChatSessions();
      toast('Session cleared', 'info');
    }
  };

  connectChatWs();
  refreshChatSessions().catch(() => renderChatMessages([]));
  document.getElementById('chat-status').textContent = 'Ready for operator input.';
}

document.addEventListener('DOMContentLoaded', initChat);
