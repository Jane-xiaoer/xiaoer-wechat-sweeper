#!/bin/bash
# 启动器测试：找不到 Python 时必须明确失败，而不是静默什么都不做。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
L="${ROOT}/launcher.sh"
PASS=0; FAIL=0

ok() {   # ok <名字> <期望> <实际>
  if [[ "$3" == "$2" ]]; then
    echo "  ✅ $1"; PASS=$((PASS+1))
  else
    echo "  ❌ $1 → 期望 [$2]，实际 [$3]"; FAIL=$((FAIL+1))
  fi
}

echo "找 Python"

# 1. 本机装了 CLT，应该能找到一个能跑的
got="$(bash "${L}" --which 2>/dev/null)"
rc=$?
ok "本机能找到可用的 python3" "0" "${rc}"
[[ -x "${got}" ]] && { echo "     找到 ${got}"; PASS=$((PASS+1)); } \
  || { echo "  ❌ 找到的不可执行：${got}"; FAIL=$((FAIL+1)); }

# 2. 候选全都不存在时，必须以 20 退出（让 AppleScript 弹窗告诉用户）
XIAOER_PY_CANDIDATES="/nope/python3 /also/nope/python3" \
  bash "${L}" --which >/dev/null 2>&1
ok "一个都找不到时退出码 20" "20" "$?"

# 3. 候选存在但根本不是 python，也要当作找不到
TMP="$(mktemp -d)"
printf '#!/bin/bash\nexit 1\n' > "${TMP}/python3"
chmod +x "${TMP}/python3"
XIAOER_PY_CANDIDATES="${TMP}/python3" bash "${L}" --which >/dev/null 2>&1
ok "假的 python3 不算数" "20" "$?"

# 4. 版本太老的也要当作找不到（panel.py 要 3.8+）
printf '#!/bin/bash\n[[ "$*" == *version_info* ]] && exit 1\nexit 0\n' > "${TMP}/python3"
chmod +x "${TMP}/python3"
XIAOER_PY_CANDIDATES="${TMP}/python3" bash "${L}" --which >/dev/null 2>&1
ok "版本太老不算数" "20" "$?"

# 5. 排在前面的优先
printf '#!/bin/bash\nexit 0\n' > "${TMP}/first"
printf '#!/bin/bash\nexit 0\n' > "${TMP}/second"
chmod +x "${TMP}/first" "${TMP}/second"
got="$(XIAOER_PY_CANDIDATES="${TMP}/first ${TMP}/second" bash "${L}" --which 2>/dev/null)"
ok "按候选顺序挑第一个" "${TMP}/first" "${got}"

rm -rf "${TMP}"
echo
echo "通过 ${PASS}，失败 ${FAIL}"
[[ ${FAIL} -eq 0 ]]
