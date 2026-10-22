# Giao thức Bridge ↔ lõi (Redis Streams)

Bridge (`apps/bridge`, Node 20) là tiến trình duy nhất nói chuyện với Zalo (`zca-js`) và WhatsApp (Baileys). Nó **không** truy cập PostgreSQL, **không** gọi LLM, **không** tự quyết định trả lời. Mọi trao đổi với lõi đi qua Redis Streams theo envelope chuẩn (`src/envelope.js`, khớp `gh/chassis/bus.py`):

```
event_id (UUIDv7) · type · org_id · correlation_id · actor ("bridge:<channel>") · occurred_at (ISO) · schema_version (1) · payload (JSON)
```

Bridge đọc stream bằng consumer group `bridge` (tên consumer = hostname), ack sau khi xử lý.

## Khoá bridge

Secret riêng `gh_bridge_key` (32 byte base64; `GH_BRIDGE_KEY_FILE` hoặc `GH_BRIDGE_KEY`), chung cho api, worker, bridge. Khoá master **không** được đưa cho bridge.

- **Mã hoá phiên khi truyền**: `AES-256-GCM(key = sha256(bridge_key ‖ "transport"))`, chuỗi `base64(nonce[12] ‖ ciphertext ‖ tag[16])`, AAD = `"<channel>:<session_id>"`. Lõi giải mã rồi mã hoá phong bì bằng khoá master vào `core.channel_sessions.credential_enc`; bridge chỉ giữ phiên trong RAM.
- **Permit gửi tin**: `base64url(JSON claims) + "." + base64url(HMAC-SHA256(key = sha256(bridge_key ‖ "permit"), phần trước dấu chấm))`.
  Claims: `{"nonce", "draft_id", "channel", "thread_id", "thread_type": "group"|"user", "body_sha256" (hex của text UTF-8), "exp" (epoch giây)}`.
  Bridge chỉ gửi khi: chữ ký đúng (so sánh hằng thời gian) · `exp` chưa qua · `channel`, `thread_id`, `thread_type` khớp lệnh · `sha256(text)` khớp · `SET gh:permit:used:<nonce> 1 NX EX 600` thành công (dùng một lần). Sai bất kỳ điều gì → không gửi, trả `send.result` `ok:false` kèm `error` (`PERMIT_INVALID|PERMIT_EXPIRED|PERMIT_MISMATCH|PERMIT_REUSED`).
- **Permit tự gửi (v0.1.72, thiết kế v0.1.71 §4.1)** — chỉ ba trường hợp Sếp duyệt (Luật cứng), phát duy nhất từ `send_auto` của lõi.
  Claims: `{"nonce", "auto_send_id", "basis": "owner_dm"|"owner_tag"|"support", "channel", "thread_id", "thread_type", "body_sha256", "exp"}` (không có `draft_id`).
  Ngoài các kiểm trên, bridge kiểm **lớp 2** theo khoá Redis lõi ghi, TRƯỚC khi tiêu nonce: phiên có `gh:bridge:account_kind:<kênh>:<phiên>` ≠ `bot` (hoặc chưa có khoá) ⇒ từ chối mọi claims có `basis`;
  `owner_dm` đòi `thread_type='user'` và `thread_id ∈ gh:bridge:owner_thread:<kênh>`; `owner_tag` đòi `thread_type='group'` và `thread_id ∈ gh:bridge:listen:<kênh>`;
  `support` đòi `thread_type='group'` và `thread_id ∈ gh:bridge:support:<kênh>`. Sai ⇒ `PERMIT_MISMATCH`; `basis` lạ ⇒ `PERMIT_MISMATCH`; không đọc được Redis ⇒ `SCOPE_CHECK_FAILED`.
  Claims **không** `basis` là permit bản nháp (đã qua Xác nhận + mã PIN) và **bắt buộc** `draft_id` (chuỗi khác rỗng hoặc số) — thiếu ⇒ `PERMIT_INVALID`.

## Lõi → bridge

`gh.bridge.control`
| type | payload |
|---|---|
| `session.login` | `{channel, session_id, credential: "<transport>"\|null}` — có credential: đăng nhập lại bằng phiên đã lưu; hỏng/hết hạn → `session.ended reason=expired`. `null`: sinh QR mới. |
| `session.logout` | `{channel, session_id}` — đăng xuất trên nền tảng, xoá phiên khỏi RAM, trả `session.ended reason=logged_out`. |
| `session.close` | `{channel, session_id}` — (v0.1.62) ĐÓNG kết nối trong RAM của phiên cũ, không đăng xuất nền tảng, không phát `session.ended`. Lõi gửi khi một phiên mới của cùng kênh thành `active` (hoặc khi Sếp tạo QR mới huỷ phiên chờ QR cũ), để bridge không giữ kết nối ma. |
| `directory.sync` | `{channel, session_id}` — đồng bộ nhóm ngay. |

`gh.bridge.outbound`
| type | payload |
|---|---|
| `message.send` | `{channel, session_id, thread_id, thread_type, text, permit}` |

## Bridge → lõi

`gh.bridge.status`
| type | payload |
|---|---|
| `bridge.hello` | `{channels: ["zalo","whatsapp"], version}` — khi khởi động; lõi trả `session.login` kèm credential cho phiên đang `active` VÀ phiên `error` còn credential (mất mạng, bị mở nơi khác) — miễn chưa có phiên nào mới hơn của kênh. Nút «Nối lại bằng phiên đã lưu» (`POST /channels/{type}/resume`) gửi đúng lệnh này. |
| `session.qr` | `{channel, session_id, image: "data:image/png;base64,…", expires_at}` — mỗi QR mới (QR hết hạn → bridge tự xin QR mới, tối đa 5 lần rồi `session.ended reason=expired`). WhatsApp: khi máy chủ gửi `companion_reg_refresh` cho thiết bị chưa ghép cặp, bridge luôn đổi khoá ghép cặp; nếu đã có mã đang hiện thì gửi lại ĐÚNG mã đó (cùng `expires_at`, không tính vào 5 mã), chưa có mã thì chỉ đổi khoá (mã đầu mang khoá mới). Mã WhatsApp dày (≈ 57 ô, mức sửa lỗi L, vùng trắng 4 ô, ảnh 640px) nên Console vẽ ảnh lớn: tới 320px, co theo bề rộng thẻ (không dùng khối 88px như Zalo). |
| `session.scanned` | `{channel, session_id, display_name?}` |
| `session.active` | `{channel, session_id, account: {id, name, phone?}, credential: "<transport>"}` |
| `session.reconnecting` | `{channel, session_id, error: "NETWORK_UNAVAILABLE"}` — (v0.1.62) mất mạng / máy chủ nền tảng chưa tới: bridge GIỮ phiên (credential còn nguyên) và tự thử nối lại không giới hạn (lùi tối đa 60 giây); báo sau 2 lần hụt liên tiếp. Chỉ khi nền tảng trả 401 / đăng xuất (`loggedOut`) mới `session.ended reason=expired`. Lõi đặt phiên `error` (giữ credential, `ended_at` rỗng); nối lại được thì bridge gửi lại `session.active`. Trong lúc này bridge ngừng `heartbeat` của phiên. |
| `session.credential` | `{channel, session_id, credential}` — phiên được làm mới (Baileys `creds.update`, gộp tối đa 1 lần / 5 giây). |
| `session.ended` | `{channel, session_id, reason: "expired"\|"logged_out"\|"error", error?}` — `error` là mã ngắn, Console đổi sang câu tiếng Việt thân thiện + «Chi tiết kỹ thuật»: `QR_EXPIRED` · `PAIRING_REJECTED` (401 khi đang ghép bằng QR) · `WA_NETWORK` / `WA_NO_QR_<mã>` (socket WhatsApp đóng 4 lần liên tiếp, cách nhau 2s·4s·8s, trước khi ra mã QR đầu tiên; riêng 403 không thử lại mà kết thúc ngay bằng `FORBIDDEN`) · `CREDENTIAL_REJECTED\|INVALID\|UNREADABLE` · `LOGGED_OUT_REMOTELY` · `CONNECTION_REPLACED` · `FORBIDDEN` · `MULTIDEVICE_MISMATCH` · `RECONNECT_FAILED` · `BRIDGE_KEY_MISSING` · `LOGIN_FAILED` · `NO_LOGIN_INFO` (Zalo quét xong nhưng không có thông tin đăng nhập) · `CHANNEL_UNAVAILABLE` (bridge không tạo được adapter của kênh). |
| `heartbeat` | `{channel, session_id, latency_ms, queued}` — mỗi 15 giây cho mỗi phiên đang hoạt động. Ngoài ra bridge đặt `gh:bridge:heartbeat` (TTL 45s) như giai đoạn 1. |
| `send.result` | `{nonce, draft_id, auto_send_id?, channel, ok, external_msg_id?, error?}` — `auto_send_id` chỉ có khi permit là permit tự gửi (v0.1.72). |

`gh.bridge.directory`
| type | payload |
|---|---|
| `groups` | `{channel, session_id, groups_complete, groups: [{external_id, name, member_count, members_complete, members: [{external_id, name, phone?, role?}]}]}` — khi đăng nhập xong, mỗi 2 giờ (`GH_BRIDGE_DIRECTORY_MS`), sau khi phát hiện nhóm mới (cách lần quét trước ≥ 10 phút) và khi có `directory.sync` (nút «Cập nhật danh sách nhóm»; hai lệnh chạm nhau dùng chung một lượt quét). Thu thành viên của **mọi** nhóm, kể cả nhóm «Không nghe» và nhóm chờ chọn (Sếp chốt Q5=2), giãn cách `GH_BRIDGE_DIRECTORY_GAP_MS` (1 giây) giữa các lần hỏi nền tảng. Nền tảng trả lỗi ⇒ bridge DỪNG cả lượt (không hỏi tiếp), gửi phần đã quét với `groups_complete=false` và lùi lịch: gấp đôi chu kỳ mỗi lần lỗi liên tiếp, tối đa 8 giờ; một lượt đủ thì về chu kỳ gốc. Giãn cách thật: mọi lượt quét (chu kỳ, nhóm mới, nối lại sau mất mạng) cách lúc BẮT ĐẦU lượt trước ≥ 10 phút (kể cả lượt lỗi), trừ lần đầu sau đăng nhập; đang lùi lịch vì lỗi thì nhóm mới / nối lại chờ hết chu kỳ lùi; nút «Cập nhật danh sách nhóm» (`directory.sync`) cách lượt trước ≥ 5 phút, bấm sớm hơn bị bỏ qua. `role` = `admin`\|`member` khi nền tảng cho biết (Zalo `creatorId`/`adminIds`, WhatsApp `participant.admin`), bỏ trống nếu không biết. `members_complete` = nhận đủ số thành viên nền tảng báo; `groups_complete` = lượt quét không lỗi và có nhóm. Chỉ khi cờ `true` lõi mới đặt `left_at` cho thành viên vắng và cờ «đã rời» cho nhóm vắng; `members` có thể rỗng nếu nền tảng không cho lấy. |
| `group.discovered` | `{channel, session_id, group_id, name, member_count, first_message}` — tin đầu tiên từ một nhóm chưa có trong danh bạ của phiên (mỗi nhóm báo một lần; `member_count` có thể `null`). `first_message` = chính tin đó, nguyên dạng payload của `gh.bridge.inbound` `message` (bridge KHÔNG XADD tin này lên luồng inbound vì nhóm chưa nằm trong tập nghe; không ghi nội dung vào log). Lõi tạo nhóm ở «Lắng nghe im lặng» (`silent`, mặc định theo quyết định Sếp 17/10 — hoặc `pending` / `off` nếu Sếp đặt `new_group_mode` của kênh là `ask` / `off`) và bắn WS `group.discovered` để Console làm mới thẻ kênh (hỏi Sếp «Lắng nghe im lặng» hay «Không nghe» chỉ khi `pending`). Thu hết tình báo (Sếp 17/10): nhóm vào `silent` ⇒ lõi LƯU luôn `first_message` như một tin inbound thường (`ingest_message`, bắn `raw.new`); nhóm vào `pending` / `off` ⇒ KHÔNG lưu nội dung, không tạo người gửi (giữ «chưa lưu tin trước khi Sếp chọn»). Không lưu trùng: `raw.event_keys (channel_id, external_msg_id)` chặn khi bus giao lại sự kiện. `first_message` thiếu / không thuộc đúng `group_id` ⇒ lõi bỏ qua (bridge cũ vẫn chạy, chỉ mất tin đầu). Nội dung tin KHÔNG được nằm lại trong stream (MAXLEN 100000, AOF bật): bridge chỉ gắn `first_message` khi (1) danh bạ của phiên đã quét xong lượt đủ (chưa quét ⇒ nhóm «Không nghe» cũ cũng trông như nhóm lạ) và (2) cờ `gh:bridge:new_group_mode:<kênh>` là `silent` (chưa có cờ = lõi cũ ⇒ coi như `silent`; `ask` / `off` ⇒ không gắn, nên nội dung nhóm chưa được Sếp cho nghe không bao giờ vào Redis); lõi XDEL bản ghi `group.discovered` ngay sau khi commit (cả khi không lưu), và khi bus đưa bản ghi vào `gh.bridge.directory.dlq` thì bỏ `first_message` khỏi bản sao DLQ và xoá bản gốc. Tin thứ hai trở đi bị bỏ cho tới khi lõi đồng bộ tập nghe `gh:bridge:listen:<channel>` (thường chưa tới một giây). |

| `group.self_joined` | (v0.1.72) `{channel, session_id, group_id, name, added_by\|null, added_by_alt\|null, method: "add"\|"create"\|"link"\|"unknown", at}` — chính tài khoản được thêm vào nhóm. Zalo: `group_event` loại `join` có ownId trong `updateMembers` (không dựa cờ `isSelf` của thư viện); `added_by` = `sourceId`; `sourceId` là chính mình (vào bằng link) ⇒ `method:"link"`, `added_by:null`; thiếu ⇒ `unknown`. WhatsApp: `group-participants.update` `action:"add"` có mình (so cả JID lẫn LID: `id`, `lid`, `phoneNumber` của participant) ⇒ `added_by` = `author` (LID), `added_by_alt` = `authorPn`; author là mình ⇒ `link`; không có author ⇒ `unknown`; `groups.upsert` (nhóm vừa tạo) ⇒ `method:"create"`. Phát với MỌI loại tài khoản (lõi quyết định theo `account_kind`); bridge hẹn quét lại danh bạ để lấy thành viên, tin đầu vẫn đi `group.discovered` như cũ. |
| `group.self_removed` | (v0.1.72) `{channel, session_id, group_id, by\|null, at}` — Zalo `remove_member` / `block_member` có mình (`by` = `sourceId`); WhatsApp `action:"remove"` có mình (`by` = `author`, tự rời ⇒ `null`). |
| `group.owner_ping` | (v0.1.72) `{channel, session_id, group_id, name, at}` — **không có nội dung tin**. Tin của Owner (ID hoặc alt ∈ `gh:bridge:owner:<kênh>`) có `mentions_self` trong nhóm KHÔNG nghe (`off`/`paused`/`pending`, hoặc nhóm lạ). Chỉ khi phiên là `bot`. |

`gh.bridge.inbound`
| type | payload |
|---|---|
| `message` | `{channel, session_id, external_msg_id, external_group_id\|null, group_name?, sender_external_id, sender_name, sender_phone?, occurred_at, kind: "text"\|"image"\|"file"\|"sticker"\|"reaction"\|"system"\|"other", body_text\|null, direction: "inbound"\|"outbound", mentions_self: bool, replies_to_self: bool, payload: {…nguyên văn từ thư viện…}}` — `replies_to_self` (v0.1.72): tin trích dẫn tin của chính tài khoản (Zalo `data.quote.ownerId == ownId`; WhatsApp `contextInfo.participant ∈ {jid, lid}` của mình). |
| `owner.dm` | (v0.1.72) `{channel, session_id, external_msg_id, sender_external_id, sender_alt_id\|null, occurred_at, kind, body_text\|null, forwarded: bool}` — tin 1-1 tới bot từ ID (hoặc alt, so cả dạng bỏ hậu tố thiết bị) ∈ `gh:bridge:owner:<kênh>`, **bất kể** `direct_mode` / `listen_direct` (kênh lệnh, không phải thu dữ liệu khách). KHÔNG đồng thời phát `message`. `forwarded`: WhatsApp `contextInfo.isForwarded` / `forwardingScore > 0`; Zalo luôn `false` (thư viện không báo). |
| `owner.pair` | (v0.1.72) `{channel, session_id, sender_external_id, sender_alt_id\|null, sender_name, occurred_at, code}` — tin 1-1 (không phải tin chuyển tiếp) khi khoá `gh:bridge:pairing:<kênh>` tồn tại và thân (chuẩn hoá NFC) khớp `^\s*(/?(ghep\|ghép\|start)\s*)?\d{6}\s*$` (không phân biệt hoa thường) — chỉ chuyển `code` 6 số, KHÔNG chuyển thân tin, không phát `message`. Xét trước `owner.dm`. |

## Chỉ nghe nhóm đã bật (khoá cứng)

Lõi giữ tập `gh:bridge:listen:<channel>` = mã nhóm phía nền tảng có `listen_mode` thuộc `tagged_only`/`silent`/`proactive` (hằng `LISTENING_MODES` ở `api/gh/data/common.py`; `off`, `paused` và `pending` KHÔNG nghe), và khoá `gh:bridge:listen_direct:<channel>` = `"0"` nếu Sếp chọn không lưu tin 1-1, `"1"` còn lại (tin 1-1 mặc định LƯU IM LẶNG từ v0.1.65; khoá chưa có cũng coi là lưu). Cùng lúc lõi ghi cờ `gh:bridge:new_group_mode:<channel>` = `silent` | `ask` | `off` (thiết lập «nhóm mới» của kênh, mặc định `silent`) để bridge biết có được gắn tin đầu vào `group.discovered` hay không; làm mới mỗi khi tập nghe đổi, khi Sếp đổi thiết lập «nhóm mới» và khi đổi tài khoản làm thiết lập về mặc định. Bridge kiểm tra **trước khi** XADD: tin nhóm không thuộc tập → bỏ ngay (không lưu, không log nội dung; nhóm lạ không đi luồng inbound mà báo `group.discovered` kèm tin đầu để lõi quyết định lưu hay không). Lõi kiểm lần hai ở ingest. Tin của chính tài khoản (outbound) cũng theo luật này.

`listen_mode = pending` (v0.1.65): nhóm mới phát hiện, chờ Sếp chọn — không lưu tin (kể cả tin đầu đi kèm `group.discovered`) nhưng vẫn thu thành viên; từ quyết định Sếp 17/10 chỉ xảy ra khi Sếp đặt «Nhóm mới: Hỏi Sếp» (mặc định nhóm mới vào `silent` và nằm ngay trong tập nghe). Nhóm đang `off` ở bản cũ giữ nguyên `off`.

Tin 1-1 theo `core.organizations.settings.direct_mode.<kênh>`: `silent` (mặc định) lưu vào Kho thô nhưng không đi đường nhanh, không tự trả lời và **không đưa cho agent trực**; `reply` = như `listen_direct` bật của bản cũ (đường nhanh + agent trực được trả lời); `off` = không lưu. Ngoại lệ: người được Sếp gán BOT đích danh thì tin 1-1 của họ vẫn đi đường nhanh và BOT đó phụ trách dù kênh để `silent`.

## Kênh quản trị — tài khoản riêng của bot (v0.1.72)

Lõi ghi thêm (cùng lúc đồng bộ tập nghe; bridge **chỉ đọc**): `gh:bridge:account_kind:<kênh>:<phiên>` = `bot` | `owner_self`;
`gh:bridge:owner:<kênh>` (set ID + alt của Owner); `gh:bridge:owner_thread:<kênh>` (set thread 1-1 với Owner); `gh:bridge:pairing:<kênh>`
(chuỗi `"1"`, TTL = hạn mã ghép); `gh:bridge:support:<kênh>` (set mã nhóm đang bật Chế độ hỗ trợ).

- Phiên `account_kind ≠ bot` (hoặc chưa có khoá): bridge KHÔNG phát `owner.dm`, `owner.pair`, `group.owner_ping`, không bỏ tin nào — hành vi cũ giữ nguyên.
- Phiên `bot`: tin mình gửi (selfListen / `fromMe`) trong thread ∈ `gh:bridge:owner_thread:<kênh>` bị **bỏ** (trả lời kênh quản trị không vào Kho thô).
- `group.self_joined` / `group.self_removed` phát với mọi loại tài khoản.

## Ánh xạ thư viện (theo bridge của dự án cũ)

- **Zalo** `zca-js`: `new Zalo({selfListen: true, imageMetadataGetter})`; phiên đã lưu → `zalo.login(credential)`; mới → `zalo.loginQR({}, cb)` với `LoginQRCallbackEventType.QRCodeGenerated` (`evt.data.image` base64 PNG), `QRCodeScanned`, `GotLoginInfo` (`evt.data` = credential `{imei, cookie, userAgent}`), `QRCodeExpired`/`QRCodeDeclined` → `evt.actions.retry()` hoặc `abort()`. Tin: `api.listener.on('message', msg)` — `msg.threadId`, `msg.type` (`ThreadType.Group|User`), `msg.data.uidFrom`, `msg.data.dName`, `msg.data.msgId`, `msg.data.ts`, `msg.data.content` (chuỗi hoặc object), `msg.data.mentions`, `msg.isSelf`. Nhóm: `api.getAllGroups()` → `gridVerMap`; `api.getGroupInfo(ids).gridInfoMap[id]` (`name`, `totalMember`, `memVerList`). Gửi: `api.sendMessage({msg}, threadId, ThreadType)`.
- **WhatsApp** Baileys (ghim chính xác `7.0.0-rc14` — bản mới nhất trên npm; không dùng `^`): `version` lấy từ `fetchLatestWaWebVersion()` (GET sw.js của web.whatsapp.com, nhớ 30 phút, không bao giờ cũ hơn bản đóng gói) với user-agent TỰ NHẬN `Gen-Harness-bridge/<phiên bản bridge>` và `sec-fetch-site: cross-site` — ghi đè mặc định giả Chrome của Baileys (luật cứng: không né chống bot); WhatsApp từ chối hay lỗi thì để thư viện dùng bản đóng gói. `makeWASocket({version, auth, printQRInTerminal: false, logger: pino silent})`, trạng thái xác thực giữ trong RAM (`initAuthCreds` + `BufferJSON`) và tuần tự hoá thành credential; `connection.update` (`qr` → PNG data URL bằng `qrcode`, `connection === 'open'`, `DisconnectReason.loggedOut`), `messages.upsert` (`type === 'notify'`; text `m.message.conversation` hoặc `extendedTextMessage.text`; nhóm khi `remoteJid` kết thúc `@g.us`, người gửi `m.key.participant`, `m.pushName`, `m.key.fromMe`), `groupFetchAllParticipating()`, `sock.sendMessage(jid, {text})`.
