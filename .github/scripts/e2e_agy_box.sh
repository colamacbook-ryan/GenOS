#!/usr/bin/env bash
# Kiểm hộp riêng Antigravity (agy-box) trên bản cài THẬT sau cài mới / sau `genh update` (v0.1.63, H3).
# Dùng từ e2e-install.yml (cài mới) và e2e-upgrade (nâng cấp từ bản cũ chưa có box).
#
# Biến môi trường:
#   GEN_HARNESS_HOME  gốc cài đặt (bắt buộc)      GH_PORT  cổng genh status (bắt buộc)
#   GENH              đường dẫn genh (mặc định $GEN_HARNESS_HOME/bin/genh)
#   GENH_COMPOSE_FILE compose genh thật sự dùng (chế độ pr); trống ⇒ $GEN_HARNESS_HOME/deploy/compose.yaml
#   MODE, TAG         chế độ (pr|release) và tag đang kiểm: tag < v0.1.63 chưa có box ⇒ bỏ qua; còn lại BẮT BUỘC có box.
#   AGY_BOX_WAIT_TRIES, AGY_BOX_WAIT_SLEEP  số lần/nghỉ giữa các lần chờ healthy (mặc định 30 × 5 giây)
#
# Kiểm: khoá gh_agy_key (32 byte base64, 0644) · agy-box running+healthy · trong box: /run/secrets chỉ có gh_agy_key,
# không biến DB/Redis/khoá, không mount claude_state/gh_objects/run/docker.sock, root read-only, không root, không capability ·
# box không phân giải được db/redis/bridge/browser-redis (không ở mạng default) · api gọi được /healthz của box và
# yêu cầu không ký bị từ chối 401. Không in giá trị bí mật.
set -euo pipefail

home="${GEN_HARNESS_HOME:?thiếu GEN_HARNESS_HOME}"
port="${GH_PORT:?thiếu GH_PORT}"
genh="${GENH:-$home/bin/genh}"
if [ -n "${GENH_COMPOSE_FILE:-}" ]; then compose="$GENH_COMPOSE_FILE"; else compose="$home/deploy/compose.yaml"; fi
secrets_dir="$(dirname "$compose")/../secrets"
tries="${AGY_BOX_WAIT_TRIES:-30}"
nap="${AGY_BOX_WAIT_SLEEP:-5}"

fail() { echo "::error::$*" >&2; exit 1; }

# 1. Bản này có box không? tag < v0.1.63 (chạy tay cho bản cũ) ⇒ bỏ qua; mọi trường hợp khác BẮT BUỘC.
require=1
if [ "${MODE:-release}" = "release" ] && [[ "${TAG:-}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+ ]]; then
  if [ "$(printf '%s\nv0.1.63\n' "$TAG" | sort -V | head -n1)" != "v0.1.63" ]; then require=0; fi
fi
if ! grep -q '^  agy-box:' "$compose"; then
  if [ "$require" = 0 ]; then echo "::notice::compose của ${TAG:-bản này} chưa có agy-box (< v0.1.63) — bỏ qua."; exit 0; fi
  fail "compose genh dùng ($compose) không có dịch vụ agy-box — bản ${TAG:-này} phải mang hộp riêng Antigravity."
fi

# 2. Khoá gh_agy_key do genh sinh (cài mới và cả nâng cấp từ bản cũ chưa có).
key="$secrets_dir/gh_agy_key"
[ -f "$key" ] || fail "thiếu $key — genh không tự sinh gh_agy_key."
python3 - "$key" <<'PY' || fail "gh_agy_key không phải 32 byte base64 không xuống dòng."
import base64, sys
raw = open(sys.argv[1], "rb").read()
assert raw == raw.strip() and len(base64.b64decode(raw, validate=True)) == 32
PY
[ "$(stat -c %a "$key")" = "644" ] || fail "gh_agy_key phải quyền 0644 (box chạy uid khác), đang $(stat -c %a "$key")."

# 3. agy-box running + healthy (chờ healthcheck qua start_period).
status=""
ok=0
for _ in $(seq 1 "$tries"); do
  status="$("$genh" status --install-dir "$home" --port "$port" 2>&1 || true)"
  if printf '%s\n' "$status" | awk '$1=="agy-box" && $2=="running" && $3=="healthy"{f=1} END{exit !f}'; then ok=1; break; fi
  sleep "$nap"
done
if [ "$ok" != 1 ]; then
  printf '%s\n' "$status" >&2
  cid_dbg="$(docker ps -aq --filter label=com.docker.compose.service=agy-box | head -n1)"
  [ -z "$cid_dbg" ] || docker logs --tail 80 "$cid_dbg" >&2 2>&1 || true
  fail "agy-box không running+healthy sau $((tries * nap)) giây."
fi
echo "OK: genh status — agy-box running healthy"

cid_of() { docker ps -q --filter "label=com.docker.compose.project=gen-harness" --filter "label=com.docker.compose.service=$1" | head -n1; }
box="$(cid_of agy-box)"
[ -n "$box" ] || fail "không thấy container agy-box."

# 4. Bên trong box (mỗi vi phạm một dòng FAIL; cuối cùng in OK).
read -r -d '' BOX_PROBE <<'PY' || true
import os, socket
bad = []
try:
    secrets = sorted(os.listdir("/run/secrets"))
except OSError:
    secrets = []
if secrets != ["gh_agy_key"]:
    bad.append("FAIL /run/secrets có %s (chỉ được gh_agy_key)" % secrets)
if os.path.exists("/run/secrets/gh_master_key"):
    bad.append("FAIL box thấy /run/secrets/gh_master_key")
FORBID = ("DATABASE", "REDIS", "MASTER", "BRIDGE", "BROWSER", "OFFSITE", "BACKUP", "SETUP_TOKEN", "POSTGRES")
def names(pairs):
    return sorted({n for n in pairs if any(w in n.upper() for w in FORBID)})
env_bad = names(os.environ)
try:
    raw = open("/proc/1/environ", "rb").read().split(b"\0")
    env_bad = sorted(set(env_bad) | set(names(x.split(b"=", 1)[0].decode("utf-8", "replace") for x in raw if x)))
except OSError:
    pass
if env_bad:
    bad.append("FAIL biến môi trường nhạy cảm trong box: %s" % env_bad)
mounts = set()
for line in open("/proc/self/mountinfo"):
    parts = line.split()
    if len(parts) > 4:
        mounts.add(parts[4])
# Review v0.1.63 (P1-1): hộp không mount volume nào — kể cả agy_state (token đến theo từng yêu cầu đã ký).
for m in ("/var/lib/gh/agy", "/var/lib/gh/claude", "/var/lib/gh/objects", "/var/lib/gh/host", "/var/run/docker.sock",
          "/run/docker.sock"):
    if m in mounts or os.path.exists(m) and m.endswith(".sock"):
        bad.append("FAIL box mount %s" % m)
try:
    open("/etc/gh-write-probe", "w").close()
    os.unlink("/etc/gh-write-probe")
    bad.append("FAIL root filesystem của box ghi được (phải read_only)")
except OSError:
    pass
if os.getuid() == 0:
    bad.append("FAIL box chạy bằng root")
for line in open("/proc/self/status"):
    if line.startswith("CapEff:") and int(line.split()[1], 16) != 0:
        bad.append("FAIL box còn capability (CapEff=%s)" % line.split()[1])
# Gói sửa review v0.1.63 (P2-2): hộp chỉ ở mạng internal — không ra thẳng Internet; ra qua proxy agy-egress, proxy chặn
# đích IP nội bộ (gateway Docker 172.17.0.1, LAN).
if os.environ.get("HTTPS_PROXY") != "http://agy-egress:3128":
    bad.append("FAIL box thiếu HTTPS_PROXY=http://agy-egress:3128")
try:
    socket.create_connection(("1.1.1.1", 443), 3).close()
    bad.append("FAIL box ra thẳng Internet được (phải qua proxy agy-egress)")
except OSError:
    pass
try:
    s = socket.create_connection(("agy-egress", 3128), 5)
    s.sendall(b"CONNECT 172.17.0.1:443 HTTP/1.1\r\nHost: 172.17.0.1:443\r\n\r\n")
    first = s.recv(64).split(b"\r\n", 1)[0]
    s.close()
    if b" 403 " not in first + b" ":
        bad.append("FAIL proxy agy-egress không chặn CONNECT tới gateway 172.17.0.1 (%r)" % first)
except OSError as e:
    bad.append("FAIL box không nối được proxy agy-egress: %s" % type(e).__name__)
for host in ("db", "redis", "bridge", "browser-redis"):
    try:
        socket.gethostbyname(host)
        bad.append("FAIL box phân giải được %s — đang ở chung mạng với dịch vụ lõi" % host)
    except OSError:
        pass
print("\n".join(bad) if bad else "OK")
PY
out="$(docker exec "$box" python3 -c "$BOX_PROBE" 2>&1)" || fail "không chạy được kiểm tra trong agy-box: $out"
printf '%s\n' "$out"
if printf '%s\n' "$out" | grep -q '^FAIL'; then fail "agy-box không cô lập đúng cấu hình (xem dòng FAIL ở trên)."; fi
printf '%s\n' "$out" | grep -qx 'OK' || fail "kiểm tra trong agy-box không trả OK: $out"
echo "OK: trong agy-box — không khoá master/DB/Redis, root read-only, không đặc quyền, không thấy mạng lõi"

# 5. api (cùng mạng agy-bus) gọi được box; yêu cầu không ký bị từ chối.
api="$(cid_of api)"
[ -n "$api" ] || fail "không thấy container api."
read -r -d '' API_PROBE <<'PY' || true
import urllib.error, urllib.request
try:
    ok = urllib.request.urlopen("http://agy-box:8090/healthz", timeout=5).status == 200
except Exception as e:
    ok = False
    print("FAIL api không gọi được agy-box:8090/healthz: %s" % type(e).__name__)
if ok:
    req = urllib.request.Request("http://agy-box:8090/v1/models", data=b"{}", method="POST")
    try:
        urllib.request.urlopen(req, timeout=5)
        print("FAIL yêu cầu KHÔNG ký được box chấp nhận")
    except urllib.error.HTTPError as e:
        print("OK" if e.code == 401 else "FAIL yêu cầu không ký trả %d (phải 401)" % e.code)
    except Exception as e:
        print("FAIL yêu cầu không ký: %s" % type(e).__name__)
PY
out="$(docker exec "$api" python3 -c "$API_PROBE" 2>&1)" || fail "không chạy được kiểm tra trong api: $out"
printf '%s\n' "$out"
printf '%s\n' "$out" | grep -qx 'OK' || fail "api ↔ agy-box không đúng hợp đồng: $out"
echo "OK: api gọi được agy-box; yêu cầu không ký bị 401"
