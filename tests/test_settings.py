"""settings 单测。跑法：python3 -m unittest discover -s tests -v"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings  # noqa: E402


class Base(unittest.TestCase):
    """每个用例都在自己的临时配置目录里跑，不碰用户真实配置"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        original = settings._config_dir
        settings._config_dir = lambda: self.tmp / "cfg"
        self.addCleanup(lambda: setattr(settings, "_config_dir", original))


class TestRoundTrip(Base):
    def test_存了能取回来(self):
        target = self.tmp / "微信文件整理"
        target.mkdir()
        settings.set_last_dest(target)
        self.assertEqual(settings.get_last_dest(), str(target))

    def test_没存过返回_None(self):
        self.assertIsNone(settings.get_last_dest())

    def test_后写的盖掉先写的(self):
        a, b = self.tmp / "a", self.tmp / "b"
        a.mkdir(); b.mkdir()
        settings.set_last_dest(a)
        settings.set_last_dest(b)
        self.assertEqual(settings.get_last_dest(), str(b))


class TestFallback(Base):
    def test_路径已经不在了就当没有(self):
        """外接盘拔了、文件夹被删了、被改名了"""
        target = self.tmp / "会被删掉"
        target.mkdir()
        settings.set_last_dest(target)
        shutil.rmtree(str(target))
        self.assertIsNone(settings.get_last_dest())

    def test_存的是文件不是文件夹也当没有(self):
        f = self.tmp / "这是个文件"
        f.write_text("x")
        settings.set_last_dest(f)
        self.assertIsNone(settings.get_last_dest())

    def test_空路径不写进去(self):
        settings.set_last_dest("")
        self.assertIsNone(settings.get_last_dest())


class TestBroken(Base):
    """配置文件坏了不能让工具打不开——它只是个记忆，不是必需品"""

    def _write_raw(self, text):
        d = settings._config_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "settings.json").write_text(text, encoding="utf-8")

    def test_json_损坏返回空配置(self):
        self._write_raw("{这不是 json")
        self.assertEqual(settings.load(), {})
        self.assertIsNone(settings.get_last_dest())

    def test_json_是数组不是对象也不崩(self):
        self._write_raw("[1, 2, 3]")
        self.assertEqual(settings.load(), {})

    def test_坏文件还能被覆盖写回去(self):
        self._write_raw("{坏的")
        target = self.tmp / "新目标"
        target.mkdir()
        self.assertTrue(settings.set_last_dest(target))
        self.assertEqual(settings.get_last_dest(), str(target))

    def test_目录不可写时安静失败(self):
        ro = self.tmp / "只读"
        ro.mkdir()
        os.chmod(str(ro), 0o500)
        self.addCleanup(lambda: os.chmod(str(ro), 0o700))
        settings._config_dir = lambda: ro / "cfg"
        self.assertFalse(settings.set_last_dest(self.tmp))
        self.assertIsNone(settings.get_last_dest())


class TestPrefs(Base):
    """除了文件夹，面板上还有两样用户会调的：留几个月的滑杆、先查重的勾选"""

    def test_没存过时给出厂值(self):
        p = settings.get_prefs()
        self.assertEqual(p["keep"], 1)
        self.assertTrue(p["dedup"])

    def test_存了能取回来(self):
        settings.set_prefs(keep=6, dedup=False)
        p = settings.get_prefs()
        self.assertEqual(p["keep"], 6)
        self.assertFalse(p["dedup"])

    def test_一个不留也要记住(self):
        """keep=0 是合法值（微信里一个都不留），别被当成空值丢掉"""
        settings.set_prefs(keep=0, dedup=True)
        self.assertEqual(settings.get_prefs()["keep"], 0)

    def test_超范围的值夹回去(self):
        """滑杆是 0-12，配置文件被手改坏了也不能让面板显示成乱的"""
        settings.set_prefs(keep=999, dedup=True)
        self.assertEqual(settings.get_prefs()["keep"], 12)
        settings.set_prefs(keep=-5, dedup=True)
        self.assertEqual(settings.get_prefs()["keep"], 0)

    def test_脏数据回退到出厂值(self):
        d = settings._config_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "settings.json").write_text('{"keep": "六个月", "dedup": "是"}',
                                         encoding="utf-8")
        p = settings.get_prefs()
        self.assertEqual(p["keep"], 1)
        self.assertTrue(p["dedup"])

    def test_跟文件夹互不干扰(self):
        target = self.tmp / "目标"
        target.mkdir()
        settings.set_last_dest(target)
        settings.set_prefs(keep=9, dedup=False)
        self.assertEqual(settings.get_last_dest(), str(target))
        self.assertEqual(settings.get_prefs()["keep"], 9)


if __name__ == "__main__":
    unittest.main()
