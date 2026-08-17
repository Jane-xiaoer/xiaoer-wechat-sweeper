# 自动更新实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让小耳微信清扫器打开时自动装好新版，用户不点任何按钮。

**Architecture:** 新增独立模块 `updater.py`（查版本 / 下载 / 验证 / 替换），`panel.py` 只在 `main()` 里调它。macOS 走整包 `.app` 替换 + 三重验签（sha256 / 签名与 TeamID / 公证），Windows 走目录覆盖。任何一环失败都静默退回旧版正常启动。

**Tech Stack:** Python 3.9 标准库（urllib / hashlib / zipfile / subprocess），零第三方依赖；测试用标准库 unittest；macOS 用 `ditto`、`codesign`、`spctl`。

**Spec:** `docs/superpowers/specs/2026-08-16-auto-update-design.md`

## Global Constraints

这些是全局要求，每个任务都隐含包含：

- **产品代码零第三方依赖** —— app 跑在系统自带 `/usr/bin/python3`，不能 `pip install` 任何东西。
- **必须兼容 Python 3.9.6** —— 系统 python3 就是这个版本。禁止 `match` 语句、`int | None` 式类型标注、`zip(strict=)`、`dict |` 合并等 3.10+ 语法。
- **测试用标准库 `unittest`** —— 本机没有 pytest，不要为了测试引入依赖。跑法 `python3 -m unittest discover -s tests -v`。
- **仓库** `Jane-xiaoer/xiaoer-wechat-sweeper`，API `https://api.github.com/repos/Jane-xiaoer/xiaoer-wechat-sweeper/releases/latest`
- **合法 TeamIdentifier 只有 `3DP32PZ62M`**（Developer ID Application: Juan Li）。
- **失败兜底一律是「旧版正常打开」** —— 更新链路上任何异常都不能让工具打不开。所有对外函数吞掉异常返回失败值，不向上抛。
- **注释用中文**，跟 `panel.py` / `wechat_cleaner.py` 现有风格一致：解释「为什么」，不复述「做了什么」。
- **不动 `wechat_cleaner.py`** —— 清扫核心逻辑与本次改动无关。

## 文件结构

| 文件 | 职责 |
|------|------|
| `updater.py`（新建） | 全部更新逻辑。版本比对、查询、下载、验证、替换、重开。**唯一新增的产品文件** |
| `panel.py`（改 `main()` 与 `do_GET`） | 启动时调 updater；有更新时把 `/` 换成更新页；新增 `/api/update/status` |
| `updating.html`（新建） | 「正在更新到 vX.Y.Z…」水彩小页，轮询状态 |
| `scripts/build-macos-app.sh`（改） | 把 `VERSION` / `updater.py` / `updating.html` 拷进 bundle |
| `tests/test_updater.py`（新建） | 纯函数单测：版本比对、asset 挑选、TeamID 解析、check 降级 |
| `tests/make_fixtures.sh`（新建） | 造验签链测试素材：正常包 / 篡改包 / ad-hoc 签名包 |
| `tests/test_verify_chain.sh`（新建） | 验签链集成测试，在沙盒目录跑，不碰 `/Applications` |

`updater.py` 单独成文件而不是塞进 `panel.py`：后者已经 449 行，且更新逻辑与面板逻辑没有共享状态，边界干净。

---

### Task 1: 版本读取与比对

**Files:**
- Create: `updater.py`
- Create: `tests/test_updater.py`

**Interfaces:**
- Consumes: 无（第一个任务）
- Produces:
  - `parse_version(v) -> tuple` —— `"v2.10.0"` → `(2, 10, 0)`，脏数据 → `(0,)`
  - `current_version() -> str` —— 读同目录 `VERSION`，读不到返回 `"0.0.0"`
  - `HERE: Path` —— `updater.py` 所在目录

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_updater.py`：

```python
"""updater 纯函数单测。跑法：python3 -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import updater  # noqa: E402


class TestParseVersion(unittest.TestCase):
    def test_基本解析(self):
        self.assertEqual(updater.parse_version("2.3.0"), (2, 3, 0))

    def test_去掉_v_前缀(self):
        """GitHub 的 tag 是 v2.3.0，本地 VERSION 是 2.3.0，比对前要统一"""
        self.assertEqual(updater.parse_version("v2.3.0"), (2, 3, 0))

    def test_两位数版本号(self):
        """字符串比较下 '2.10.0' < '2.9.0'，是本项目最容易踩的坑"""
        self.assertGreater(updater.parse_version("2.10.0"),
                           updater.parse_version("2.9.0"))

    def test_脏数据不抛异常(self):
        for bad in ("", "abc", None, "2.x.0", "  "):
            self.assertEqual(updater.parse_version(bad), (0,))

    def test_空白容忍(self):
        self.assertEqual(updater.parse_version(" 2.3.0\n"), (2, 3, 0))


class TestCurrentVersion(unittest.TestCase):
    def test_读到仓库根的_VERSION(self):
        v = updater.current_version()
        self.assertRegex(v, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `cd ~/projects/wechat-cleaner && python3 -m unittest discover -s tests -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'updater'`

- [ ] **Step 3: 写最小实现**

创建 `updater.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/projects/wechat-cleaner && python3 -m unittest discover -s tests -v`
Expected: PASS，6 个测试全绿

- [ ] **Step 5: 提交**

```bash
cd ~/projects/wechat-cleaner
git add updater.py tests/test_updater.py
git commit -m "更新器：版本读取与比对

字符串比版本号会判定 2.10.0 < 2.9.0，用元组。"
```

---

### Task 2: 查询远端版本（check）

**Files:**
- Modify: `updater.py`
- Modify: `tests/test_updater.py`

**Interfaces:**
- Consumes: `parse_version()`、`current_version()`、`API`（Task 1）
- Produces:
  - `pick_asset(assets, is_win) -> dict | None` —— 从 release assets 里挑本平台的 zip
  - `check(timeout=3) -> dict | None` —— 有新版时返回 `{"version", "notes", "url", "sha256", "size"}`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_updater.py`（放在 `if __name__` 之前）：

```python
ASSETS = [
    {"name": "Xiaoer-WeChat-Cleaner-v2.3.0-Windows.zip",
     "browser_download_url": "https://example.com/win.zip",
     "digest": "sha256:aaa", "size": 5252136},
    {"name": "Xiaoer-WeChat-Cleaner-v2.3.0.zip",
     "browser_download_url": "https://example.com/mac.zip",
     "digest": "sha256:bbb", "size": 8160192},
]


class TestPickAsset(unittest.TestCase):
    def test_mac_挑不带_Windows_的那个(self):
        a = updater.pick_asset(ASSETS, is_win=False)
        self.assertEqual(a["browser_download_url"], "https://example.com/mac.zip")

    def test_win_挑带_Windows_的那个(self):
        a = updater.pick_asset(ASSETS, is_win=True)
        self.assertEqual(a["browser_download_url"], "https://example.com/win.zip")

    def test_没有匹配的返回_None(self):
        self.assertIsNone(updater.pick_asset([], is_win=False))

    def test_忽略非_zip_资产(self):
        only_txt = [{"name": "notes.txt", "browser_download_url": "x",
                     "digest": "", "size": 1}]
        self.assertIsNone(updater.pick_asset(only_txt, is_win=False))


class TestCheck(unittest.TestCase):
    """check() 的铁律：任何异常都返回 None，绝不向上抛。"""

    def _fake_api(self, payload):
        """把 _fetch_json 换成固定返回，避免测试联网"""
        original = updater._fetch_json
        updater._fetch_json = lambda url, timeout: payload
        self.addCleanup(lambda: setattr(updater, "_fetch_json", original))

    def test_有新版返回信息(self):
        self._fake_api({"tag_name": "v99.0.0", "body": "更新说明",
                        "assets": ASSETS})
        got = updater.check()
        self.assertEqual(got["version"], "99.0.0")
        self.assertEqual(got["notes"], "更新说明")
        self.assertEqual(got["sha256"], "bbb")     # 去掉 sha256: 前缀

    def test_已是最新返回_None(self):
        self._fake_api({"tag_name": "v0.0.1", "body": "", "assets": ASSETS})
        self.assertIsNone(updater.check())

    def test_同版本不更新(self):
        self._fake_api({"tag_name": "v" + updater.current_version(),
                        "body": "", "assets": ASSETS})
        self.assertIsNone(updater.check())

    def test_网络异常返回_None(self):
        def boom(url, timeout):
            raise OSError("网络不通")
        original = updater._fetch_json
        updater._fetch_json = boom
        self.addCleanup(lambda: setattr(updater, "_fetch_json", original))
        self.assertIsNone(updater.check())

    def test_畸形_JSON_返回_None(self):
        self._fake_api({"没有": "tag_name"})
        self.assertIsNone(updater.check())

    def test_有新版但没有本平台资产返回_None(self):
        self._fake_api({"tag_name": "v99.0.0", "body": "", "assets": []})
        self.assertIsNone(updater.check())
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `cd ~/projects/wechat-cleaner && python3 -m unittest discover -s tests -v`
Expected: FAIL —— `AttributeError: module 'updater' has no attribute 'pick_asset'`

- [ ] **Step 3: 写最小实现**

在 `updater.py` 顶部 import 区补 `import urllib.request`，然后追加：

```python
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
```

同时在 Task 1 的常量区下面补平台判断（`updater.py` 不 import `wechat_cleaner`，保持它可独立测试）：

```python
def is_win():
    return sys.platform.startswith("win")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/projects/wechat-cleaner && python3 -m unittest discover -s tests -v`
Expected: PASS，16 个测试全绿

- [ ] **Step 5: 提交**

```bash
cd ~/projects/wechat-cleaner
git add updater.py tests/test_updater.py
git commit -m "更新器：查询远端版本

check() 吞掉所有异常返回 None——查不到和没新版对调用方是一回事。"
```

---

### Task 3: 校验用的纯函数

**Files:**
- Modify: `updater.py`
- Modify: `tests/test_updater.py`

**Interfaces:**
- Consumes: `TEAM_ID`（Task 1）
- Produces:
  - `sha256_of(path) -> str`
  - `parse_team_id(text) -> str | None` —— 从 `codesign -dv` 输出里抠 TeamIdentifier
  - `app_bundle_path() -> Path | None` —— 当前跑在哪个 `.app` 里，跑源码时返回 None

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_updater.py`：

```python
import tempfile


class TestSha256(unittest.TestCase):
    def test_算得对(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello")
            p = f.name
        self.addCleanup(lambda: os.unlink(p))
        # echo -n hello | shasum -a 256
        self.assertEqual(
            updater.sha256_of(p),
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")


class TestParseTeamId(unittest.TestCase):
    def test_正常签名(self):
        out = ("Identifier=xyz.xiaoerai.wechat-cleaner\n"
               "Authority=Developer ID Application: Juan Li (3DP32PZ62M)\n"
               "TeamIdentifier=3DP32PZ62M\n")
        self.assertEqual(updater.parse_team_id(out), "3DP32PZ62M")

    def test_adhoc_签名没有_TeamID(self):
        """codesign -s - 签的包，TeamIdentifier=not set，必须当作不合法"""
        self.assertIsNone(updater.parse_team_id("TeamIdentifier=not set\n"))

    def test_完全没这一行(self):
        self.assertIsNone(updater.parse_team_id("Identifier=com.foo\n"))

    def test_空输入(self):
        self.assertIsNone(updater.parse_team_id(""))
```

在测试文件顶部 import 区补 `import os`。

- [ ] **Step 2: 跑测试确认它失败**

Run: `cd ~/projects/wechat-cleaner && python3 -m unittest discover -s tests -v`
Expected: FAIL —— `AttributeError: module 'updater' has no attribute 'sha256_of'`

- [ ] **Step 3: 写最小实现**

`updater.py` 顶部 import 区补 `import hashlib`，然后追加：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/projects/wechat-cleaner && python3 -m unittest discover -s tests -v`
Expected: PASS，21 个测试全绿

顺便手工确认 `app_bundle_path()` 在仓库里跑时返回 None：

Run: `cd ~/projects/wechat-cleaner && python3 -c "import updater; print(updater.app_bundle_path())"`
Expected: `None`

- [ ] **Step 5: 提交**

```bash
cd ~/projects/wechat-cleaner
git add updater.py tests/test_updater.py
git commit -m "更新器：校验用的纯函数

parse_team_id 是验签链里最容易漏的一环——签名有效不等于是我们签的。"
```

---

### Task 4: macOS 整包替换

**Files:**
- Modify: `updater.py`
- Create: `tests/make_fixtures.sh`
- Create: `tests/test_verify_chain.sh`

**Interfaces:**
- Consumes: `sha256_of()`、`parse_team_id()`、`app_bundle_path()`、`TEAM_ID`（Task 1、3）
- Produces:
  - `verify_app(app_path) -> (bool, str)` —— 跑完签名 / TeamID / 公证三关，返回是否通过与原因
  - `install(info, on_state=None) -> bool` —— 下载到替换全流程
  - `rollback_if_needed()` —— 启动时检查上次替换有没有半途崩掉
  - `restart()` —— 重开 app 并退出当前进程

- [ ] **Step 1: 写验签链的集成测试**

创建 `tests/make_fixtures.sh`：

```bash
#!/bin/bash
# 造验签链测试素材。三个包分别触发链上不同的关卡。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIX="$ROOT/.tmp/fixtures"
SRC="$ROOT/dist/Xiaoer-WeChat-Cleaner-v2.3.0.zip"

[[ -f "$SRC" ]] || { echo "缺少素材包 $SRC"; exit 1; }

rm -rf "$FIX"; mkdir -p "$FIX"

# 1. 正常包：应该全过
cp "$SRC" "$FIX/good.zip"

# 2. 篡改包：改掉一个字节，sha256 那关就该拦下
cp "$SRC" "$FIX/tampered.zip"
printf '\x00' | dd of="$FIX/tampered.zip" bs=1 seek=1000 count=1 conv=notrunc 2>/dev/null

# 3. 别人签的包：用 ad-hoc 重签，TeamID 那关该拦下
#    （拿不到第二张 Developer ID 证书，ad-hoc 走的是同一条防线：
#     签名本身有效，但 TeamIdentifier 不是 3DP32PZ62M）
mkdir -p "$FIX/resign"
ditto -x -k "$SRC" "$FIX/resign"
codesign --force --deep --sign - "$FIX/resign/小耳微信清扫器.app" 2>/dev/null
ditto -c -k --sequesterRsrc --keepParent \
  "$FIX/resign/小耳微信清扫器.app" "$FIX/foreign.zip"

echo "素材就绪：$FIX"
ls -1 "$FIX"/*.zip
```

创建 `tests/test_verify_chain.sh`：

```bash
#!/bin/bash
# 验签链集成测试。全程在沙盒目录，不碰 /Applications。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIX="$ROOT/.tmp/fixtures"
WORK="$ROOT/.tmp/verify-test"
PASS=0; FAIL=0

bash "$ROOT/tests/make_fixtures.sh" >/dev/null

check() {   # check <名字> <zip> <期望 ok|reject>
  local name="$1" zip="$2" want="$3"
  rm -rf "$WORK"; mkdir -p "$WORK"
  ditto -x -k "$zip" "$WORK" 2>/dev/null
  local app
  app="$(find "$WORK" -maxdepth 1 -name '*.app' | head -1)"
  local got
  got="$(cd "$ROOT" && python3 -c "
import sys, updater
ok, why = updater.verify_app('$app')
print('ok' if ok else 'reject:' + why)
" 2>&1)"
  if [[ "$got" == "$want"* ]]; then
    echo "  ✅ $name → $got"; PASS=$((PASS+1))
  else
    echo "  ❌ $name → 期望 $want，实际 $got"; FAIL=$((FAIL+1))
  fi
}

echo "验签链测试"
check "正常包全过"         "$FIX/good.zip"     ok
check "别人签的包被拒"      "$FIX/foreign.zip"  reject

# 篡改包测的是 sha256 那一关，不进 verify_app
echo "sha256 关卡"
GOOD_SUM="$(shasum -a 256 "$FIX/good.zip" | cut -d' ' -f1)"
TAMP_SUM="$(shasum -a 256 "$FIX/tampered.zip" | cut -d' ' -f1)"
if [[ "$GOOD_SUM" != "$TAMP_SUM" ]]; then
  echo "  ✅ 篡改一个字节后 sha256 不同"; PASS=$((PASS+1))
else
  echo "  ❌ 篡改后 sha256 竟然一样"; FAIL=$((FAIL+1))
fi

rm -rf "$WORK"
echo
echo "通过 $PASS，失败 $FAIL"
[[ $FAIL -eq 0 ]]
```

```bash
chmod +x tests/make_fixtures.sh tests/test_verify_chain.sh
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `cd ~/projects/wechat-cleaner && bash tests/test_verify_chain.sh`
Expected: FAIL —— `module 'updater' has no attribute 'verify_app'`，两条 check 都报 ❌

- [ ] **Step 3: 写最小实现**

`updater.py` 顶部 import 区补 `import shutil`、`import subprocess`、`import tempfile`，追加：

```python
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


def install(info, on_state=None):
    """下载 → 验证 → 替换。任何一步不对就中止，一个字节都不写进 /Applications。"""
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/projects/wechat-cleaner && bash tests/test_verify_chain.sh`
Expected: `通过 3，失败 0` —— 正常包 `ok`，ad-hoc 重签包 `reject:签名方不是我们`，篡改包 sha256 不同

再跑一次单测确认没弄坏别的：

Run: `cd ~/projects/wechat-cleaner && python3 -m unittest discover -s tests -v`
Expected: PASS，21 个

- [ ] **Step 5: 提交**

```bash
cd ~/projects/wechat-cleaner
git add updater.py tests/make_fixtures.sh tests/test_verify_chain.sh
git commit -m "更新器：macOS 整包替换与三重验签

先把旧 app 挪成 .old 再就位，替换途中崩了还能救回来。"
```

---

### Task 5: Windows 目录覆盖

**Files:**
- Modify: `updater.py`

**Interfaces:**
- Consumes: `sha256_of()`、`_download()`、`is_win()`、`HERE`（Task 1–4）
- Produces: `install()` 在 Windows 上走目录覆盖分支（对外签名不变）

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_updater.py`：

```python
def _rm_readonly(p):
    import shutil as _sh
    os.chmod(str(p), 0o700)
    _sh.rmtree(str(p), ignore_errors=True)


class TestWinInstallGuard(unittest.TestCase):
    """Windows 分支在 mac 上没法端到端测，但守卫逻辑要能测。

    直接测 _install_win 而不是 install()：后者在 mac 上会先因为
    app_bundle_path() 返回 None 而失败，测不到写权限这条守卫。
    """

    def test_目录不可写时返回_False(self):
        ro = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rm_readonly(ro))
        os.chmod(str(ro), 0o500)          # 只读

        original_here = updater.HERE
        updater.HERE = ro
        self.addCleanup(lambda: setattr(updater, "HERE", original_here))

        self.assertFalse(
            updater._install_win({"url": "x", "sha256": "", "size": 0}))

    def test_下载失败时返回_False(self):
        """可写目录 + 下载不通 → 依然安全返回 False，不抛"""
        rw = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rm_readonly(rw))

        original_here = updater.HERE
        updater.HERE = rw
        self.addCleanup(lambda: setattr(updater, "HERE", original_here))

        original_dl = updater._download
        updater._download = lambda url, dest, on_state=None: False
        self.addCleanup(lambda: setattr(updater, "_download", original_dl))

        self.assertFalse(
            updater._install_win({"url": "x", "sha256": "", "size": 0}))
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `cd ~/projects/wechat-cleaner && python3 -m unittest discover -s tests -v`
Expected: FAIL —— `AttributeError: module 'updater' has no attribute '_install_win'`，两条都红

- [ ] **Step 3: 写最小实现**

把 Task 4 的 `install()` 改成先分流，macOS 部分原样搬进 `_install_mac()`：

```python
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
        with zipfile.ZipFile(str(zip_path)) as z:
            z.extractall(str(stage))

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
```

`updater.py` 顶部 import 区补 `import zipfile`。

同时把 Task 4 里 `install()` 的函数名改成 `_install_mac`，函数体不变。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/projects/wechat-cleaner && python3 -m unittest discover -s tests -v`
Expected: PASS，23 个

Run: `cd ~/projects/wechat-cleaner && bash tests/test_verify_chain.sh`
Expected: `通过 3，失败 0`（mac 分支没被改坏）

- [ ] **Step 5: 提交**

```bash
cd ~/projects/wechat-cleaner
git add updater.py tests/test_updater.py
git commit -m "更新器：Windows 目录覆盖分支

没签名反而简单，但程序目录不可写就跳过——不要管理员权限。"
```

---

### Task 6: 接进面板

**Files:**
- Modify: `panel.py`（`main()`、`do_GET`）
- Create: `updating.html`

**Interfaces:**
- Consumes: `updater.check()`、`install()`、`rollback_if_needed()`、`restart()`、`current_version()`
- Produces: 面板启动时的更新流程；新路由 `/api/update/status`

- [ ] **Step 1: 写更新页**

创建 `updating.html`（复用 `watercolor-ui.css`，跟面板同一套视觉语汇）：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>小耳微信清扫器 · 正在更新</title>
<link rel="icon" type="image/png" href="/assets/app-icon.png">
<link rel="stylesheet" href="/watercolor-ui.css">
<style>
  .updating { max-width: 520px; margin: 18vh auto; text-align: center; }
  .updating h1 { font-size: 22px; font-weight: 600; margin-bottom: 10px; }
  .updating p { opacity: .72; font-size: 14px; line-height: 1.7; }
  .bar { height: 6px; border-radius: 3px; background: rgba(47,107,79,.14);
         margin: 24px auto 12px; overflow: hidden; }
  .bar i { display: block; height: 100%; width: 0;
           background: #2F6B4F; transition: width .3s ease; }
</style>
</head>
<body>
<main class="updating">
  <img class="watercolor-mascot" src="/assets/watercolor-mascot.png" alt="" aria-hidden="true">
  <h1 id="title">正在更新到 v<span id="ver">…</span></h1>
  <div class="bar"><i id="bar"></i></div>
  <p id="hint">正在下载，稍等一下就好</p>
</main>
<script>
const HINTS = {
  downloading: "正在下载，稍等一下就好",
  verifying: "正在验证签名与公证",
  done: "更新好了，正在重开",
  failed: "这次没更新成，先用现在这版",
};
async function poll() {
  try {
    const s = await (await fetch("/api/update/status")).json();
    document.getElementById("ver").textContent = s.version || "";
    document.getElementById("bar").style.width = (s.percent || 0) + "%";
    document.getElementById("hint").textContent = HINTS[s.state] || "";
    if (s.state === "done" || s.state === "failed") {
      document.getElementById("title").textContent =
        s.state === "done" ? "更新好了" : "继续用现在这版";
      setTimeout(() => location.href = "/api/update/restart", 900);
      return;
    }
  } catch (e) { /* 面板可能正在重开，忽略 */ }
  setTimeout(poll, 400);
}
poll();
</script>
</body>
</html>
```

- [ ] **Step 2: 改 panel.py**

在 `panel.py` 顶部 import 区（第 30 行 `import wechat_cleaner as wc` 旁边）加：

```python
import updater                            # noqa: E402
```

在 `IS_WIN = wc.IS_WIN`（第 75 行）下面加更新状态：

```python
# 更新状态。main() 里检测到新版才会填，do_GET 靠 UPDATE["info"] 判断走哪条路。
UPDATE = {"info": None, "state": "idle", "percent": 0}
```

在 `do_GET` 的 `/` 分支最前面（第 247 行 `if route in (...)` 之内、读 panel.html 之前）插入分流：

```python
            if UPDATE["info"]:
                self._send((HERE / "updating.html").read_bytes(), "text/html")
                return
```

在 `/api/status` 那一组路由旁边（第 357 行附近）新增两条：

```python
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
```

把 `main()` 改成：

```python
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


def do_update(info):
    """后台装。装成了让前端跳 /api/update/restart 触发重开，
    装不成就把状态标 failed，前端会放行回正常面板。"""
    def on_state(state, percent):
        UPDATE["state"] = state
        UPDATE["percent"] = percent
    ok = updater.install(info, on_state)
    UPDATE["state"] = "done" if ok else "failed"
    UPDATE["percent"] = 100 if ok else 0
```

- [ ] **Step 3: 手工验证「没有新版时一切照旧」**

VERSION 当前是 `2.3.0`，跟线上一致，所以 `check()` 应该返回 None，面板行为一字不变：

Run: `cd ~/projects/wechat-cleaner && python3 panel.py`
Expected: 面板正常打开，标题栏那行打印出 `v2.3.0`，扫描、选文件夹、清理全部照旧。确认后 Ctrl-C。

- [ ] **Step 4: 手工验证「有新版时走更新页」**

把 VERSION 临时改低来伪造「有新版」，但源码目录下 `app_bundle_path()` 返回 None，`install()` 会立刻返回 False —— 正好验证失败路径能不能安全放行回正常面板：

```bash
cd ~/projects/wechat-cleaner
echo "2.0.0" > VERSION
python3 panel.py
```

Expected: 浏览器先显示「正在更新到 v2.3.0」，约 1 秒后变「继续用现在这版」，再自动跳回正常面板，功能可用。

跑完立刻改回来：

```bash
echo "2.3.0" > VERSION
git diff --stat VERSION    # 应该没有输出
```

- [ ] **Step 5: 提交**

```bash
cd ~/projects/wechat-cleaner
git add panel.py updating.html
git commit -m "面板接入自动更新

有新版时 / 路由换成更新页，装完自动重开；装不成放行回正常面板。"
```

---

### Task 7: 打包与真机验证

**Files:**
- Modify: `scripts/build-macos-app.sh:36-40`
- Modify: `VERSION`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: 前六个任务的全部产出
- Produces: 带自动更新能力的 v2.4.0 包

- [ ] **Step 1: 改打包脚本**

`scripts/build-macos-app.sh` 里这段：

```bash
cp "$ROOT/panel.html" "$ROOT/panel.py" "$ROOT/watercolor-ui.css" \
   "$ROOT/wechat_cleaner.py" "$ROOT/dedup.py" \
   "$APP/Contents/Resources/app/"
```

改成（补 `updater.py`、`updating.html`、`VERSION` 三个）：

```bash
cp "$ROOT/panel.html" "$ROOT/panel.py" "$ROOT/watercolor-ui.css" \
   "$ROOT/wechat_cleaner.py" "$ROOT/dedup.py" \
   "$ROOT/updater.py" "$ROOT/updating.html" "$ROOT/VERSION" \
   "$APP/Contents/Resources/app/"
```

VERSION 必须进 bundle：`current_version()` 读的就是它，漏了会退化成 `0.0.0`，导致每次打开都判定有新版、反复更新。

- [ ] **Step 2: 打包并确认文件进去了**

```bash
cd ~/projects/wechat-cleaner
echo "2.4.0" > VERSION
bash scripts/build-macos-app.sh
ls "dist/小耳微信清扫器.app/Contents/Resources/app/"
```

Expected: 列表里有 `updater.py`、`updating.html`、`VERSION`

```bash
cat "dist/小耳微信清扫器.app/Contents/Resources/app/VERSION"
```

Expected: `2.4.0`

- [ ] **Step 3: 验证打出来的包自己能过验签链**

```bash
cd ~/projects/wechat-cleaner
python3 -c "
import updater
ok, why = updater.verify_app('dist/小耳微信清扫器.app')
print('ok' if ok else 'reject: ' + why)
"
```

Expected: 未公证前会是 `reject: 公证校验没过` —— 这正说明第 6 关是活的。公证后再跑一次：

```bash
bash scripts/build-macos-app.sh --notarize
python3 -c "
import updater
ok, why = updater.verify_app('dist/小耳微信清扫器.app')
print('ok' if ok else 'reject: ' + why)
"
```

Expected: `ok`

- [ ] **Step 4: 端到端真机验证**

这是全套里最关键的一步 —— 拿 `/Applications` 里那份旧版当小白鼠，真升一次。

先发布 v2.4.0：

```bash
cd ~/projects/wechat-cleaner
git add -A && git commit -m "发布 v2.4.0"
git tag v2.4.0 && git push && git push --tags
gh release create v2.4.0 \
  "dist/Xiaoer-WeChat-Cleaner-v2.4.0.zip" \
  --title "v2.4.0 · 自动更新" --notes-file /tmp/release-notes.md
```

然后把本机装的那份降到旧版，双击它：

```bash
/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" \
  "/Applications/小耳微信清扫器.app/Contents/Info.plist"
open "/Applications/小耳微信清扫器.app"
```

Expected 用户视角：双击 → 浏览器显示「正在更新到 v2.4.0」→ 进度条走完 → 自动重开 → 面板出来，已经是新版。

验证结果：

```bash
cat "/Applications/小耳微信清扫器.app/Contents/Resources/app/VERSION"
ls -d "/Applications/小耳微信清扫器.app.old" 2>&1   # 应该已被清掉
spctl -a -vvv -t execute "/Applications/小耳微信清扫器.app"
```

Expected: VERSION 是 `2.4.0`；`.old` 不存在；spctl `accepted`

再双击一次，确认已是最新时不会重复更新：

Expected: 直接进面板，不出更新页

- [ ] **Step 5: 补 CHANGELOG 并提交**

在 `CHANGELOG.md` 顶部加一节，跟现有条目一样用叙述口吻讲清楚做了什么、为什么：

```markdown
## 2.4.0 · 2026-08-16

**打开就自动更新了。**

以前发了新版，你手上那份不会知道——只能自己回 Release 页重新下一次。
现在双击打开时它会先问一句 GitHub，有新版就直接装好再进面板，
不用点任何按钮。查不到、超时、装不成，一律照常用现在这版，
绝不会因为更新失败而打不开。

macOS 上是整包替换：app 是签名公证过的，改里面任何一个文件
都会让签名失效、被系统判定成损坏，所以只能换整个 app。
换之前要过三关——包的 sha256、签名与 TeamID、公证票据。
第二关最要紧：签名有效只证明「某个 Apple 开发者签了它」，
不证明是我们，所以要认准 TeamID。三关有一关不过就一个字节都不写进去。

Windows 上没有签名，走目录覆盖，同样先验 sha256。
装在没有写权限的地方（比如 Program Files）就静默跳过，不要管理员权限。
```

```bash
cd ~/projects/wechat-cleaner
git add CHANGELOG.md VERSION scripts/build-macos-app.sh
git commit -m "发布 v2.4.0：自动更新"
git push
```

---

## 自查

**Spec 覆盖：**

| Spec 要求 | 落在哪 |
|-----------|--------|
| C1 打开时就装好 | Task 6 `main()` |
| 整包替换不热更新脚本 | Task 4 `_install_mac()` |
| 三重验签（sha256 / 签名+TeamID / 公证） | Task 3 纯函数 + Task 4 `verify_app()` |
| TeamID == 3DP32PZ62M | Task 3 `parse_team_id()` + Task 4 `verify_app()` |
| 失败一律退回旧版 | Task 2 `check()` 吞异常、Task 4/5 `install()` 返回 False、Task 6 `failed` 放行 |
| 3 秒超时 | Task 6 `check(timeout=3)` |
| `.old` 回滚 | Task 4 `rollback_if_needed()` |
| tag 的 v 前缀 | Task 1 `parse_version()` + Task 2 `check()` |
| 元组比版本号 | Task 1 |
| 权限不足静默跳过 | Task 4 `os.access`、Task 5 `_install_win()` |
| Windows 目录覆盖 | Task 5 |
| 打包带上 VERSION/updater | Task 7 Step 1 |
| 开发时不自我更新 | Task 3 `app_bundle_path()` 返回 None |
| 测试素材用 dist/ 里的真包 | Task 4 `make_fixtures.sh` |

无缺口。

**类型一致性：** `check()` 产出的 dict 键 `version / notes / url / sha256 / size`，在 Task 4/5 的 `install()` 与 Task 6 的 `/api/update/status` 中用的是同一组名字。`on_state(state, percent)` 两参签名在 `_download()`、`_install_mac()`、`_install_win()`、`do_update()` 四处一致。`install()` 在 Task 4 定义、Task 5 拆成 `_install_mac` / `_install_win` 并保持对外签名不变。

**占位符扫描：** 无 TBD / TODO / 「类似 Task N」。每个代码步骤都有可直接粘贴的完整实现。
