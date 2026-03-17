const DB_NAME = 'widget-media-cache';
const STORE_NAME = 'incoming_media';
const DB_VERSION = 1;
const MEDIA_TTL_MS = 24 * 60 * 60 * 1000;

function hasIndexedDb(): boolean {
  return typeof indexedDB !== 'undefined';
}

export interface CachedMediaAttachment {
  name: string;
  type: string;
  url: string;
  kind?: 'image' | 'animation';
}

interface CachedMediaRecord {
  cache_key: string;
  session_id: string;
  message_key: string;
  attachment_type: string;
  attachment_name: string;
  media_url: string;
  kind: 'image' | 'animation';
  created_at: number;
  expires_at: number;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (!hasIndexedDb()) {
      reject(new Error('IndexedDB is not available'));
      return;
    }
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: 'cache_key' });
        store.createIndex('session_id', 'session_id', { unique: false });
        store.createIndex('expires_at', 'expires_at', { unique: false });
      }
    };

    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function withStore<T>(
  mode: IDBTransactionMode,
  fn: (store: IDBObjectStore, resolve: (value: T) => void, reject: (reason?: unknown) => void) => void
): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, mode);
        const store = tx.objectStore(STORE_NAME);
        fn(store, resolve, reject);
        tx.oncomplete = () => db.close();
        tx.onerror = () => reject(tx.error);
      })
  );
}

export async function saveIncomingMedia(
  sessionId: string,
  messageKey: string,
  attachment: CachedMediaAttachment
): Promise<void> {
  if (!hasIndexedDb()) return;
  const now = Date.now();
  const record: CachedMediaRecord = {
    cache_key: `${sessionId}:${messageKey}`,
    session_id: sessionId,
    message_key: messageKey,
    attachment_type: attachment.type,
    attachment_name: attachment.name,
    media_url: attachment.url,
    kind: attachment.kind || (attachment.type === 'video/mp4' ? 'animation' : 'image'),
    created_at: now,
    expires_at: now + MEDIA_TTL_MS
  };

  await withStore<void>('readwrite', (store, resolve, reject) => {
    const request = store.put(record);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function getIncomingMedia(
  sessionId: string,
  messageKey: string
): Promise<CachedMediaAttachment | null> {
  if (!hasIndexedDb()) return null;
  const cacheKey = `${sessionId}:${messageKey}`;
  const record = await withStore<CachedMediaRecord | null>('readonly', (store, resolve, reject) => {
    const request = store.get(cacheKey);
    request.onsuccess = () => resolve((request.result as CachedMediaRecord | undefined) || null);
    request.onerror = () => reject(request.error);
  });

  if (!record) return null;
  if (record.expires_at <= Date.now()) {
    await withStore<void>('readwrite', (store, resolve, reject) => {
      const request = store.delete(cacheKey);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
    return null;
  }

  return {
    name: record.attachment_name,
    type: record.attachment_type,
    url: record.media_url,
    kind: record.kind
  };
}

export async function cleanupExpiredMedia(): Promise<void> {
  if (!hasIndexedDb()) return;
  const now = Date.now();
  await withStore<void>('readwrite', (store, resolve, reject) => {
    const request = store.openCursor();
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) {
        resolve();
        return;
      }
      const record = cursor.value as CachedMediaRecord;
      if (record.expires_at <= now) {
        cursor.delete();
      }
      cursor.continue();
    };
    request.onerror = () => reject(request.error);
  });
}

export async function clearSessionMedia(sessionId: string): Promise<void> {
  if (!hasIndexedDb()) return;
  await withStore<void>('readwrite', (store, resolve, reject) => {
    const index = store.index('session_id');
    const request = index.openCursor(IDBKeyRange.only(sessionId));
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) {
        resolve();
        return;
      }
      cursor.delete();
      cursor.continue();
    };
    request.onerror = () => reject(request.error);
  });
}
