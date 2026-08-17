#!/bin/bash
# 造验签链测试素材。三个包分别触发链上不同的关卡。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIX="$ROOT/.tmp/fixtures"
SRC="$ROOT/dist/Xiaoer-WeChat-Cleaner-v2.3.0.zip"

[[ -f "$SRC" ]] || { echo "缺少素材包 $SRC"; exit 1; }

rm -rf "$FIX"; mkdir -p "$FIX"

# 1. 正常包：应该全过
cp "$SRC" "$FIX/good.zip"

# 2. 篡改包：改掉一个字节，sha256 那关就该拦下
cp "$SRC" "$FIX/tampered.zip"
printf '\x00' | dd of="$FIX/tampered.zip" bs=1 seek=1000 count=1 conv=notrunc 2>/dev/null

# 3. 别人签的包：用 ad-hoc 重签，TeamID 那关该拦下
#    （拿不到第二张 Developer ID 证书，ad-hoc 走的是同一条防线：
#     签名本身有效，但 TeamIdentifier 不是 3DP32PZ62M）
mkdir -p "$FIX/resign"
ditto -x -k "$SRC" "$FIX/resign"
codesign --force --deep --sign - "$FIX/resign/小耳微信清扫器.app" 2>/dev/null
ditto -c -k --sequesterRsrc --keepParent \
  "$FIX/resign/小耳微信清扫器.app" "$FIX/foreign.zip"

echo "素材就绪：$FIX"
ls -1 "$FIX"/*.zip
