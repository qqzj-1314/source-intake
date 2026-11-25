import { randomUUID } from 'node:crypto';

export class PreviewError extends Error {
  constructor(message, code = 'preview-invalid') {
    super(message);
    this.name = 'PreviewError';
    this.code = code;
  }
}

export class PreviewCache {
  constructor({ ttlMs = 10 * 60 * 1000, maxEntries = 100, now = Date.now, randomId = randomUUID } = {}) {
    this.ttlMs = ttlMs;
    this.maxEntries = maxEntries;
    this.now = now;
    this.randomId = randomId;
    this.entries = new Map();
  }

  prune() {
    const current = this.now();
    for (const [id, entry] of this.entries) {
      if (entry.expiresAt <= current) this.entries.delete(id);
    }
  }

  create({ sessionId = null, request, plan }) {
    this.prune();
    if (sessionId) {
      for (const [id, entry] of this.entries) {
        if (entry.sessionId === sessionId) this.entries.delete(id);
      }
    }
    while (this.entries.size >= this.maxEntries) {
      this.entries.delete(this.entries.keys().next().value);
    }
    const id = this.randomId();
    const createdAt = this.now();
    const expiresAt = createdAt + this.ttlMs;
    this.entries.set(id, { sessionId, request, plan, createdAt, expiresAt });
    return { preview_id: id, expires_at: new Date(expiresAt).toISOString() };
  }

  take(id) {
    const entry = this.entries.get(id);
    if (!entry) throw new PreviewError('预览已失效或不存在，请重新预览。');
    this.entries.delete(id);
    if (entry.expiresAt <= this.now()) {
      throw new PreviewError('预览已过期，请重新预览。', 'preview-expired');
    }
    this.prune();
    return entry;
  }

  get size() {
    return this.entries.size;
  }
}
