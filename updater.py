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


def _fetch_json(url, timeout):
    """单独一层，方便测试替换掉，不用真联网"""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "xiaoer-wechat-cleaner",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_asset(assets, is_win):
    """mac 要不带 -Windows 的那个 zip，Windows 要带的那个"""
    for a in assets or []:
        name = a.get("name") or ""
        if not name.endswith(".zip"):
            continue
        if ("-Windows" in name) == bool(is_win):
            return a
    return None


def check(timeout=3):
    """问 GitHub 有没有新版。

    3 秒超时是硬上限：GitHub API 在国内经常慢，超时就当没有新版，
    下次打开再说，绝不让用户干等。
    任何异常都返回 None——「查不到」和「没新版」对调用方是同一件事。
    """
    try:
        data = _fetch_json(API, timeout)
        remote = str(data["tag_name"]).lstrip("v")
        if parse_version(remote) <= parse_version(current_version()):
            return None
        asset = pick_asset(data.get("assets"), is_win())
        if not asset:
            return None
        return {
            "version": remote,
            "notes": data.get("body") or "",
            "url": asset["browser_download_url"],
            "sha256": (asset.get("digest") or "").replace("sha256:", ""),
            "size": asset.get("size") or 0,
        }
    except Exception:
        return None
