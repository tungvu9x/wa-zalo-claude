import fs from 'node:fs';
import path from 'node:path';
import { Zalo, ThreadType, LoginQRCallbackEventType } from 'zca-js';
import { logger } from '../log.js';
import { sha256Hex } from '../signer.js';
import { extFromMime } from './mime.js';

const log = logger('zalo');

// msgType cua Zalo co file dinh kem can tai ve
const MEDIA_TYPES = new Set(['chat.photo', 'share.file', 'chat.voice', 'chat.gif']);

/**
 * Zalo ca nhan qua zca-js (unofficial). Chi nghe.
 * Dang nhap: cookie da luu; het han thi tu chuyen sang QR. Anh QR gui len Cloud de hien
 * tren trang quan tri, dong thoi ghi ra data/zalo-qr.png.
 * Moi truong hop khong tai duoc file deu ghi thanh attachment co error, khong mat tin.
 */
export class ZaloConnector {
  name = 'zalo';

  constructor({ config, onMessage, reportAuth }) {
    this.config = config;
    this.onMessage = onMessage;
    this.reportAuth = reportAuth || (() => {});
    this.state = 'starting';
    this.detail = null;
    this.account = null;
    this.lastMessageAt = null;
    this.api = null;
    this.groupNames = new Map();
    this.credPath = path.join(config.dataDir, 'zalo-credentials.json');
    this.qrPath = path.join(config.dataDir, 'zalo-qr.png');
    this.stopping = false;
    this.loggingIn = false;
  }

  status() {
    return { state: this.state, detail: this.detail, account: this.account, last_message_at: this.lastMessageAt };
  }

  _set(state, detail = null, extra = {}) {
    this.state = state;
    this.detail = detail;
    this.reportAuth(this.name, { state, detail, account: this.account, ...extra });
  }

  async start() {
    await this._login();
  }

  async _login() {
    if (this.loggingIn || this.stopping) return;
    this.loggingIn = true;
    try {
      const zalo = new Zalo({ selfListen: true, checkUpdate: false, logging: false });
      let api = null;
      if (fs.existsSync(this.credPath)) {
        try {
          api = await zalo.login(JSON.parse(fs.readFileSync(this.credPath, 'utf8')));
        } catch (e) {
          log.warn('cookie Zalo khong con hieu luc, chuyen sang dang nhap QR', e.message);
          fs.rmSync(this.credPath, { force: true });
        }
      }
      if (!api) api = await this._loginQR(zalo);
      this.api = api;
      await this._loadAccount();
      this._listen();
    } catch (e) {
      this._set('login_failed', e.message);
      log.error('dang nhap Zalo loi', e);
      // thu lai sau 60 giay, tranh vong lap qua nhanh
      setTimeout(() => this._login(), 60_000);
    } finally {
      this.loggingIn = false;
    }
  }

  _loginQR(zalo) {
    this._set('qr_needed', 'dang tao ma QR');
    return zalo.loginQR({ qrPath: this.qrPath }, (ev) => {
      switch (ev.type) {
        case LoginQRCallbackEventType.QRCodeGenerated:
          ev.actions.saveToFile(this.qrPath).catch(() => {});
          log.info(`Quet ma QR Zalo tren trang quan tri hoac file ${this.qrPath}`);
          this._set('qr_needed', null, { qr: { kind: 'png', data: ev.data.image } });
          break;
        case LoginQRCallbackEventType.QRCodeExpired:
          this._set('qr_needed', 'ma QR het han, dang tao ma moi');
          ev.actions.retry();
          break;
        case LoginQRCallbackEventType.QRCodeScanned:
          this._set('qr_scanned', `${ev.data?.display_name || ''} da quet, xac nhan tren dien thoai`.trim());
          break;
        case LoginQRCallbackEventType.QRCodeDeclined:
          this._set('qr_needed', 'dang nhap bi tu choi tren dien thoai, tao ma moi');
          ev.actions.retry();
          break;
        case LoginQRCallbackEventType.GotLoginInfo:
          fs.writeFileSync(
            this.credPath,
            JSON.stringify({ imei: ev.data.imei, userAgent: ev.data.userAgent, cookie: ev.data.cookie }),
            { mode: 0o600 },
          );
          fs.rmSync(this.qrPath, { force: true });
          log.info('da luu credentials Zalo');
          break;
      }
    });
  }

  async _loadAccount() {
    try {
      const info = await this.api.fetchAccountInfo();
      this.account = info?.profile?.displayName || info?.profile?.zaloName || null;
    } catch {
      this.account = null;
    }
  }

  _listen() {
    const l = this.api.listener;
    l.on('connected', () => {
      this._set('ready');
      log.info('san sang');
    });
    l.on('message', (m) => {
      this._handle(m).catch((e) => log.error('xu ly tin loi', e));
    });
    l.on('disconnected', (code, reason) => {
      this._set('disconnected', `${code} ${reason ?? ''}`.trim());
      log.warn('mat ket noi', this.detail);
    });
    l.on('closed', (code, reason) => {
      if (this.stopping) return;
      this._set('closed', `${code} ${reason ?? ''}`.trim());
      log.warn('listener dong', this.detail);
    });
    l.on('error', (e) => log.error('listener loi', e));
    l.start({ retryOnClose: true });
    this._set('connecting');
  }

  _stopListener() {
    try {
      this.api?.listener?.stop();
    } catch {}
    this.api = null;
  }

  /** Lenh tu trang quan tri. */
  async command(action) {
    if (action === 'relogin') {
      this._stopListener();
      fs.rmSync(this.credPath, { force: true });
      this.account = null;
      this._login();
      return 'dang tao ma QR moi';
    }
    if (action === 'logout') {
      this._stopListener();
      fs.rmSync(this.credPath, { force: true });
      this.account = null;
      this._set('logged_out', 'da dang xuat tren may nay');
      return 'da dang xuat';
    }
    throw new Error(`zalo khong ho tro lenh ${action}`);
  }

  async stop() {
    this.stopping = true;
    this._stopListener();
  }

  async _groupName(threadId) {
    if (this.groupNames.has(threadId)) return this.groupNames.get(threadId);
    let name = null;
    try {
      const info = await this.api.getGroupInfo(threadId);
      name = info?.gridInfoMap?.[threadId]?.name ?? null;
    } catch {}
    this.groupNames.set(threadId, name);
    return name;
  }

  async _download(href) {
    const res = await fetch(href, { signal: AbortSignal.timeout(60_000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const len = Number(res.headers.get('content-length') || 0);
    if (len > this.config.maxFileBytes) return { tooBig: true, size: len };
    const buffer = Buffer.from(await res.arrayBuffer());
    if (buffer.length > this.config.maxFileBytes) return { tooBig: true, size: buffer.length };
    return { buffer, mime: res.headers.get('content-type')?.split(';')[0] || null };
  }

  async _handle(m) {
    const d = m.data;
    if (!d) return;
    const isGroup = m.type === ThreadType.Group;
    const threadId = String(m.threadId);
    const content = d.content;
    const msgType = String(d.msgType || '');
    let body = null;
    const attachments = [];
    const files = [];

    if (typeof content === 'string') {
      body = content;
    } else if (content && typeof content === 'object') {
      if (MEDIA_TYPES.has(msgType) && typeof content.href === 'string') {
        let params = {};
        try {
          params = typeof content.params === 'string' ? JSON.parse(content.params) : content.params || {};
        } catch {}
        const title = typeof content.title === 'string' ? content.title : null;
        try {
          const got = await this._download(content.href);
          if (got.tooBig) {
            attachments.push({ file_name: title, mime: null, size: got.size, sha256: null, error: 'vượt giới hạn dung lượng' });
          } else {
            const ext = params.fileExt || extFromMime(got.mime);
            const file_name = title && /\.[A-Za-z0-9]{1,5}$/.test(title) ? title : `${title || msgType}-${d.msgId}.${ext}`;
            const sha256 = sha256Hex(got.buffer);
            files.push({ sha256, buffer: got.buffer, mime: got.mime });
            attachments.push({ file_name, mime: got.mime, size: got.buffer.length, sha256 });
          }
        } catch (e) {
          attachments.push({ file_name: title, mime: null, size: null, sha256: null, error: `download: ${e.message}`.slice(0, 500) });
        }
      } else {
        body = `[${msgType}] ${[content.title, content.href].filter((x) => typeof x === 'string').join(' ')}`.trim();
      }
    }
    if (body === null && attachments.length === 0) return;

    const chat_name = isGroup ? await this._groupName(threadId) : m.isSelf ? null : d.dName || null;
    this.lastMessageAt = new Date().toISOString();
    this.onMessage(
      {
        channel: 'zalo',
        external_id: `${threadId}:${d.msgId}`,
        chat_id: threadId,
        chat_name,
        is_group: isGroup,
        sender_id: d.uidFrom != null ? String(d.uidFrom) : null,
        sender_name: m.isSelf ? null : d.dName || null,
        direction: m.isSelf ? 'out' : 'in',
        body,
        sent_at: new Date(Number(d.ts) || Date.now()).toISOString(),
        raw: { msgType, cliMsgId: d.cliMsgId ?? null },
        attachments,
      },
      files,
    );
  }
}
