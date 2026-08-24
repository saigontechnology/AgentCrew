// DOM rendering helpers for the chat UI. All user/assistant text is rendered
// via textContent (never innerHTML) to avoid injecting untrusted content.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    if (key === 'class') {
      node.className = value;
    } else if (key === 'dataset') {
      Object.assign(node.dataset, value);
    } else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2), value);
    } else if (key === 'text') {
      node.textContent = value;
    } else {
      node.setAttribute(key, value);
    }
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function autoTitle(text) {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return 'New conversation';
  return clean.length > 40 ? `${clean.slice(0, 40)}…` : clean;
}

// Build the DOM for a single message.
export function buildMessage(message) {
  const isUser = message.role === 'user';
  const article = el('article', { class: `message role-${message.role}` });
  const role = el('div', { class: 'message-role', text: isUser ? 'You' : 'AI' });
  const body = el('div', { class: 'message-body' });
  const thinkingText = message.thinkingText || '';
  const thinkingNode = el(
    'div',
    { class: `message-thinking${thinkingText ? '' : ' hidden'}` },
    el('span', { class: 'message-thinking-content', text: thinkingText })
  );
  const thinkingContent = thinkingNode.firstChild;
  body.append(thinkingNode);
  const textNode = el('div', { class: 'message-text', text: message.text || '' });
  if (message.error) textNode.classList.add('error');
  if (message.streaming) textNode.classList.add('streaming');
  body.append(textNode);

  const meta = el('div', { class: 'message-meta' });

  if (Array.isArray(message.attachments) && message.attachments.length) {
    for (const att of message.attachments) {
      meta.append(
        el('span', {
          class: 'attachment-chip',
          text: `📎 ${att.name || 'file'} (${formatFileSize(att.size || 0)})`,
          title: `${att.name || 'file'} — ${att.type || 'unknown type'}`,
        })
      );
    }
  }

  if (message.status) {
    meta.append(el('span', { class: `status-pill ${message.status}`, text: message.status }));
  }
  if (message.agentName) {
    meta.append(el('span', { class: 'status-pill', text: `agent: ${message.agentName}` }));
  }
  if (message.createdAt) {
    meta.append(el('span', { text: formatTime(message.createdAt) }));
  }
  if (message.error) {
    meta.append(el('span', { class: 'status-pill failed', text: 'error' }));
  }
  if (message.retry) {
    meta.append(message.retry);
  }

  body.append(meta);
  article.append(role, body);
  return { article, textNode, thinkingNode, thinkingContent };
}

// Render the full transcript.
export function renderTranscript(container, messages) {
  container.replaceChildren();
  if (!messages.length) {
    container.append(
      el('div', {
        class: 'empty-state',
        children: [
          el('h1', { text: 'AgentCrew A2A Chat' }),
          el('p', { text: 'Select an agent and start a conversation. Files up to 10 MB each, up to 5 per message.' }),
        ],
      })
    );
    return;
  }
  for (const message of messages) {
    const { article } = buildMessage(message);
    container.append(article);
  }
  container.scrollTop = container.scrollHeight;
}

// Render the conversation list in the sidebar.
export function renderConversationList(container, conversations, activeId, handlers) {
  container.replaceChildren();
  const sorted = [...conversations].sort(
    (a, b) => new Date(b.updatedAt || 0) - new Date(a.updatedAt || 0)
  );
  for (const conv of sorted) {
    const item = el('div', {
      class: `conversation-item${conv.id === activeId ? ' active' : ''}`,
      dataset: { id: conv.id },
      onclick: () => handlers.open(conv.id),
    });
    item.append(el('span', { class: 'conversation-title', text: conv.title || 'New conversation' }));
    const actions = el('div', { class: 'conversation-actions' });
    actions.append(
      el('button', {
        type: 'button',
        text: '✎',
        title: 'Rename',
        'aria-label': `Rename ${conv.title || 'conversation'}`,
        onclick: (event) => {
          event.stopPropagation();
          handlers.rename(conv.id);
        },
      }),
      el('button', {
        type: 'button',
        text: '🗑',
        title: 'Delete',
        'aria-label': `Delete ${conv.title || 'conversation'}`,
        onclick: (event) => {
          event.stopPropagation();
          handlers.delete(conv.id);
        },
      })
    );
    item.append(actions);
    container.append(item);
  }
}

// Populate the agent selector.
export function setAgentOptions(select, agents, selectedName, lockedName) {
  select.replaceChildren();
  for (const agent of agents) {
    const option = el('option', { value: agent.name, text: agent.name });
    if (agent.description) option.title = agent.description;
    select.append(option);
  }
  if (lockedName) {
    select.value = lockedName;
    select.disabled = true;
  } else {
    select.disabled = false;
    if (selectedName && agents.some((a) => a.name === selectedName)) {
      select.value = selectedName;
    } else if (agents.length) {
      select.value = agents[0].name;
    }
  }
}

// Modal helpers ---------------------------------------------------------------

export function showModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.classList.remove('hidden');
}

export function hideModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.classList.add('hidden');
}

export function showAuthError(message) {
  const node = document.getElementById('auth-error');
  if (node) {
    node.textContent = message;
    node.classList.remove('hidden');
  }
}

export function clearAuthError() {
  const node = document.getElementById('auth-error');
  if (node) {
    node.textContent = '';
    node.classList.add('hidden');
  }
}

export function setServerStatus(ok, text) {
  const node = document.getElementById('server-status');
  if (node) {
    node.textContent = text;
    node.classList.toggle('ok', ok);
    node.classList.toggle('error', !ok);
  }
}

export function setConnectionStatus(ok, text) {
  const node = document.getElementById('connection-status');
  if (node) {
    node.textContent = text;
    node.classList.toggle('ok', ok);
    node.classList.toggle('error', !ok);
  }
}

export function showBanner(text, actions = []) {
  const banner = document.getElementById('input-required-banner');
  if (!banner) return;
  banner.replaceChildren();
  banner.append(el('span', { text }));
  if (actions.length) {
    const wrap = el('div', { class: 'banner-actions' });
    for (const action of actions) {
      wrap.append(
        el('button', { type: 'button', text: action.text, onclick: action.onclick })
      );
    }
    banner.append(wrap);
  }
  banner.classList.remove('hidden');
}

export function hideBanner() {
  const banner = document.getElementById('input-required-banner');
  if (banner) banner.classList.add('hidden');
}

export function showAgentLock(text) {
  const node = document.getElementById('agent-lock');
  if (node) {
    node.textContent = text;
    node.classList.remove('hidden');
  }
}

export function hideAgentLock() {
  const node = document.getElementById('agent-lock');
  if (node) node.classList.add('hidden');
}
