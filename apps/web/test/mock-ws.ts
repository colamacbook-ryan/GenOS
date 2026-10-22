/**
 * Minimal RFC 6455 server for the mock API's `/api/v1/ws` (text frames,
 * ping/pong, close codes). Enough for the Console's realtime client; not a
 * general WebSocket implementation. Node-only, used by vite --mode mock.
 */
import { createHash } from 'node:crypto';
import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';

const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

export interface MockSocket {
  id: number;
  /** Arbitrary per-connection data (the user's permissions). */
  meta: Record<string, unknown>;
  send: (text: string) => void;
  close: (code?: number, reason?: string) => void;
  open: boolean;
}

function frame(opcode: number, payload: Buffer): Buffer {
  const len = payload.length;
  let head: Buffer;
  if (len < 126) {
    head = Buffer.from([0x80 | opcode, len]);
  } else if (len < 65536) {
    head = Buffer.alloc(4);
    head[0] = 0x80 | opcode;
    head[1] = 126;
    head.writeUInt16BE(len, 2);
  } else {
    head = Buffer.alloc(10);
    head[0] = 0x80 | opcode;
    head[1] = 127;
    head.writeBigUInt64BE(BigInt(len), 2);
  }
  return Buffer.concat([head, payload]);
}

let seq = 0;

/**
 * Complete the handshake on `socket` and return a handle. `onText` gets
 * every text message from the client.
 */
export function acceptWebSocket(
  req: IncomingMessage,
  socket: Duplex,
  onText: (ws: MockSocket, text: string) => void,
  onClose: (ws: MockSocket) => void,
): MockSocket | null {
  const key = req.headers['sec-websocket-key'];
  if (typeof key !== 'string') {
    socket.destroy();
    return null;
  }
  const accept = createHash('sha1').update(key + GUID).digest('base64');
  socket.write(
    'HTTP/1.1 101 Switching Protocols\r\n' +
      'Upgrade: websocket\r\n' +
      'Connection: Upgrade\r\n' +
      `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
  );

  const ws: MockSocket = {
    id: ++seq,
    meta: {},
    open: true,
    send: (text) => {
      if (!ws.open) return;
      socket.write(frame(0x1, Buffer.from(text, 'utf8')));
    },
    close: (code = 1000, reason = '') => {
      if (!ws.open) return;
      ws.open = false;
      const r = Buffer.from(reason, 'utf8');
      const p = Buffer.alloc(2 + r.length);
      p.writeUInt16BE(code, 0);
      r.copy(p, 2);
      socket.write(frame(0x8, p));
      setTimeout(() => socket.end(), 50);
      onClose(ws);
    },
  };

  let buf = Buffer.alloc(0);
  socket.on('data', (chunk: Buffer) => {
    buf = Buffer.concat([buf, chunk]);
    while (buf.length >= 2) {
      const opcode = buf[0] & 0x0f;
      const masked = (buf[1] & 0x80) !== 0;
      let len = buf[1] & 0x7f;
      let off = 2;
      if (len === 126) {
        if (buf.length < 4) return;
        len = buf.readUInt16BE(2);
        off = 4;
      } else if (len === 127) {
        if (buf.length < 10) return;
        len = Number(buf.readBigUInt64BE(2));
        off = 10;
      }
      const maskLen = masked ? 4 : 0;
      if (buf.length < off + maskLen + len) return;
      const mask = masked ? buf.subarray(off, off + 4) : null;
      const payload = Buffer.from(buf.subarray(off + maskLen, off + maskLen + len));
      if (mask) for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];
      buf = buf.subarray(off + maskLen + len);
      if (opcode === 0x1) onText(ws, payload.toString('utf8'));
      else if (opcode === 0x8) {
        if (ws.open) {
          ws.open = false;
          socket.write(frame(0x8, payload.subarray(0, 2)));
          socket.end();
          onClose(ws);
        }
      } else if (opcode === 0x9) socket.write(frame(0xa, payload));
    }
  });
  const gone = () => {
    if (ws.open) {
      ws.open = false;
      onClose(ws);
    }
  };
  socket.on('close', gone);
  socket.on('error', gone);
  return ws;
}
