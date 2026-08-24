// AgentCrew A2A embedded chat UI — main application logic.
//
// Responsibilities:
//   - Session-only API key handling (Jupyter-style, only when required)
//   - Conversation management (create/open/rename/delete) persisted in IndexedDB
//   - Agent selection per turn, with input-required continuation locking
//   - Inline file upload (max 5 files, 10 MB each) as A2A raw parts
//   - Incremental streaming render with append/replace semantics
//   - Active-task recovery after refresh (GetTask then resubscribe)

import {
  A2AClient,
  AuthError,
  TERMINAL_STATES,
  RESUBSCRIBABLE_STATES,
  normalizeStatus,
  isTerminal,
} from './a2a.js';
import * as db from './db.js';
import * as ui from './ui.js';

const MAX_FILES = 5;
const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB
const KEY_STORAGE = 'a2a_api_key'; // sessionStorage only — never persisted

const state = {
  client: new A2AClient(),
  config: { authRequired: false },
  agents: [],
  conversations: [],
  currentConversationId: null,
  currentMessages: [],
  selectedAgent: null,
  pendingFiles: [], // { name, type, size, bytes(base64) } — bytes kept in memory only
  sending: false,
  activeController: null,
  inputRequiredTask: null, // { taskId, agentName } while awaiting input
  recoveryInProgress: false,
  // Per-user-message attachment bytes retained for the session so retry can
  // resend them; never written to IndexedDB.
  sessionAttachmentBytes: new Map(),
};

// In-memory DOM node cache for the live transcript so streaming updates touch
// only the affected message instead of re-rendering the whole list.
const liveNodes = new Map();

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function genId() {
  if (crypto && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function chunkedBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = '';
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(chunkedBase64(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

// Debounced IndexedDB write for streaming text updates.
const persistTimers = new Map();
function persistSoon(message) {
  clearTimeout(persistTimers.get(message.id));
  persistTimers.set(
    message.id,
    setTimeout(() => {
      persistTimers.delete(message.id);
      db.putMessage({ ...message }).catch(() => {});
    }, 400)
  );
}
function persistNow(message) {
  clearTimeout(persistTimers.get(message.id));
  persistTimers.delete(message.id);
  return db.putMessage({ ...message });
}

function findAssistantFor(userMessage) {
  if (userMessage.assistantMessageId) {
    return state.currentMessages.find(
      (m) => m.id === userMessage.assistantMessageId
    );
  }
  const idx = state.currentMessages.indexOf(userMessage);
  for (let i = idx + 1; i < state.currentMessages.length; i += 1) {
    if (state.currentMessages[i].role === 'assistant') {
      return state.currentMessages[i];
    }
  }
  return null;
}

function currentConversation() {
  return state.conversations.find(
    (c) => c.id === state.currentConversationId
  );
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderAll() {
  ui.renderConversationList(
    document.getElementById('conversation-list'),
    state.conversations,
    state.currentConversationId,
    {
      open: openConversation,
      rename: renameConversation,
      delete: requestDeleteConversation,
    }
  );
  renderTranscript();
  renderAgentSelector();
  renderPendingFiles();
  updateComposerState();
}

function renderTranscript() {
  const container = document.getElementById('transcript');
  liveNodes.clear();
  if (!state.currentMessages.length) {
    ui.renderTranscript(container, []);
    return;
  }
  container.replaceChildren();
  for (const message of state.currentMessages) {
    let retry = null;
    if (message.role === 'assistant' && message.error && message.retryable) {
      retry = ui.el('button', {
        type: 'button',
        class: 'retry-btn',
        text: 'Retry',
        onclick: () => retryMessage(message),
      });
    }
    const { article, textNode, thinkingNode, thinkingContent } = ui.buildMessage({ ...message, retry });
    liveNodes.set(message.id, { article, textNode, thinkingNode, thinkingContent });
    container.append(article);
  }
  container.scrollTop = container.scrollHeight;
}

function renderAgentSelector() {
  const select = document.getElementById('agent-select');
  const locked = state.inputRequiredTask
    ? state.inputRequiredTask.agentName
    : null;
  ui.setAgentOptions(
    select,
    state.agents,
    state.selectedAgent,
    locked
  );
  if (locked) {
    ui.showAgentLock(`Continuing with ${locked}`);
  } else {
    ui.hideAgentLock();
  }
}

function updateComposerState() {
  const sendBtn = document.getElementById('send-btn');
  const input = document.getElementById('message-input');
  const hasContent = input.value.trim().length > 0 || state.pendingFiles.length > 0;
  sendBtn.disabled = state.sending || !hasContent;
  input.disabled = state.sending;
  document.getElementById('attach-btn').disabled = state.sending;
  if (state.inputRequiredTask) {
    ui.showBanner(
      'The agent is waiting for your input. Reply to continue this task.',
      [
        {
          text: 'Start a new message instead',
          onclick: () => {
            state.inputRequiredTask = null;
            renderAgentSelector();
            ui.hideBanner();
            updateComposerState();
          },
        },
      ]
    );
  } else {
    ui.hideBanner();
  }
}

function renderPendingFiles() {
  const container = document.getElementById('file-preview');
  container.replaceChildren();
  if (!state.pendingFiles.length) {
    container.classList.add('hidden');
    return;
  }
  container.classList.remove('hidden');
  for (let i = 0; i < state.pendingFiles.length; i += 1) {
    const file = state.pendingFiles[i];
    const chip = ui.el('span', { class: 'file-chip' });
    chip.append(
      ui.el('span', { class: 'file-name', text: file.name, title: file.name }),
      ui.el('span', { class: 'file-size', text: ui.formatFileSize(file.size) })
    );
    chip.append(
      ui.el('button', {
        type: 'button',
        text: '×',
        'aria-label': `Remove ${file.name}`,
        onclick: () => {
          state.pendingFiles.splice(i, 1);
          renderPendingFiles();
          updateComposerState();
        },
      })
    );
    container.append(chip);
  }
}

function setHint(text, isError = false) {
  const node = document.getElementById('composer-hint');
  node.textContent = text || '';
  node.style.color = isError ? 'var(--danger)' : '';
}

// ---------------------------------------------------------------------------
// Initialization & authentication
// ---------------------------------------------------------------------------

async function init() {
  ui.setServerStatus(true, 'Connecting…');
  try {
    state.config = await state.client.fetchConfig();
  } catch (error) {
    ui.setServerStatus(false, 'Server unreachable');
    ui.setConnectionStatus(false, 'offline');
    return;
  }

  try {
    state.agents = await state.client.listAgents();
  } catch (error) {
    ui.setConnectionStatus(false, 'agents unavailable');
  }

  state.conversations = await db.getAllConversations();
  if (state.conversations.length) {
    state.conversations.sort(
      (a, b) => new Date(b.updatedAt || 0) - new Date(a.updatedAt || 0)
    );
  }

  const key = sessionStorage.getItem(KEY_STORAGE);
  if (key) state.client.setApiKey(key);

  if (state.config.authRequired) {
    if (key && (await validateStoredKey())) {
      ui.setConnectionStatus(true, 'connected');
    } else {
      sessionStorage.removeItem(KEY_STORAGE);
      state.client.setApiKey(null);
      showAuthModal();
      return;
    }
  } else {
    ui.setConnectionStatus(true, 'connected');
  }

  if (state.conversations.length) {
    await openConversation(state.conversations[0].id, { skipRecovery: false });
  } else {
    await createConversation();
  }
  renderAll();
  ui.setServerStatus(true, 'Connected');
}

async function validateStoredKey() {
  if (!state.agents.length) return false;
  try {
    return await state.client.validateKey(state.agents[0].name);
  } catch {
    return false;
  }
}

function showAuthModal() {
  ui.clearAuthError();
  ui.showModal('auth-modal');
  document.getElementById('api-key-input').focus();
}

function hideAuthModal() {
  ui.hideModal('auth-modal');
}

async function submitAuthKey(event) {
  event.preventDefault();
  const input = document.getElementById('api-key-input');
  const key = input.value.trim();
  if (!key) return;
  const submitBtn = document.getElementById('auth-submit');
  submitBtn.disabled = true;
  state.client.setApiKey(key);
  try {
    if (state.agents.length && !(await state.client.validateKey(state.agents[0].name))) {
      throw new AuthError('Invalid API key');
    }
    sessionStorage.setItem(KEY_STORAGE, key);
    hideAuthModal();
    ui.setConnectionStatus(true, 'connected');
    state.config.authRequired = true;
    if (!state.currentConversationId) {
      if (state.conversations.length) {
        await openConversation(state.conversations[0].id, { skipRecovery: true });
      } else {
        await createConversation();
      }
    } else {
      renderAll();
    }
  } catch (error) {
    state.client.setApiKey(null);
    ui.showAuthError(error instanceof AuthError ? 'Invalid API key' : 'Could not reach the server');
  } finally {
    submitBtn.disabled = false;
  }
}

// Reopen the key modal on any 401 from the agent RPC.
async function handleAuthError() {
  sessionStorage.removeItem(KEY_STORAGE);
  state.client.setApiKey(null);
  state.sending = false;
  updateComposerState();
  showAuthModal();
}

// ---------------------------------------------------------------------------
// Conversation management
// ---------------------------------------------------------------------------

async function createConversation() {
  const now = new Date().toISOString();
  const conv = {
    id: genId(),
    title: 'New conversation',
    selectedAgent: state.selectedAgent || (state.agents[0] && state.agents[0].name) || null,
    createdAt: now,
    updatedAt: now,
  };
  await db.putConversation(conv);
  state.conversations.unshift(conv);
  state.currentConversationId = conv.id;
  state.currentMessages = [];
  state.pendingFiles = [];
  state.inputRequiredTask = null;
  if (!state.selectedAgent && conv.selectedAgent) {
    state.selectedAgent = conv.selectedAgent;
  }
  renderAll();
  document.getElementById('message-input').focus();
  return conv;
}

async function openConversation(id, opts = {}) {
  if (state.sending) return;
  if (state.activeController) {
    state.activeController.abort();
    state.activeController = null;
  }
  state.currentConversationId = id;
  const conv = currentConversation();
  if (conv) state.selectedAgent = conv.selectedAgent || state.selectedAgent;
  state.currentMessages = await db.getMessages(id);
  state.currentMessages.sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));
  state.pendingFiles = [];
  state.inputRequiredTask = null;
  renderAll();
  if (opts.skipRecovery !== true) {
    await recoverInFlightTask();
  }
  document.getElementById('message-input').focus();
}

function renameConversation(id) {
  const conv = state.conversations.find((c) => c.id === id);
  if (!conv) return;
  const title = window.prompt('Conversation title:', conv.title || '');
  if (title === null) return;
  const clean = title.trim();
  if (!clean) return;
  conv.title = clean;
  conv.updatedAt = new Date().toISOString();
  db.putConversation(conv).then(() => renderAll());
}

function requestDeleteConversation(id) {
  const conv = state.conversations.find((c) => c.id === id);
  const text = document.getElementById('confirm-text');
  text.textContent = `"${conv ? conv.title : 'This conversation'}" and its messages will be permanently deleted from this browser.`;
  state.pendingDeleteId = id;
  ui.showModal('confirm-modal');
  document.getElementById('confirm-delete-btn').focus();
}

async function confirmDeleteConversation() {
  const id = state.pendingDeleteId;
  state.pendingDeleteId = null;
  ui.hideModal('confirm-modal');
  if (!id) return;
  await db.deleteMessagesForConversation(id);
  await db.deleteConversation(id);
  state.conversations = state.conversations.filter((c) => c.id !== id);
  if (state.currentConversationId === id) {
    state.currentConversationId = null;
    state.currentMessages = [];
    if (state.conversations.length) {
      await openConversation(state.conversations[0].id, { skipRecovery: true });
    } else {
      await createConversation();
    }
  }
  renderAll();
}

// ---------------------------------------------------------------------------
// File handling
// ---------------------------------------------------------------------------

async function addFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const errors = [];
  if (state.pendingFiles.length + files.length > MAX_FILES) {
    errors.push(`Up to ${MAX_FILES} files per message.`);
  }
  const oversized = files.find((f) => f.size > MAX_FILE_SIZE);
  if (oversized) {
    errors.push(`"${oversized.name}" exceeds the 10 MB limit.`);
  }
  if (errors.length) {
    setHint(errors.join(' '), true);
    return;
  }
  for (const file of files) {
    if (state.pendingFiles.length >= MAX_FILES) break;
    try {
      const bytes = await readFileAsBase64(file);
      state.pendingFiles.push({
        name: file.name,
        type: file.type || 'application/octet-stream',
        size: file.size,
        bytes,
      });
    } catch {
      setHint(`Could not read "${file.name}".`, true);
    }
  }
  setHint('');
  renderPendingFiles();
  updateComposerState();
}

// ---------------------------------------------------------------------------
// Sending
// ---------------------------------------------------------------------------

async function sendMessage() {
  if (state.sending) return;
  const input = document.getElementById('message-input');
  const text = input.value.trim();
  if (!text && !state.pendingFiles.length) return;

  let conv = currentConversation();
  if (!conv) {
    conv = await createConversation();
  }

  // Input-required continuation: reuse the original taskId + agent because task
  // stores are agent-namespaced. Otherwise a normal turn with a fresh taskId.
  const continuation = state.inputRequiredTask;
  const agentName = continuation
    ? continuation.agentName
    : document.getElementById('agent-select').value || state.selectedAgent;
  if (!agentName) {
    setHint('No agent available.', true);
    return;
  }
  state.selectedAgent = agentName;
  conv.selectedAgent = agentName;
  conv.updatedAt = new Date().toISOString();
  await db.putConversation(conv);

  const now = new Date().toISOString();
  const userMessage = {
    id: genId(),
    conversationId: conv.id,
    role: 'user',
    text,
    taskId: continuation ? continuation.taskId : '',
    isContinuation: Boolean(continuation),
    agentName,
    status: 'pending',
    attachments: state.pendingFiles.map((f) => ({
      name: f.name,
      type: f.type,
      size: f.size,
    })),
    createdAt: now,
  };
  const assistantMessage = {
    id: genId(),
    conversationId: conv.id,
    role: 'assistant',
    text: '',
    thinkingText: '',
    taskId: continuation ? continuation.taskId : '',
    agentName,
    status: 'working',
    streaming: true,
    createdAt: now,
  };
  userMessage.assistantMessageId = assistantMessage.id;

  // Keep bytes in session memory for retry; never persist them.
  if (state.pendingFiles.length) {
    state.sessionAttachmentBytes.set(userMessage.id, state.pendingFiles.map((f) => ({ ...f })));
  }

  state.currentMessages.push(userMessage, assistantMessage);
  await db.putMessage(userMessage);
  await db.putMessage(assistantMessage);

  if (conv.title === 'New conversation' && text) {
    conv.title = ui.autoTitle(text);
    conv.updatedAt = new Date().toISOString();
    await db.putConversation(conv);
  }

  const sentFiles = state.pendingFiles;
  state.pendingFiles = [];
  state.inputRequiredTask = null;
  input.value = '';
  input.style.height = 'auto';
  renderAll();
  updateComposerState();

  await runStream(userMessage, assistantMessage, agentName, continuation ? continuation.taskId : '', sentFiles);
}

async function runStream(userMessage, assistantMessage, agentName, taskId, attachments) {
  state.sending = true;
  state.activeController = new AbortController();
  updateComposerState();

  try {
    const stream = state.client.sendMessageStream(agentName, {
      contextId: state.currentConversationId,
      taskId,
      text: userMessage.text,
      attachments,
    });
    for await (const chunk of stream) {
      handleChunk(chunk, userMessage, assistantMessage);
    }
  } catch (error) {
    if (error instanceof AuthError) {
      await persistNow({ ...assistantMessage, status: 'failed', streaming: false, error: 'Authentication required' });
      await handleAuthError();
      renderTranscript();
      updateComposerState();
      return;
    }
    assistantMessage.streaming = false;
    assistantMessage.status = 'failed';
    assistantMessage.error = error.message || 'Request failed';
    assistantMessage.retryable = true;
    userMessage.status = 'failed';
    await persistNow({ ...userMessage });
    await persistNow({ ...assistantMessage });
    renderTranscript();
  } finally {
    state.sending = false;
    state.activeController = null;
    updateComposerState();
  }
}

function handleChunk(chunk, userMessage, assistantMessage) {
  if (chunk.error) {
    assistantMessage.streaming = false;
    assistantMessage.status = 'failed';
    assistantMessage.error = chunk.error;
    assistantMessage.retryable = true;
    userMessage.status = 'failed';
    persistNow({ ...userMessage });
    persistNow({ ...assistantMessage });
    renderTranscript();
    return;
  }

  // Authoritative taskId arrives on the first Task/status/artifact event.
  if (chunk.taskId && !userMessage.taskId) {
    userMessage.taskId = chunk.taskId;
    assistantMessage.taskId = chunk.taskId;
    persistNow({ ...userMessage });
  }

  if (chunk.eventType === 'artifact-update') {
    if (chunk.isThinkingArtifact) {
      assistantMessage.thinkingText =
        chunk.append
          ? assistantMessage.thinkingText + chunk.text
          : chunk.text;
      updateLiveThinking(assistantMessage);
      persistSoon(assistantMessage);
    } else {
      assistantMessage.text =
        chunk.append ? assistantMessage.text + chunk.text : chunk.text;
      updateLiveText(assistantMessage);
      persistSoon(assistantMessage);
    }
  } else if (chunk.eventType === 'message') {
    assistantMessage.text += chunk.text;
    updateLiveText(assistantMessage);
    persistSoon(assistantMessage);
  }

  if (chunk.eventType === 'status-update' || chunk.eventType === 'task') {
    assistantMessage.status = chunk.status;
    userMessage.status = chunk.status;
    if (chunk.final) {
      assistantMessage.streaming = false;
      userMessage.status = chunk.status;
      persistNow({ ...userMessage });
      persistNow({ ...assistantMessage });
      renderTranscript();
    } else {
      persistSoon({ ...assistantMessage });
      updateLiveStatus(assistantMessage);
    }
  } else if (chunk.eventType === 'input-required') {
    assistantMessage.status = 'input-required';
    assistantMessage.streaming = false;
    if (chunk.text && !assistantMessage.text) {
      assistantMessage.text = chunk.text;
    }
    userMessage.status = 'input-required';
    if (chunk.taskId) {
      userMessage.taskId = chunk.taskId;
      assistantMessage.taskId = chunk.taskId;
    }
    state.inputRequiredTask = {
      taskId: chunk.taskId || userMessage.taskId,
      agentName: assistantMessage.agentName,
    };
    persistNow({ ...userMessage });
    persistNow({ ...assistantMessage });
    renderTranscript();
    renderAgentSelector();
    updateComposerState();
  }
}

function updateLiveText(message) {
  const node = liveNodes.get(message.id);
  if (node) node.textNode.textContent = message.text || '';
  scrollIfNearBottom();
}

function updateLiveThinking(message) {
  const node = liveNodes.get(message.id);
  const text = message.thinkingText || '';
  if (node && node.thinkingContent) {
    node.thinkingContent.textContent = text;
  }
  if (node && node.thinkingNode) {
    node.thinkingNode.classList.toggle('hidden', !text);
  }
  scrollIfNearBottom();
}

function updateLiveStatus(message) {
  const node = liveNodes.get(message.id);
  if (!node || !node.article) return;
  const pill = node.article.querySelector('.status-pill');
  if (pill) {
    pill.textContent = message.status || '';
    pill.className = `status-pill ${message.status || ''}`;
  }
}

function scrollIfNearBottom() {
  const container = document.getElementById('transcript');
  const nearBottom =
    container.scrollHeight - container.scrollTop - container.clientHeight < 120;
  if (nearBottom) container.scrollTop = container.scrollHeight;
}

// ---------------------------------------------------------------------------
// Retry
// ---------------------------------------------------------------------------

async function retryMessage(assistantMessage) {
  const idx = state.currentMessages.indexOf(assistantMessage);
  const userMessage = idx > 0 ? state.currentMessages[idx - 1] : null;
  if (!userMessage || userMessage.role !== 'user') return;
  const attachments = state.sessionAttachmentBytes.get(userMessage.id) || [];
  // A retry of a normal turn starts a fresh task; an input-required
  // continuation reuses the original taskId + agent.
  const taskId = userMessage.isContinuation ? userMessage.taskId : '';
  userMessage.taskId = taskId;
  assistantMessage.text = '';
  assistantMessage.thinkingText = '';
  assistantMessage.error = null;
  assistantMessage.retryable = false;
  assistantMessage.status = 'working';
  assistantMessage.streaming = true;
  userMessage.status = 'pending';
  await persistNow({ ...userMessage });
  await persistNow({ ...assistantMessage });
  renderTranscript();
  updateComposerState();
  await runStream(
    userMessage,
    assistantMessage,
    userMessage.agentName,
    taskId,
    attachments
  );
}

// ---------------------------------------------------------------------------
// Active-task recovery
// ---------------------------------------------------------------------------

async function recoverInFlightTask() {
  if (state.recoveryInProgress) return;
  state.recoveryInProgress = true;
  try {
    const userMessage = [...state.currentMessages]
      .reverse()
      .find(
        (m) =>
          m.role === 'user' &&
          m.taskId &&
          RESUBSCRIBABLE_STATES.has(normalizeStatus(m.status))
      );
    if (!userMessage) return;
    const assistantMessage = findAssistantFor(userMessage);
    const agentName = userMessage.agentName;
    const taskId = userMessage.taskId;

    let task;
    try {
      task = await state.client.getTask(agentName, taskId);
    } catch (error) {
      if (error instanceof AuthError) {
        await handleAuthError();
        return;
      }
      // Task no longer exists on the server — mark the turn failed.
      userMessage.status = 'failed';
      if (assistantMessage) {
        assistantMessage.status = 'failed';
        assistantMessage.streaming = false;
        assistantMessage.error = 'Task is no longer available on the server.';
      }
      await persistNow({ ...userMessage });
      if (assistantMessage) await persistNow({ ...assistantMessage });
      renderTranscript();
      return;
    }

    const taskState = normalizeStatus(
      (task.status && task.status.state) || ''
    );

    if (isTerminal(taskState)) {
      // Terminal snapshot: render/finalize directly from authoritative data.
      const chunks = state.client.materializeTask(task);
      const answerText = chunks
        .filter((c) => c.eventType === 'artifact-update' && !c.isThinkingArtifact)
        .map((c) => c.text)
        .join('');
      if (assistantMessage) {
        assistantMessage.text = answerText || assistantMessage.text;
        assistantMessage.status = taskState;
        assistantMessage.streaming = false;
        await persistNow({ ...assistantMessage });
      }
      userMessage.status = taskState;
      await persistNow({ ...userMessage });
      renderTranscript();
      return;
    }

    if (taskState === 'input-required') {
      state.inputRequiredTask = { taskId, agentName };
      if (assistantMessage) {
        if (!assistantMessage.text) {
          const statusText = (task.status && task.status.message && task.status.message.parts || [])
            .filter((p) => typeof p.text === 'string')
            .map((p) => p.text)
            .join('');
          assistantMessage.text = statusText;
        }
        assistantMessage.status = 'input-required';
        assistantMessage.streaming = false;
        await persistNow({ ...assistantMessage });
      }
      userMessage.status = 'input-required';
      await persistNow({ ...userMessage });
      renderTranscript();
      renderAgentSelector();
      updateComposerState();
      return;
    }

    // Genuinely live non-terminal task: seed the accumulator from the snapshot
    // (so replay emits no duplicates) then resubscribe until terminal.
    state.client.accumulator.seedFromTask(task);
    if (assistantMessage) {
      assistantMessage.status = taskState;
      assistantMessage.streaming = true;
      await persistNow({ ...assistantMessage });
    }
    userMessage.status = taskState;
    await persistNow({ ...userMessage });
    renderTranscript();

    state.sending = true;
    updateComposerState();
    try {
      for await (const chunk of state.client.subscribeToTask(agentName, taskId)) {
        handleChunk(chunk, userMessage, assistantMessage);
      }
    } catch (error) {
      if (error instanceof AuthError) {
        await handleAuthError();
        return;
      }
      if (assistantMessage) {
        assistantMessage.streaming = false;
        assistantMessage.status = 'failed';
        assistantMessage.error = error.message || 'Recovery failed';
        await persistNow({ ...assistantMessage });
      }
      userMessage.status = 'failed';
      await persistNow({ ...userMessage });
      renderTranscript();
    } finally {
      state.sending = false;
      updateComposerState();
    }
  } finally {
    state.recoveryInProgress = false;
  }
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------

function wireEvents() {
  document.getElementById('auth-form').addEventListener('submit', submitAuthKey);
  // The auth modal is required when the server has a key configured, so it is
  // not dismissed via backdrop click; it closes only on successful auth.
  document.querySelectorAll('[data-close-confirm]').forEach((node) =>
    node.addEventListener('click', () => ui.hideModal('confirm-modal'))
  );
  document.getElementById('confirm-delete-btn').addEventListener('click', confirmDeleteConversation);

  document.getElementById('new-chat-btn').addEventListener('click', createConversation);
  document.getElementById('sidebar-toggle').addEventListener('click', () => {
    document.getElementById('sidebar').classList.add('open');
    document.getElementById('sidebar-toggle').setAttribute('aria-expanded', 'true');
  });
  document.getElementById('sidebar-close').addEventListener('click', () => {
    document.getElementById('sidebar').classList.remove('open');
    document.getElementById('sidebar-toggle').setAttribute('aria-expanded', 'false');
  });

  document.getElementById('agent-select').addEventListener('change', (event) => {
    state.selectedAgent = event.target.value;
    const conv = currentConversation();
    if (conv) {
      conv.selectedAgent = event.target.value;
      conv.updatedAt = new Date().toISOString();
      db.putConversation(conv);
    }
  });

  const input = document.getElementById('message-input');
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
    updateComposerState();
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendMessage();
    }
  });

  document.getElementById('send-btn').addEventListener('click', sendMessage);
  document.getElementById('attach-btn').addEventListener('click', () => {
    document.getElementById('file-input').click();
  });
  document.getElementById('file-input').addEventListener('change', (event) => {
    addFiles(event.target.files);
    event.target.value = '';
  });

  const dropZone = document.getElementById('drop-zone');
  ['dragenter', 'dragover'].forEach((name) =>
    dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.add('dragover');
    })
  );
  ['dragleave', 'drop'].forEach((name) =>
    dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.remove('dragover');
    })
  );
  dropZone.addEventListener('drop', (event) => {
    addFiles(event.dataTransfer.files);
  });
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

wireEvents();
init();
