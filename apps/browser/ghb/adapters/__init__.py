"""Adapter theo nền tảng — khoá trùng `gh.social.platforms.PLATFORMS` phía api."""

from ghb.adapters.base import Adapter
from ghb.adapters.facebook import FacebookAdapter

ADAPTERS: dict[str, Adapter] = {a.key: a for a in (FacebookAdapter(),)}
