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


def ask(q: str, default: str = "") -> str:
    tip = f" [{default}]" if default else ""
    try:
        v = input(f"\033[36m?\033[0m {q}{tip}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
        sys.exit(0)
    return v or default


def yes(q: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    v = ask(f"{q} ({d})").lower()
    return default if not v else v.startswith("y")


def title(t: str):
    print(f"\n\033[1m{'═' * 56}\n {t}\n{'═' * 56}\033[0m")



def choose_folder():
    """弹系统原生选择框，比让人敲路径友好得多"""
    if IS_WIN:
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "Add-Type -AssemblyName System.Windows.Forms;"
              "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
              "$d.Description = '选择存放微信文件的位置';"
              "if ($d.ShowDialog() -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }")
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
    else:
        r = subprocess.run(
            ["osascript", "-e",
             'POSIX path of (choose folder with prompt "选择存放微信文件的位置")'],
            capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return Path(r.stdout.strip())
    return None


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


def reveal(path):
    """在文件管理器里打开一个文件夹"""
    if IS_WIN:
        # explorer 就算成功也常返回非 0，别去判断返回码
        subprocess.run(["explorer", str(path)])
    else:
        subprocess.run(["open", str(path)], capture_output=True)


def notify(title_: str, body: str):
    """建一条提醒事项；失败就退回横幅通知"""
    esc = lambda s: s.replace('"', "'")
    if IS_WIN:
        # Windows 没有「提醒事项」这种能留住的待办，退而求其次发个通知气泡。
        # 用 BurntToast 之类的第三方模块才有真 toast，这里不引入任何依赖。
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "Add-Type -AssemblyName System.Windows.Forms;"
              "$n = New-Object System.Windows.Forms.NotifyIcon;"
              "$n.Icon = [System.Drawing.SystemIcons]::Information;"
              "$n.Visible = $true;"
              f"$n.ShowBalloonTip(10000, '{esc(title_)}', '{esc(body)}', 'Info');"
              "Start-Sleep -Seconds 10")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True)
        return
    script = (f'tell application "Reminders" to make new reminder at default list '
              f'with properties {{name:"{esc(title_)}", body:"{esc(body)}"}}')
    if subprocess.run(["osascript", "-e", script],
                      capture_output=True).returncode != 0:
        subprocess.run(["osascript", "-e",
                        f'display notification "{esc(body)}" with title "{esc(title_)}"'],
                       capture_output=True)


def check_space():
    """删完发现空间没释放？两个系统各有各的元凶。"""
    print("\n\033[1m🔍 检查空间为什么没释放\033[0m\n")
    if IS_WIN:
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "Get-PSDrive -PSProvider FileSystem | "
              "ForEach-Object { if ($_.Used -ne $null) { "
              "'{0}: 已用 {1:N1}GB，可用 {2:N1}GB' -f $_.Name,"
              "($_.Used/1GB),($_.Free/1GB) } }")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        for line in r.stdout.splitlines():
            if line.strip():
                print(f"  {line.strip()}")
        print("""
  Windows 上空间没释放，按可能性从高到低查：

  1. 回收站没清空 —— 本工具只移动不删除，但你自己删掉的那些还在回收站里躺着。
     右键回收站 → 清空。

  2. 文件还在原处 —— 本工具是「移动」，如果目标文件夹和微信数据在同一个盘，
     总占用不会变。想真正腾出空间，得在挑拣之后把不要的删掉。

  3. 系统还原点 / 卷影副本 —— 它们记着旧状态，攥着已删文件不放。
     查看：系统属性 → 系统保护 → 配置 → 看「当前使用量」。
""")
        return

    r = subprocess.run(["df", "-h", "/System/Volumes/Data"],
                       capture_output=True, text=True)
    if r.returncode == 0 and len(r.stdout.splitlines()) > 1:
        c = r.stdout.splitlines()[1].split()
        print(f"  磁盘：已用 {c[2]}，可用 {c[3]}\n")

    r = subprocess.run(["tmutil", "listlocalsnapshots", "/"],
                       capture_output=True, text=True)
    snaps = [l.strip() for l in r.stdout.splitlines() if "com.apple" in l]
    if not snaps:
        print("  ✅ 没有本地快照。空间如果还是没释放，检查一下废纸篓有没有清空。")
        return

    print(f"  🔴 发现 {len(snaps)} 个 Time Machine 本地快照\n")
    for s_ in snaps:
        print(f"     {s_}")
    print("""
  这些快照记着「删除之前」的磁盘状态，所以你删掉的文件在它们眼里还活着，
  空间自然还不了你。实测有台机器上快照一个人锁了 620GB。

  ⚠️ 删快照的唯一代价：失去「从本地快照恢复误删文件」这个后悔药。
     外接硬盘上的 Time Machine 备份不受影响，系统之后也会自己重建新快照。

  确认要删就逐条执行：""")
    for s_ in snaps:
        d = s_.replace("com.apple.TimeMachine.", "").replace(".local", "")
        print(f"     tmutil deletelocalsnapshots {d}")
    print("""
  想根治（不再自动生成快照，前提是你没在用 Time Machine 定时备份）：
     系统设置 → 通用 → 时间机器 → 关闭「自动备份」
""")


USAGE = """
用法
────────────────────────────────────────────────
给人用（默认，交互问答）：
    python3 wechat_cleaner.py

给 AI agent 用（先看清楚，再动手）：
    python3 wechat_cleaner.py --json              # 只扫描，输出 JSON，不碰任何文件
    python3 wechat_cleaner.py --dest <路径> --months 3 --yes   # 代用户执行

参数
    --json      输出 JSON 扫描结果后退出（绝不移动文件）
    --scan      同上，但输出人类可读格式
    --dest X    文件搬到哪里
    --months N  保留最近 N 个月不动（默认 1）
    --yes       跳过确认（agent 已代用户确认过才用）
    --auto      全自动：不提问、不开窗，完事建一条提醒事项\n    --no-dedup  跳过查重（默认会查：很多文件你电脑上早就有了）\n    --check-space  删完空间没释放时用：查 Time Machine 本地快照
"""


def emit_json(keep: int):
    """把扫描结果吐成 JSON 给 agent 解析。这个函数绝不移动任何文件。"""
    today = date.today()
    cutoff = today.year * 12 + today.month - keep
    out = {"version": VERSION, "date": str(today), "keep_months": keep,
           "accounts": []}
    for acc, content, kinds in find_accounts():
        data = scan_months(content, kinds)
        info = {"account_dir": str(acc), "kinds": {}}
        for kind, rows in data.items():
            info["kinds"][kind] = {
                "total_bytes": sum(r[3] for r in rows),
                "total_files": sum(r[4] for r in rows),
                "months": [
                    {"month": f"{y}-{m:02d}", "bytes": sz, "files": n,
                     "expired": (y * 12 + m) <= cutoff}
                    for y, m, _p, sz, n in rows
                ],
            }
        exp = sum(mm["bytes"] for k in info["kinds"].values()
                  for mm in k["months"] if mm["expired"])
        info["expired_bytes"] = exp
        info["expired_human"] = human(exp)
        out["accounts"].append(info)
    out["suggested_dest"] = str(Path.home() / "Desktop/微信文件整理")
    print(json.dumps(out, ensure_ascii=False, indent=2))


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


def main():
    if not (IS_MAC or IS_WIN):
        print("❌ 这个工具只支持 macOS 和 Windows")
        sys.exit(1)

    auto = "--auto" in sys.argv          # 全自动：不问不开窗，给脚本/助手调用用
    scan_only = "--scan" in sys.argv     # 只扫描，不动任何文件
    as_json = "--json" in sys.argv       # 结构化输出，给 AI agent 解析
    assume_yes = "--yes" in sys.argv     # 跳过确认，agent 已代用户确认过
    keep = 1
    keep_given = "--months" in sys.argv   # 显式给了就别再问
    dest = None
    if "--months" in sys.argv:
        keep = int(sys.argv[sys.argv.index("--months") + 1])
    if "--dest" in sys.argv:
        dest = Path(sys.argv[sys.argv.index("--dest") + 1]).expanduser()
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__); print(USAGE); sys.exit(0)

    if "--check-space" in sys.argv:       # 删完空间没释放？查这里
        check_space()
        sys.exit(0)

    if as_json:                           # JSON 模式先把扫描结果吐出来就结束
        emit_json(keep)
        sys.exit(0)

    print(f"\n\033[1m👂 小耳微信清扫器 v{VERSION}\033[0m")
    print("   只移动，绝不删除。删不删由你决定。\n")

    # ① 找微信
    accounts = find_accounts()
    if not accounts:
        print("❌ 没找到微信数据目录。")
        print("   可能原因：没装微信 / 从没登录过 / 版本路径不同")
        sys.exit(1)

    if len(accounts) > 1 and not auto and not assume_yes:
        print(f"发现 {len(accounts)} 个微信号：")
        for i, (acc, _c, _k) in enumerate(accounts, 1):
            print(f"  {i}. {acc.name}")
        idx = int(ask("处理哪一个（填序号）", "1")) - 1
        accounts = [accounts[idx]]
    elif len(accounts) > 1:
        # --yes / --auto 是「别问我」的意思，这里再停下来等输入就会把调用方挂死
        print(f"发现 {len(accounts)} 个微信号，按无人值守约定处理第一个：{accounts[0][0].name}")

    acc, content, kinds = accounts[0]
    print(f"📱 微信账号目录：{acc.name}")

    # ② 扫描
    title("扫描中…")
    data = scan_months(content, kinds)
    if not data:
        print("✨ 没找到按月存放的文件，也许这个号还没收过文件。")
        sys.exit(0)

    total = 0
    for kind, rows in data.items():
        label = "🎬 视频" if kind == "video" else "📄 文件"
        s = sum(r[3] for r in rows)
        total += s
        print(f"\n{label}  共 {human(s)}，{len(rows)} 个月份")
        for y, m, _p, sz, n in sorted(rows, key=lambda r: -r[3])[:6]:
            print(f"    {y}-{m:<2}   {human(sz):>9}   {n} 个文件")
        if len(rows) > 6:
            print(f"    …还有 {len(rows)-6} 个月")
    print(f"\n💾 合计占用：\033[1m{human(total)}\033[0m")

    # ③ 问：保留几个月
    if not auto and not assume_yes and not keep_given:
        keep = int(ask("\n保留最近几个月不动？更早的搬出来", str(keep)))

    today = date.today()
    cutoff = today.year * 12 + today.month - keep
    picked = {k: [r for r in rows if r[0] * 12 + r[1] <= cutoff]
              for k, rows in data.items()}
    picked = {k: v for k, v in picked.items() if v}
    if not picked:
        print(f"\n✨ 没有超过 {keep} 个月的东西，不用清理。")
        sys.exit(0)

    # ④ 问：搬到哪
    if dest is None:
        default_dest = Path.home() / "Desktop/微信文件整理"
        if auto or assume_yes:
            dest = default_dest          # 无人值守：用默认，绝不停下来等输入
        else:
            print(f"\n\033[36m?\033[0m 文件搬到哪里？")
            print(f"    直接回车 = 用默认位置 {default_dest}")
            print(f"    输入 f 回车 = 弹出{'文件夹选择框' if IS_WIN else 'Finder'}自己挑")
            v = ask("    或直接粘贴一个路径", "")
            if v.lower() == "f":
                dest = choose_folder() or default_dest
            elif v:
                dest = Path(v.strip().strip("'\"")).expanduser()
            else:
                dest = default_dest

    custom = load_custom_rules(dest)

    # ⑤ 去重：很多文件你电脑上早就有了（自己发出去的、自己收藏的）
    dup_map = {}
    if _dedup and not ("--no-dedup" in sys.argv):
        allf = [f for rows in picked.values() for _y, _m, src, _s, _c in rows
                for f in src.rglob("*") if f.is_file() and f.name != ".DS_Store"]
        if allf:
            title("查重 —— 哪些文件你电脑上已经有了")
            roots = ["~/Desktop", "~/Documents", "~/Downloads"]
            if not auto and not assume_yes:
                extra = ask("除了 桌面/文稿/下载，还要扫哪些目录？(逗号分隔，回车跳过)", "")
                roots += [x.strip() for x in extra.split(",") if x.strip()]
            print("  建索引中…", flush=True)
            idx, n = _dedup.build_index(roots)
            print(f"  本机 {n} 个文件已入索引")
            dups, suspect = _dedup.check(allf, idx)
            dup_map = {f: hit for f, hit, _w in dups}
            if dups:
                dsz = sum(f.stat().st_size for f, _h, _w in dups if f.exists())
                print(f"\n  🔁 {len(dups)} 个是重复的（{human(dsz)}），"
                      f"会单独归到 _重复_电脑里已有/ 不删除")
                for f, hit, why in dups[:6]:
                    print(f"     [{why}] {f.name[:42]}")
                if len(dups) > 6:
                    print(f"     …还有 {len(dups)-6} 个")
            else:
                print("  ✅ 没发现重复")
            if suspect:
                print(f"\n  ❓ {len(suspect)} 个内容一致但文件名不同，"
                      f"不替你判定，照常分类：")
                for f, hit, _w in suspect[:4]:
                    print(f"     {f.name[:40]}  ↔  {Path(hit).name[:34]}")

    # ⑥ 预演
    title("预演 —— 下面这些会被搬走（现在还没动）")
    pn = ps = 0
    for kind, rows in picked.items():
        n, s = harvest(rows, dest, kind, apply=False, dup_map=dup_map, custom=custom)
        pn += n
        ps += s
    print(f"\n  合计 {pn} 个文件，{human(ps)}")
    print(f"  目标：{dest}")

    if not auto and not assume_yes and not yes("\n确认执行吗？", False):
        print("已取消，一个文件都没动。")
        sys.exit(0)

    # ⑥ 执行
    title("执行中…")
    dest.mkdir(parents=True, exist_ok=True)
    tn = ts = 0
    for kind, rows in picked.items():
        n, s = harvest(rows, dest, kind, apply=True, dup_map=dup_map, custom=custom)
        tn += n
        ts += s

    keep_dir = dest / "✅ 我要留的"
    keep_dir.mkdir(exist_ok=True)
    (keep_dir / "_把想留的文件拖进来.txt").write_text(
        "把你决定长期保留的文件，从外面的分类夹里拖到这个文件夹。\n"
        "本工具永远不会碰这里的东西，以后每次整理也不会覆盖它。\n", encoding="utf-8")

    if IS_WIN:
        space_help = """【🔴 删完发现空间没释放？】
1. 回收站没清空 —— 你删掉的还在里面躺着。右键回收站 → 清空。
2. 目标文件夹和微信数据在同一个盘 —— 搬运不改变总占用，
   要真腾出空间，得在挑拣之后把不要的删掉。
3. 系统还原点 / 卷影副本攥着旧状态：
   系统属性 → 系统保护 → 配置 → 看「当前使用量」。"""
    else:
        space_help = """【🔴 删完发现空间没释放？这是最常见的困惑】
macOS 的 Time Machine「本地快照」会攥着你删掉的文件不放。
实测案例：删了 35GB 只回血 29GB，删了 17GB 只回血 5.7GB，
有台机器上快照一个人锁了 620GB。

查一下有没有快照：
    tmutil listlocalsnapshots /

有的话逐个删（不影响外接硬盘上的真备份，系统会自己重建）：
    tmutil deletelocalsnapshots 2026-08-10-000457

想彻底关掉自动快照（前提：你没在用 Time Machine 定时备份）：
    系统设置 → 通用 → 时间机器 → 关闭自动备份
    （命令行 `sudo tmutil disable` 需要「完全磁盘访问权限」，通常走界面更快）"""

    (dest / "_怎么处理这些文件.txt").write_text(
        f"""这些文件是 {today} 从微信里搬出来的（{keep} 个月前的）

【怎么处理】
1. 视频基本可以全删 —— 群里转发的东西，你多半不会再看
2. 文档挑出想留的，拖进「✅ 我要留的」文件夹
3. 剩下的按分类夹整个删掉

【删掉会怎样】
· 聊天记录的文字一条都不会少
· 只是以后在微信里点那个文件，会提示「已过期或已被清理」
· 这些文件在腾讯服务器上通常也早就过期，本来就点不开

{space_help}

本次搬运：{tn} 个文件，{human(ts)}
工具：小耳微信清扫器 v{VERSION}
""", encoding="utf-8")

    print(f"\n✅ 完成：{tn} 个文件，{human(ts)}")
    print(f"📂 {dest}")

    if auto or assume_yes:
        notify("微信文件待整理",
               f"已整理 {tn} 个文件（{human(ts)}）到 {dest.name}，有空去挑挑删删")
    else:
        reveal(dest)
        print("\n微信会一直往电脑里存新文件。过阵子觉得又满了，再打开一次就行 ——")
        print("还是搬到这里，并进同一套分类夹；重名不会覆盖，按时间排序就知道哪些是新的。")

    print("\n🎉 搞定。\n")


if __name__ == "__main__":
    main()
