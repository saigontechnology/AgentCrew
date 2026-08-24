// Minimal A2A v1 JSON-RPC client for the embedded chat UI.
//
// Talks directly to the per-agent JSON-RPC endpoints (POST /<agent>/) using
// the same wire semantics as the a2a-js SDK:
//   - SendStreamingMessage / SubscribeToTask return Server-Sent Events where
//     each `data:` line is a JSON-RPC 2.0 response whose `result` carries one
//     of: task, message, statusUpdate, artifactUpdate.
//   - Non-streaming calls (GetTask, CancelTask) return a plain JSON-RPC
//     response.
//   - Task state enum strings (TASK_STATE_*) are normalized to lowercase
//     application states; completed/failed/canceled/rejected are terminal.

export class AuthError extends Error {
  constructor(message) {
    super(message);
    this.name = 'AuthError';
  }
}

export const TERMINAL_STATES = new Set([
  'completed',
  'failed',
  'canceled',
  'rejected',
]);

export const RESUBSCRIBABLE_STATES = new Set([
  'submitted',
  'working',
  'input-required',
]);

export function toTaskState(enumStr) {
  switch (enumStr) {
    case 'TASK_STATE_SUBMITTED':
      return 'submitted';
    case 'TASK_STATE_WORKING':
      return 'working';
    case 'TASK_STATE_INPUT_REQUIRED':
      return 'input-required';
    case 'TASK_STATE_COMPLETED':
      return 'completed';
    case 'TASK_STATE_FAILED':
      return 'failed';
    case 'TASK_STATE_CANCELED':
      return 'canceled';
    case 'TASK_STATE_REJECTED':
      return 'rejected';
    case 'TASK_STATE_AUTH_REQUIRED':
      return 'auth-required';
    default:
      return 'unknown';
  }
}

// Normalize states for persistence: rejected -> failed, auth-required ->
// input-required (interrupted, not terminal).
export function normalizeStatus(state) {
  if (state === 'rejected') return 'failed';
  if (state === 'auth-required') return 'input-required';
  return state;
}

export function isTerminal(state) {
  return TERMINAL_STATES.has(state);
}

function genId() {
  if (crypto && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function extractTextFromParts(parts) {
  if (!Array.isArray(parts)) return '';
  return parts
    .filter((part) => typeof part.text === 'string')
    .map((part) => part.text)
    .join('');
}

// Per-(taskId, artifactId) text accumulation honoring append/replace semantics.
class ArtifactAccumulator {
  constructor() {
    this.map = new Map();
  }

  key(taskId, artifactId) {
    return `${taskId}:${artifactId}`;
  }

  accumulate(taskId, artifactId, rawText, append) {
    const key = this.key(taskId, artifactId);
    const prev = this.map.get(key) ?? '';
    if (append) {
      this.map.set(key, prev + rawText);
      return { text: rawText, append: true };
    }
    if (prev.length === 0) {
      this.map.set(key, rawText);
      return { text: rawText, append: true };
    }
    if (rawText.startsWith(prev)) {
      const suffix = rawText.slice(prev.length);
      this.map.set(key, rawText);
      return { text: suffix, append: true };
    }
    this.map.set(key, rawText);
    return { text: rawText, append: false };
  }

  // Clear entries for a task and seed from an authoritative snapshot so a
  // later identical snapshot emits no duplicate chunks.
  seedFromTask(task) {
    const prefix = `${task.id}:`;
    for (const key of this.map.keys()) {
      if (key.startsWith(prefix)) this.map.delete(key);
    }
    if (!Array.isArray(task.artifacts)) return;
    for (const artifact of task.artifacts) {
      if (!artifact.artifactId) continue;
      this.map.set(
        this.key(task.id, artifact.artifactId),
        extractTextFromParts(artifact.parts)
      );
    }
  }

  // Emit chunks only for content not yet present locally (dedupe on replay).
  reconcile(task, taskId, contextId) {
    const chunks = [];
    if (!Array.isArray(task.artifacts)) return chunks;
    for (const artifact of task.artifacts) {
      const artifactId = artifact.artifactId;
      if (!artifactId) continue;
      const rawText = extractTextFromParts(artifact.parts);
      const localText = this.map.get(this.key(taskId, artifactId)) ?? '';
      let deltaText;
      let append;
      if (rawText === localText) {
        continue;
      } else if (rawText.startsWith(localText)) {
        deltaText = rawText.slice(localText.length);
        append = true;
      } else {
        deltaText = rawText;
        append = false;
      }
      this.map.set(this.key(taskId, artifactId), rawText);
      chunks.push({
        text: deltaText,
        append,
        artifactId,
        isThinkingArtifact: artifactId.startsWith('thinking_'),
      });
    }
    return chunks;
  }

  reset() {
    this.map.clear();
  }
}

// Parse an SSE response body into JSON-RPC envelopes.
async function* readSSE(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        let eventType = 'message';
        let data = '';
        for (const line of raw.split('\n')) {
          const trimmed = line.trim();
          if (trimmed.startsWith('event:')) {
            eventType = trimmed.slice(6).trim();
          } else if (trimmed.startsWith('data:')) {
            data += trimmed.slice(5).trim() + '\n';
          }
        }
        if (data) {
          try {
            yield { event: eventType, data: JSON.parse(data.trim()) };
          } catch {
            // Ignore malformed SSE frames.
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export class A2AClient {
  constructor() {
    this.apiKey = null;
    this.accumulator = new ArtifactAccumulator();
  }

  setApiKey(key) {
    this.apiKey = key || null;
  }

  headers(extra = {}) {
    const headers = {
      'Content-Type': 'application/json',
      'A2A-Version': '1.0',
      ...extra,
    };
    if (this.apiKey) {
      headers.Authorization = `Bearer ${this.apiKey}`;
    }
    return headers;
  }

  async fetchConfig() {
    const resp = await fetch('/_a2a-ui/config');
    if (!resp.ok) throw new Error(`Config request failed: ${resp.status}`);
    return resp.json();
  }

  async listAgents() {
    const resp = await fetch('/agents');
    if (!resp.ok) throw new Error(`Agents request failed: ${resp.status}`);
    return resp.json();
  }

  // Validate the stored key against a protected per-agent card endpoint.
  async validateKey(agentName) {
    const resp = await fetch(`/${encodeURIComponent(agentName)}/.well-known/agent-card.json`, {
      headers: this.headers(),
    });
    return resp.ok;
  }

  async rpc(agentName, method, params) {
    const resp = await fetch(`/${encodeURIComponent(agentName)}/`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: genId(),
        method,
        params,
      }),
    });
    if (resp.status === 401) {
      throw new AuthError('Authentication required');
    }
    if (!resp.ok) {
      throw new Error(`RPC ${method} failed: ${resp.status}`);
    }
    const body = await resp.json();
    if (body.error) {
      const err = new Error(
        body.error.message || `RPC ${method} error: ${body.error.code}`
      );
      err.code = body.error.code;
      throw err;
    }
    return body.result;
  }

  async getTask(agentName, taskId) {
    return this.rpc(agentName, 'GetTask', { id: taskId });
  }

  // Build the normalized chunk list for one JSON-RPC stream envelope.
  _normalizeEnvelope(envelope) {
    const chunks = [];
    if (envelope.error) {
      chunks.push({
        text: '',
        append: true,
        eventType: 'error',
        status: null,
        final: true,
        taskId: null,
        contextId: null,
        artifactId: null,
        isThinkingArtifact: false,
        error: envelope.error.message || 'Stream error',
      });
      return chunks;
    }
    const result = envelope.result;
    if (!result) return chunks;

    let taskId = null;
    let contextId = null;
    if (result.task) {
      taskId = result.task.id;
      contextId = result.task.contextId;
    } else if (result.message) {
      contextId = result.message.contextId || null;
      taskId = result.message.taskId || null;
    } else if (result.statusUpdate) {
      taskId = result.statusUpdate.taskId;
      contextId = result.statusUpdate.contextId;
    } else if (result.artifactUpdate) {
      taskId = result.artifactUpdate.taskId;
      contextId = result.artifactUpdate.contextId;
    }

    const addChunk = (partial) => {
      chunks.push({
        text: '',
        append: true,
        eventType: 'task',
        status: null,
        final: false,
        taskId,
        contextId,
        artifactId: null,
        isThinkingArtifact: false,
        error: null,
        ...partial,
      });
    };

    if (result.message) {
      addChunk({
        text: extractTextFromParts(result.message.parts),
        eventType: 'message',
      });
      return chunks;
    }

    if (result.task) {
      const task = result.task;
      const state = normalizeStatus(toTaskState(task.status && task.status.state));
      for (const c of this.accumulator.reconcile(task, taskId, contextId)) {
        addChunk({ ...c, eventType: 'artifact-update' });
      }
      const messageParts =
        (task.status && task.status.message && task.status.message.parts) || [];
      if (state === 'input-required') {
        addChunk({
          text: extractTextFromParts(messageParts),
          eventType: 'input-required',
          status: 'input-required',
          final: false,
        });
      } else {
        const terminal = isTerminal(state);
        addChunk({
          text:
            messageParts.length > 0
              ? extractTextFromParts(messageParts)
              : '',
          eventType: terminal ? 'status-update' : 'task',
          status: state,
          final: terminal,
        });
      }
      return chunks;
    }

    if (result.statusUpdate) {
      const su = result.statusUpdate;
      const state = normalizeStatus(
        toTaskState(su.status && su.status.state)
      );
      const messageParts =
        (su.status && su.status.message && su.status.message.parts) || [];
      if (state === 'input-required') {
        addChunk({
          text: extractTextFromParts(messageParts),
          eventType: 'input-required',
          status: 'input-required',
          final: false,
        });
      } else {
        addChunk({
          text:
            messageParts.length > 0
              ? extractTextFromParts(messageParts)
              : '',
          eventType: 'status-update',
          status: state,
          final: isTerminal(state),
        });
      }
      return chunks;
    }

    if (result.artifactUpdate) {
      const au = result.artifactUpdate;
      const artifact = au.artifact || {};
      const artifactId = artifact.artifactId;
      const rawText = extractTextFromParts(artifact.parts);
      const isThinking = artifactId ? artifactId.startsWith('thinking_') : false;
      let emit;
      if (!taskId || !artifactId) {
        emit = { text: rawText, append: true };
      } else {
        emit = this.accumulator.accumulate(taskId, artifactId, rawText, au.append);
      }
      addChunk({
        text: emit.text,
        append: emit.append,
        eventType: 'artifact-update',
        artifactId,
        isThinkingArtifact: isThinking,
        final: Boolean(au.lastChunk),
      });
      return chunks;
    }

    return chunks;
  }

  // Stream a normal/continuation turn. Yields normalized chunks.
  async *sendMessageStream(agentName, { contextId, taskId, text, attachments }) {
    const parts = [];
    if (text) parts.push({ text });
    for (const file of attachments || []) {
      parts.push({
        raw: file.bytes,
        filename: file.name || 'file',
        mediaType: file.type || 'application/octet-stream',
      });
    }
    const params = {
      message: {
        messageId: genId(),
        role: 'ROLE_USER',
        parts,
        contextId: contextId || '',
        taskId: taskId || '',
      },
      configuration: {
        acceptedOutputModes: ['text/plain', 'application/json'],
        returnImmediately: true,
      },
    };
    const resp = await fetch(`/${encodeURIComponent(agentName)}/`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: genId(),
        method: 'SendStreamingMessage',
        params,
      }),
    });
    if (resp.status === 401) {
      throw new AuthError('Authentication required');
    }
    if (!resp.ok) {
      throw new Error(`SendStreamingMessage failed: ${resp.status}`);
    }
    for await (const frame of readSSE(resp)) {
      for (const chunk of this._normalizeEnvelope(frame.data)) {
        yield chunk;
      }
    }
  }

  // Resubscribe to an existing task. Yields normalized chunks.
  async *subscribeToTask(agentName, taskId) {
    const resp = await fetch(`/${encodeURIComponent(agentName)}/`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: genId(),
        method: 'SubscribeToTask',
        params: { id: taskId },
      }),
    });
    if (resp.status === 401) {
      throw new AuthError('Authentication required');
    }
    if (!resp.ok) {
      throw new Error(`SubscribeToTask failed: ${resp.status}`);
    }
    for await (const frame of readSSE(resp)) {
      for (const chunk of this._normalizeEnvelope(frame.data)) {
        yield chunk;
      }
    }
  }

  // Materialize a full task snapshot into authoritative chunks (used when a
  // recovered task is already terminal).
  materializeTask(task) {
    const chunks = [];
    const taskId = task.id;
    const contextId = task.contextId;
    if (Array.isArray(task.artifacts)) {
      for (const artifact of task.artifacts) {
        if (!artifact.artifactId) continue;
        const isThinking = artifact.artifactId.startsWith('thinking_');
        chunks.push({
          text: extractTextFromParts(artifact.parts),
          append: true,
          eventType: 'artifact-update',
          artifactId: artifact.artifactId,
          isThinkingArtifact: isThinking,
          final: false,
          taskId,
          contextId,
          status: null,
          error: null,
        });
      }
    }
    const state = normalizeStatus(toTaskState(task.status && task.status.state));
    const messageParts =
      (task.status && task.status.message && task.status.message.parts) || [];
    if (state === 'input-required') {
      chunks.push({
        text: extractTextFromParts(messageParts),
        append: true,
        eventType: 'input-required',
        status: 'input-required',
        final: false,
        taskId,
        contextId,
        artifactId: null,
        isThinkingArtifact: false,
        error: null,
      });
    } else {
      chunks.push({
        text:
          messageParts.length > 0 ? extractTextFromParts(messageParts) : '',
        append: true,
        eventType: isTerminal(state) ? 'status-update' : 'task',
        status: state,
        final: isTerminal(state),
        taskId,
        contextId,
        artifactId: null,
        isThinkingArtifact: false,
        error: null,
      });
    }
    return chunks;
  }

  resetAccumulator() {
    this.accumulator.reset();
  }
}
