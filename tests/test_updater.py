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
