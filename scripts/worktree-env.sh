#!/usr/bin/env bash
# Chuẩn bị một git worktree để chạy test mà không cài lại phụ thuộc: mượn node_modules và .venv của bản chính,
# nhưng các gói nội bộ (@gen-harness/*) và mã Python `gh` trỏ vào chính worktree.
#   source scripts/worktree-env.sh /đường/dẫn/bản/chính  <tên-riêng>   (vd: c1)
# Sau đó: make/pytest/npm chạy như bình thường; CSDL mẫu, db Redis và cổng e2e tách riêng theo <tên-riêng>.
set -e
MAIN="${1:?cần đường dẫn bản chính}"; TAG="${2:?cần tên riêng}"
WT="$(git rev-parse --show-toplevel)"
if [ "$WT" = "$MAIN" ]; then echo "Đang ở bản chính, không cần"; return 0 2>/dev/null || exit 0; fi
if [ ! -e "$WT/node_modules" ]; then
  mkdir "$WT/node_modules"
  for e in "$MAIN"/node_modules/* "$MAIN"/node_modules/.bin "$MAIN"/node_modules/.package-lock.json; do
    [ "$(basename "$e")" = "@gen-harness" ] && continue
    ln -s "$e" "$WT/node_modules/$(basename "$e")"
  done
  mkdir "$WT/node_modules/@gen-harness"
  for p in contracts tokens ui; do ln -s "../../packages/$p" "$WT/node_modules/@gen-harness/$p"; done
  ln -s ../../apps/web "$WT/node_modules/@gen-harness/web"
fi
[ -e "$WT/apps/web/node_modules" ] || ln -s "$MAIN/apps/web/node_modules" "$WT/apps/web/node_modules"
[ -e "$WT/apps/api/.venv" ] || ln -s "$MAIN/apps/api/.venv" "$WT/apps/api/.venv"
# PYTHONPATH đứng trước .pth của bản cài editable → `import gh` lấy mã của worktree.
export PYTHONPATH="$WT/apps/api"
export GH_TEST_TEMPLATE="gh_test_${TAG}"
case "$TAG" in *[0-9]*) N="${TAG//[!0-9]/}";; *) N=9;; esac
export GH_TEST_REDIS="redis://localhost:6379/$((N % 14 + 1))"
export E2E_PORT="$((5200 + N))"
echo "worktree $WT · python gh=$(cd "$WT/apps/api" && .venv/bin/python -c 'import gh,os;print(os.path.dirname(gh.__file__))') · test db $GH_TEST_TEMPLATE · $GH_TEST_REDIS · e2e :$E2E_PORT"
