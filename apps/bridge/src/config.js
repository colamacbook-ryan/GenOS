// Cấu hình bridge từ biến môi trường. Không đọc/ghi file nào ngoài GH_BRIDGE_KEY_FILE (chỉ đọc).
import { readFileSync } from 'node:fs';
import os from 'node:os';

export const VERSION = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version;
export const CHANNELS = ['zalo', 'whatsapp'];

/**
 * Đọc khoá bridge (32 byte base64). Trả { key: Buffer|null, error: string|null }.
 * Không có khoá → bridge vẫn chạy nhưng từ chối gửi tin và không phát credential.
 */
export function loadBridgeKey(env = process.env) {
  let raw = env.GH_BRIDGE_KEY || '';
  if (env.GH_BRIDGE_KEY_FILE) {
    try {
      raw = readFileSync(env.GH_BRIDGE_KEY_FILE, 'utf8');
    } catch (err) {
      return { key: null, error: `BRIDGE_KEY_FILE_UNREADABLE: ${err.code || err.message}` };
    }
  }
  raw = raw.trim();
  if (!raw) return { key: null, error: 'BRIDGE_KEY_MISSING' };
  const key = Buffer.from(raw, 'base64');
  if (key.length !== 32) return { key: null, error: 'BRIDGE_KEY_INVALID' };
  return { key, error: null };
}

export function loadConfig(env = process.env) {
  const { key, error } = loadBridgeKey(env);
  return {
    redisUrl: env.GH_REDIS_URL || 'redis://localhost:6379/0',
    port: Number(env.GH_BRIDGE_PORT || 3100),
    streamMaxlen: Number(env.GH_STREAM_MAXLEN || 100000),
    orgId: env.GH_ORG_ID || '',
    consumer: env.GH_BRIDGE_CONSUMER || os.hostname(),
    heartbeatMs: Number(env.GH_BRIDGE_HEARTBEAT_MS || 15000),
    // Chu kỳ quét danh bạ + thành viên mọi nhóm: 2 giờ (nhóm mới được báo ngay qua group.discovered, xem sessions.js).
    directoryMs: Number(env.GH_BRIDGE_DIRECTORY_MS || 2 * 60 * 60 * 1000),
    // Giãn cách giữa hai lần hỏi nền tảng khi quét thành viên (ms).
    directoryGapMs: Number(env.GH_BRIDGE_DIRECTORY_GAP_MS || 1000),
    bridgeKey: key,
    bridgeKeyError: error,
  };
}
