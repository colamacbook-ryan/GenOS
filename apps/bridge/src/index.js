// Gen-Harness bridge: tiến trình duy nhất nói chuyện với Zalo (zca-js) và WhatsApp (Baileys).
// Không truy cập PostgreSQL, không gọi LLM, không tự trả lời. Giao thức: docs/api/bridge-protocol.md.
import http from 'node:http';
import { createClient } from 'redis';
import { CHANNELS, VERSION, loadConfig } from './config.js';
import { Keyring } from './crypto.js';
import { STREAMS } from './envelope.js';
import { CONTROL_STREAM, OUTBOUND_STREAM, RedisBus } from './bus.js';
import { SessionManager } from './sessions.js';
import { createLogger } from './log.js';
import { ZaloAdapter, loadZaloLib } from './channels/zalo.js';
import { WhatsAppAdapter, loadWhatsAppLib } from './channels/whatsapp.js';

const cfg = loadConfig();
const log = createLogger();
const keyring = new Keyring(cfg.bridgeKey);
const state = { startedAt: new Date().toISOString(), redis: 'down' };

const redis = createClient({ url: cfg.redisUrl });
const reader = redis.duplicate();
for (const [name, c] of [['main', redis], ['reader', reader]]) {
  c.on('error', (err) => {
    if (name === 'main') state.redis = 'down';
    log.error('redis lỗi', { client: name, error: err.message });
  });
}
redis.on('ready', () => { state.redis = 'ok'; });

const bus = new RedisBus({ redis, reader, maxlen: cfg.streamMaxlen, consumer: cfg.consumer, logger: log });

// Thư viện kênh nạp lười một lần; lỗi nạp → kênh đó trả session.ended error.
const libs = {};
async function loadLibs() {
  const [z, w] = await Promise.allSettled([loadZaloLib(), loadWhatsAppLib()]);
  if (z.status === 'fulfilled') libs.zalo = z.value; else log.error('không nạp được zca-js', { error: z.reason?.message });
  if (w.status === 'fulfilled') libs.whatsapp = w.value; else log.error('không nạp được Baileys', { error: w.reason?.message });
}

function createAdapter(channel, opts) {
  if (!libs[channel]) throw new Error(`CHANNEL_UNAVAILABLE:${channel}`);
  return channel === 'zalo'
    ? new ZaloAdapter({ lib: libs.zalo, directoryGapMs: cfg.directoryGapMs, ...opts })
    : new WhatsAppAdapter({ lib: libs.whatsapp, ...opts });
}

const manager = new SessionManager({
  bus, keyring, createAdapter, channels: CHANNELS, logger: log, orgId: cfg.orgId,
  heartbeatMs: cfg.heartbeatMs, directoryMs: cfg.directoryMs,
});

const server = http.createServer((req, res) => {
  if (req.url === '/health') {
    const ok = state.redis === 'ok';
    res.writeHead(ok ? 200 : 503, { 'content-type': 'application/json' });
    res.end(JSON.stringify({
      status: ok ? 'ok' : 'degraded',
      version: VERSION,
      started_at: state.startedAt,
      redis: state.redis,
      send: keyring.enabled ? 'enabled' : 'disabled',
      channels: CHANNELS.filter((c) => libs[c]),
      sessions: manager.health(),
    }));
    return;
  }
  res.writeHead(404).end();
});

// Thư viện kênh đôi khi để lọt promise bị từ chối: ghi log (không kèm nội dung) thay vì làm sập tiến trình.
process.on('unhandledRejection', (err) => log.error('promise bị từ chối không xử lý', { error: err?.name || String(err).slice(0, 80) }));

async function main() {
  if (!keyring.enabled) log.warn('chưa có khoá bridge: từ chối mọi message.send và mọi đăng nhập', { error: cfg.bridgeKeyError });
  await Promise.all([redis.connect(), reader.connect()]);
  await loadLibs();
  server.listen(cfg.port, () => log.info('bridge nghe cổng', { port: cfg.port, version: VERSION }));

  await bus.publish(STREAMS.status, 'bridge.hello', { channels: CHANNELS, version: VERSION }, { actor: 'bridge', orgId: cfg.orgId })
    .catch((err) => log.error('không gửi được bridge.hello', { error: err.message }));

  await manager.heartbeat().catch(() => {});
  const timer = setInterval(() => manager.heartbeat().catch((e) => log.error('heartbeat lỗi', { error: e.message })), cfg.heartbeatMs);

  const consuming = bus.consume({
    [CONTROL_STREAM]: (evt) => manager.handleControl(evt),
    [OUTBOUND_STREAM]: (evt) => manager.handleOutbound(evt),
  }).catch((err) => {
    log.error('vòng đọc stream dừng', { error: err.message });
    process.exit(1);
  });

  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    log.info('bridge dừng');
    clearInterval(timer);
    bus.stop();
    manager.shutdown();
    server.close();
    await Promise.race([consuming, new Promise((r) => { setTimeout(r, 6000); })]);
    await Promise.allSettled([redis.quit(), reader.quit()]);
    process.exit(0);
  };
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
}

main().catch((err) => {
  log.error('bridge không khởi động được', { error: err.message });
  process.exit(1);
});
