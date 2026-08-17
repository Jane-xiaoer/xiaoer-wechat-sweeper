"""小耳微信清扫器 · 记住你的选择

存在 app 外面，不放 bundle 里：放里面既会破坏签名，又会被自动更新
整包替换掉——每更新一次，用户上次选的文件夹就丢一次。

这里存的只是「记忆」，不是工具运转的必需品。所以读写全程不抛异常：
配置坏了、目录没权限，最坏的结果也只是回到默认路径，绝不能让工具打不开。
"""
import json
import os
import sys
from pathlib import Path

FILENAME = "settings.json"


def _config_dir():
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or (Path.home() / "AppData/Roaming")
        return Path(base) / "xiaoer-wechat-cleaner"
    return Path.home() / "Library/Application Support/xiaoer-wechat-cleaner"


def load():
    """读配置。文件不存在、内容损坏、没权限，一律当空配置。"""
    try:
        d = json.loads((_config_dir() / FILENAME).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(**kv):
    """写配置。写不进去就算了——记不住选择只是不方便，不值得报错打扰人。"""
    try:
        d = load()
        d.update(kv)
        _config_dir().mkdir(parents=True, exist_ok=True)
        (_config_dir() / FILENAME).write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def get_last_dest():
    """上次搬到哪儿了。

    路径可能已经不在了——外接盘拔了、文件夹被删了或改名了。
    这种情况当作没有记录，让调用方回退到默认路径。
    """
    p = load().get("last_dest")
    if not p:
        return None
    try:
        return p if Path(p).expanduser().is_dir() else None
    except OSError:
        return None


def set_last_dest(path):
    if not path:
        return False
    return save(last_dest=str(path))


# 面板上那两样用户会调的东西的出厂值
DEFAULT_KEEP = 1          # 滑杆「留 1 个月」
DEFAULT_DEDUP = True      # 勾选「先查重」
KEEP_MIN, KEEP_MAX = 0, 12


def get_prefs():
    """上次把滑杆拖到哪、查重勾没勾。

    读不出来或者读到脏数据就回出厂值——配置文件是可以被手改坏的，
    但面板不能因此显示成乱的。
    """
    d = load()
    try:
        keep = int(d.get("keep", DEFAULT_KEEP))
    except (TypeError, ValueError):
        keep = DEFAULT_KEEP
    keep = max(KEEP_MIN, min(KEEP_MAX, keep))

    dedup = d.get("dedup", DEFAULT_DEDUP)
    if not isinstance(dedup, bool):
        dedup = DEFAULT_DEDUP

    return {"keep": keep, "dedup": dedup}


def set_prefs(keep, dedup):
    """keep=0 是合法值（微信里一个都不留），别当成空值丢掉"""
    try:
        keep = max(KEEP_MIN, min(KEEP_MAX, int(keep)))
    except (TypeError, ValueError):
        return False
    return save(keep=keep, dedup=bool(dedup))
