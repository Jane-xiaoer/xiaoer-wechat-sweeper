#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
👂 微信存储清理器 · 本地面板

双击启动 → 浏览器自动打开 → 点着用，不用碰终端。

设计取舍：
  · 只监听 127.0.0.1，随机端口，退出即关 —— 不对外暴露任何东西
  · 零依赖，http.server 是 Python 标准库
  · 「选文件夹」浏览器给不了真实路径，所以后端调系统原生选择框
    （macOS 走 osascript，Windows 走 PowerShell 的 FolderBrowserDialog）
  · 所有危险动作（真正移动文件）必须前端显式确认后才发 /api/run
"""

import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wechat_cleaner as wc          # noqa: E402
import updater                       # noqa: E402
import settings                      # noqa: E402
try:
    import dedup as _dedup           # noqa: E402
except ImportError:
    _dedup = None

STATE = {"running": False, "done": False, "dest": None,
         "stage": 0, "detail": "", "result": None}

# 更新状态。main() 里检测到新版才会填 info，
# do_GET 靠它判断 / 该给面板还是给更新页。
UPDATE = {"info": None, "state": "idle", "percent": 0}


def log(msg):
    STATE["log"].append(msg)


def scan_payload(keep=3):
    from datetime import date
    accounts = wc.find_accounts()
    if not accounts:
        return {"ok": False, "error": "没找到微信数据目录。可能没装微信，或者从没登录过。"}
    acc, content, kinds = accounts[0]
    data = wc.scan_months(content, kinds)
    today = date.today()
    cutoff = today.year * 12 + today.month - keep
    kinds = {}
    total = expired = 0
    for kind, rows in data.items():
        months = []
        for y, m, _p, sz, n in sorted(rows, key=lambda r: -r[3]):
            exp = (y * 12 + m) <= cutoff
            months.append({"m": f"{y}-{m:02d}", "bytes": sz,
                           "human": wc.human(sz), "files": n, "expired": exp})
            total += sz
            if exp:
                expired += sz
        kinds[kind] = {"months": months,
                       "total": wc.human(sum(r[3] for r in rows))}
    # 优先本次会话里选过的，其次上次用过的，最后才是默认位置
    dest_guess = Path(STATE.get("dest")
                      or settings.get_last_dest()
                      or (Path.home() / "Desktop/微信文件整理"))
    rec = wc.read_record(dest_guess)
    return {"ok": True, "account": acc.name, "kinds": kinds,
            "total": wc.human(total), "expired": wc.human(expired),
            "kept": wc.human(total - expired), "expired_bytes": expired,
            "last": rec.get("last"), "total_runs": len(rec.get("runs", [])),
            "suggest": str(dest_guess)}


IS_WIN = wc.IS_WIN

# Windows 的文件夹选择框：-STA 是必须的，没有它对话框根本弹不出来；
# 输出编码要显式设成 UTF-8，否则中文路径回来是乱码。
PS_PICK = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "Add-Type -AssemblyName System.Windows.Forms;"
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
    "$d.Description = '选择存放微信文件的位置';"
    "$d.ShowNewFolderButton = $true;"
    "if ($d.ShowDialog() -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }"
)


def pick_folder():
    """弹系统原生选择框 —— 浏览器拿不到真实路径，只能借系统的"""
    if IS_WIN:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", PS_PICK],
            capture_output=True, text=True, encoding="utf-8", errors="ignore")
    else:
        r = subprocess.run(
            ["osascript", "-e",
             'POSIX path of (choose folder with prompt "选择存放微信文件的位置")'],
            capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def reveal(path):
    """在文件管理器里打开一个文件夹"""
    p = str(path)
    if IS_WIN:
        # explorer 就算成功也常返回非 0，别去判断返回码
        subprocess.run(["explorer", p])
    else:
        subprocess.run(["open", p], capture_output=True)


def do_run(keep, dest, use_dedup):
    from datetime import date
    from collections import defaultdict
    STATE.update(running=True, done=False, log=[], dest=dest,
                 stage=1, detail="", result=None)
    try:
        acc, content, kinds = wc.find_accounts()[0]
        data = wc.scan_months(content, kinds)
        today = date.today()
        cutoff = today.year * 12 + today.month - keep
        picked = {k: [r for r in rows if r[0] * 12 + r[1] <= cutoff]
                  for k, rows in data.items()}
        picked = {k: v for k, v in picked.items() if v}
        if not picked:
            STATE.update(running=False, done=True, stage=4,
                         detail=f"没有超过 {keep} 个月的文件")
            return

        d = Path(dest).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        settings.set_last_dest(d)     # 真搬到这儿了，下次打开默认还用它
        custom = wc.load_custom_rules(d)

        allf = [f for rows in picked.values() for _y, _m, src, _s, _c in rows
                for f in src.rglob("*") if f.is_file() and f.name != ".DS_Store"]

        dup_map = {}
        if use_dedup and _dedup:
            STATE.update(stage=1, detail="正在建本机索引…")
            idx, n = _dedup.build_index(["~/Desktop", "~/Documents", "~/Downloads"])
            STATE.update(stage=2, detail=f"已扫 {n:,} 个本机文件")
            dups, _sus = _dedup.check(allf, idx)
            dup_map = {f: h for f, h, _w in dups}
            STATE.update(detail=f"筛出 {len(dups)} 个重复")
        else:
            STATE.update(stage=2, detail="已跳过查重")

        STATE.update(stage=3, detail=f"共 {len(allf)} 个文件")
        buckets = defaultdict(int)
        tn = ts = 0
        for kind, rows in picked.items():
            for _y, _m, src, _s, _c in rows:
                for f in list(src.rglob("*")):
                    if f.is_file() and f.name != ".DS_Store":
                        buckets[("_重复_电脑里已有" if f in dup_map
                                 else wc.category_of(f.name, custom))] += 1
            import io
            buf, old_out = io.StringIO(), sys.stdout
            sys.stdout = buf
            try:
                n, sz = wc.harvest(rows, d, kind, apply=True,
                                   dup_map=dup_map, custom=custom)
            finally:
                sys.stdout = old_out
            tn += n
            ts += sz

        misc = buckets.get("📎 杂项", 0) + buckets.get("📄 文档/📎 其他文档", 0)
        wc.write_record(d, tn, ts, keep, len(dup_map))
        STATE["result"] = {
            "n": tn, "size": wc.human(ts), "dest": str(d),
            "buckets": sorted(((k, v) for k, v in buckets.items()
                               if not k.startswith("_")), key=lambda x: -x[1]),
            "misc": misc, "dups": len(dup_map)}
        STATE.update(stage=4, detail="已打开文件夹")
        reveal(d)
    except Exception as e:
        STATE.update(detail=f"出错：{type(e).__name__}: {e}")
    finally:
        STATE.update(running=False, done=True)


# ── 简转繁 ──────────────────────────────────────────────
# 只用于「显示」：界面文案、接口回给前端的分类名与进度文案。
# 硬盘上的文件夹名一律保持简体（wechat_cleaner.py 里那套），
# 免得用户已经清扫过的目录旁边又冒出一套繁体夹子。
TRAD = {
    "扫": "掃", "电": "電", "脑": "腦", "占": "佔",
    "还": "還", "过": "過", "里": "裡", "发": "發",
    "个": "個", "说": "說", "现": "現", "开": "開",
    "动": "動", "这": "這", "么": "麼", "夹": "夾",
    "复": "複", "归": "歸", "类": "類", "录": "錄",
    "机": "機", "从": "從", "实": "實", "体": "體",
    "号": "號", "认": "認", "筛": "篩", "单": "單",
    "后": "後", "应": "應", "删": "刪", "误": "誤",
    "总": "總", "盘": "盤", "统": "統", "网": "網",
    "连": "連", "话": "話", "记": "記", "图": "圖",
    "见": "見", "终": "終", "条": "條", "给": "給",
    "别": "別", "进": "進", "关": "關", "标": "標",
    "间": "間", "线": "線", "压": "壓", "缩": "縮",
    "东": "東", "们": "們", "价": "價", "会": "會",
    "余": "餘", "净": "淨", "几": "幾", "吗": "嗎",
    "响": "響", "块": "塊", "处": "處", "并": "並",
    "拣": "揀", "换": "換", "数": "數", "时": "時",
    "杂": "雜", "来": "來", "档": "檔", "没": "沒",
    "炉": "爐", "点": "點", "独": "獨", "监": "監",
    "着": "著", "确": "確", "经": "經", "结": "結",
    "联": "聯", "规": "規", "视": "視", "计": "計",
    "议": "議", "该": "該", "请": "請", "转": "轉",
    "轮": "輪", "载": "載", "运": "運", "选": "選",
    "释": "釋", "钮": "鈕", "顶": "頂", "项": "項",
    "频": "頻", "骤": "驟",
    # 分类名要用的：视频/文档/简历/合同与表单/课件与教程/书籍/报告与白皮书/代码/安装包
    "简": "簡", "历": "歷", "与": "與", "课": "課",
    "书": "書", "报": "報", "码": "碼", "装": "裝",
    "据": "據", "错": "錯",
}
_TRAD_TABLE = str.maketrans(TRAD)
# 一简对多繁的，先按词组定死再逐字转：
# 「干净」的「干」在台湾是「乾」，单字表会转成「幹淨」。
# 「台」保留不转 —— 「一台機器」比「一臺機器」自然。
TRAD_WORDS = (("干净", "乾淨"),)


def to_tw(text):
    """把一段文字转成繁体显示。只碰文案，别拿它处理路径。"""
    if not text:
        return text
    for zh, tw in TRAD_WORDS:
        text = text.replace(zh, tw)
    return text.translate(_TRAD_TABLE)


class H(BaseHTTPRequestHandler):
    def _send(self, obj, ctype="application/json"):
        body = (json.dumps(obj, ensure_ascii=False).encode()
                if ctype == "application/json" else obj)
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html", "/watercolor", "/watercolor/"):
            if UPDATE["info"]:
                self._send((HERE / "updating.html").read_bytes(), "text/html")
                return
            html = (HERE / "panel.html").read_text(encoding="utf-8")
            html = to_tw(html)
            html = html.replace("🧹 開始清理", "開始清掃")
            html = html.replace("下一步 →", "下一步　›")
            html = html.replace("開始清掃 →", "開始清掃　›")
            html = html.replace("📂 去看文件夾", "查看文件夾")
            html = html.replace("📁 换一個文件夾…", "選擇另一個文件夾…")
            html = html.replace("📁 換一個文件夾…", "選擇另一個文件夾…")
            html = html.replace(
                '<div class="slider">',
                '<div class="slider"><span class="brush-cursor" '
                'aria-hidden="true"></span>',
            )
            html = html.replace(
                "</head>",
                '<link rel="icon" type="image/png" href="/assets/app-icon.png?v=2.2.0">\n'
                '<link rel="stylesheet" href="/watercolor-ui.css?v=2.2.0">\n</head>',
            )
            html = html.replace(
                "<main>",
                '<main><img class="watercolor-mascot" '
                'src="/assets/watercolor-mascot.png?v=2.2.0" '
                'alt="" aria-hidden="true">',
            )
            html = html.replace(
                '<div class="foot">',
                '<span class="watercolor-seal" aria-hidden="true">小<br>耳</span>'
                '<div class="foot">',
            )
            html = html.replace(
                "</body>",
                '<script>(()=>{const s=document.getElementById("keep"),'
                'b=document.querySelector(".brush-cursor");if(!s||!b)return;'
                'const paint=()=>{const p=(+s.value-+s.min)/(+s.max-+s.min);'
                's.style.setProperty("--paint",`${p*100}%`);'
                'b.style.left=`${s.offsetLeft+10+p*(s.offsetWidth-20)}px`;'
                'b.style.top=`${s.offsetTop+s.offsetHeight/2}px`};'
                's.setAttribute("aria-label","拖動毛筆選擇保留月份");'
                's.title="拖動毛筆，選擇要保留的月份";'
                's.addEventListener("input",paint);window.addEventListener('
                '"resize",paint);requestAnimationFrame(paint)})()</script>\n</body>',
            )
            html = html.replace(
                "</body>",
                '<script>(()=>{const raw=window.go;let changing=false;'
                'window.go=n=>{const cur=document.querySelector(".step.on");'
                'if(changing)return;if(!cur||cur.id===`p${n}`||matchMedia('
                '"(prefers-reduced-motion: reduce)").matches)return raw(n);'
                'changing=true;cur.classList.add("ink-away");setTimeout(()=>{'
                'cur.classList.remove("ink-away");raw(n);changing=false},260)}})()'
                '</script>\n</body>',
            )
            if "step=" in self.path:
                try:
                    step = max(1, min(5, int(self.path.split("step=")[1].split("&")[0])))
                    html = html.replace(
                        "</body>",
                        f"<script>setTimeout(() => go({step}), 900)</script>\n</body>",
                    )
                except ValueError:
                    pass
            self._send(html.encode("utf-8"), "text/html")
        elif route == "/watercolor-ui.css":
            self._send((HERE / "watercolor-ui.css").read_bytes(), "text/css")
        elif route in ("/assets/app-icon.png", "/favicon.ico"):
            self._send(
                (HERE / "assets" / "app-icon.png").read_bytes(),
                "image/png",
            )
        elif route == "/assets/watercolor-paper.png":
            self._send(
                (HERE / "assets" / "watercolor-paper.png").read_bytes(),
                "image/png",
            )
        elif route == "/assets/watercolor-mascot.png":
            self._send(
                (HERE / "assets" / "watercolor-mascot.png").read_bytes(),
                "image/png",
            )
        elif route == "/assets/watercolor-button.png":
            self._send(
                (HERE / "assets" / "watercolor-button.png").read_bytes(),
                "image/png",
            )
        elif route == "/assets/watercolor-brush-thumb.png":
            self._send(
                (HERE / "assets" / "watercolor-brush-thumb.png").read_bytes(),
                "image/png",
            )
        elif self.path.startswith("/api/scan"):
            keep = 3
            if "keep=" in self.path:
                try:
                    keep = int(self.path.split("keep=")[1].split("&")[0])
                except ValueError:
                    pass
            payload = scan_payload(keep)
            if payload.get("error"):
                payload["error"] = to_tw(payload["error"])
            self._send(payload)
        elif self.path.startswith("/api/open"):
            import urllib.parse
            q = urllib.parse.urlparse(self.path).query
            tgt = urllib.parse.parse_qs(q).get("p", [""])[0]
            if tgt:
                reveal(Path(tgt).expanduser())
            self._send({"ok": True})
        elif self.path == "/api/pick":
            picked = pick_folder()
            if picked:
                # 选完当场就记，不等真跑完——选了却没跑就关掉的话，
                # 下次打开还得重选一遍，那正是这功能要省掉的麻烦
                settings.set_last_dest(picked)
            self._send({"path": picked})
        elif self.path == "/api/update/status":
            self._send({"version": (UPDATE["info"] or {}).get("version", ""),
                        "state": UPDATE["state"],
                        "percent": UPDATE["percent"]})
        elif self.path == "/api/update/restart":
            if UPDATE["state"] == "done":
                threading.Thread(target=updater.restart, daemon=True).start()
                self._send({"ok": True})
            else:
                UPDATE["info"] = None          # 更新失败，放行回正常面板
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
        elif self.path == "/api/status":
            detail = STATE.get("detail") or ""
            # 报错信息保持原样：里面可能带着真实路径，转成繁体会误导人去找
            if not detail.startswith("出错"):
                detail = to_tw(detail)
            res = STATE.get("result")
            if res:
                res = dict(res)
                # 只翻分类名。dest 是硬盘上的真实路径，一个字都不能动
                res["buckets"] = [[to_tw(k), v] for k, v in res.get("buckets", [])]
            self._send({"running": STATE["running"], "done": STATE["done"],
                        "stage": STATE.get("stage"), "detail": detail,
                        "result": res})
        elif self.path == "/api/snapshots":
            # Time Machine 本地快照是 macOS 独有的坑，Windows 没这回事
            if IS_WIN:
                self._send({"snapshots": []})
                return
            r = subprocess.run(["tmutil", "listlocalsnapshots", "/"],
                               capture_output=True, text=True)
            snaps = [l.strip().replace("com.apple.TimeMachine.", "").replace(".local", "")
                     for l in r.stdout.splitlines() if "com.apple" in l]
            self._send({"snapshots": snaps})
        else:
            self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or "{}")
        if self.path == "/api/run":
            if STATE["running"]:
                self._send({"ok": False, "error": "正在跑"})
                return
            threading.Thread(target=do_run, daemon=True, args=(
                int(body.get("keep", 3)), body.get("dest"),
                bool(body.get("dedup", True)))).start()
            self._send({"ok": True})
        else:
            self.send_error(404)

    def log_message(self, *a):
        pass            # 别把 HTTP 日志刷进终端，用户看的是浏览器


MAC_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)


def win_browsers():
    """Windows 上浏览器可能装在三个地方（64 位、32 位、只给当前用户装）"""
    out = []
    bases = [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
             os.environ.get("LOCALAPPDATA")]
    for b in filter(None, bases):
        out += [str(Path(b) / "Google/Chrome/Application/chrome.exe"),
                str(Path(b) / "Microsoft/Edge/Application/msedge.exe"),
                str(Path(b) / "BraveSoftware/Brave-Browser/Application/brave.exe")]
    return out


def open_ui(url):
    """优先用 Chrome 的 --app 模式：无地址栏、无标签页，看着就是个独立应用窗口。
    没装 Chrome 就退回系统默认浏览器。"""
    for exe in (win_browsers() if IS_WIN else MAC_BROWSERS):
        if Path(exe).exists():
            subprocess.Popen([exe, f"--app={url}", "--window-size=880,1180"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def do_update(info):
    """后台装。装成了让前端跳 /api/update/restart 触发重开，
    装不成就把状态标 failed，前端会放行回正常面板。"""
    def on_state(state, percent):
        UPDATE["state"] = state
        UPDATE["percent"] = percent
    ok = updater.install(info, on_state)
    UPDATE["state"] = "done" if ok else "failed"
    UPDATE["percent"] = 100 if ok else 0


def main():
    updater.rollback_if_needed()      # 上次替换半途崩了就先救回来

    info = updater.check(timeout=3)   # 查不到、超时、没新版都返回 None
    if info:
        UPDATE["info"] = info
        UPDATE["state"] = "downloading"

    s = socket.socket()
    s.bind(("127.0.0.1", 0))          # 让系统挑个空闲端口，避免撞上别的服务
    port = s.getsockname()[1]
    s.close()
    srv = HTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}/"
    print(f"\n👂 小耳微信清扫器 v{updater.current_version()} · 面板已启动\n   {url}")
    print("   浏览器应该会自动打开。用完关掉这个终端窗口即可。\n")
    threading.Thread(target=lambda: (time.sleep(0.6), open_ui(url)),
                     daemon=True).start()

    if info:
        threading.Thread(target=lambda: do_update(info), daemon=True).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
