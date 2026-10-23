-- Gen-Harness · v0.1.76 (gói L1 «lõi trí nhớ + hợp đồng»): lõi trí nhớ 5 lớp (spec Bước 1 r6.1 §4A.1).
-- Chỉ THÊM, chạy lại an toàn (CREATE … IF NOT EXISTS; ALTER … ADD COLUMN IF NOT EXISTS; ràng buộc/chỉ mục trong khối
-- DO $$ … IF NOT EXISTS $$; seed bằng INSERT … ON CONFLICT DO NOTHING). Không DROP bảng/cột, không đổi kiểu cột.
-- (Spec ghi tên 0043_v0175_tri_nho — v0.1.75 đã phát hành nên đổi thành v0176; nội dung không đổi.)
--
-- Nội dung:
--   a) extension unaccent, btree_gist (schema public) + hàm mem.fold (bỏ dấu + chữ thường, IMMUTABLE, ghi đủ schema
--      để cột sinh STORED / chỉ mục biểu thức dựng lại được khi pg_restore đặt search_path = '' — CNS-066)
--   b) biz.signal_kinds (bảng tra 10 loại + system/legacy) — có ở 0043 để kind_hint / dispute_signal_kind có FK ngay
--   c) L1: clean.evidence / clean.meaning_units thêm cột; clean.media_texts; refinery.group_watermarks/analyses/fact_retry;
--      view refinery.group_budget (+ cột thread_key/analysis_id trên agent.model_calls)
--   d) L2: mem.entities / aliases / mentions / context_types / group_contexts
--   e) L3: mem.predicates (seed 29) / facts (hai thời gian, EXCLUDE, trigger bất biến + bằng chứng hoãn) / fact_evidence /
--      fact_status_events / disputes
--   f) L4 (dẫn xuất): mem.relation_heat_weekly / entity_summaries / owner_profile + view mem.v_owner_profile
--   g) raw.attachments thêm org_id (backfill từ raw.events) — CNS-082c
--   h) backfill mem.entities từ core.persons (chưa gộp, chưa xoá) và core.groups
--   i) RLS theo org_id + GRANT cho gh_app (khuôn 0042) cho mọi bảng mới

-- ─── a) extension + mem.fold ────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS unaccent SCHEMA public;
CREATE EXTENSION IF NOT EXISTS btree_gist SCHEMA public;

CREATE SCHEMA IF NOT EXISTS mem;       -- trí nhớ bền: thực thể, bí danh, sự thật hai thời gian, bối cảnh nhóm

DO $$
DECLARE r text;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT USAGE ON SCHEMA mem TO gh_app;
    -- Bảng/hàm tạo SAU này trong schema mem cũng được cấp (theo vai trò đang chạy migration, như 0014).
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA mem GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gh_app', current_user);
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA mem GRANT EXECUTE ON FUNCTIONS TO gh_app', current_user);
  END IF;
END $$;

-- mem.fold: tiếng Việt bỏ dấu (đ → d) + chữ thường. Gọi hàm bằng tên ĐỦ schema (public.unaccent + từ điển
-- 'public.unaccent'::regdictionary): pg_restore chạy với search_path rỗng, tên trần sẽ không phân giải được.
-- unaccent() chỉ STABLE, nhưng kết quả cố định theo từ điển ⇒ khai IMMUTABLE để dùng được trong cột sinh và chỉ mục.
CREATE OR REPLACE FUNCTION mem.fold(text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
  AS $$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, $1)) $$;

-- ─── b) biz.signal_kinds ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS biz.signal_kinds (
  code        text PRIMARY KEY,
  label_vi    text NOT NULL,
  description_vi text,
  on_intel    boolean NOT NULL DEFAULT true,     -- false: không lên kênh tình báo (system, legacy)
  sort_order  int NOT NULL DEFAULT 0
);
INSERT INTO biz.signal_kinds (code, label_vi, description_vi, on_intel, sort_order) VALUES
  ('nghia_vu_loi_hua',      'Nghĩa vụ & lời hứa',     'Lời hứa, nghĩa vụ hai chiều, lời hứa có điều kiện', true, 1),
  ('viec_han',              'Việc & hạn',             'Hạn nộp, việc giao chưa xong, "trước 5h gửi báo cáo"', true, 2),
  ('cau_hoi_cho_xu_ly',     'Câu hỏi chờ xử lý',      'Câu hỏi đang chờ ai đó trả lời (khách, nội bộ, người thân)', true, 3),
  ('su_kien_dip',           'Sự kiện & dịp',          'Giỗ, sinh nhật, họp lớp, khai trương, cưới hỏi (có ngày âm lịch)', true, 4),
  ('suc_khoe_an_toan',      'Sức khoẻ & an toàn',     'Ốm đau, tai nạn, nhân viên bị thương', true, 5),
  ('xung_dot_khieu_nai',    'Xung đột & khiếu nại',   'Khách phàn nàn, cãi nhau trong nhóm, căng thẳng gia đình', true, 6),
  ('quan_he_nong_nguoi',    'Quan hệ nóng/nguội',     'Khách quen im khác nhịp, bạn thân lâu không liên lạc', true, 7),
  ('co_hoi_ket_noi',        'Cơ hội & kết nối',       'Ai cần đúng hàng Sếp có, giới thiệu đối tác, việc làm, học bổng', true, 8),
  ('rui_ro_lua_dao',        'Rủi ro & lừa đảo',       'Đổi STK, mạo danh, link lạ, giục chuyển tiền', true, 9),
  ('thi_truong_thong_tin',  'Thị trường & thông tin', 'NCC tăng giá, giá đối thủ, chính sách mới', true, 10),
  ('system',                'Hệ thống',               'Không lên kênh tình báo', false, 90),
  ('legacy',                'Cũ',                     'Không lên kênh tình báo', false, 91)
ON CONFLICT (code) DO NOTHING;

-- ─── c1) clean.evidence: trạng thái kiểm trích dẫn (CNS-082f: KHÔNG đổi PK của bảng có sẵn) ───────────
ALTER TABLE clean.evidence
  ADD COLUMN IF NOT EXISTS quote_status      text NOT NULL DEFAULT 'unverifiable',
  ADD COLUMN IF NOT EXISTS source_field      text,
  ADD COLUMN IF NOT EXISTS span_start        int,
  ADD COLUMN IF NOT EXISTS span_end          int,
  ADD COLUMN IF NOT EXISTS extra_spans       jsonb NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS verified_at       timestamptz,
  ADD COLUMN IF NOT EXISTS extractor_version text;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_quote_status_chk') THEN
    ALTER TABLE clean.evidence ADD CONSTRAINT evidence_quote_status_chk
      CHECK (quote_status IN ('verified', 'absent', 'unverifiable'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evidence_source_field_chk') THEN
    ALTER TABLE clean.evidence ADD CONSTRAINT evidence_source_field_chk
      CHECK (source_field IS NULL OR source_field IN ('body', 'transcript', 'ocr', 'caption'));
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS evidence_unverified_idx ON clean.evidence (quote_status) WHERE quote_status <> 'verified';

-- ─── c2) clean.meaning_units (có sẵn, phân vùng): cột mới + chỉ mục chữ bỏ dấu ──────────────────────────
ALTER TABLE clean.meaning_units
  ADD COLUMN IF NOT EXISTS extractor_version text,
  ADD COLUMN IF NOT EXISTS kind_hint         text REFERENCES biz.signal_kinds(code),
  ADD COLUMN IF NOT EXISTS analysis_id       uuid,
  ADD COLUMN IF NOT EXISTS flags             jsonb NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS gate_candidate    text,
  ADD COLUMN IF NOT EXISTS level_hint        text,
  ADD COLUMN IF NOT EXISTS harm              jsonb;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'meaning_units_gate_candidate_chk') THEN
    ALTER TABLE clean.meaning_units ADD CONSTRAINT meaning_units_gate_candidate_chk
      CHECK (gate_candidate IS NULL OR gate_candidate IN (
        'lua_dao_dang_dien_ra', 'an_toan', 'khach_che_cong_khai_doa_bo',
        'loi_hua_khach_sap_vo', 'co_hoi_lon_sap_troi', 'su_co_cong_viec_gap'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'meaning_units_level_hint_chk') THEN
    ALTER TABLE clean.meaning_units ADD CONSTRAINT meaning_units_level_hint_chk
      CHECK (level_hint IS NULL OR level_hint IN ('red', 'amber', 'grey'));
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS meaning_units_conclusion_fold_idx
  ON clean.meaning_units USING gin (mem.fold(conclusion) gin_trgm_ops);

-- ─── d) L2: thực thể, bí danh, nhắc tới, bối cảnh ───────────────────────────
CREATE TABLE IF NOT EXISTS mem.entities (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL REFERENCES core.organizations(id),
  entity_type  text NOT NULL CHECK (entity_type IN ('person', 'group', 'org', 'place', 'item')),
  person_id    uuid REFERENCES core.persons(id),
  group_id     uuid REFERENCES core.groups(id),
  display_name text NOT NULL,
  is_owner     boolean NOT NULL DEFAULT false,
  merged_into  uuid REFERENCES mem.entities(id),    -- chuyển hướng khi gộp (CNS-065); một bậc là đủ nhờ nén đường đi (CNS-103)
  attrs        jsonb NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now(),
  forgotten_at timestamptz,
  CHECK (person_id IS NULL OR entity_type = 'person'),
  CHECK (group_id IS NULL OR entity_type = 'group'),
  CHECK (merged_into IS NULL OR merged_into <> id)
);
CREATE UNIQUE INDEX IF NOT EXISTS entities_org_person_uq ON mem.entities (org_id, person_id) WHERE person_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS entities_org_group_uq ON mem.entities (org_id, group_id) WHERE group_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS entities_one_owner_uq ON mem.entities (org_id) WHERE is_owner;
CREATE INDEX IF NOT EXISTS entities_merged_into_idx ON mem.entities (merged_into) WHERE merged_into IS NOT NULL;
CREATE INDEX IF NOT EXISTS entities_name_trgm_idx ON mem.entities USING gin (mem.fold(display_name) gin_trgm_ops);

-- Bất biến gộp một bậc (CNS-103): đích gộp không được có merged_into; entity đang là đích của ai đó không được tự bị gộp.
-- entities.merge nén đường đi (đổi mọi entity trỏ B sang C) TRƯỚC khi đặt B.merged_into = C, nên luôn qua được kiểm này.
CREATE OR REPLACE FUNCTION mem.entities_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.merged_into IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM mem.entities t WHERE t.id = NEW.merged_into AND t.merged_into IS NOT NULL) THEN
      RAISE EXCEPTION 'mem.entities: đích gộp % đã bị gộp vào entity khác (phải trỏ thẳng tới đích cuối)', NEW.merged_into
        USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM mem.entities c WHERE c.merged_into = NEW.id) THEN
      RAISE EXCEPTION 'mem.entities: % đang là đích của entity khác — nén đường đi trước khi gộp', NEW.id
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS entities_guard ON mem.entities;
CREATE TRIGGER entities_guard BEFORE INSERT OR UPDATE OF merged_into ON mem.entities
  FOR EACH ROW EXECUTE FUNCTION mem.entities_guard();

CREATE TABLE IF NOT EXISTS mem.aliases (
  id              uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id          uuid NOT NULL REFERENCES core.organizations(id),
  entity_id       uuid NOT NULL REFERENCES mem.entities(id),
  alias           text NOT NULL,
  alias_folded    text GENERATED ALWAYS AS (mem.fold(alias)) STORED,
  scope_group_id  uuid REFERENCES core.groups(id),     -- bí danh chỉ đúng trong một nhóm
  source          text NOT NULL CHECK (source IN ('display_name', 'mention', 'owner', 'merge', 'import')),
  confidence      numeric(4,3) NOT NULL DEFAULT 0.5,
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at    timestamptz NOT NULL DEFAULT now(),
  evidence_raw_id uuid
);
-- Khoá có biểu thức (coalesce) ⇒ UNIQUE index biểu thức, không phải PK (CNS-082a).
CREATE UNIQUE INDEX IF NOT EXISTS aliases_entity_alias_scope_uq
  ON mem.aliases (entity_id, alias_folded, coalesce(scope_group_id, '00000000-0000-0000-0000-000000000000'::uuid));
CREATE INDEX IF NOT EXISTS aliases_folded_trgm_idx ON mem.aliases USING gin (alias_folded gin_trgm_ops);
CREATE INDEX IF NOT EXISTS aliases_org_folded_idx ON mem.aliases (org_id, alias_folded);

CREATE TABLE IF NOT EXISTS mem.mentions (
  id                  uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id              uuid NOT NULL REFERENCES core.organizations(id),
  meaning_unit_id     uuid NOT NULL,
  meaning_observed_at timestamptz NOT NULL,
  raw_event_id        uuid,
  surface             text NOT NULL,                    -- nguyên văn "chị Tư Đồng Nai"
  entity_id           uuid REFERENCES mem.entities(id),
  method              text NOT NULL CHECK (method IN ('sender_id', 'alias_exact', 'alias_fuzzy', 'llm', 'owner')),
  confidence          numeric(4,3) NOT NULL DEFAULT 0.5,
  status              text NOT NULL CHECK (status IN ('linked', 'candidate', 'unresolved'))
);
CREATE INDEX IF NOT EXISTS mentions_entity_idx ON mem.mentions (entity_id);
CREATE INDEX IF NOT EXISTS mentions_open_idx ON mem.mentions (org_id, status) WHERE status <> 'linked';
CREATE INDEX IF NOT EXISTS mentions_unit_idx ON mem.mentions (meaning_unit_id);

-- Chính sách bối cảnh (một nguồn, spec §4B.3). Khoá `policy` — xem gh/biz/mem/contexts.py::Policy.
CREATE TABLE IF NOT EXISTS mem.context_types (
  code      text PRIMARY KEY CHECK (code IN ('kinh_doanh', 'cong_viec', 'hoc_tap', 'gia_dinh', 'ban_be', 'nhan_vien', 'giao_thuong', 'khac')),
  label_vi  text NOT NULL,
  policy    jsonb NOT NULL DEFAULT '{}'
);
INSERT INTO mem.context_types (code, label_vi, policy) VALUES
  ('kinh_doanh', 'Kinh doanh', $j${
    "gates": ["lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo", "co_hoi_lon_sap_troi", "su_co_cong_viec_gap"],
    "kinds_off": [], "kinds_off_outside_gate": [], "kind_caps": {}, "key_relation_only_kinds": [],
    "profile_match_required_kinds": [], "gate_requires_profile_match": [],
    "customer_gates_only_for_owner_customer": false,
    "score_people": true, "console_pin_allowed": true, "trust_graph": true, "people_eval_privacy": null,
    "name_on_chat": true, "customer_word": true, "vocab": {}, "lunar_dates": false, "address_by_role": false,
    "large_group": null,
    "notes_vi": "Chữ \"khách\" dùng được ở bối cảnh này."
  }$j$::jsonb),
  ('giao_thuong', 'Giao thương', $j${
    "gates": ["lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo", "co_hoi_lon_sap_troi", "su_co_cong_viec_gap"],
    "kinds_off": [], "kinds_off_outside_gate": [], "kind_caps": {}, "key_relation_only_kinds": [],
    "profile_match_required_kinds": [], "gate_requires_profile_match": [],
    "customer_gates_only_for_owner_customer": true,
    "score_people": true, "console_pin_allowed": true, "trust_graph": true, "people_eval_privacy": null,
    "name_on_chat": true, "customer_word": true, "vocab": {}, "lunar_dates": false, "address_by_role": false,
    "large_group": {"threshold": 200, "link_ads_cap": "grey"},
    "notes_vi": "Hai cổng \"khách\" chỉ khi người đó là khách của Sếp; nhóm trên 200 người: link/quảng cáo chung chỉ ở mức xám trừ khi nhắm tới Sếp hoặc khách của Sếp; nêu độ tin của nguồn."
  }$j$::jsonb),
  ('cong_viec', 'Công việc', $j${
    "gates": ["lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo", "co_hoi_lon_sap_troi", "su_co_cong_viec_gap"],
    "kinds_off": [], "kinds_off_outside_gate": [], "kind_caps": {}, "key_relation_only_kinds": [],
    "profile_match_required_kinds": [], "gate_requires_profile_match": [],
    "customer_gates_only_for_owner_customer": false,
    "score_people": true, "console_pin_allowed": true, "trust_graph": true, "people_eval_privacy": null,
    "name_on_chat": true, "customer_word": false, "vocab": {}, "lunar_dates": false, "address_by_role": false,
    "large_group": null,
    "notes_vi": "Giọng trung tính, không bàn đời tư đồng nghiệp."
  }$j$::jsonb),
  ('nhan_vien', 'Nhân viên', $j${
    "gates": ["lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo", "co_hoi_lon_sap_troi", "su_co_cong_viec_gap"],
    "kinds_off": [], "kinds_off_outside_gate": ["co_hoi_ket_noi"], "kind_caps": {"xung_dot_khieu_nai": "amber"},
    "key_relation_only_kinds": [], "profile_match_required_kinds": [], "gate_requires_profile_match": [],
    "customer_gates_only_for_owner_customer": true,
    "score_people": true, "console_pin_allowed": true, "trust_graph": true, "people_eval_privacy": "console_pin",
    "name_on_chat": false, "customer_word": false, "vocab": {}, "lunar_dates": false, "address_by_role": false,
    "large_group": null,
    "notes_vi": "Mọi đánh giá nhân viên chỉ xem trên Console có mã PIN; không nêu tên nhân viên trên kênh chat; cổng \"khách\" chỉ khi nhân viên đang làm việc với khách của Sếp."
  }$j$::jsonb),
  ('hoc_tap', 'Học tập', $j${
    "gates": ["lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo", "co_hoi_lon_sap_troi", "su_co_cong_viec_gap"],
    "kinds_off": [], "kinds_off_outside_gate": [], "kind_caps": {"co_hoi_ket_noi": "amber", "thi_truong_thong_tin": "grey"},
    "key_relation_only_kinds": [], "profile_match_required_kinds": [], "gate_requires_profile_match": [],
    "customer_gates_only_for_owner_customer": false,
    "score_people": true, "console_pin_allowed": true, "trust_graph": true, "people_eval_privacy": null,
    "name_on_chat": true, "customer_word": false, "vocab": {}, "lunar_dates": false, "address_by_role": false,
    "large_group": null,
    "notes_vi": "Hạn chót nộp bài, thi, hồ sơ trong hôm nay là việc công việc gấp."
  }$j$::jsonb),
  ('gia_dinh', 'Gia đình', $j${
    "gates": ["lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo", "su_co_cong_viec_gap"],
    "kinds_off": ["co_hoi_ket_noi", "thi_truong_thong_tin"], "kinds_off_outside_gate": [], "kind_caps": {"xung_dot_khieu_nai": "amber"},
    "key_relation_only_kinds": ["quan_he_nong_nguoi"], "profile_match_required_kinds": [], "gate_requires_profile_match": [],
    "customer_gates_only_for_owner_customer": true,
    "score_people": false, "console_pin_allowed": false, "trust_graph": false, "people_eval_privacy": null,
    "name_on_chat": true, "customer_word": false, "vocab": {"xung_dot_khieu_nai": "căng thẳng"}, "lunar_dates": true, "address_by_role": true,
    "large_group": null,
    "notes_vi": "Không chấm điểm người, không đồ thị độ tin cậy; gọi xung đột là \"căng thẳng\" và chỉ ghi sự việc; dịp tính theo ngày (có âm lịch); xưng theo vai."
  }$j$::jsonb),
  ('ban_be', 'Bạn bè', $j${
    "gates": ["lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo", "su_co_cong_viec_gap"],
    "kinds_off": ["thi_truong_thong_tin"], "kinds_off_outside_gate": [], "kind_caps": {"co_hoi_ket_noi": "amber", "xung_dot_khieu_nai": "amber"},
    "key_relation_only_kinds": [], "profile_match_required_kinds": ["co_hoi_ket_noi"], "gate_requires_profile_match": [],
    "customer_gates_only_for_owner_customer": true,
    "score_people": false, "console_pin_allowed": false, "trust_graph": false, "people_eval_privacy": null,
    "name_on_chat": true, "customer_word": false, "vocab": {}, "lunar_dates": true, "address_by_role": false,
    "large_group": null,
    "notes_vi": "Không chấm điểm người; cơ hội chỉ khi khớp Hồ sơ Sếp đã xác nhận (tối đa mức cam); đùa tục, cà khịa, kèo vui không phải xung đột hay lời hứa."
  }$j$::jsonb),
  ('khac', 'Khác', $j${
    "gates": ["lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo", "co_hoi_lon_sap_troi", "su_co_cong_viec_gap"],
    "kinds_off": [], "kinds_off_outside_gate": [], "kind_caps": {}, "key_relation_only_kinds": [],
    "profile_match_required_kinds": [], "gate_requires_profile_match": [],
    "customer_gates_only_for_owner_customer": false,
    "score_people": true, "console_pin_allowed": true, "trust_graph": true, "people_eval_privacy": null,
    "name_on_chat": true, "customer_word": false, "vocab": {}, "lunar_dates": false, "address_by_role": false,
    "large_group": null,
    "notes_vi": "Giọng trung tính."
  }$j$::jsonb)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS mem.group_contexts (
  org_id       uuid NOT NULL REFERENCES core.organizations(id),
  group_id     uuid NOT NULL REFERENCES core.groups(id),
  context      text NOT NULL REFERENCES mem.context_types(code),
  is_primary   boolean NOT NULL DEFAULT false,
  source       text NOT NULL CHECK (source IN ('owner', 'suggested')),
  confidence   numeric(4,3),
  confirmed_by uuid REFERENCES core.users(id),
  confirmed_at timestamptz,
  evidence     jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (org_id, group_id, context)
);
CREATE UNIQUE INDEX IF NOT EXISTS group_contexts_one_primary_uq ON mem.group_contexts (org_id, group_id) WHERE is_primary;

-- ─── e) L3: vị từ, sự thật hai thời gian ────────────────────────────────────
CREATE TABLE IF NOT EXISTS mem.predicates (
  code                text PRIMARY KEY,
  cardinality         text NOT NULL CHECK (cardinality IN ('single', 'multi')),
  subject_types       text[] NOT NULL,
  object_kind         text NOT NULL CHECK (object_kind IN ('entity', 'value')),
  sensitivity         text NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal', 'sensitive', 'hr')),
  dispute_signal_kind text REFERENCES biz.signal_kinds(code),
  owner_section       text CHECK (owner_section IS NULL OR owner_section IN
                        ('goals', 'resources', 'key_relations', 'values_limits', 'rhythm', 'report_prefs')),
  auto_supersede      boolean NOT NULL DEFAULT true,   -- false: đổi giá trị không bao giờ tự thay thế (CNS-096)
  description_vi      text NOT NULL
);
-- Seed 29 vị từ (R6: thêm vị từ = INSERT qua migration sau, có review). single = contact_phone, payment_account,
-- works_at, lives_in, role_in_group (CNS-082e); còn lại multi.
INSERT INTO mem.predicates (code, cardinality, subject_types, object_kind, sensitivity, dispute_signal_kind, owner_section, auto_supersede, description_vi) VALUES
  ('promised_to',         'multi',  '{person,org}',                   'value',  'normal',    'nghia_vu_loi_hua',   NULL, true,  'Chủ thể hứa làm điều gì với ai (giá trị: nội dung + người nhận)'),
  ('owes',                'multi',  '{person,org}',                   'value',  'normal',    'nghia_vu_loi_hua',   NULL, true,  'Chủ thể đang nợ ai điều gì (tiền, hàng, việc)'),
  ('asked_waiting',       'multi',  '{person}',                       'value',  'normal',    'cau_hoi_cho_xu_ly',  NULL, true,  'Chủ thể hỏi một câu và đang chờ trả lời'),
  ('deadline',            'multi',  '{person,group,org}',             'value',  'normal',    'viec_han',           NULL, true,  'Hạn chót của một việc'),
  ('scheduled_event',     'multi',  '{person,group,org}',             'value',  'normal',    NULL,                 NULL, true,  'Sự kiện/lịch hẹn có ngày giờ (đám giỗ, họp, khai trương)'),
  ('health_event',        'multi',  '{person}',                       'value',  'sensitive', 'suc_khoe_an_toan',   NULL, true,  'Sự kiện sức khoẻ/an toàn của chủ thể'),
  ('conflict_with',       'multi',  '{person,org}',                   'entity', 'normal',    'xung_dot_khieu_nai', NULL, true,  'Chủ thể đang xung đột/căng thẳng với đối tượng'),
  ('complained_about',    'multi',  '{person,org}',                   'value',  'normal',    NULL,                 NULL, true,  'Chủ thể phàn nàn về điều gì'),
  ('said_will_leave',     'multi',  '{person,org}',                   'value',  'hr',        NULL,                 NULL, true,  'Chủ thể nói sẽ bỏ đi (nghỉ việc, đổi chỗ lấy hàng)'),
  ('needs',               'multi',  '{person,org}',                   'value',  'normal',    NULL,                 NULL, true,  'Cầu: chủ thể cần mua/tìm thứ gì'),
  ('offers',              'multi',  '{person,org}',                   'value',  'normal',    NULL,                 NULL, true,  'Cung: chủ thể có/bán thứ gì'),
  ('works_at',            'single', '{person}',                       'entity', 'normal',    NULL,                 NULL, true,  'Chủ thể làm việc tại tổ chức nào'),
  ('role_in_group',       'single', '{person}',                       'value',  'normal',    NULL,                 NULL, true,  'Vai trò của chủ thể trong nhóm (giá trị: nhóm + vai)'),
  ('family_of',           'multi',  '{person}',                       'entity', 'sensitive', NULL,                 NULL, true,  'Chủ thể là người nhà của đối tượng (giá trị quan hệ nằm trong bằng chứng)'),
  ('friend_of',           'multi',  '{person}',                       'entity', 'normal',    NULL,                 NULL, true,  'Chủ thể là bạn của đối tượng'),
  ('staff_of',            'multi',  '{person}',                       'entity', 'normal',    NULL,                 NULL, true,  'Chủ thể là nhân viên của đối tượng'),
  ('customer_of',         'multi',  '{person,org}',                   'entity', 'normal',    NULL,                 NULL, true,  'Chủ thể là khách của đối tượng'),
  ('supplier_of',         'multi',  '{person,org}',                   'entity', 'normal',    NULL,                 NULL, true,  'Chủ thể là nhà cung cấp của đối tượng'),
  ('prefers',             'multi',  '{person}',                       'value',  'normal',    NULL,                 NULL, true,  'Sở thích/thói quen của chủ thể'),
  ('lives_in',            'single', '{person,org}',                   'value',  'normal',    NULL,                 NULL, true,  'Chủ thể ở đâu (tỉnh/thành, địa chỉ)'),
  ('contact_phone',       'single', '{person,org}',                   'value',  'sensitive', NULL,                 NULL, true,  'Số điện thoại liên hệ của chủ thể'),
  ('payment_account',     'single', '{person,org}',                   'value',  'sensitive', 'rui_ro_lua_dao',     NULL, false, 'Tài khoản nhận tiền của chủ thể — chỉ lưu digest + 3 số cuối; đổi giá trị luôn thành tranh chấp (CNS-096)'),
  ('display_name_changed','multi',  '{person,group,org}',             'value',  'normal',    NULL,                 NULL, true,  'Tên hiển thị của chủ thể đã đổi'),
  ('owner_goal',          'multi',  '{person}',                       'value',  'normal',    NULL,                 'goals',          true, 'Hồ sơ Sếp: mục tiêu và ưu tiên'),
  ('owner_resource',      'multi',  '{person}',                       'value',  'normal',    NULL,                 'resources',      true, 'Hồ sơ Sếp: nguồn lực (kỹ năng, thời gian, vốn, mạng lưới, hàng có)'),
  ('owner_value_limit',   'multi',  '{person}',                       'value',  'normal',    NULL,                 'values_limits',  true, 'Hồ sơ Sếp: giá trị và giới hạn'),
  ('owner_rhythm',        'multi',  '{person}',                       'value',  'normal',    NULL,                 'rhythm',         true, 'Hồ sơ Sếp: lịch và nhịp sống, giờ yên lặng'),
  ('owner_key_relation',  'multi',  '{person}',                       'entity', 'normal',    NULL,                 'key_relations',  true, 'Hồ sơ Sếp: vai trò và quan hệ quan trọng (đối tượng = thực thể)'),
  ('owner_report_pref',   'multi',  '{person}',                       'value',  'normal',    NULL,                 'report_prefs',   true, 'Hồ sơ Sếp: cách báo (muốn/không muốn nhận báo gì)')
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS mem.disputes (
  id                   uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id               uuid NOT NULL REFERENCES core.organizations(id),
  subject_id           uuid NOT NULL REFERENCES mem.entities(id),
  predicate            text NOT NULL REFERENCES mem.predicates(code),
  fact_ids             uuid[] NOT NULL DEFAULT '{}',
  dispute_signal_kind  text REFERENCES biz.signal_kinds(code),
  opened_at            timestamptz NOT NULL DEFAULT now(),
  resolved_at          timestamptz,
  resolved_by          text CHECK (resolved_by IS NULL OR resolved_by IN ('owner', 'consolidator', 'expiry')),
  winner_fact_id       uuid,
  projected_signal_id  uuid,            -- Đợt 1 chỉ ghi hàng chờ; T1 (Đợt 2) chiếu sang tín hiệu (CNS-076)
  note                 text,            -- vd 'stk_quay_lai_cu' / 'stk_moi' (so với toàn bộ lịch sử STK)
  CHECK ((resolved_at IS NULL) = (resolved_by IS NULL))
);
CREATE INDEX IF NOT EXISTS disputes_open_idx ON mem.disputes (org_id, subject_id, predicate) WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS mem.facts (
  id                  uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id              uuid NOT NULL REFERENCES core.organizations(id),
  subject_id          uuid NOT NULL REFERENCES mem.entities(id),
  predicate           text NOT NULL REFERENCES mem.predicates(code),
  object_entity_id    uuid REFERENCES mem.entities(id),
  object_value        jsonb,
  object_key          text NOT NULL,           -- khoá so sánh do CODE tính (id thực thể, hoặc băm của giá trị chuẩn hoá)
  valid_from          timestamptz NOT NULL,    -- thời điểm sự việc, neo vào giờ GỬI tin
  valid_to            timestamptz,             -- NULL = đang hiệu lực
  recorded_at         timestamptz NOT NULL DEFAULT now(),
  closed_recorded_at  timestamptz,             -- lúc hệ thống đóng/đổi trạng thái (CNS-083)
  status              text NOT NULL CHECK (status IN ('proposed', 'asserted', 'owner_confirmed', 'disputed', 'superseded', 'retracted')),
  is_single           boolean NOT NULL,        -- chép từ vị từ lúc ghi
  source_class        text NOT NULL CHECK (source_class IN ('owner', 'insider', 'member', 'outsider', 'forwarded', 'rule')),
  source_reliability  numeric(3,2) NOT NULL,
  agreement           numeric(3,2),
  confidence_exist    numeric(4,3) NOT NULL,
  confidence_content  numeric(4,3) NOT NULL,
  attributed_to       uuid REFERENCES mem.entities(id),    -- "theo lời X"
  context_code        text REFERENCES mem.context_types(code),
  sensitivity         text NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal', 'sensitive', 'hr')),
  source_raw_event_id uuid,                    -- tin sinh ra sự thật; NULL với owner_edit
  supersedes          uuid REFERENCES mem.facts(id) ON DELETE SET NULL,
  superseded_by       uuid REFERENCES mem.facts(id) ON DELETE SET NULL,
  dispute_id          uuid REFERENCES mem.disputes(id) ON DELETE SET NULL,
  evidence_n          int NOT NULL DEFAULT 0,  -- CHỈ trigger tăng
  extractor_version   text,
  created_by          text NOT NULL CHECK (created_by IN ('extractor', 'consolidator', 'owner', 'rule')),
  confirmed_by        uuid REFERENCES core.users(id),
  confirmed_at        timestamptz,
  CHECK ((object_entity_id IS NULL) <> (object_value IS NULL)),
  CHECK (valid_to IS NULL OR valid_to >= valid_from),
  CHECK (status <> 'owner_confirmed' OR confirmed_by IS NOT NULL),
  CHECK (source_class NOT IN ('outsider', 'forwarded') OR status IN ('asserted', 'disputed', 'superseded', 'retracted'))
);
-- Cách 2 nhánh của "không có evidence_n >= 1" (CNS-063): bằng chứng nằm ở bảng con, kiểm bằng CONSTRAINT TRIGGER hoãn (dưới).

DO $$
BEGIN
  -- Hai thời gian: hai dòng đơn trị hiệu lực (asserted/owner_confirmed, KỂ CẢ dòng đã đóng valid_to) không được chồng khoảng.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'facts_single_no_overlap') THEN
    ALTER TABLE mem.facts ADD CONSTRAINT facts_single_no_overlap
      EXCLUDE USING gist (subject_id WITH =, predicate WITH =,
                          tstzrange(valid_from, coalesce(valid_to, 'infinity'::timestamptz)) WITH &&)
      WHERE (is_single AND status IN ('asserted', 'owner_confirmed'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'disputes_winner_fk') THEN
    ALTER TABLE mem.disputes ADD CONSTRAINT disputes_winner_fk
      FOREIGN KEY (winner_fact_id) REFERENCES mem.facts(id) ON DELETE SET NULL;
  END IF;
END $$;

-- Idempotent: cùng (chủ thể, vị từ, giá trị, tin nguồn) chỉ một dòng còn sống.
CREATE UNIQUE INDEX IF NOT EXISTS facts_idempotent_uq
  ON mem.facts (org_id, subject_id, predicate, object_key, source_raw_event_id)
  WHERE source_raw_event_id IS NOT NULL AND status <> 'retracted';
CREATE INDEX IF NOT EXISTS facts_current_idx ON mem.facts (org_id, subject_id, predicate) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS facts_object_idx ON mem.facts (org_id, object_entity_id) WHERE object_entity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS facts_range_gist ON mem.facts USING gist (tstzrange(valid_from, coalesce(valid_to, 'infinity'::timestamptz)));
CREATE INDEX IF NOT EXISTS facts_open_status_idx ON mem.facts (org_id, status) WHERE status IN ('proposed', 'disputed');
CREATE INDEX IF NOT EXISTS facts_source_raw_idx ON mem.facts (source_raw_event_id) WHERE source_raw_event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS mem.fact_evidence (
  id              uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id          uuid NOT NULL REFERENCES core.organizations(id),
  fact_id         uuid NOT NULL REFERENCES mem.facts(id) ON DELETE CASCADE,
  kind            text NOT NULL CHECK (kind IN ('raw_event', 'meaning_unit', 'owner_edit')),
  raw_event_id    uuid,
  raw_received_at timestamptz,
  meaning_unit_id uuid,
  action_log_id   uuid,
  quote_status    text NOT NULL DEFAULT 'unverifiable' CHECK (quote_status IN ('verified', 'absent', 'unverifiable')),
  span            jsonb,                      -- {source_field, span_start, span_end}; raw_event do code cắt
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS fact_evidence_uq
  ON mem.fact_evidence (fact_id, kind, coalesce(raw_event_id, meaning_unit_id, action_log_id));
CREATE INDEX IF NOT EXISTS fact_evidence_raw_idx ON mem.fact_evidence (raw_event_id) WHERE raw_event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS mem.fact_status_events (
  id              uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id          uuid NOT NULL,
  fact_id         uuid NOT NULL REFERENCES mem.facts(id) ON DELETE CASCADE,
  from_status     text,
  to_status       text NOT NULL,
  valid_to_before timestamptz,
  valid_to_after  timestamptz,
  at              timestamptz NOT NULL DEFAULT clock_timestamp(),   -- không phải now(): nhiều đổi trong MỘT giao dịch vẫn tách thứ tự
  by              text NOT NULL CHECK (by IN ('extractor', 'consolidator', 'owner', 'erase')),
  reason          text
);
CREATE INDEX IF NOT EXISTS fact_status_events_fact_idx ON mem.fact_status_events (fact_id, at);

-- Bằng chứng: trigger AFTER INSERT tăng facts.evidence_n khi dòng `verified` hoặc `owner_edit`. Sự thật được INSERT trước
-- với evidence_n = 0 nên không đếm đôi.
CREATE OR REPLACE FUNCTION mem.fact_evidence_count() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.quote_status = 'verified' OR NEW.kind = 'owner_edit' THEN
    UPDATE mem.facts SET evidence_n = evidence_n + 1 WHERE id = NEW.fact_id;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS fact_evidence_count ON mem.fact_evidence;
CREATE TRIGGER fact_evidence_count AFTER INSERT ON mem.fact_evidence
  FOR EACH ROW EXECUTE FUNCTION mem.fact_evidence_count();

-- Kiểm bằng chứng HOÃN (CNS-093): đọc lại dòng theo id (không còn dòng ⇒ bỏ qua) và áp luật theo status HIỆN TẠI.
--   asserted / owner_confirmed / disputed: ≥ 1 bằng chứng verified hoặc owner_edit; proposed: ≥ 1 bằng chứng bất kỳ.
CREATE OR REPLACE FUNCTION mem.facts_need_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur_status text; n_ok int; n_any int;
BEGIN
  SELECT status INTO cur_status FROM mem.facts WHERE id = NEW.id;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  IF cur_status IN ('asserted', 'owner_confirmed', 'disputed') THEN
    SELECT count(*) INTO n_ok FROM mem.fact_evidence e
     WHERE e.fact_id = NEW.id AND (e.quote_status = 'verified' OR e.kind = 'owner_edit');
    IF n_ok < 1 THEN
      RAISE EXCEPTION 'mem.facts % (%) thiếu bằng chứng đã kiểm (cần >= 1 verified hoặc owner_edit)', NEW.id, cur_status
        USING ERRCODE = 'check_violation', CONSTRAINT = 'mem_facts_need_evidence';
    END IF;
  ELSIF cur_status = 'proposed' THEN
    SELECT count(*) INTO n_any FROM mem.fact_evidence e WHERE e.fact_id = NEW.id;
    IF n_any < 1 THEN
      RAISE EXCEPTION 'mem.facts % (proposed) thiếu bằng chứng', NEW.id
        USING ERRCODE = 'check_violation', CONSTRAINT = 'mem_facts_need_evidence';
    END IF;
  END IF;
  RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS mem_facts_need_evidence ON mem.facts;
CREATE CONSTRAINT TRIGGER mem_facts_need_evidence AFTER INSERT OR UPDATE OF status ON mem.facts
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mem.facts_need_evidence();

-- Bất biến nội dung sự thật. UPDATE chỉ được đổi valid_to, status, superseded_by, dispute_id, confirmed_by, confirmed_at,
-- evidence_n (chỉ khi gọi từ trigger khác), closed_recorded_at. DELETE chỉ khi gh.mem_erase = 'on' (SET LOCAL trong đường erase).
-- Mọi đổi status/valid_to ghi một dòng mem.fact_status_events (by/reason lấy từ biến phiên gh.mem_by / gh.mem_reason).
CREATE OR REPLACE FUNCTION mem.facts_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  by_who text := coalesce(nullif(current_setting('gh.mem_by', true), ''), 'extractor');
  allowed text[] := ARRAY['valid_to', 'status', 'superseded_by', 'dispute_id', 'confirmed_by', 'confirmed_at',
                          'evidence_n', 'closed_recorded_at'];
BEGIN
  IF coalesce(current_setting('gh.mem_erase', true), '') = 'on' THEN
    allowed := allowed || ARRAY['supersedes'];
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF coalesce(current_setting('gh.mem_erase', true), '') <> 'on' THEN
      RAISE EXCEPTION 'mem.facts không cho xoá (chỉ đường quên người mới được xoá)' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN OLD;
  END IF;
  -- Trong đường quên người, ON DELETE SET NULL của khoá ngoài `supersedes` cũng phải qua được.
  IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
    RAISE EXCEPTION 'mem.facts chỉ-thêm: không được sửa nội dung sự thật (chỉ valid_to, status, superseded_by, dispute_id, confirmed_by/at, evidence_n, closed_recorded_at)'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.evidence_n IS DISTINCT FROM OLD.evidence_n AND pg_trigger_depth() < 2 THEN
    RAISE EXCEPTION 'mem.facts.evidence_n chỉ do trigger tăng' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status OR NEW.valid_to IS DISTINCT FROM OLD.valid_to THEN
    INSERT INTO mem.fact_status_events (org_id, fact_id, from_status, to_status, valid_to_before, valid_to_after, by, reason)
    VALUES (OLD.org_id, OLD.id, OLD.status, NEW.status, OLD.valid_to, NEW.valid_to, by_who,
            nullif(current_setting('gh.mem_reason', true), ''));
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS facts_guard ON mem.facts;
CREATE TRIGGER facts_guard BEFORE UPDATE OR DELETE ON mem.facts
  FOR EACH ROW EXECUTE FUNCTION mem.facts_guard();
DROP TRIGGER IF EXISTS facts_no_truncate ON mem.facts;
CREATE TRIGGER facts_no_truncate BEFORE TRUNCATE ON mem.facts
  FOR EACH STATEMENT EXECUTE FUNCTION core.forbid_truncate();

-- Dòng đầu tiên của nhật ký trạng thái: lúc INSERT (from_status NULL).
CREATE OR REPLACE FUNCTION mem.facts_log_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO mem.fact_status_events (org_id, fact_id, from_status, to_status, valid_to_before, valid_to_after, by, reason)
  VALUES (NEW.org_id, NEW.id, NULL, NEW.status, NULL, NEW.valid_to,
          coalesce(nullif(current_setting('gh.mem_by', true), ''), 'extractor'),
          nullif(current_setting('gh.mem_reason', true), ''));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS facts_log_insert ON mem.facts;
CREATE TRIGGER facts_log_insert AFTER INSERT ON mem.facts
  FOR EACH ROW EXECUTE FUNCTION mem.facts_log_insert();

-- Nhật ký trạng thái chỉ-thêm: không sửa; xoá chỉ khi quên người (gh.mem_erase = 'on').
CREATE OR REPLACE FUNCTION mem.fact_status_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'mem.fact_status_events chỉ-thêm' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF coalesce(current_setting('gh.mem_erase', true), '') <> 'on' THEN
    RAISE EXCEPTION 'mem.fact_status_events chỉ-thêm (xoá chỉ qua đường quên người)' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN OLD;
END $$;
DROP TRIGGER IF EXISTS fact_status_events_guard ON mem.fact_status_events;
CREATE TRIGGER fact_status_events_guard BEFORE UPDATE OR DELETE ON mem.fact_status_events
  FOR EACH ROW EXECUTE FUNCTION mem.fact_status_events_guard();

CREATE TABLE IF NOT EXISTS refinery.fact_retry (
  id          uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id      uuid NOT NULL REFERENCES core.organizations(id),
  analysis_id uuid,
  fact_in     jsonb NOT NULL,
  error_code  text NOT NULL,
  attempts    int NOT NULL DEFAULT 0,
  next_at     timestamptz NOT NULL DEFAULT now(),
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fact_retry_next_idx ON refinery.fact_retry (next_at) WHERE attempts < 3;

-- ─── f) L4 (dẫn xuất, dựng lại được) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mem.relation_heat_weekly (
  org_id             uuid NOT NULL REFERENCES core.organizations(id),
  entity_a           uuid NOT NULL REFERENCES mem.entities(id),
  entity_b           uuid NOT NULL REFERENCES mem.entities(id),
  week               date NOT NULL,
  msgs_ab            int NOT NULL DEFAULT 0,
  msgs_ba            int NOT NULL DEFAULT 0,
  initiated_a        int NOT NULL DEFAULT 0,
  initiated_b        int NOT NULL DEFAULT 0,
  reply_median_s     int,
  heat               numeric(5,2) NOT NULL DEFAULT 0,
  state              text NOT NULL DEFAULT 'cold' CHECK (state IN ('hot', 'warm', 'cool', 'cold')),
  projection_version int NOT NULL DEFAULT 1,
  PRIMARY KEY (org_id, entity_a, entity_b, week)
);

CREATE TABLE IF NOT EXISTS mem.entity_summaries (
  entity_id          uuid PRIMARY KEY REFERENCES mem.entities(id) ON DELETE CASCADE,
  org_id             uuid NOT NULL REFERENCES core.organizations(id),
  summary            text NOT NULL,
  fact_ids           uuid[] NOT NULL DEFAULT '{}',
  projection_version int NOT NULL DEFAULT 1,
  built_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mem.owner_profile (
  org_id          uuid PRIMARY KEY REFERENCES core.organizations(id),
  owner_entity_id uuid NOT NULL REFERENCES mem.entities(id),
  version         int NOT NULL DEFAULT 1,
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Phiên bản Hồ sơ tăng mỗi khi sự thật `owner_*` đổi (vào/ra owner_confirmed, đổi valid_to) — lượt phân tích ghi profile_version.
CREATE OR REPLACE FUNCTION mem.owner_profile_bump() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.predicate NOT LIKE 'owner\_%' THEN
    RETURN NULL;
  END IF;
  IF TG_OP = 'UPDATE' AND NOT (OLD.status = 'owner_confirmed' OR NEW.status = 'owner_confirmed') THEN
    RETURN NULL;
  END IF;
  IF TG_OP = 'INSERT' AND NEW.status <> 'owner_confirmed' THEN
    RETURN NULL;
  END IF;
  INSERT INTO mem.owner_profile (org_id, owner_entity_id, version)
  VALUES (NEW.org_id, NEW.subject_id, 1)
  ON CONFLICT (org_id) DO UPDATE SET version = mem.owner_profile.version + 1, updated_at = now();
  RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS owner_profile_bump_ins ON mem.facts;
CREATE TRIGGER owner_profile_bump_ins AFTER INSERT ON mem.facts
  FOR EACH ROW EXECUTE FUNCTION mem.owner_profile_bump();
DROP TRIGGER IF EXISTS owner_profile_bump_upd ON mem.facts;
CREATE TRIGGER owner_profile_bump_upd AFTER UPDATE OF status, valid_to ON mem.facts
  FOR EACH ROW EXECUTE FUNCTION mem.owner_profile_bump();

-- Hồ sơ Sếp: sự thật đang hiệu lực của thực thể Sếp (kể cả entity đã gộp vào), nhóm theo owner_section.
CREATE OR REPLACE VIEW mem.v_owner_profile WITH (security_invoker = true) AS
SELECT f.org_id, f.id AS fact_id, p.owner_section, f.predicate, f.status, f.object_entity_id, f.object_value,
       f.valid_from, f.valid_to, f.confirmed_at, f.recorded_at, f.evidence_n
FROM mem.facts f
JOIN mem.predicates p ON p.code = f.predicate AND p.owner_section IS NOT NULL
JOIN mem.entities s ON s.id = f.subject_id
JOIN mem.entities c ON c.id = coalesce(s.merged_into, s.id) AND c.is_owner
WHERE f.predicate LIKE 'owner\_%'
  AND f.status IN ('owner_confirmed', 'proposed')
  AND f.valid_from <= now() AND (f.valid_to IS NULL OR f.valid_to > now());

-- ─── c3) media, luồng, lượt phân tích ───────────────────────────────────────
-- g) raw.attachments (bắt đầu được ghi ở L5) cần org_id để vào RLS (CNS-082c). Backfill từ raw.events; dòng mới tự điền.
ALTER TABLE raw.attachments ADD COLUMN IF NOT EXISTS org_id uuid REFERENCES core.organizations(id);
UPDATE raw.attachments a SET org_id = e.org_id
  FROM raw.events e WHERE e.id = a.event_id AND e.received_at = a.event_received_at AND a.org_id IS NULL;
CREATE OR REPLACE FUNCTION raw.attachments_fill_org() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.org_id IS NULL THEN
    SELECT e.org_id INTO NEW.org_id FROM raw.events e WHERE e.id = NEW.event_id AND e.received_at = NEW.event_received_at;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS attachments_fill_org ON raw.attachments;
CREATE TRIGGER attachments_fill_org BEFORE INSERT ON raw.attachments
  FOR EACH ROW EXECUTE FUNCTION raw.attachments_fill_org();
CREATE INDEX IF NOT EXISTS attachments_org_idx ON raw.attachments (org_id);

CREATE TABLE IF NOT EXISTS clean.media_texts (
  id               uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id           uuid NOT NULL REFERENCES core.organizations(id),
  raw_event_id     uuid NOT NULL,
  raw_received_at  timestamptz NOT NULL,
  attachment_id    uuid NOT NULL REFERENCES raw.attachments(id),
  kind             text NOT NULL CHECK (kind IN ('transcript', 'ocr', 'description')),
  text             text,
  lang             text,
  engine           text NOT NULL,
  engine_version   text NOT NULL,
  confidence       numeric(4,3),
  status           text NOT NULL CHECK (status IN ('ok', 'failed', 'skipped_budget', 'too_large')),
  error_code       text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (attachment_id, kind, engine_version)
);
CREATE INDEX IF NOT EXISTS media_texts_event_idx ON clean.media_texts (raw_event_id);

CREATE TABLE IF NOT EXISTS refinery.group_watermarks (
  org_id            uuid NOT NULL REFERENCES core.organizations(id),
  thread_key        text NOT NULL,          -- g:<group_id> | dm:<person_id>:<account_id> | (về sau) mail:<thread>
  group_id          uuid,
  last_received_at  timestamptz,
  last_event_id     uuid,
  last_analysis_at  timestamptz,
  pending_n         int NOT NULL DEFAULT 0,
  priority          text NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal', 'low', 'stretched')),
  stretched_at      timestamptz,
  capped_at         timestamptz,
  stretched_minutes int NOT NULL DEFAULT 0,
  capped_minutes    int NOT NULL DEFAULT 0,
  PRIMARY KEY (org_id, thread_key)
);

CREATE TABLE IF NOT EXISTS refinery.analyses (
  id               uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id           uuid NOT NULL REFERENCES core.organizations(id),
  thread_key       text NOT NULL,
  run_id           uuid,
  window_from      timestamptz,
  window_to        timestamptz,
  new_n            int NOT NULL DEFAULT 0,
  context_n        int NOT NULL DEFAULT 0,
  facts_in_n       int NOT NULL DEFAULT 0,
  profile_version  int,
  contract_version text,
  model            text,
  tokens_in        int,
  tokens_out       int,
  cache_read       int,
  stage            text NOT NULL CHECK (stage IN ('extract', 'verify', 'verify_strong')),
  status           text NOT NULL CHECK (status IN ('ok', 'contract_error', 'failed')),
  errors           jsonb NOT NULL DEFAULT '[]',     -- mã lỗi + id, KHÔNG nội dung
  started_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz
);
CREATE INDEX IF NOT EXISTS analyses_thread_idx ON refinery.analyses (org_id, thread_key, started_at DESC);

-- Chi phí theo luồng: MỘT nguồn với gh/ai_cost.py (agent.model_calls × agent.model_prices, VND; model CLI thuê bao = 0 ₫).
-- Cột thread_key/analysis_id do L2 ghi (gh/ai_cost.py); trần/giãn nhịp lưu ở refinery.group_watermarks.
ALTER TABLE agent.model_calls
  ADD COLUMN IF NOT EXISTS thread_key  text,
  ADD COLUMN IF NOT EXISTS analysis_id uuid;
CREATE INDEX IF NOT EXISTS model_calls_thread_idx ON agent.model_calls (org_id, thread_key, at DESC) WHERE thread_key IS NOT NULL;

CREATE OR REPLACE VIEW refinery.group_budget WITH (security_invoker = true) AS
SELECT c.org_id,
       c.thread_key,
       (c.at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS day,
       coalesce(sum(c.tokens_in), 0)::bigint        AS tokens_in,
       coalesce(sum(c.tokens_out), 0)::bigint       AS tokens_out,
       coalesce(sum(coalesce(c.cost_vnd,
         CASE WHEN p.kind IN ('antigravity_cli', 'claude_code_cli') THEN 0
              WHEN mp.model_id IS NOT NULL THEN
                (coalesce(c.tokens_in, 0) * coalesce(mp.in_vnd_per_mtok, 0)
                 + coalesce(c.tokens_out, 0) * coalesce(mp.out_vnd_per_mtok, 0)) / 1000000
         END)), 0)::numeric(16,2)                   AS cost_vnd,
       count(*)::int                                AS calls
FROM agent.model_calls c
LEFT JOIN agent.models m ON m.id = c.model_id
LEFT JOIN agent.providers p ON p.id = m.provider_id
LEFT JOIN agent.model_prices mp ON mp.model_id = c.model_id
WHERE c.thread_key IS NOT NULL AND c.status = 'ok'
GROUP BY c.org_id, c.thread_key, (c.at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date;

-- ─── h) backfill thực thể từ hồ sơ có sẵn (chưa gộp, chưa xoá) — chạy lại không nhân đôi ────────────────
INSERT INTO mem.entities (org_id, entity_type, person_id, display_name)
SELECT p.org_id, 'person', p.id, p.display_name
  FROM core.persons p
 WHERE p.merged_into_id IS NULL AND p.deleted_at IS NULL
ON CONFLICT (org_id, person_id) WHERE person_id IS NOT NULL DO NOTHING;

INSERT INTO mem.entities (org_id, entity_type, group_id, display_name)
SELECT g.org_id, 'group', g.id, g.name
  FROM core.groups g
ON CONFLICT (org_id, group_id) WHERE group_id IS NOT NULL DO NOTHING;

-- Bí danh gốc: tên hiển thị của từng thực thể (chạy lại không nhân đôi nhờ UNIQUE index biểu thức).
INSERT INTO mem.aliases (org_id, entity_id, alias, source, confidence)
SELECT e.org_id, e.id, e.display_name, 'display_name', 1.0
  FROM mem.entities e
 WHERE e.merged_into IS NULL AND e.forgotten_at IS NULL AND btrim(e.display_name) <> ''
ON CONFLICT (entity_id, alias_folded, coalesce(scope_group_id, '00000000-0000-0000-0000-000000000000'::uuid)) DO NOTHING;

-- ─── i) RLS theo org_id + GRANT cho gh_app (khuôn 0042) ─────────────────────
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'mem.entities', 'mem.aliases', 'mem.mentions', 'mem.group_contexts',
    'mem.disputes', 'mem.facts', 'mem.fact_evidence', 'mem.fact_status_events',
    'mem.relation_heat_weekly', 'mem.entity_summaries', 'mem.owner_profile',
    'clean.media_texts', 'refinery.group_watermarks', 'refinery.analyses', 'refinery.fact_retry',
    'raw.attachments'
  ] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS org_isolation ON %s', t);
    EXECUTE format($p$CREATE POLICY org_isolation ON %s
      USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
             OR current_setting('app.org_id', true) IS NULL
             OR current_setting('app.org_id', true) = '')
      WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
             OR current_setting('app.org_id', true) IS NULL
             OR current_setting('app.org_id', true) = '')$p$, t);
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT USAGE ON SCHEMA mem TO gh_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA mem TO gh_app;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA mem TO gh_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON biz.signal_kinds, clean.media_texts,
          refinery.group_watermarks, refinery.analyses, refinery.fact_retry TO gh_app;
    GRANT SELECT ON refinery.group_budget TO gh_app;
    -- Bảng tra do migration quản (R6: thêm vị từ/loại tín hiệu = INSERT qua migration có review): ứng dụng chỉ ĐỌC.
    -- (mem.context_types giữ UPDATE: Sếp chỉnh chính sách bối cảnh trên Console.)
    REVOKE INSERT, UPDATE, DELETE ON mem.predicates, biz.signal_kinds FROM gh_app;
    REVOKE INSERT, DELETE ON mem.context_types FROM gh_app;
    -- Nhật ký trạng thái chỉ-thêm: ứng dụng không UPDATE (trigger vẫn chặn; DELETE chỉ khi quên người).
    REVOKE UPDATE ON mem.fact_status_events FROM gh_app;
  END IF;
END $$;
