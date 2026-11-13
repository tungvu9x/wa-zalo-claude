import fs from 'node:fs';
import { config } from './config.js';
import { logger } from './log.js';
import { Outbox } from './outbox.js';
import { CloudClient } from './cloud-client.js';
import { Sync } from './sync.js';
import { Control } from './control.js';
import { WhatsAppConnector } from './connectors/whatsapp.js';
import { ZaloConnector } from './connectors/zalo.js';

const log = logger('agent');
fs.mkdirSync(config.dataDir, { recursive: true });

const outbox = new Outbox(config.dataDir);
const connectors = [];
const client = new CloudClient();
const sync = new Sync({ outbox, client, getConnectors: () => connectors });
const control = new Control({ client, getConnectors: () => connectors });

// Ghi ben vung truoc, gui sau: connector khong bao gio doi mang
const onMessage = (payload, files) => {
  try {
    outbox.enqueue(payload, files);
    sync.kick();
  } catch (e) {
    log.error(`không ghi được tin ${payload.external_id} vào outbox`, e);
  }
};

process.on('unhandledRejection', (e) => log.error('unhandledRejection', e));
process.on('uncaughtException', (e) => log.error('uncaughtException', e));

// Khong await: Zalo co the dang cho quet QR, WhatsApp van phai chay song song
function startConnector(c) {
  connectors.push(c);
  c.start().catch((e) => log.error(`${c.name} khởi động lỗi, các kênh khác vẫn chạy`, e));
}

async function shutdown(sig) {
  log.info(`nhận ${sig}, đang dừng`);
  sync.stop();
  control.stop();
  await Promise.allSettled(connectors.map((c) => c.stop?.()));
  outbox.close();
  process.exit(0);
}
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));

log.info(`agent ${config.agentVersion} khởi động, cloud=${config.cloudUrl}, queue=${JSON.stringify(outbox.counts())}`);
sync.start();
control.start();
const reportAuth = control.reportAuth;
if (config.enableWhatsapp) startConnector(new WhatsAppConnector({ config, onMessage, reportAuth }));
if (config.enableZalo) startConnector(new ZaloConnector({ config, onMessage, reportAuth }));
