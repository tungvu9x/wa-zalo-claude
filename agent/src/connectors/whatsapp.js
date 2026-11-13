import path from 'node:path';
import pkg from 'whatsapp-web.js';
import qrcode from 'qrcode-terminal';
import { logger } from '../log.js';
import { sha256Hex } from '../signer.js';
import { extFromMime } from './mime.js';

const { Client, LocalAuth } = pkg;
const log = logger('whatsapp');

/**
 * Chi nghe, khong gui gi, khong danh dau da doc, khong auto-reply.
 * Dung message_create de bat ca tin den lan tin do chinh may nay gui.
 * QR / ma ghep noi gui len Cloud de hien tren trang quan tri.
 */
export class WhatsAppConnector {
  name = 'whatsapp';

  constructor({ config, onMessage, reportAuth }) {
    this.config = config;
    this.onMessage = onMessage;
    this.reportAuth = reportAuth || (() => {});
    this.account = null;
    this.loggedOut = false;
    this.pairPhone = null;
    this.state = 'starting';
    this.detail = null;
    this.lastMessageAt = null;
    this.client = null;
    this.stopping = false;
    this.retryTimer = null;
  }

  status() {
    return { state: this.state, detail: this.detail, account: this.account, last_message_at: this.lastMessageAt };
  }

  _set(state, detail = null, extra = {}) {
    this.state = state;
    this.detail = detail;
    this.reportAuth(this.name, { state, detail, account: this.account, ...extra });
  }

  /** Lenh tu trang quan tri: relogin (QR moi), pair {phone} (ma 8 ky tu), logout. */
  async command(action, args = {}) {
    if (!['relogin', 'pair', 'logout'].includes(action)) throw new Error(`whatsapp khong ho tro lenh ${action}`);
    if (action === 'pair') {
      const phone = String(args.phone || '').replace(/\D/g, '');
      if (phone.length < 9) throw new Error('so dien thoai khong hop le (vd 84901234567)');
      this.pairPhone = phone;
    } else {
      this.pairPhone = null;
    }
    this.loggedOut = action === 'logout';
    clearTimeout(this.retryTimer);
    this.retryTimer = null;
    const old = this.client;
    this.client = null;
    // logout() go lien ket thiet bi tren dien thoai va xoa session LocalAuth
    if (old) {
      await old.logout().catch(() => {});
      await old.destroy().catch(() => {});
    }
    this.account = null;
    if (this.loggedOut) {
      this._set('logged_out', 'da dang xuat tren may nay');
      return 'da dang xuat';
    }
    this._set('starting', action === 'pair' ? 'dang lay ma ghep noi' : 'dang tao ma QR moi');
    this._boot();
    return 'dang khoi dong lai';
  }

  async start() {
    await this._boot();
  }

  async stop() {
    this.stopping = true;
    clearTimeout(this.retryTimer);
    await this.client?.destroy().catch(() => {});
  }

  async _boot() {
    if (this.stopping || this.loggedOut) return;
    const c = new Client({
      authStrategy: new LocalAuth({ dataPath: path.join(this.config.dataDir, 'wwebjs') }),
      ...(this.pairPhone ? { pairWithPhoneNumber: { phoneNumber: this.pairPhone, showNotification: true } } : {}),
      puppeteer: {
        headless: true,
        executablePath: this.config.chromePath,
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
      },
    });
    this.client = c;

    c.on('qr', (qr) => {
      if (this.client !== c) return;
      log.info('Quét QR bằng WhatsApp > Thiết bị đã liên kết (hoặc trên trang quản trị):');
      qrcode.generate(qr, { small: true });
      this._set('qr_needed', null, { qr: { kind: 'text', data: qr } });
    });
    c.on('code', (code) => {
      if (this.client !== c) return;
      log.info(`Mã ghép nối WhatsApp: ${code}`);
      this._set('pair_code', null, { pairing_code: code });
    });
    c.on('authenticated', () => {
      if (this.client !== c) return;
      this.pairPhone = null;
      this._set('authenticated');
      log.info('đã xác thực');
    });
    c.on('ready', () => {
      if (this.client !== c) return;
      const w = c.info?.wid?.user;
      this.account = [c.info?.pushname, w ? `+${w}` : null].filter(Boolean).join(' · ') || null;
      this._set('ready');
      log.info('sẵn sàng');
    });
    c.on('auth_failure', (m) => {
      if (this.client !== c) return;
      this._set('auth_failure', String(m));
      log.error('xác thực thất bại', m);
    });
    c.on('disconnected', (reason) => {
      if (this.client !== c) return;
      this._set('disconnected', String(reason));
      log.warn('mất kết nối', reason);
      this._restart();
    });
    c.on('message_create', (msg) => {
      this._handle(msg).catch((e) => log.error(`xử lý tin ${msg?.id?._serialized} lỗi`, e));
    });

    try {
      await c.initialize();
    } catch (e) {
      if (this.client !== c) return;
      this._set('init_failed', e.message);
      log.error('initialize lỗi', e);
      this._restart();
    }
  }

  _restart() {
    if (this.stopping || this.loggedOut || this.retryTimer) return;
    const old = this.client;
    this.retryTimer = setTimeout(async () => {
      this.retryTimer = null;
      await old?.destroy().catch(() => {});
      await this._boot();
    }, 30_000);
  }

  async _handle(msg) {
    if (msg.isStatus || msg.from === 'status@broadcast') return;
    if (!msg.body && !msg.hasMedia) return; // thong bao he thong, reaction...

    const chat = await msg.getChat();
    let senderName = null;
    try {
      const ct = await msg.getContact();
      senderName = ct.pushname || ct.name || ct.number || null;
    } catch {}

    const fromMe = Boolean(msg.fromMe);
    const selfId = this.client?.info?.wid?._serialized || 'me';
    const attachments = [];
    const files = [];

    if (msg.hasMedia) {
      try {
        const media = await msg.downloadMedia();
        if (!media) throw new Error('downloadMedia trả về null (media đã hết hạn?)');
        const buffer = Buffer.from(media.data, 'base64');
        const fallbackName = `${msg.type}-${msg.timestamp}.${extFromMime(media.mimetype)}`;
        const file_name = media.filename || fallbackName;
        if (buffer.length > this.config.maxFileBytes) {
          attachments.push({ file_name, mime: media.mimetype, size: buffer.length, sha256: null, error: 'vượt giới hạn dung lượng' });
        } else {
          const sha256 = sha256Hex(buffer);
          files.push({ sha256, buffer, mime: media.mimetype });
          attachments.push({ file_name, mime: media.mimetype, size: buffer.length, sha256 });
        }
      } catch (e) {
        attachments.push({ file_name: null, mime: null, size: null, sha256: null, error: `download: ${e.message}`.slice(0, 500) });
      }
    }

    this.lastMessageAt = new Date().toISOString();
    this.onMessage(
      {
        channel: 'whatsapp',
        external_id: msg.id._serialized,
        chat_id: chat.id._serialized,
        chat_name: chat.name || null,
        is_group: Boolean(chat.isGroup),
        sender_id: fromMe ? selfId : msg.author || msg.from,
        sender_name: fromMe ? null : senderName,
        direction: fromMe ? 'out' : 'in',
        body: msg.body || null,
        sent_at: new Date(msg.timestamp * 1000).toISOString(),
        raw: { type: msg.type, from: msg.from, to: msg.to, author: msg.author ?? null, hasQuotedMsg: Boolean(msg.hasQuotedMsg) },
        attachments,
      },
      files,
    );
  }
}
