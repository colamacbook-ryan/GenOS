// Log JSON một dòng. Không bao giờ ghi nội dung tin nhắn, ảnh QR hay credential vào log.
export function createLogger(sink = (line) => process.stdout.write(`${line}\n`), base = {}) {
  const write = (level, msg, fields = {}) => {
    sink(JSON.stringify({ ts: new Date().toISOString(), level, msg, ...base, ...fields }));
  };
  return {
    info: (msg, fields) => write('INFO', msg, fields),
    warn: (msg, fields) => write('WARN', msg, fields),
    error: (msg, fields) => write('ERROR', msg, fields),
    child: (extra) => createLogger(sink, { ...base, ...extra }),
  };
}

export const silentLogger = {
  info() {}, warn() {}, error() {}, child() { return silentLogger; },
};
