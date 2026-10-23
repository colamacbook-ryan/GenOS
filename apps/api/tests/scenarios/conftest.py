"""Đưa scripts/intel-eval và scripts/import-export (thư mục có gạch ngang) vào sys.path."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
for _sub in ("scripts/intel-eval", "scripts/import-export"):
    _p = str(REPO / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
