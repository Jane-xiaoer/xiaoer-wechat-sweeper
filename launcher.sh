#!/bin/bash
# 小耳微信清扫器 · macOS 启动器
#
# 为什么要有这个文件：以前 AppleScript 直接跑 /usr/bin/python3，
# 可那玩意儿根本不是 Python，只是 xcode-select 的转发壳
# （标识就叫 com.apple.dt.xcode_select.tool-shim-public），
# 真身在 Command Line Tools 里。普通用户没装过 Xcode 就没有它。
# 加上启动命令把输出全丢进了 /dev/null，失败时用户零反馈——
# 只看到 Dock 图标闪一下就没了，也就是大家说的「双击闪退」。
#
# 现在：按顺序找一个真能跑的 python3，找不到就以 20 退出，
# 让 AppleScript 弹窗说人话；找到了就把日志写进文件，方便以后排查。
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="${HOME}/Library/Logs"
LOG="${LOG_DIR}/小耳微信清扫器.log"

# 顺序是有讲究的：先找真实的解释器，最后才轮到 /usr/bin/python3 那个壳。
# 直接去碰壳会触发系统那个「要装开发者工具」的弹窗，很吓人。
default_candidates() {
  echo /opt/homebrew/bin/python3
  echo /usr/local/bin/python3
  ls -1 /Library/Frameworks/Python.framework/Versions/*/bin/python3 2>/dev/null | sort -rV
  echo /Library/Developer/CommandLineTools/usr/bin/python3
  echo /usr/bin/python3
}

find_python() {
  local list
  if [[ -n "${XIAOER_PY_CANDIDATES:-}" ]]; then
    list="$(printf '%s\n' ${XIAOER_PY_CANDIDATES})"
  else
    list="$(default_candidates)"
  fi
  while IFS= read -r p; do
    [[ -n "${p}" && -x "${p}" ]] || continue
    # 光存在不够，得真能跑、且版本够新（panel.py 要 3.8+）
    "${p}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
      >/dev/null 2>&1 || continue
    echo "${p}"
    return 0
  done <<< "${list}"
  return 1
}

PY="$(find_python)" || exit 20

if [[ "${1:-}" == "--which" ]]; then
  echo "${PY}"
  exit 0
fi

mkdir -p "${LOG_DIR}" 2>/dev/null
{
  echo "──────── $(date '+%Y-%m-%d %H:%M:%S') 启动 ────────"
  echo "解释器：${PY}"
} >> "${LOG}" 2>/dev/null

cd "${HERE}" || exit 21
"${PY}" panel.py >> "${LOG}" 2>&1 &
exit 0
