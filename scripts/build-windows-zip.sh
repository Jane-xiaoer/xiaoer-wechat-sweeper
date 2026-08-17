#!/bin/bash
# 打 Windows 版 zip。v2.3.0 那次是手工打的，使用说明.txt 都没入库，
# 发一次就丢一次——这个脚本让它可复现。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "${ROOT}/VERSION")"
DIST="${ROOT}/dist"
STAGE="${ROOT}/.tmp/windows-build/小耳微信清扫器"
ZIP="${DIST}/Xiaoer-WeChat-Cleaner-v${VERSION}-Windows.zip"

rm -rf "${ROOT}/.tmp/windows-build"
mkdir -p "${STAGE}/assets" "${DIST}"

# Windows 版是源码直接跑，不打包 exe，所以把 app 要用的文件原样拷进去
cp "${ROOT}/panel.py" "${ROOT}/panel.html" "${ROOT}/watercolor-ui.css" \
   "${ROOT}/wechat_cleaner.py" "${ROOT}/dedup.py" \
   "${ROOT}/updater.py" "${ROOT}/updating.html" "${ROOT}/VERSION" \
   "${STAGE}/"
cp "${ROOT}/assets/"*.png "${STAGE}/assets/"
cp "${ROOT}/windows/使用说明.txt" "${STAGE}/"
cp "${ROOT}/小耳微信清扫器.bat" "${STAGE}/"

rm -f "${ZIP}"
# 必须用 Python 打包，不能用 zip 命令：Info-ZIP 存中文名时不设 UTF-8 标志位
# (0x800)，Python 的 zipfile 解压时就按 cp437 解码，
# 「小耳微信清扫器.bat」会变成一串乱码——自动更新完用户就双击不着入口了。
# Python 写非 ASCII 文件名时会自动设这个标志位。
python3 - "${ROOT}/.tmp/windows-build" "${ZIP}" <<'PY'
import sys, zipfile
from pathlib import Path

src, out = Path(sys.argv[1]), sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(src.rglob("*")):
        z.write(p, p.relative_to(src).as_posix())

with zipfile.ZipFile(out) as z:
    bad = [i.filename for i in z.infolist()
           if not i.filename.isascii() and not (i.flag_bits & 0x800)]
    if bad:
        sys.exit("中文文件名没带 UTF-8 标志：%s" % bad[:3])
    print("%d 个文件，中文名编码正常" % len(z.infolist()))
PY

echo "${ZIP}"
