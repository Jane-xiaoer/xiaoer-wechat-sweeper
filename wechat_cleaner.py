#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
👂 小耳微信清扫器 for macOS / Windows

各个群里发的文件，微信默默存在你电脑上，你根本找不到在哪。

小耳微信清扫器帮你翻出来 —— 电脑里已有的、自己重复的，先剔掉；剩下的分好类。

你花五分钟挑一遍：有用的留下，没用的删掉。资料沉淀了，内存也清了。

特点：
  · 零依赖，只用 Python 标准库（macOS 自带 python3；Windows 装个 Python 即可）
  · 只移动，绝不删除 —— 删不删由你决定
  · 默认预演，看清楚了才动手
  · 自动发现微信目录，支持多个微信号

作者：小耳 (Xiaoer)  ·  MIT License
"""

import os
import re
import sys
import json
import shutil
import platform
import subprocess
from pathlib import Path
from datetime import date
from collections import defaultdict

VERSION = "1.1"

try:
    import dedup as _dedup
except ImportError:
    _dedup = None

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"

# ── 微信数据目录 ────────────────────────────────────────────
# 两代微信的目录布局不同，但「按 YYYY-MM 分月」这层是一样的，
# 所以扫描 / 分类 / 搬运的逻辑两边通用，只有下面这张表要分开：
#   新版 4.x（mac 与 Windows 同一代）  <账号>/msg/{file,video}/YYYY-MM
#   旧版 3.x（Windows 上还有存量）     <账号>/FileStorage/{File,Video}/YYYY-MM
# 每项 = (内容目录名, {逻辑类型: 实际子目录名}, 用来认出这套布局的探针目录)
LAYOUTS = [
    ("msg", {"video": "video", "file": "file"}, ("file", "video", "attach")),
    ("FileStorage", {"video": "Video", "file": "File"}, ("File", "Video", "Image")),
]

# mac 上微信装在沙盒容器里，位置固定
MAC_ROOTS = [
    "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files",
    "Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat",
    "Library/Application Support/com.tencent.xinWeChat",
]

# ── 分类：一级按「是什么文件」，文档再按「是什么文档」细分 ──────
# 刻意不按主题（Claude / 出海 / 提示词…）分 —— 那是每个人自己的兴趣地图，
# 别人打开只会看到一堆空文件夹。想要主题分类的，走 我的分类.txt（见下）。
CATEGORIES = [
    ("🎬 视频",   {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v", ".webm", ".rmvb"}),
    ("🖼 图片",   {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".tiff", ".svg"}),
    ("🎵 音频",   {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".amr"}),
    ("📦 压缩包", {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"}),
    ("💾 安装包", {".dmg", ".pkg", ".exe", ".msi", ".apk", ".ipa", ".deb"}),
    ("💻 代码",   {".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".sh",
                  ".html", ".css", ".json", ".xml"}),
    ("📄 文档",   {".pdf", ".doc", ".docx", ".pages", ".rtf", ".txt", ".md", ".epub",
                  ".mobi", ".ppt", ".pptx", ".key", ".xls", ".xlsx", ".csv", ".numbers"}),
]

# 文档的二级分类。关键词要够长够具体 —— 用「年报」会误伤「哪年报、怎么报」，
# 这是实测踩过的坑。
DOC_SUBCATS = [
    ("💼 简历",        ["简历", "履历", "resume", "curriculum vitae"]),
    ("📋 合同与表单",  ["合同", "协议", "申请表", "审批", "报销", "发票", "凭证",
                       "登记表", "承诺书", "授权书", "意见书", "回执"]),
    ("🎓 课件与教程",  ["课件", "讲义", "教程", "培训", "手册", "指南", "入门",
                       "实战", "课程", "教材", "workshop", "tutorial"]),
    ("📚 书籍",        ["《", "读书笔记", "书单", "全文", "译本", "epub", "mobi",
                       "pdfdrive", "z-lib"]),
    ("📊 报告与白皮书", ["报告", "白皮书", "蓝皮书", "研究", "洞察", "调研",
                       "趋势", "展望", "年鉴", "指数", "report", "whitepaper"]),
]

CUSTOM_FILE = "我的分类.txt"
CUSTOM_TEMPLATE = """# 想按自己关心的话题分类？在这里写，一行一个。
# 格式：  分类名 = 关键词1, 关键词2, 关键词3
# 文件名里出现任一关键词就归到该分类，比下面的通用分类优先。
# 以 # 开头的是注释。删掉本行下面的示例即可停用。
#
# AI工具 = claude, cursor, copilot, prompt, 提示词
# 出海 = 出海, 跨境, 海外, 外贸
# 我的行业 = 半导体, 芯片, 晶圆
"""


def load_custom_rules(folder: Path):
    """读目标文件夹里的 我的分类.txt。没有就现场生成一份带注释的模板。"""
    f = folder / CUSTOM_FILE
    if not f.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            f.write_text(CUSTOM_TEMPLATE, encoding="utf-8")
        except OSError:
            pass
        return []
    rules = []
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        cat, kws = line.split("=", 1)
        kw = [k.strip().lower() for k in kws.split(",") if k.strip()]
        if cat.strip() and kw:
            rules.append((cat.strip(), kw))
    return rules


MONTH_DIR = re.compile(r"^(\d{4})-(\d{1,2})$")
DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")      # 认 Windows 盘符路径


def setup_windows_console():
    """Windows 控制台默认是 GBK，中文会变成一堆方块；顺便打开 ANSI 转义支持，
    不然满屏都是 ←[1m 这种控制符。控制台和 Python 的流要一起改，只改一边照样乱。"""
    if not IS_WIN:
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetConsoleOutputCP(65001)
        k32.SetConsoleCP(65001)
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004，让 \033[1m 之类正常生效
        handle = k32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(handle, ctypes.byref(mode)):
            k32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass                                   # 没控制台（被重定向）就不用管
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


setup_windows_console()


# ═══════════════ 小工具 ═══════════════
def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


# ═══════════════ 1. 找微信 ═══════════════
def win_data_roots():
    """Windows 上数据目录可以被用户搬到任意盘，不能靠猜。
    微信把它记在 %APPDATA%\\Tencent\\<品牌>\\config\\*.ini 里 ——
    每个 ini 就是一行纯路径，指向数据目录的父目录。这是最可靠的来源；
    读不到再退回几个默认位置。"""
    roots, seen = [], []

    def add(p):
        try:
            p = Path(p)
        except (TypeError, ValueError):
            return
        # ini 里记的是父目录，真正的数据在它下面的 xwechat_files / WeChat Files；
        # 但也可能直接就是数据目录，所以三种都试。
        for cand in (p / "xwechat_files", p / "WeChat Files", p):
            key = str(cand).lower()
            if key not in seen:
                seen.append(key)
                roots.append(cand)

    appdata = os.environ.get("APPDATA")
    if appdata:
        for brand in ("xwechat", "WeChat"):
            cfg = Path(appdata) / "Tencent" / brand / "config"
            if not cfg.is_dir():
                continue
            for ini in sorted(cfg.glob("*.ini")):
                try:
                    # utf-8-sig：这些 ini 可能带 BOM，带了就会把路径判断顶歪
                    txt = ini.read_text(encoding="utf-8-sig", errors="ignore")
                except OSError:
                    continue
                for line in txt.splitlines():
                    line = line.strip().lstrip("\ufeff")
                    # 只认看着像 C:\... 的绝对路径，跳过键值对和空行
                    if DRIVE_PATH.match(line) and "=" not in line:
                        add(line)

    home = Path.home()
    for d in (home / "Documents", home,
              home / "Documents" / "Tencent Files", Path("D:/"), Path("E:/")):
        add(d)
    return roots


def find_accounts():
    """返回 [(账号目录, 内容目录, {逻辑类型: 实际子目录名})]，
    支持一台电脑登过多个微信号，也支持新旧两套目录布局。"""
    if IS_WIN:
        bases = win_data_roots()
    else:
        bases = [Path.home() / rel for rel in MAC_ROOTS]

    found, seen = [], set()
    for base in bases:
        try:
            if not base.is_dir():
                continue
            entries = list(base.iterdir())
        except OSError:          # 盘不在、没权限：跳过，不能让它崩掉整轮扫描
            continue
        for acc in entries:
            if not acc.is_dir():
                continue
            for sub, kinds, probe in LAYOUTS:
                content = acc / sub
                if content.is_dir() and any((content / s).is_dir() for s in probe):
                    key = str(content).lower()
                    if key not in seen:
                        seen.add(key)
                        found.append((acc, content, kinds))
                    break
    return found


def scan_months(content: Path, kinds=None):
    """扫出 {子类: [(年, 月, 路径, 大小, 文件数)]}"""
    kinds = kinds or {"video": "video", "file": "file"}
    out = {}
    for kind, sub in kinds.items():
        base = content / sub
        if not base.is_dir():
            continue
        rows = []
        for p in base.iterdir():
            m = MONTH_DIR.match(p.name)
            if not (p.is_dir() and m):
                continue
            n = sz = 0
            for f in p.rglob("*"):
                if f.is_file():
                    n += 1
                    try:
                        sz += f.stat().st_size
                    except OSError:
                        pass
            if n:
                rows.append((int(m.group(1)), int(m.group(2)), p, sz, n))
        if rows:
            out[kind] = sorted(rows)
    return out


# ═══════════════ 2. 分类 ═══════════════
def doc_subcat(name: str) -> str:
    low = name.lower()
    for label, kws in DOC_SUBCATS:
        if any(k in name or k in low for k in kws):
            return label
    return "📎 其他文档"


def category_of(name: str, custom=None) -> str:
    """自定义规则优先（用户特意配的），然后按文件形态，文档再分二级"""
    low = name.lower()
    for cat, kws in (custom or []):
        if any(k in low for k in kws):
            return cat
    ext = Path(name).suffix.lower()
    for label, exts in CATEGORIES:
        if ext in exts:
            return f"📄 文档/{doc_subcat(name)}" if label == "📄 文档" else label
    return "📎 杂项"


# ═══════════════ 主流程 ═══════════════
def harvest(months_rows, dest_root: Path, kind: str, apply: bool,
            dup_map=None, custom=None):
    """搬进 dest_root 的固定分类夹。

    刻意不按批次分文件夹：第二次搬的直接并进同一套分类夹，靠文件自带的
    修改时间（= 你当初在群里收到它的时间）排序区分新旧。否则三次不整理
    就会躺着三个文件夹，越攒越乱。
    """
    dup_map = dup_map or {}
    n = sz = 0
    buckets = defaultdict(int)
    for _y, _m, src, _s, _c in months_rows:
        for f in list(src.rglob("*")):
            if not f.is_file() or f.name == ".DS_Store":
                continue
            cat = ("_重复_电脑里已有" if f in dup_map
                   else category_of(f.name, custom))
            buckets[cat] += 1
            n += 1
            try:
                sz += f.stat().st_size
            except OSError:
                pass
            if not apply:
                continue
            d = dest_root / cat
            d.mkdir(parents=True, exist_ok=True)
            t = d / f.name
            i = 1
            while t.exists():
                t = d / f"{f.stem}_{i}{f.suffix}"      # 不覆盖，重名就加序号
                i += 1
            try:
                shutil.move(str(f), str(t))            # move 会保留原始时间
            except Exception as e:
                print(f"    ⚠️ {f.name[:40]}: {e}")

    if apply:
        for _y, _m, src, _s, _c in months_rows:
            shutil.rmtree(src, ignore_errors=True)

    print(f"\n  {'✅ 已搬运' if apply else '将搬运'} "
          f"{'视频' if kind == 'video' else '文件'}：")
    for c in sorted(buckets, key=lambda x: -buckets[x]):
        print(f"      {c}  {buckets[c]} 个")
    return n, sz


RECORD = ".清扫记录.json"


def read_record(folder: Path):
    f = folder / RECORD
    if not f.exists():
        return {"runs": []}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"runs": []}


def write_record(folder: Path, n: int, size: int, keep: int, dups: int):
    rec = read_record(folder)
    rec["runs"].append({"date": str(date.today()), "files": n, "bytes": size,
                        "keep_months": keep, "dups": dups})
    rec["last"] = str(date.today())
    try:
        (folder / RECORD).write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
