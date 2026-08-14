#!/bin/bash
# 双击我就能跑。第一次打开如果被 macOS 拦，请右键 →「打开」。
cd "$(dirname "$0")" || exit 1
clear
python3 wechat_cleaner.py
echo ""
echo "───────────────────────────────────────"
read -n 1 -s -r -p "按任意键关闭这个窗口…"
echo ""
