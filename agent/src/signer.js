import crypto from 'node:crypto';
import { config } from './config.js';

export const sha256Hex = (buf) => crypto.createHash('sha256').update(buf).digest('hex');

// message = "{ts}\n{METHOD}\n{path}\n{sha256 noi dung}", khop voi cloud/app/security.py
export function signHeaders({ method, path, contentSha256 }) {
  const ts = Math.floor(Date.now() / 1000).toString();
  const signature = crypto
    .createHmac('sha256', config.deviceSecret)
    .update(`${ts}\n${method}\n${path}\n${contentSha256}`)
    .digest('hex');
  return {
    'x-device-id': config.deviceId,
    'x-timestamp': ts,
    'x-signature': signature,
    'x-content-sha256': contentSha256,
  };
}
