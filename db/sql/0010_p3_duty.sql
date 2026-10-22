-- Gen-Harness · giai đoạn 3 · agent trực kênh (ARCHITECTURE §5, docs/api/phase-3-duty.md)
-- Giữ quy ước docs/handoff/03-database.md: UUIDv7, org_id, timestamptz, không ENUM.

-- ─── Phạm vi kênh ───────────────────────────────────────────────────────────
-- Khoá chính cũ (agent_id, channel_id, group_id) buộc group_id NOT NULL nên không diễn đạt được "cả kênh"
-- (gồm tin 1-1). group_id NULL = mọi nhóm đang nghe của kênh + tin nhắn riêng trên kênh đó.
ALTER TABLE agent.channel_scopes DROP CONSTRAINT channel_scopes_pkey;
ALTER TABLE agent.channel_scopes ALTER COLUMN group_id DROP NOT NULL;
ALTER TABLE agent.channel_scopes ALTER COLUMN channel_id SET NOT NULL;
ALTER TABLE agent.channel_scopes ADD COLUMN id uuid NOT NULL DEFAULT core.uuid_v7() PRIMARY KEY;
CREATE UNIQUE INDEX channel_scopes_agent_channel_group ON agent.channel_scopes (agent_id, channel_id, group_id)
  NULLS NOT DISTINCT;
CREATE INDEX ON agent.channel_scopes (channel_id, group_id);

-- ─── Danh tính: giới hạn tần suất ──────────────────────────────────────────
-- {"decisions_per_min": 20, "drafts_per_hour": 30}; khoá thiếu → mặc định trong gh/biz/duty/engine.py.
ALTER TABLE agent.identities ADD COLUMN limits jsonb NOT NULL DEFAULT '{}';

-- ─── Quyết định ────────────────────────────────────────────────────────────
ALTER TABLE agent.decisions
  ADD COLUMN trigger_unit_id uuid,                 -- đơn vị ý nghĩa kích hoạt (chống trùng: một đơn vị một quyết định / agent)
  ADD COLUMN requested       text,                 -- quyết định model đề xuất trước khi qua policy (NULL = bị loại)
  ADD COLUMN outcome         text,                 -- noted | suggested | held | blocked | rejected | none
  ADD COLUMN autonomy_level  smallint CHECK (autonomy_level BETWEEN 0 AND 6),
  ADD COLUMN cited_refs      jsonb NOT NULL DEFAULT '[]',   -- phần ngữ cảnh model trích dẫn (tập con context_refs)
  ADD COLUMN proposal        jsonb;                -- {"text": "…"} gợi ý/bản nháp; {"note_entry_id": "…"} với note
CREATE UNIQUE INDEX decisions_agent_trigger_unit ON agent.decisions (agent_id, trigger_unit_id)
  WHERE trigger_unit_id IS NOT NULL;
CREATE INDEX ON agent.decisions (org_id, decision, at DESC);
