#!/bin/bash
# 验签链集成测试。全程在沙盒目录，不碰 /Applications。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIX="$ROOT/.tmp/fixtures"
WORK="$ROOT/.tmp/verify-test"
PASS=0; FAIL=0

bash "$ROOT/tests/make_fixtures.sh" >/dev/null

check() {   # check <名字> <zip> <期望 ok|reject>
  local name="$1" zip="$2" want="$3"
  rm -rf "$WORK"; mkdir -p "$WORK"
  ditto -x -k "$zip" "$WORK" 2>/dev/null
  local app
  app="$(find "$WORK" -maxdepth 1 -name '*.app' | head -1)"
  local got
  got="$(cd "$ROOT" && python3 -c "
import sys, updater
ok, why = updater.verify_app('${app}')
print('ok' if ok else 'reject:' + why)
" 2>&1)"
  # 变量名后紧跟字符时必须写 ${}，否则 macOS 自带的 bash 3.2
  # 会把后面的中文当成变量名的一部分，报 "unbound variable"
  if [[ "${got}" == "${want}"* ]]; then
    echo "  ✅ ${name} → ${got}"; PASS=$((PASS+1))
  else
    echo "  ❌ ${name} → 期望 ${want}，实际 ${got}"; FAIL=$((FAIL+1))
  fi
}

echo "验签链测试"
check "正常包全过"         "$FIX/good.zip"     ok
check "别人签的包被拒"      "$FIX/foreign.zip"  reject

# 篡改包测的是 sha256 那一关，不进 verify_app
echo "sha256 关卡"
GOOD_SUM="$(shasum -a 256 "$FIX/good.zip" | cut -d' ' -f1)"
TAMP_SUM="$(shasum -a 256 "$FIX/tampered.zip" | cut -d' ' -f1)"
if [[ "$GOOD_SUM" != "$TAMP_SUM" ]]; then
  echo "  ✅ 篡改一个字节后 sha256 不同"; PASS=$((PASS+1))
else
  echo "  ❌ 篡改后 sha256 竟然一样"; FAIL=$((FAIL+1))
fi

rm -rf "${WORK}"
echo
echo "通过 ${PASS}，失败 ${FAIL}"
[[ ${FAIL} -eq 0 ]]
