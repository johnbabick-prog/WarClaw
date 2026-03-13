/**
 * WarClaw — Chat Module
 * WebSocket streaming AI chat.
 */

let chatWs = null;
let chatHistory = [];
let isStreaming = false;

const HINTS = [
  'What systems are on this LAN?',
  'Create a navigation dashboard',
  'Build a MODBUS register viewer',
  'Explain NMEA 0183 sentences',
  'Create a ship status overview app',
  'How do I integrate with AIS?',
];

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function formatMessageContent(text) {
  // Code blocks
  text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    return `<pre><code>${escapeHtml(code.trim())}</code></pre>`;
  });
  // Inline code
  text = text.replace(/`([^`]+)`/g, (_, code) => {
    return `<code>${escapeHtml(code)}</code>`;
  });
  // Bold
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  return text;
}

function appendMessage(role, content, streaming = false) {
  const container = document.getElementById('chat-messages');
  const id = `msg-${Date.now()}`;

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
        ${streaming ? '<span class="typing-cursor"></span>' : formatMessageContent(escapeHtml(content))}
      </div>
    </div>
  `;

  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
  return id;
}

function updateStreamingMessage(msgId, fullText) {
  const el = document.getElementById(`${msgId}-content`);
  if (!el) return;
  el.innerHTML = formatMessageContent(escapeHtml(fullText)) + '<span class="typing-cursor"></span>';
  const container = document.getElementById('chat-messages');
  container.scrollTop = container.scrollHeight;
}

function finalizeStreamingMessage(msgId, fullText) {
  const el = document.getElementById(`${msgId}-content`);
  if (!el) return;
  el.innerHTML = formatMessageContent(escapeHtml(fullText));
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

  chatWs.onerror = () => {
    toast('Chat connection error', 'error');
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

async function sendChatMessage() {
  if (isStreaming) return;

  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;

  if (!State.modelReady) {
    toast('No AI model loaded. Go to Hardware tab to load a model.', 'error');
    return;
  }

  input.value = '';
  input.style.height = 'auto';

  appendMessage('user', message);
  chatHistory.push({ role: 'user', content: message });

  const ws = connectChatWs();
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    const connected = await waitForSocketOpen(ws).then(() => true).catch(e => {
      toast('Could not connect to AI: ' + e.message, 'error');
      return false;
    });
    if (!connected) return;
  }

  const msgId = appendMessage('assistant', '', true);
  let responseText = '';

  isStreaming = true;
  document.getElementById('chat-send-btn').disabled = true;

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.token) {
      responseText += data.token;
      updateStreamingMessage(msgId, responseText);
    } else if (data.done) {
      finalizeStreamingMessage(msgId, responseText);
      chatHistory.push({ role: 'assistant', content: responseText });
      isStreaming = false;
      document.getElementById('chat-send-btn').disabled = false;
    } else if (data.error) {
      finalizeStreamingMessage(msgId, `[Error: ${data.error}]`);
      isStreaming = false;
      document.getElementById('chat-send-btn').disabled = false;
    }
  };

  ws.send(JSON.stringify({
    message,
    history: chatHistory.slice(-10),  // last 10 turns context
    max_tokens: 2048,
    temperature: 0.7,
  }));
}

function initChat() {
  // Hint chips
  const hintContainer = document.querySelector('.chat-hints');
  HINTS.forEach(hint => {
    const chip = document.createElement('span');
    chip.className = 'hint-chip';
    chip.textContent = hint;
    chip.onclick = () => {
      document.getElementById('chat-input').value = hint;
      sendChatMessage();
    };
    hintContainer.appendChild(chip);
  });

  // Send button
  document.getElementById('chat-send-btn').onclick = sendChatMessage;
  document.querySelectorAll('.prompt-mode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.getElementById('chat-input').value = btn.dataset.prompt || '';
      document.getElementById('chat-input').dispatchEvent(new Event('input'));
      document.getElementById('chat-input').focus();
    });
  });

  // Enter to send (Shift+Enter for newline)
  const input = document.getElementById('chat-input');
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  });

  // Auto-resize textarea
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  });

  // Clear chat
  document.getElementById('chat-clear-btn').onclick = () => {
    document.getElementById('chat-messages').innerHTML = '';
    chatHistory = [];
    toast('Chat cleared', 'info');
  };

  // Welcome message (not added to history)
  setTimeout(() => {
    appendMessage('assistant',
      'WarClaw AI standing by.\n\n' +
      'Use this console like a live local operations assistant:\n' +
      '• summarize systems discovered on the LAN\n' +
      '• recommend AI integrations and agents\n' +
      '• generate dashboards and tooling routes\n' +
      '• explain NMEA, MODBUS, IEC 61162, and app architecture decisions\n\n' +
      'If the core is offline, load a GGUF model under Hardware / Model first.'
    );
  }, 300);

  // Connect WS eagerly
  connectChatWs();
}

document.addEventListener('DOMContentLoaded', initChat);
