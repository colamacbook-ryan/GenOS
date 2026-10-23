# ruff: noqa: E501
"""Lõi trí nhớ L2: thực thể, bí danh, nhắc tới, móc gộp người, mem.fold, đăng ký cụm, vị từ, một nơi ghi sự thật."""

import ast
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from gh.biz import CLUSTERS
from gh.biz.mem import aliases, entities, facts, mentions, predicates
from gh.db import sessionmaker
from gh.identity import service as ident
from gh.textnorm import strip_accents
from tests.mem_helpers import T, mk_entity, mk_org, mk_person_row, mkfact, rows
from tests.phase2 import org_id
from tests.test_gop_nguoi_v016x import _detect, _merge_pending, _two_people, _user

GH_DIR = Path(__file__).resolve().parents[1] / "gh"


# ─── mem.fold ───────────────────────────────────────────────────────────────

_VI_WORDS = ["Đồng Nai", "Nguyễn Thị Hương", "thợ mộc", "Lê Văn Bảy", "ĐÀ NẴNG", "Phạm Ngọc Ánh", "quả ổi", "Trường Sơn",
             "Hải Phòng", "chị Tư", "đường Đinh Tiên Hoàng", "Ông Ba Kho", "ừ ờ ớ ở ỡ", "Đặng Thùy Dương", "Việt Nam", "xin chào",
             "ẩu đả", "nghĩa vụ & lời hứa", "0901.234.567", "Bảy_Kho-01", "ñandú Zoë", "ÀÁẢÃẠ ĂẰẮẲẴẶ ÂẦẤẨẪẬ", "ÈÉẺẼẸ ÊỀẾỂỄỆ",
             "ÌÍỈĨỊ ÒÓỎÕỌ ÔỒỐỔỖỘ ƠỜỚỞỠỢ", "ÙÚỦŨỤ ƯỪỨỬỮỰ ỲÝỶỸỴ", "àáảãạ ăằắẳẵặ âầấẩẫậ", "èéẻẽẹ êềếểễệ", "ìíỉĩị òóỏõọ ôồốổỗộ ơờớởỡợ",
             "ùúủũụ ưừứửữự ỳýỷỹỵ", "đ Đ"]


async def test_mem_core_fold_matches_python_strip_accents_on_200_strings(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    samples: list[str] = []
    i = 0
    while len(samples) < 200:
        a, b = _VI_WORDS[i % len(_VI_WORDS)], _VI_WORDS[(i * 7 + 3) % len(_VI_WORDS)]
        samples.append(f"{a} {b}" if i % 2 else a.upper() if i % 3 == 0 else a)
        i += 1
    for s in samples:
        got = (await db.execute(text("SELECT mem.fold(:s)"), {"s": s})).scalar_one()
        assert got == strip_accents(s).lower(), (s, got)


async def test_mem_core_fold_is_immutable_and_usable_in_generated_column(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    vol = (await db.execute(text("SELECT provolatile FROM pg_proc WHERE oid = 'mem.fold(text)'::regprocedure"))).scalar_one()
    assert (vol.decode() if isinstance(vol, bytes) else vol) == "i"
    src = (await db.execute(text("SELECT prosrc FROM pg_proc WHERE oid = 'mem.fold(text)'::regprocedure"))).scalar_one()
    assert "public.unaccent" in src and "'public.unaccent'::regdictionary" in src      # ghi đủ schema (CNS-066)


# ─── thực thể ───────────────────────────────────────────────────────────────

async def test_mem_core_ensure_person_group_lazy_and_idempotent(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    pid = await mk_person_row(db, org, "Chị Tư")
    e1 = await entities.ensure_person(db, org, pid)
    e2 = await entities.ensure_person(db, org, pid)
    assert e1.id == e2.id and e1.entity_type == "person" and e1.person_id == pid and e1.display_name == "Chị Tư"
    # bí danh gốc từ tên hiển thị
    m = await aliases.resolve(db, org, "chi tu")
    assert [x.entity_id for x in m] == [e1.id]
    with pytest.raises(entities.EntityError):
        await entities.ensure_person(db, org, uuid.uuid4())
    # nhóm
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": org})).scalar_one()
    gid = (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name)
                                    VALUES (:o, 'GRP-1', :c, 'g1', 'Nhóm Kho') RETURNING id"""), {"o": org, "c": ch})).scalar_one()
    g1, g2 = await entities.ensure_group(db, org, gid), await entities.ensure_group(db, org, gid)
    assert g1.id == g2.id and g1.entity_type == "group" and g1.group_id == gid


async def test_mem_core_ensure_person_follows_merged_person(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    keep, drop = await mk_person_row(db, org, "An"), await mk_person_row(db, org, "An B")
    await db.execute(text("UPDATE core.persons SET merged_into_id = :k WHERE id = :d"), {"k": keep, "d": drop})
    ek = await entities.ensure_person(db, org, keep)
    ed = await entities.ensure_person(db, org, drop)
    assert ed.id == ek.id


async def test_mem_core_owner_entity_unique(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    o1, o2 = await entities.owner(db, org), await entities.owner(db, org)
    assert o1.id == o2.id and o1.is_owner
    other = await mk_entity(db, org, "Khác")
    with pytest.raises(Exception, match="entities_one_owner_uq|duplicate"):
        async with db.begin_nested():
            await db.execute(text("UPDATE mem.entities SET is_owner = true WHERE id = :i"), {"i": other})
    with pytest.raises(entities.EntityError):                       # không gộp Sếp vào người khác
        await entities.merge(db, org, o1.id, other)


async def test_mem_core_entity_check_constraints(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    pid = await mk_person_row(db, org, "P")
    for sql in ("INSERT INTO mem.entities (org_id, entity_type, group_id, display_name) VALUES (:o, 'person', :g, 'x')",
                "INSERT INTO mem.entities (org_id, entity_type, person_id, display_name) VALUES (:o, 'org', :p, 'x')"):
        with pytest.raises(Exception, match="check"):
            async with db.begin_nested():
                await db.execute(text(sql), {"o": org, "g": uuid.uuid4(), "p": pid})
    e = await mk_entity(db, org, "Z")
    with pytest.raises(Exception, match="check"):
        async with db.begin_nested():
            await db.execute(text("UPDATE mem.entities SET merged_into = id WHERE id = :i"), {"i": e})


# ─── bí danh ────────────────────────────────────────────────────────────────

async def test_mem_core_alias_scope_and_resolution(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": org})).scalar_one()
    g_kho = (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name)
                                      VALUES (:o, 'GRP-1', :c, 'g1', 'Kho') RETURNING id"""), {"o": org, "c": ch})).scalar_one()
    g_ban = (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name)
                                      VALUES (:o, 'GRP-2', :c, 'g2', 'Bán') RETURNING id"""), {"o": org, "c": ch})).scalar_one()
    bay_kho = await mk_entity(db, org, "Nguyễn Văn Bảy")
    bay_ban = await mk_entity(db, org, "Lê Bảy")
    await aliases.add(db, org, bay_kho, "anh Bảy", scope_group_id=g_kho, source="mention", confidence=0.9)
    await aliases.add(db, org, bay_ban, "anh Bảy", scope_group_id=g_ban, source="mention", confidence=0.9)
    # cùng cách gọi, khác nhóm ⇒ khác người
    assert [m.entity_id for m in await aliases.resolve(db, org, "Anh Bảy", g_kho)][:1] == [bay_kho]
    assert [m.entity_id for m in await aliases.resolve(db, org, "anh bay", g_ban)][:1] == [bay_ban]
    assert await aliases.resolve(db, org, "anh Bảy", None) == []        # ngoài nhóm: bí danh riêng không áp
    # bỏ từ xưng hô: "Nguyễn Văn Bảy" khớp tên gốc; "chị Nguyễn Văn Bảy" cũng
    assert (await aliases.resolve(db, org, "chị Nguyễn Văn Bảy"))[0].entity_id == bay_kho
    # gần đúng
    fz = await aliases.resolve(db, org, "Nguyen Van Bai")
    assert fz and fz[0].entity_id == bay_kho and fz[0].method == "alias_fuzzy"
    # thêm lại = làm mới, không nhân đôi; chữ rỗng bỏ qua
    a1 = await aliases.add(db, org, bay_kho, "ANH BẢY", scope_group_id=g_kho)
    a2 = await aliases.add(db, org, bay_kho, "anh bảy", scope_group_id=g_kho)
    assert a1 == a2 and await aliases.add(db, org, bay_kho, "   ") is None
    n = (await db.execute(text("SELECT count(*) FROM mem.aliases WHERE entity_id = :e"), {"e": bay_kho})).scalar_one()
    assert n == 2                                                       # tên gốc + "anh Bảy"
    # sau gộp: resolve trả thực thể đích
    await entities.merge(db, org, bay_ban, bay_kho)
    assert (await aliases.resolve(db, org, "anh Bảy", g_ban))[0].entity_id == bay_kho


async def test_mem_core_alias_generated_column_is_folded(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    e = await mk_entity(db, org, "Đặng Thùy Dương")
    got = (await db.execute(text("SELECT alias_folded FROM mem.aliases WHERE entity_id = :e"), {"e": e})).scalar_one()
    assert got == "dang thuy duong"
    with pytest.raises(Exception, match="generated|alias_folded"):
        async with db.begin_nested():
            await db.execute(text("UPDATE mem.aliases SET alias_folded = 'x' WHERE entity_id = :e"), {"e": e})


# ─── nhắc tới ───────────────────────────────────────────────────────────────

async def test_mem_core_mentions_link_candidate_unresolved(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    tu = await mk_entity(db, org, "Chị Tư Đồng Nai")
    unit, when = uuid.uuid4(), T(3)
    m1 = await mentions.link(db, org, meaning_unit_id=unit, meaning_observed_at=when, surface="chị Tư Đồng Nai")
    assert (m1.status, m1.method, m1.entity_id) == ("linked", "alias_exact", tu)
    m2 = await mentions.link(db, org, meaning_unit_id=unit, meaning_observed_at=when, surface="chị Tu Dong Nay")
    assert m2.status == "candidate" and m2.method == "alias_fuzzy" and m2.entity_id == tu
    m3 = await mentions.link(db, org, meaning_unit_id=unit, meaning_observed_at=when, surface="ông Chín Cần Thơ")
    assert (m3.status, m3.entity_id) == ("unresolved", None)
    sender = await mentions.link(db, org, meaning_unit_id=unit, meaning_observed_at=when, surface="em", sender_entity_id=tu)
    assert (sender.status, sender.method, sender.confidence) == ("linked", "sender_id", 1.0)
    opened = await mentions.open_mentions(db, org)
    assert {m.status for m in opened} == {"candidate", "unresolved"}
    # hai ứng viên chính xác ngang nhau ⇒ không tự nối
    dup = await mk_entity(db, org, "Chín")
    await mk_entity(db, org, "Chín")
    amb = await mentions.link(db, org, meaning_unit_id=unit, meaning_observed_at=when, surface="Chín")
    assert amb.status == "candidate" and amb.entity_id in {dup} | set((await db.execute(text("SELECT id FROM mem.entities WHERE display_name = 'Chín'"))).scalars())


# ─── móc gộp người (identity) ───────────────────────────────────────────────

async def test_mem_core_identity_merge_redirects_entities_and_revert_splits(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    pa, pb = await _two_people(db, org)
    ea, eb = await entities.ensure_person(db, org, pa), await entities.ensure_person(db, org, pb)
    await facts.assert_fact(db, mkfact(org, ea.id, "0901", T(1), speaker=ea.id))
    await facts.assert_fact(db, mkfact(org, eb.id, "0902", T(2), speaker=eb.id))
    await db.commit()
    assert await _detect(org) == 1
    out = await _merge_pending(db, org)
    kept, dropped = uuid.UUID(out["person"]["id"]), uuid.UUID(out["merged"]["id"])
    ek = (await entities.ensure_person(db, org, kept)).id
    ed = (await db.execute(text("SELECT id FROM mem.entities WHERE person_id = :p"), {"p": dropped})).scalar_one()
    assert (await entities.get(db, ed)).merged_into == ek  # type: ignore[union-attr]
    # đối soát sau gộp: hai số điện thoại trái nhau ⇒ disputed (không bị dời, không mất)
    st = (await db.execute(text("SELECT status FROM mem.facts WHERE predicate = 'contact_phone' ORDER BY object_value"))).scalars().all()
    assert st == ["disputed", "disputed"]
    snap = (await db.execute(text("SELECT snapshot FROM core.identity_merge_log WHERE op = 'merge'"))).scalar_one()
    assert snap["mem"]["target"] == str(ek) and snap["mem"]["source"] == str(ed)
    # đảo ngược ⇒ tách lại thực thể
    log_id = (await db.execute(text("SELECT id FROM core.identity_merge_log WHERE op = 'merge'"))).scalar_one()
    async with sessionmaker()() as s:
        await ident.revert(s, org, log_id, await _user(db))
        await s.commit()
    await db.commit()
    assert (await entities.get(db, ed)).merged_into is None  # type: ignore[union-attr]


async def test_mem_core_person_refs_excluded_documents_mem_columns() -> None:
    for k in ("mem.entities.person_id", "mem.facts.subject_id", "mem.disputes.subject_id"):
        assert k in ident.PERSON_REFS_EXCLUDED and len(ident.PERSON_REFS_EXCLUDED[k]) > 20


# ─── vị từ, cụm, một nơi ghi ────────────────────────────────────────────────

async def test_mem_core_predicates_seed_29_with_cardinality_and_auto_supersede(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    allp = await predicates.all_predicates(db)
    assert len(allp) == 29
    singles = {p.code for p in allp if p.is_single}
    assert singles == {"contact_phone", "payment_account", "works_at", "lives_in", "role_in_group"}
    no_auto = {p.code for p in allp if not p.auto_supersede}
    assert no_auto == {"payment_account"}                              # CNS-096
    pa = await predicates.get(db, "payment_account")
    assert pa.sensitivity == "sensitive" and pa.dispute_signal_kind == "rui_ro_lua_dao"
    assert {p.owner_section for p in allp if p.code.startswith("owner_")} == {
        "goals", "resources", "key_relations", "values_limits", "rhythm", "report_prefs"}
    with pytest.raises(predicates.UnknownPredicate):
        await predicates.get(db, "xyz")
    assert (await db.execute(text("SELECT count(*) FROM biz.signal_kinds"))).scalar_one() == 12


def test_mem_core_clusters_register_mem_and_media_and_routers_load() -> None:
    assert CLUSTERS[-2:] == ("mem", "media")
    from gh import biz

    assert len(biz.routers()) == len(CLUSTERS)
    paths = {getattr(r, "path", "") for rt in biz.routers() for r in rt.routes}
    assert {"/facts", "/facts/{fact_id}/correct", "/groups/{group_id}/contexts"} <= paths


def test_mem_core_jobs_collects_job_modules(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import importlib
    import sys

    from gh.biz.mem import jobs as memjobs

    pkg = tmp_path / "fakepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "job_a.py").write_text("HOOKS = []\nJOBS = [(1, {'minute': {1}})]\n")
    (pkg / "job_b.py").write_text("JOBS = [(2, {})]\n")
    (pkg / "other.py").write_text("JOBS = [(99, {})]\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        mods = memjobs.job_modules(importlib.import_module("fakepkg"))
        assert [m.__name__ for m in mods] == ["fakepkg.job_a", "fakepkg.job_b"]
        assert [j[0] for j in memjobs.collect(mods, "JOBS")] == [1, 2]
        assert memjobs.collect(mods, "HOOKS") == []
    finally:
        sys.modules.pop("fakepkg", None)
    assert memjobs.JOBS == [] or all(isinstance(j, tuple) for j in memjobs.JOBS)


def test_mem_core_single_writer_ast_only_facts_py_inserts_into_mem_facts() -> None:
    """`assert_fact` là MỘT nơi duy nhất ghi `mem.facts` (spec §4A.1): duyệt AST mọi chuỗi trong gh/ — chỉ `facts.py` được có
    câu INSERT INTO mem.facts, và trong đó đúng MỘT chỗ (`_insert_row`)."""
    import re

    pat = re.compile(r"insert\s+into\s+mem\.facts\s*\(", re.IGNORECASE)
    hits: dict[str, int] = {}
    for path in GH_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and pat.search(node.value):
                rel = str(path.relative_to(GH_DIR))
                hits[rel] = hits.get(rel, 0) + 1
    assert hits == {"biz/mem/facts.py": 1}, hits


def test_mem_core_no_other_module_deletes_facts_outside_forget() -> None:
    bad = []
    for path in GH_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "delete from mem.facts" in " ".join(node.value.split()).lower():
                bad.append(str(path.relative_to(GH_DIR)))
    assert bad == ["biz/mem/forget.py"], bad


async def test_mem_core_rows_helper_sanity(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    e = await mk_entity(db, org)
    await facts.assert_fact(db, mkfact(org, e, "x", T(2)))
    assert await rows(db, e) == [(2, None, "asserted", "x")]
