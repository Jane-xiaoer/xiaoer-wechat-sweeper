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


if __name__ == "__main__":
    unittest.main()
