#!/bin/bash
# 双击我 → 浏览器自动打开面板。第一次被 macOS 拦的话，右键 →「打开」。
cd "$(dirname "$0")" || exit 1
clear
python3 panel.py
