import fs from 'node:fs';
import { config } from './config.js';
import { logger } from './log.js';

const log = logger('sync');
// 4xx nay la loi tam thoi hoac xac thuc, khong phai loi du lieu
const RETRYABLE_4XX = new Set([401, 403, 408, 425, 429]);
const why = (e) => (e.cause?.code ? `${e.message} (${e.cause.code})` : e.message);
const isDataError = (e) => e.status >= 400 && e.status < 500 && !RETRYABLE_4XX.has(e.status);

export class Sync {
  constructor({ outbox, client, getConnectors }) {
    this.outbox = outbox;
    this.client = client;
    this.getConnectors = getConnectors;
    this.busy = { msgs: false, files: false };
    this.lastError = null;
    this.lastOkAt = null;
  }

  start() {
    this.timers = [
      setInterval(() => this.flushMessages(), config.flushMs),
      setInterval(() => this.flushFiles(), config.flushMs),
      setInterval(() => this.heartbeat(), config.heartbeatMs),
      setInterval(() => this.outbox.gc(), 3_600_000),
    ];
    this.outbox.gc();
    this.heartbeat();
  }

  stop() {
    (this.timers || []).forEach(clearInterval);
  }

  kick() {
    this.flushMessages();
  }

  async flushMessages() {
    if (this.busy.msgs) return;
    this.busy.msgs = true;
    try {
      for (;;) {
        const rows = this.outbox.dueMessages(50);
        if (!rows.length) break;
        const ok = await this._sendBatch(rows);
        if (!ok) break;
      }
    } finally {
      this.busy.msgs = false;
    }
  }

  async _sendBatch(rows) {
    try {
      await this.client.postJson('/v1/messages', { messages: rows.map((r) => JSON.parse(r.payload)) });
      rows.forEach((r) => this.outbox.ackMessage(r.id));
      this.lastOkAt = Date.now();
      this.lastError = null;
      return true;
    } catch (e) {
      if (isDataError(e)) {
        // Mot tin hong khong duoc chan ca batch: gui tung tin, tin loi di vao bang dead
        if (rows.length > 1) return this._sendOneByOne(rows);
        log.error(`tin ${rows[0].id} bi Cloud tu choi, chuyen vao dead`, e);
        this.outbox.deadMessage(rows[0], e.message);
        return true;
      }
      this.lastError = why(e);
      log.warn(`gui tin that bai (${rows.length} tin), se thu lai`, why(e));
      rows.forEach((r) => this.outbox.backoffMessage(r.id, r.attempts));
      return false;
    }
  }

  async _sendOneByOne(rows) {
    for (const r of rows) {
      const ok = await this._sendBatch([r]);
      if (!ok) return false;
    }
    return true;
  }

  async flushFiles() {
    if (this.busy.files) return;
    this.busy.files = true;
    try {
      for (const f of this.outbox.dueFiles(5)) {
        const p = this.outbox.filePath(f.sha256);
        if (!fs.existsSync(p)) {
          log.error(`mat file ${f.sha256} tren dia, bo qua`);
          this.outbox.ackFile(f.sha256);
          continue;
        }
        try {
          await this.client.putFile(f.sha256, p, f.size, f.mime);
          this.outbox.ackFile(f.sha256);
          this.lastOkAt = Date.now();
        } catch (e) {
          if (isDataError(e)) {
            log.error(`file ${f.sha256} bi Cloud tu choi, bo`, e);
            this.outbox.ackFile(f.sha256);
          } else {
            this.lastError = why(e);
            log.warn(`upload file ${f.sha256} that bai, se thu lai`, why(e));
            this.outbox.backoffFile(f.sha256, f.attempts);
          }
        }
      }
    } finally {
      this.busy.files = false;
    }
  }

  async heartbeat() {
    try {
      const connectors = {};
      for (const c of this.getConnectors()) connectors[c.name] = c.status();
      await this.client.postJson('/v1/heartbeat', {
        agent_version: config.agentVersion,
        connectors,
        queue: { ...this.outbox.counts(), last_error: this.lastError },
      });
    } catch (e) {
      log.warn('heartbeat that bai', why(e));
    }
  }
}
