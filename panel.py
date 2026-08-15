#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
👂 微信存储清理器 · 本地面板

双击启动 → 浏览器自动打开 → 点着用，不用碰终端。

设计取舍：
  · 只监听 127.0.0.1，随机端口，退出即关 —— 不对外暴露任何东西
  · 零依赖，http.server 是 Python 标准库
  · 「选文件夹」浏览器给不了真实路径，所以后端调 macOS 原生 Finder 选择框
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
try:
    import dedup as _dedup           # noqa: E402
except ImportError:
    _dedup = None

STATE = {"running": False, "done": False, "dest": None,
         "stage": 0, "detail": "", "result": None}


def log(msg):
    STATE["log"].append(msg)


def scan_payload(keep=3):
    from datetime import date
    accounts = wc.find_accounts()
    if not accounts:
        return {"ok": False, "error": "没找到微信数据目录。可能没装微信，或者从没登录过。"}
    acc, msg = accounts[0]
    data = wc.scan_months(msg)
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
    dest_guess = Path(STATE.get("dest") or (Path.home() / "Desktop/微信文件整理"))
    rec = wc.read_record(dest_guess)
    return {"ok": True, "account": acc.name, "kinds": kinds,
            "total": wc.human(total), "expired": wc.human(expired),
            "kept": wc.human(total - expired), "expired_bytes": expired,
            "last": rec.get("last"), "total_runs": len(rec.get("runs", [])),
            "suggest": str(dest_guess)}


def pick_folder():
    """调 macOS 原生选择框 —— 浏览器拿不到真实路径，只能借系统的"""
    r = subprocess.run(
        ["osascript", "-e",
         'POSIX path of (choose folder with prompt "选择存放微信文件的位置")'],
        capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def do_run(keep, dest, use_dedup):
    from datetime import date
    from collections import defaultdict
    STATE.update(running=True, done=False, log=[], dest=dest,
                 stage=1, detail="", result=None)
    try:
        acc, msg = wc.find_accounts()[0]
        data = wc.scan_months(msg)
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
        subprocess.run(["open", str(d)], capture_output=True)
    except Exception as e:
        STATE.update(detail=f"出错：{type(e).__name__}: {e}")
    finally:
        STATE.update(running=False, done=True)


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
        if self.path in ("/", "/index.html"):
            self._send((HERE / "panel.html").read_bytes(), "text/html")
        elif self.path.startswith("/api/scan"):
            keep = 3
            if "keep=" in self.path:
                try:
                    keep = int(self.path.split("keep=")[1].split("&")[0])
                except ValueError:
                    pass
            self._send(scan_payload(keep))
        elif self.path.startswith("/api/open"):
            import urllib.parse
            q = urllib.parse.urlparse(self.path).query
            tgt = urllib.parse.parse_qs(q).get("p", [""])[0]
            if tgt:
                subprocess.run(["open", str(Path(tgt).expanduser())],
                               capture_output=True)
            self._send({"ok": True})
        elif self.path == "/api/pick":
            self._send({"path": pick_folder()})
        elif self.path == "/api/status":
            self._send({"running": STATE["running"], "done": STATE["done"],
                        "stage": STATE.get("stage"), "detail": STATE.get("detail"),
                        "result": STATE.get("result")})
        elif self.path == "/api/snapshots":
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


def open_ui(url):
    """优先用 Chrome 的 --app 模式：无地址栏、无标签页，看着就是个独立应用窗口。
    没装 Chrome 就退回系统默认浏览器。"""
    for chrome in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                   "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"):
        if Path(chrome).exists():
            subprocess.Popen([chrome, f"--app={url}", "--window-size=880,1180"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def main():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))          # 让系统挑个空闲端口，避免撞上别的服务
    port = s.getsockname()[1]
    s.close()
    srv = HTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}/"
    print(f"\n👂 小耳微信清扫器 · 面板已启动\n   {url}")
    print("   浏览器应该会自动打开。用完关掉这个终端窗口即可。\n")
    threading.Thread(target=lambda: (time.sleep(0.6), open_ui(url)),
                     daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
