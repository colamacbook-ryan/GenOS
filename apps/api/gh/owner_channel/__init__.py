"""Bot quản trị 1-1 (v0.1.72): tài khoản Zalo/WhatsApp RIÊNG của bot, kênh quản trị với Sếp, Sếp gọi bot trong nhóm,
chế độ hỗ trợ nhóm.

LUẬT CỨNG (CLAUDE.md › Luật cứng, Sếp duyệt 18/10/2026): chỉ khi kênh chạy bằng tài khoản riêng của bot
(`core.channel_sessions.account_kind = 'bot'`), bot được TỰ GỬI, không qua PIN, trong ĐÚNG ba trường hợp —
`owner_dm` (kênh quản trị 1-1), `owner_tag` (Sếp tag bot trong nhóm), `support` (nhóm Sếp đã bật chế độ hỗ trợ) — và chỉ
qua MỘT cửa: `gh.owner_channel.autosend.send_auto`. Không module nào khác của gói này được phát lên luồng gửi của bridge
(test AST: tests/test_outbound_only_two_paths_v0172.py).
"""
