#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🧹 微信存储清理器 for macOS

微信会把聊天里收到的文件和视频永久存在电脑上，按月堆积、从不回收。
这个工具帮你把「够老的」那些挪出来、分好类，你自己决定删哪些。

特点：
  · 零依赖，只用 Python 标准库（macOS 自带 python3 就能跑）
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

# ── 微信可能的安装位置（新版 4.x 和旧版都覆盖）──────────────
WECHAT_ROOTS = [
    "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files",
    "Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat",
    "Library/Application Support/com.tencent.xinWeChat",
]

# ── 按文件类型分类（通用，不涉及个人兴趣）──────────────────
CATEGORIES = [
    ("📄 文档",   {".pdf", ".doc", ".docx", ".pages", ".rtf", ".txt", ".md", ".epub"}),
    ("📊 表格",   {".xls", ".xlsx", ".csv", ".numbers"}),
    ("📽 演示",   {".ppt", ".pptx", ".key"}),
    ("🖼 图片",   {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".tiff", ".svg"}),
    ("🎬 视频",   {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v", ".webm", ".rmvb"}),
    ("🎵 音频",   {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".amr"}),
    ("📦 压缩包", {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"}),
    ("💾 安装包", {".dmg", ".pkg", ".exe", ".msi", ".apk", ".ipa", ".deb"}),
    ("💻 代码",   {".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".sh", ".html", ".css", ".json", ".xml"}),
]
MONTH_DIR = re.compile(r"^(\d{4})-(\d{1,2})$")


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
    """弹 Finder 原生选择框，比让人敲路径友好得多"""
    r = subprocess.run(
        ["osascript", "-e",
         'POSIX path of (choose folder with prompt "选择存放微信文件的位置")'],
        capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return Path(r.stdout.strip())
    return None


# ═══════════════ 1. 找微信 ═══════════════
def find_accounts():
    """返回 [(账号目录, msg目录)]，支持一台电脑登过多个微信号"""
    home = Path.home()
    found = []
    for rel in WECHAT_ROOTS:
        base = home / rel
        if not base.is_dir():
            continue
        for acc in base.iterdir():
            if not acc.is_dir():
                continue
            msg = acc / "msg"
            if msg.is_dir() and any((msg / s).is_dir() for s in ("file", "video", "attach")):
                found.append((acc, msg))
    return found


def scan_months(msg: Path):
    """扫出 {子类: [(年, 月, 路径, 大小, 文件数)]}"""
    out = {}
    for kind in ("video", "file"):
        base = msg / kind
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
def category_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    for label, exts in CATEGORIES:
        if ext in exts:
            return label
    return "📎 其他"


# ═══════════════ 3. 定时任务 ═══════════════
PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
        <string>--auto</string>
        <string>--months</string><string>{months}</string>
        <string>--dest</string><string>{dest}</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
{times}
    </array>
    <key>StandardOutPath</key><string>{log}</string>
    <key>StandardErrorPath</key><string>{log}</string>
    <key>RunAtLoad</key><false/>
</dict>
</plist>
"""


def install_schedule(every_months: int, keep: int, dest: Path) -> bool:
    label = "com.xiaoer.wechat-cleaner"
    script = Path(__file__).resolve()
    plist_path = Path.home() / f"Library/LaunchAgents/{label}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    times = "".join(
        f"        <dict><key>Month</key><integer>{m}</integer>"
        f"<key>Day</key><integer>1</integer>"
        f"<key>Hour</key><integer>10</integer>"
        f"<key>Minute</key><integer>0</integer></dict>\n"
        for m in range(1, 13, every_months)
    )
    plist_path.write_text(PLIST.format(
        label=label, python=sys.executable, script=script, months=keep,
        dest=str(dest), times=times,
        log=f"/tmp/{label}.log"), encoding="utf-8")

    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                   capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(f"  ✅ 定时任务已装：每 {every_months} 个月的 1 号 10:00 自动运行")
        print(f"     配置文件 {plist_path}")
        print(f"     想取消：launchctl bootout gui/{uid}/{label}")
        return True
    print(f"  ⚠️ 安装失败：{r.stderr.strip()[:160]}")
    return False


def notify(title_: str, body: str):
    """建一条提醒事项；失败就退回横幅通知"""
    esc = lambda s: s.replace('"', "'")
    script = (f'tell application "Reminders" to make new reminder at default list '
              f'with properties {{name:"{esc(title_)}", body:"{esc(body)}"}}')
    if subprocess.run(["osascript", "-e", script],
                      capture_output=True).returncode != 0:
        subprocess.run(["osascript", "-e",
                        f'display notification "{esc(body)}" with title "{esc(title_)}"'],
                       capture_output=True)



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
    --months N  保留最近 N 个月不动（默认 3）
    --yes       跳过确认（agent 已代用户确认过才用）
    --auto      定时任务模式：全自动 + 建提醒事项\n    --no-dedup  跳过查重（默认会查：很多文件你电脑上早就有了）\n    --check-space  删完空间没释放时用：查 Time Machine 本地快照
"""


def emit_json(keep: int):
    """把扫描结果吐成 JSON 给 agent 解析。这个函数绝不移动任何文件。"""
    today = date.today()
    cutoff = today.year * 12 + today.month - keep
    out = {"version": VERSION, "date": str(today), "keep_months": keep,
           "accounts": []}
    for acc, msg in find_accounts():
        data = scan_months(msg)
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
def harvest(months_rows, dest_root: Path, kind: str, apply: bool, dup_map=None):
    """把选中的月份搬进 dest_root，按类型分好。
    dup_map 里的文件不参与分类，单独归到 _重复_电脑里已有/ 供用户过目。"""
    dup_map = dup_map or {}
    if not months_rows:
        return 0, 0
    y1, m1 = months_rows[0][0], months_rows[0][1]
    y2, m2 = months_rows[-1][0], months_rows[-1][1]
    span = f"{y1}年{m1}月" if (y1, m1) == (y2, m2) else (
        f"{y1}年{m1}月-{m2}月" if y1 == y2 else f"{y1}年{m1}月-{y2}年{m2}月")
    # 每次运行自成一批，绝不跟上次的混在一起：
    #   上批用户没清完的、他决定留下的，都不该被新一批冲掉或搞混
    batch = dest_root / f"⚠️ 待清理_{date.today():%Y%m%d}"
    box = batch / f"微信{'视频' if kind == 'video' else '文件'}_{span}"

    n = sz = 0
    buckets = defaultdict(int)
    for _y, _m, src, _s, _c in months_rows:
        for f in list(src.rglob("*")):
            if not f.is_file() or f.name == ".DS_Store":
                continue
            cat = ("_重复_电脑里已有" if f in dup_map else category_of(f.name))
            buckets[cat] += 1
            n += 1
            try:
                sz += f.stat().st_size
            except OSError:
                pass
            if not apply:
                continue
            d = box / cat
            d.mkdir(parents=True, exist_ok=True)
            t = d / f.name
            i = 1
            while t.exists():
                t = d / f"{f.stem}_{i}{f.suffix}"
                i += 1
            try:
                shutil.move(str(f), str(t))
            except Exception as e:
                print(f"    ⚠️ {f.name[:40]}: {e}")

    if apply:
        for _y, _m, src, _s, _c in months_rows:
            shutil.rmtree(src, ignore_errors=True)

    print(f"\n  {'✅ 已搬到' if apply else '将搬到'} {box.name}/")
    for c in sorted(buckets, key=lambda x: -buckets[x]):
        print(f"      {c}  {buckets[c]} 个")
    return n, sz



def check_space():
    """删完发现空间没释放？八成是 Time Machine 本地快照攥着不放。"""
    print("\n\033[1m🔍 检查空间为什么没释放\033[0m\n")
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
    print(f"""
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


def main():
    if platform.system() != "Darwin":
        print("❌ 这个工具只适用于 macOS")
        sys.exit(1)

    auto = "--auto" in sys.argv          # 定时任务用：全自动，不问不开窗
    scan_only = "--scan" in sys.argv     # 只扫描，不动任何文件
    as_json = "--json" in sys.argv       # 结构化输出，给 AI agent 解析
    assume_yes = "--yes" in sys.argv     # 跳过确认，agent 已代用户确认过
    keep = 3
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

    print(f"\n\033[1m🧹 微信存储清理器 v{VERSION}\033[0m")
    print("   只移动，绝不删除。删不删由你决定。\n")

    # ① 找微信
    accounts = find_accounts()
    if not accounts:
        print("❌ 没找到微信数据目录。")
        print("   可能原因：没装微信 / 从没登录过 / 版本路径不同")
        sys.exit(1)

    if len(accounts) > 1 and not auto:
        print(f"发现 {len(accounts)} 个微信号：")
        for i, (acc, _m) in enumerate(accounts, 1):
            print(f"  {i}. {acc.name}")
        idx = int(ask("处理哪一个（填序号）", "1")) - 1
        accounts = [accounts[idx]]

    acc, msg = accounts[0]
    print(f"📱 微信账号目录：{acc.name}")

    # ② 扫描
    title("扫描中…")
    data = scan_months(msg)
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
    if not auto:
        keep = int(ask("\n保留最近几个月不动？更早的搬出来", "3"))

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
        print(f"\n\033[36m?\033[0m 文件搬到哪里？")
        print(f"    直接回车 = 用默认位置 {default_dest}")
        print(f"    输入 f 回车 = 弹出 Finder 自己挑")
        v = ask("    或直接粘贴一个路径", "")
        if v.lower() == "f":
            dest = choose_folder() or default_dest
        elif v:
            dest = Path(v.strip().strip("'\"")).expanduser()
        else:
            dest = default_dest

    # ⑤ 去重：很多文件你电脑上早就有了（自己发出去的、自己收藏的）
    dup_map = {}
    if _dedup and not ("--no-dedup" in sys.argv):
        allf = [f for rows in picked.values() for _y, _m, src, _s, _c in rows
                for f in src.rglob("*") if f.is_file() and f.name != ".DS_Store"]
        if allf:
            title("查重 —— 哪些文件你电脑上已经有了")
            roots = ["~/Desktop", "~/Documents", "~/Downloads"]
            if not auto:
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
        n, s = harvest(rows, dest, kind, apply=False, dup_map=dup_map)
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
        n, s = harvest(rows, dest, kind, apply=True, dup_map=dup_map)
        tn += n
        ts += s

    keep_dir = dest / "✅ 我要留的"
    keep_dir.mkdir(exist_ok=True)
    (keep_dir / "_把想留的文件拖进来.txt").write_text(
        "把你决定长期保留的文件从「⚠️ 待清理_日期」里拖到这个文件夹。\n"
        "本工具永远不会碰这里的东西，以后每次整理也不会覆盖它。\n", encoding="utf-8")

    batch_dir = dest / f"⚠️ 待清理_{today:%Y%m%d}"
    (batch_dir / "_怎么处理这批.txt").write_text(
        f"""这批文件是 {today} 从微信里搬出来的（{keep} 个月前的）

【怎么处理】
1. 视频基本可以全删 —— 群里转发的东西，你多半不会再看
2. 文档挑出想留的，拖进上一层的「✅ 我要留的」文件夹
3. 剩下的连同这个「⚠️ 待清理_{today:%Y%m%d}」文件夹整个删掉

【删掉会怎样】
· 聊天记录的文字一条都不会少
· 只是以后在微信里点那个文件，会提示「已过期或已被清理」
· 这些文件在腾讯服务器上通常也早就过期，本来就点不开

【🔴 删完发现空间没释放？这是最常见的困惑】
macOS 的 Time Machine「本地快照」会攥着你删掉的文件不放。
实测案例：删了 35GB 只回血 29GB，删了 17GB 只回血 5.7GB，
有台机器上快照一个人锁了 620GB。

查一下有没有快照：
    tmutil listlocalsnapshots /

有的话逐个删（不影响外接硬盘上的真备份，系统会自己重建）：
    tmutil deletelocalsnapshots 2026-08-10-000457

想彻底关掉自动快照（前提：你没在用 Time Machine 定时备份）：
    系统设置 → 通用 → 时间机器 → 关闭自动备份
    （命令行 `sudo tmutil disable` 需要「完全磁盘访问权限」，通常走界面更快）

本次搬运：{tn} 个文件，{human(ts)}
工具：微信存储清理器 v{VERSION}
""", encoding="utf-8")

    print(f"\n✅ 完成：{tn} 个文件，{human(ts)}")
    print(f"📂 {dest}")

    if auto or assume_yes:
        notify("微信文件待整理",
               f"已整理 {tn} 个文件（{human(ts)}）到 {dest.name}，有空去挑挑删删")
    else:
        subprocess.run(["open", str(dest)], capture_output=True)

        # ⑦ 定期任务
        title("要不要定期自动做这件事？")
        print("  微信会一直往电脑里存新文件，不管的话过阵子又堆起来。")
        if yes("  设置定期自动整理？", True):
            every = int(ask("  隔几个月跑一次", "3"))
            if 12 % every:
                print(f"  ⚠️ {every} 不能整除 12，改用 3")
                every = 3
            install_schedule(every, keep, dest)

    print("\n🎉 搞定。\n")


if __name__ == "__main__":
    main()
