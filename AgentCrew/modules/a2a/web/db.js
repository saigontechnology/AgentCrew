// IndexedDB persistence for conversations and messages.
//
// The browser is the single source of truth for the UI. Conversation IDs are
// A2A contextIds, and each user message stores the authoritative server taskId
// (once assigned), the agent used, and the latest task status.

const DB_NAME = 'agentcrew-a2a-ui';
const DB_VERSION = 1;

let dbPromise = null;

function openDB() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (event) => {
      const db = event.target.result;
      if (!db.objectStoreNames.contains('conversations')) {
        db.createObjectStore('conversations', { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains('messages')) {
        const store = db.createObjectStore('messages', { keyPath: 'id' });
        store.createIndex('conversationId', 'conversationId', {
          unique: false,
        });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

// Run a read/write transaction helper that resolves with the request result.
function tx(storeName, mode, fn) {
  return openDB().then(
    (db) =>
      new Promise((resolve, reject) => {
        const transaction = db.transaction(storeName, mode);
        const store = transaction.objectStore(storeName);
        const request = fn(store);
        transaction.oncomplete = () => resolve(request && request.result);
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      })
  );
}

// --- Conversations -----------------------------------------------------------

export function getAllConversations() {
  return tx('conversations', 'readonly', (store) => store.getAll());
}

export function getConversation(id) {
  return tx('conversations', 'readonly', (store) => store.get(id));
}

export function putConversation(conversation) {
  return tx('conversations', 'readwrite', (store) => store.put(conversation));
}

export function deleteConversation(id) {
  return tx('conversations', 'readwrite', (store) => store.delete(id));
}

// --- Messages ----------------------------------------------------------------

export function getMessages(conversationId) {
  return openDB().then(
    (db) =>
      new Promise((resolve, reject) => {
        const transaction = db.transaction('messages', 'readonly');
        const index = transaction.objectStore('messages').index('conversationId');
        const request = index.getAll(conversationId);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      })
  );
}

export function putMessage(message) {
  return tx('messages', 'readwrite', (store) => store.put(message));
}

export function getMessage(id) {
  return tx('messages', 'readonly', (store) => store.get(id));
}

export function deleteMessagesForConversation(conversationId) {
  return openDB().then(
    (db) =>
      new Promise((resolve, reject) => {
        const transaction = db.transaction('messages', 'readwrite');
        const index = transaction.objectStore('messages').index('conversationId');
        const request = index.openCursor(conversationId);
        request.onsuccess = () => {
          const cursor = request.result;
          if (cursor) {
            cursor.delete();
            cursor.continue();
          }
        };
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error);
      })
  );
}

// --- Patch helpers -----------------------------------------------------------

export function updateConversation(id, patch) {
  return getConversation(id).then((conv) => {
    if (!conv) return null;
    const updated = { ...conv, ...patch, id };
    updated.updatedAt = new Date().toISOString();
    return putConversation(updated).then(() => updated);
  });
}

export function updateMessage(id, patch) {
  return getMessage(id).then((msg) => {
    if (!msg) return null;
    const updated = { ...msg, ...patch, id };
    return putMessage(updated).then(() => updated);
  });
}
