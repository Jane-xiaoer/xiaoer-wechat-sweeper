"""updater 纯函数单测。跑法：python3 -m unittest discover -s tests -v"""
import os
import sys
import tempfile
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


def _rm_readonly(p):
    import shutil as _sh
    os.chmod(str(p), 0o700)
    _sh.rmtree(str(p), ignore_errors=True)


def shutil_which(name):
    import shutil as _sh
    return _sh.which(name)


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


class TestExtractUtf8(unittest.TestCase):
    """入口文件叫「小耳微信清扫器.bat」，名字坏掉 = 用户双击不着 = 工具死了"""

    def test_正常包解出正确中文名(self):
        """Python 打的包会自动带 0x800 标志，这是我们自己发版走的路"""
        import zipfile as zf
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rm_readonly(d))
        with zf.ZipFile(str(d / "a.zip"), "w") as z:
            z.writestr("小耳微信清扫器/小耳微信清扫器.bat", "@echo off")
        updater._extract_utf8(d / "a.zip", d / "out")
        self.assertTrue((d / "out/小耳微信清扫器/小耳微信清扫器.bat").exists())

    def test_没带标志位的包也能解出正确中文名(self):
        """zip 命令打的包没有 0x800 标志——v2.3.0 就是这么打的。
        不兜住的话，自动更新完入口 bat 会变成乱码名，用户双击不着。

        用真的 zip 命令造素材：Python 的 zipfile 写非 ASCII 名时
        会自动补上标志位，造不出这种包。
        """
        import subprocess
        if not shutil_which("zip"):
            self.skipTest("没有 zip 命令")
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rm_readonly(d))
        (d / "小耳微信清扫器").mkdir()
        (d / "小耳微信清扫器/小耳微信清扫器.bat").write_text("@echo off")
        subprocess.run(["zip", "-qr", str(d / "b.zip"), "小耳微信清扫器"],
                       cwd=str(d), check=True)

        import zipfile as zf
        with zf.ZipFile(str(d / "b.zip")) as z:
            flagged = [i for i in z.infolist() if i.flag_bits & 0x800]
        self.assertEqual(flagged, [], "素材没造对：zip 命令这次带上标志位了")

        updater._extract_utf8(d / "b.zip", d / "out")
        self.assertTrue((d / "out/小耳微信清扫器/小耳微信清扫器.bat").exists(),
                        "入口 bat 名字坏了，用户就双击不着了")

    def test_不让_zip_跳出解压目录(self):
        import zipfile as zf
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rm_readonly(d))
        with zf.ZipFile(str(d / "evil.zip"), "w") as z:
            z.writestr("../跑出去了.txt", "x")
        updater._extract_utf8(d / "evil.zip", d / "out")
        self.assertFalse((d / "跑出去了.txt").exists())


if __name__ == "__main__":
    unittest.main()
