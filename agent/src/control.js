import { logger } from './log.js';

const log = logger('control');

/**
 * Kenh dieu khien voi Cloud:
 *  - reportAuth: gui ngay trang thai dang nhap / ma QR len Cloud de trang quan tri hien thi.
 *  - poll lenh (dang nhap lai, ghep noi, dang xuat) moi 5 giay va chuyen cho connector.
 * Loi mang chi ghi log, khong anh huong viec thu tin.
 */
export class Control {
  constructor({ client, getConnectors, pollMs = 5_000 }) {
    this.client = client;
    this.getConnectors = getConnectors;
    this.pollMs = pollMs;
    this.busy = false;
    this.pending = new Map(); // connector -> payload chua gui duoc
  }

  start() {
    this.timer = setInterval(() => this.tick(), this.pollMs);
  }

  stop() {
    clearInterval(this.timer);
  }

  reportAuth = (connector, payload) => {
    this.pending.set(connector, { connector, ...payload });
    this._flushAuth();
  };

  async _flushAuth() {
    for (const [name, payload] of [...this.pending]) {
      try {
        await this.client.postJson('/v1/auth-state', payload);
        if (this.pending.get(name) === payload) this.pending.delete(name);
      } catch (e) {
        log.warn(`gui trang thai ${name} that bai, se thu lai`, e.message);
      }
    }
  }

  async tick() {
    if (this.busy) return;
    this.busy = true;
    try {
      if (this.pending.size) await this._flushAuth();
      const res = await this.client.postJson('/v1/commands/poll', {});
      for (const cmd of res.commands || []) await this._run(cmd);
    } catch (e) {
      // Cloud chua chay hoac mat mang: bo qua, lan sau thu lai
    } finally {
      this.busy = false;
    }
  }

  async _run(cmd) {
    const c = this.getConnectors().find((x) => x.name === cmd.connector);
    let ok = false;
    let detail;
    try {
      if (!c?.command) throw new Error(`khong co connector ${cmd.connector}`);
      detail = await c.command(cmd.action, cmd.args || {});
      ok = true;
      log.info(`lenh ${cmd.connector}.${cmd.action}: ${detail}`);
    } catch (e) {
      detail = e.message;
      log.error(`lenh ${cmd.connector}.${cmd.action} loi`, e);
    }
    await this.client.postJson('/v1/commands/result', { id: cmd.id, ok, detail: String(detail ?? '').slice(0, 500) }).catch(() => {});
  }
}
