import fs from 'node:fs';
import { Readable } from 'node:stream';
import { config } from './config.js';
import { sha256Hex, signHeaders } from './signer.js';

export class HttpError extends Error {
  constructor(status, body) {
    super(`HTTP ${status}: ${String(body).slice(0, 300)}`);
    this.status = status;
  }
}

export class CloudClient {
  async postJson(path, obj) {
    const body = Buffer.from(JSON.stringify(obj));
    const res = await fetch(config.cloudUrl + path, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...signHeaders({ method: 'POST', path, contentSha256: sha256Hex(body) }),
      },
      body,
      signal: AbortSignal.timeout(30_000),
    });
    const text = await res.text();
    if (!res.ok) throw new HttpError(res.status, text);
    return text ? JSON.parse(text) : {};
  }

  async putFile(sha256, filePath, size, mime) {
    const path = `/v1/files/${sha256}`;
    const res = await fetch(config.cloudUrl + path, {
      method: 'PUT',
      headers: {
        'content-type': 'application/octet-stream',
        'content-length': String(size),
        'x-mime': mime || '',
        ...signHeaders({ method: 'PUT', path, contentSha256: sha256 }),
      },
      body: Readable.toWeb(fs.createReadStream(filePath)),
      duplex: 'half',
      signal: AbortSignal.timeout(300_000),
    });
    const text = await res.text();
    if (!res.ok) throw new HttpError(res.status, text);
    return text ? JSON.parse(text) : {};
  }
}
