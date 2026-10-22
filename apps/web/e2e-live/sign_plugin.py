"""Ký (Ed25519) một manifest plugin cho luồng "nạp plugin từ tệp" thật (giai đoạn 5.3 luồng 7).

Mô phỏng người phát triển ký gói TRƯỚC khi nộp (khoá riêng không bao giờ đi qua trình duyệt/API). Payload phải
khớp CHÍNH XÁC cách `gh.plugins_api.routes.install_local` kiểm: `orjson.dumps(manifest, OPT_SORT_KEYS) + b"|" +
code_sha256_hex`. Khoá công khai tương ứng nằm ở `GH_PLUGIN_TRUSTED_SIGNING_KEYS` mà `run.sh` truyền cho api.

Dùng: python3 sign_plugin.py <đường dẫn khoá riêng, 32 byte raw> <manifest JSON (chuỗi)> <code_sha256 hex>
In ra: chữ ký base64 (đúng ô "Chữ ký" ở hộp thoại Nạp plugin từ tệp).
"""
import base64
import json
import sys

import orjson
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

key_path, manifest_json, code_sha256 = sys.argv[1], sys.argv[2], sys.argv[3]
priv = Ed25519PrivateKey.from_private_bytes(open(key_path, "rb").read())
manifest = json.loads(manifest_json)
payload = orjson.dumps(manifest, option=orjson.OPT_SORT_KEYS) + b"|" + code_sha256.encode()
print(base64.b64encode(priv.sign(payload)).decode())
