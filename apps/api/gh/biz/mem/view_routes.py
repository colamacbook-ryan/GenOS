"""Khung rỗng (L4 sở hữu): các đường xem "Tôi" / Toàn cảnh / Bối cảnh / Đối tượng / Bằng chứng (spec §7.2b).
`gh.biz.mem.routes` gắn router này vào router chung của cụm."""

from fastapi import APIRouter

router = APIRouter(tags=["mem-view"])
