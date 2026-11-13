import 'dotenv/config';
import path from 'node:path';

const need = (name) => {
  const v = process.env[name];
  if (!v) throw new Error(`Thiếu biến môi trường ${name} (xem .env.example)`);
  return v;
};
const flag = (name, def) => (process.env[name] ?? String(def)).toLowerCase() === 'true';

export const config = {
  agentVersion: '0.2.0',
  cloudUrl: need('CLOUD_URL').replace(/\/+$/, ''),
  deviceId: need('DEVICE_ID'),
  deviceSecret: need('DEVICE_SECRET'),
  dataDir: path.resolve(process.env.DATA_DIR || './data'),
  enableWhatsapp: flag('ENABLE_WHATSAPP', true),
  enableZalo: flag('ENABLE_ZALO', true),
  maxFileBytes: Number(process.env.MAX_FILE_MB || 100) * 1024 * 1024,
  chromePath: process.env.CHROME_PATH || undefined,
  heartbeatMs: 60_000,
  flushMs: 5_000,
};
