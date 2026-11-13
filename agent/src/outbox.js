import Database from 'better-sqlite3';
import fs from 'node:fs';
import path from 'node:path';

const now = () => Date.now();
export const backoffMs = (attempts) => Math.min(300_000, 2_000 * 2 ** Math.min(attempts, 8));

/**
 * Hang doi ben vung tren dia. Tin va file chi bi xoa sau khi Cloud xac nhan.
 * Mat mang hoac Cloud loi khong lam mat du lieu.
 */
export class Outbox {
  constructor(dataDir) {
    this.filesDir = path.join(dataDir, 'files');
    fs.mkdirSync(this.filesDir, { recursive: true });
    this.db = new Database(path.join(dataDir, 'outbox.db'));
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('synchronous = NORMAL');
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS msgs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payload TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_try INTEGER NOT NULL DEFAULT 0
      );
      CREATE TABLE IF NOT EXISTS files (
        sha256 TEXT PRIMARY KEY,
        mime TEXT,
        size INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_try INTEGER NOT NULL DEFAULT 0
      );
      CREATE TABLE IF NOT EXISTS dead (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payload TEXT NOT NULL,
        error TEXT,
        at INTEGER NOT NULL
      );
    `);
    this.s = {
      insMsg: this.db.prepare('INSERT INTO msgs(payload) VALUES (?)'),
      insFile: this.db.prepare('INSERT OR IGNORE INTO files(sha256, mime, size) VALUES (?,?,?)'),
      dueMsgs: this.db.prepare('SELECT id, payload, attempts FROM msgs WHERE next_try <= ? ORDER BY id LIMIT ?'),
      delMsg: this.db.prepare('DELETE FROM msgs WHERE id = ?'),
      bumpMsg: this.db.prepare('UPDATE msgs SET attempts = attempts + 1, next_try = ? WHERE id = ?'),
      insDead: this.db.prepare('INSERT INTO dead(payload, error, at) VALUES (?,?,?)'),
      dueFiles: this.db.prepare('SELECT sha256, mime, size, attempts FROM files WHERE next_try <= ? ORDER BY rowid LIMIT ?'),
      delFile: this.db.prepare('DELETE FROM files WHERE sha256 = ?'),
      bumpFile: this.db.prepare('UPDATE files SET attempts = attempts + 1, next_try = ? WHERE sha256 = ?'),
      cntMsgs: this.db.prepare('SELECT COUNT(*) AS n FROM msgs'),
      cntFiles: this.db.prepare('SELECT COUNT(*) AS n FROM files'),
      cntDead: this.db.prepare('SELECT COUNT(*) AS n FROM dead'),
      allFiles: this.db.prepare('SELECT sha256 FROM files'),
    };
    this._tx = this.db.transaction((payload, files) => {
      for (const f of files) this.s.insFile.run(f.sha256, f.mime ?? null, f.buffer.length);
      this.s.insMsg.run(JSON.stringify(payload));
    });
  }

  filePath(sha256) {
    return path.join(this.filesDir, sha256);
  }

  /** files: [{ sha256, buffer, mime }] */
  enqueue(payload, files = []) {
    for (const f of files) {
      const p = this.filePath(f.sha256);
      if (!fs.existsSync(p)) {
        const tmp = `${p}.tmp`;
        fs.writeFileSync(tmp, f.buffer);
        fs.renameSync(tmp, p);
      }
    }
    this._tx(payload, files);
  }

  dueMessages(limit = 50) {
    return this.s.dueMsgs.all(now(), limit);
  }
  ackMessage(id) {
    this.s.delMsg.run(id);
  }
  backoffMessage(id, attempts) {
    this.s.bumpMsg.run(now() + backoffMs(attempts), id);
  }
  deadMessage(row, error) {
    this.db.transaction(() => {
      this.s.insDead.run(row.payload, String(error).slice(0, 500), now());
      this.s.delMsg.run(row.id);
    })();
  }

  dueFiles(limit = 5) {
    return this.s.dueFiles.all(now(), limit);
  }
  ackFile(sha256) {
    this.s.delFile.run(sha256);
    fs.rmSync(this.filePath(sha256), { force: true });
  }
  backoffFile(sha256, attempts) {
    this.s.bumpFile.run(now() + backoffMs(attempts), sha256);
  }

  counts() {
    return {
      messages: this.s.cntMsgs.get().n,
      files: this.s.cntFiles.get().n,
      dead: this.s.cntDead.get().n,
    };
  }

  /** Xoa file mo coi (ghi file xong nhung chua kip insert DB khi crash). */
  gc() {
    const known = new Set(this.s.allFiles.all().map((r) => r.sha256));
    for (const name of fs.readdirSync(this.filesDir)) {
      const full = path.join(this.filesDir, name);
      const base = name.replace(/\.tmp$/, '');
      const old = now() - fs.statSync(full).mtimeMs > 3_600_000;
      if (old && (name.endsWith('.tmp') || !known.has(base))) fs.rmSync(full, { force: true });
    }
  }

  close() {
    this.db.close();
  }
}
