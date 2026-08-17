"""小耳微信清扫器 · 自动更新

打开时查一次 GitHub，有新版就装好再进面板。装不成就当无事发生，
照常用旧版——这是个会搬用户文件的工具，宁可不更新，不可打不开。
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = "Jane-xiaoer/xiaoer-wechat-sweeper"
API = "https://api.github.com/repos/%s/releases/latest" % REPO
TEAM_ID = "3DP32PZ62M"          # Developer ID Application: Juan Li


def is_win():
    return sys.platform.startswith("win")


def parse_version(v):
    """'v2.10.0' → (2, 10, 0)。

    必须转元组再比：字符串比较下 '2.10.0' < '2.9.0'，
    版本号进两位数就会静默不更新。
    脏数据返回 (0,)，比不过任何正常版本，等于「不更新」。
    """
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except (ValueError, AttributeError, TypeError):
        return (0,)


def current_version():
    """VERSION 跟 updater.py 同目录——仓库里在根目录，
    bundle 里在 Contents/Resources/app/，两边都对得上。"""
    try:
        return (HERE / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"
