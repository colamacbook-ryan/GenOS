"""Chạy `gh.identity.service.detect` thật trên CSDL (giai đoạn 5.3 luồng 6 — hợp nhất danh tính).

`detect()` chỉ được nối dây như một JOB định kỳ của worker (`gh.worker.detect_identities`), không có endpoint
HTTP gọi tay — script này gọi thẳng đúng hàm đó trên một phiên DB mới, giống cách `apps/api/tests/
test_identity_notebook.py` làm ở test tích hợp, để `live-phase3.spec.ts` có thể ép chạy dò cặp trùng ngay khi
cần thay vì đợi lịch.
"""
import asyncio, os, sys
sys.path.insert(0, os.environ.get("GH_API_DIR", os.path.join(os.path.dirname(__file__), "../../api")))
from sqlalchemy import text
from gh.db import sessionmaker
from gh.identity import service as identity


async def main() -> None:
    sm = sessionmaker()
    async with sm() as db:
        org = (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()
        n = await identity.detect(db, org)
        await db.commit()
    print(f"identity.detect: {n} cặp mới")


asyncio.run(main())
