"""小耳微信清扫器 · 自动更新

打开时查一次 GitHub，有新版就装好再进面板。装不成就当无事发生，
照常用旧版——这是个会搬用户文件的工具，宁可不更新，不可打不开。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
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


def sha256_of(path):
    """分块读，8MB 的包也不占内存"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_team_id(text):
    """从 codesign -dv 的输出里抠 TeamIdentifier。

    这是整条验签链里最容易漏、也最要命的一环：签名有效只证明
    「某个 Apple 开发者签了它」，不证明是我们。中间人拿自己的
    Developer ID 签一个公证过的包进来，签名校验和 spctl 都会放行。

    ad-hoc 签名（codesign -s -）这一行是 'not set'，一并当作不合法。
    """
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("TeamIdentifier="):
            tid = line.split("=", 1)[1].strip()
            return None if tid in ("", "not set") else tid
    return None


def app_bundle_path():
    """当前 python 跑在哪个 .app 里。

    panel.py / updater.py 住在 X.app/Contents/Resources/app/，往上三级就是 .app。
    直接跑仓库源码时匹配不上，返回 None——顺带保证开发时永远不会
    触发自我更新，把工作目录搞坏。
    """
    if is_win():
        return None
    if len(HERE.parents) >= 3:
        p = HERE.parents[2]
        if p.suffix == ".app":
            return p
    return None


def _run(cmd):
    """跑命令，返回 (returncode, stdout+stderr)。
    codesign -dv 把信息写在 stderr，所以两股合并。"""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def verify_app(app_path):
    """三关：签名完整 → 确实是我们签的 → 公证有效。

    返回 (通过与否, 原因)。原因只用于日志，不给用户看——
    静默更新不该拿这种细节打扰人。
    """
    app_path = str(app_path)

    rc, out = _run(["codesign", "--verify", "--deep", "--strict", app_path])
    if rc != 0:
        return False, "签名不完整"

    rc, out = _run(["codesign", "-dv", "--verbose=4", app_path])
    tid = parse_team_id(out)
    if tid != TEAM_ID:
        return False, "签名方不是我们（TeamID=%s）" % tid

    rc, out = _run(["spctl", "-a", "-t", "execute", app_path])
    if rc != 0:
        return False, "公证校验没过"

    return True, ""


def _download(url, dest, on_state=None):
    """下载到 dest。失败返回 False，不抛。"""
    import urllib.request
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "xiaoer-wechat-cleaner"})
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_state and total:
                    on_state("downloading", int(done * 100 / total))
        return True
    except Exception:
        return False


def _old_path(app_path):
    """Path.with_suffix 会把 '小耳微信清扫器.app' 变成 '小耳微信清扫器.old'，
    丢掉 .app 后缀，所以直接拼字符串。"""
    return Path(str(app_path) + ".old")


def rollback_if_needed():
    """上次替换半途崩了的话，靠 .old 救回来。启动时调一次。"""
    app = app_bundle_path()
    if not app:
        return False
    old = _old_path(app)
    if not old.exists():
        return False
    if not app.exists():
        old.rename(app)
        return True
    shutil.rmtree(str(old), ignore_errors=True)   # 主体还在，残留直接清掉
    return False


def _extract_utf8(zip_path, dest):
    """解压，并修好中文文件名。

    zip 格式用一个标志位（0x800）声明「文件名是 UTF-8」。Info-ZIP 的
    zip 命令存中文名时不设这一位，Python 的 zipfile 只好按 cp437 解码，
    「小耳微信清扫器.bat」会变成一串乱码——入口文件没了，更新完等于把
    工具搞死。我们自己的包已经用 Python 打（会自动设这一位），
    这里再兜一层：没设标志位的名字，把 cp437 解回字节再按 UTF-8 读。
    """
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(zip_path)) as z:
        for item in z.infolist():
            name = item.filename
            if not (item.flag_bits & 0x800):
                try:
                    name = name.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass          # 本来就是 ASCII，或者真的是别的编码，原样用
            target = dest / name
            if not str(target.resolve()).startswith(str(dest.resolve())):
                continue          # 防 zip 里藏 ../../ 跳出解压目录
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(item) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def install(info, on_state=None):
    """下载 → 验证 → 替换。任何一步不对就中止。"""
    if is_win():
        return _install_win(info, on_state)
    return _install_mac(info, on_state)


def _install_win(info, on_state=None):
    """Windows 没有签名，链短一半：下载 → 验 sha256 → 解压 → 覆盖目录。

    正在跑的 .py 可以被覆盖（Python 已经读进内存了），不像 exe 会被锁。
    程序目录不可写（比如装在 Program Files）就静默跳过——
    一个清扫工具不该要管理员权限。
    """
    dest = HERE
    if not os.access(str(dest), os.W_OK):
        return False

    work = Path(tempfile.mkdtemp(prefix="xiaoer-update-"))
    try:
        zip_path = work / "update.zip"
        if not _download(info["url"], zip_path, on_state):
            return False

        if on_state:
            on_state("verifying", 0)
        if info.get("sha256") and sha256_of(zip_path) != info["sha256"]:
            return False

        stage = work / "stage"
        _extract_utf8(zip_path, stage)

        # zip 里可能多包一层同名目录，取真正含 panel.py 的那层
        root = stage
        if not (root / "panel.py").exists():
            subs = [d for d in stage.iterdir() if d.is_dir()]
            if len(subs) != 1 or not (subs[0] / "panel.py").exists():
                return False
            root = subs[0]

        for item in root.iterdir():
            target = dest / item.name
            if item.is_dir():
                if target.exists():
                    shutil.rmtree(str(target), ignore_errors=True)
                shutil.copytree(str(item), str(target))
            else:
                shutil.copy2(str(item), str(target))
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(str(work), ignore_errors=True)


def _install_mac(info, on_state=None):
    """整包替换 .app。app 是签名公证过的，改里面任何一个文件都会让
    签名失效、被系统判定成损坏，所以只能换整个 app。"""
    app = app_bundle_path()
    if not app or not os.access(str(app.parent), os.W_OK):
        return False        # 跑源码，或装在没写权限的地方——静默跳过

    work = Path(tempfile.mkdtemp(prefix="xiaoer-update-"))
    try:
        zip_path = work / "update.zip"
        if not _download(info["url"], zip_path, on_state):
            return False

        if on_state:
            on_state("verifying", 0)
        if info.get("sha256") and sha256_of(zip_path) != info["sha256"]:
            return False

        stage = work / "stage"
        stage.mkdir()
        rc, _ = _run(["ditto", "-x", "-k", str(zip_path), str(stage)])
        if rc != 0:
            return False

        new_apps = list(stage.glob("*.app"))
        if len(new_apps) != 1:
            return False
        ok, _why = verify_app(new_apps[0])
        if not ok:
            return False

        old = _old_path(app)
        shutil.rmtree(str(old), ignore_errors=True)
        app.rename(old)                       # 先挪开，崩了还能靠它救回
        rc, _ = _run(["ditto", str(new_apps[0]), str(app)])
        if rc != 0:
            old.rename(app)                   # 就位失败，立刻回滚
            return False
        shutil.rmtree(str(old), ignore_errors=True)
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(str(work), ignore_errors=True)


def restart():
    """重开 app，当前进程立刻退出。用 os._exit 是因为 HTTP server
    还在别的线程里 serve_forever，正常 return 回不到主循环。"""
    app = app_bundle_path()
    try:
        if app:
            subprocess.Popen(["open", "-a", str(app)])
        elif is_win():
            bat = HERE / "小耳微信清扫器.bat"
            subprocess.Popen(["cmd", "/c", "start", "", str(bat)],
                             creationflags=0x00000008)   # DETACHED_PROCESS
    except Exception:
        pass
    os._exit(0)
