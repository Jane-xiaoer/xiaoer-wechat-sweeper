"""启动路径的测试。

用户双击后 AppleScript 壳启动完就退出，Dock 图标随即消失。
这时候如果面板还没起来，用户就是盯着空气——看着跟闪退一模一样。
所以「查更新」这种要联网的事，绝不能挡在启动路径上：
GitHub 在国内本来就常年不通。
"""
import sys
import threading
import time
import unittest
import urllib.request
from http.server import HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import panel  # noqa: E402
import updater  # noqa: E402


class TestStartupNotBlockedByNetwork(unittest.TestCase):

    def setUp(self):
        panel.UPDATE.update(info=None, state="idle", percent=0)
        panel.UPDATE_CHECKED.clear()
        self.addCleanup(panel.UPDATE_CHECKED.set)

    def _serve(self):
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]; s.close()
        srv = HTTPServer(("127.0.0.1", port), panel.H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return port

    def test_查更新卡死时首页最多等三秒还是要出面板(self):
        """模拟 GitHub 完全不通、连超时都不返回的最坏情况"""
        original = updater.check
        updater.check = lambda timeout=3: time.sleep(60)
        self.addCleanup(lambda: setattr(updater, "check", original))

        threading.Thread(target=panel.check_update_async, daemon=True).start()
        port = self._serve()

        t = time.time()
        html = urllib.request.urlopen(
            "http://127.0.0.1:%d/" % port, timeout=10).read().decode()
        waited = time.time() - t

        self.assertLess(waited, 5, "首页等太久了，用户会以为闪退")
        self.assertIn("開始清", html, "等不到结论就该给正常面板，不能空着")

    def test_查到新版时首页给更新页(self):
        original = updater.check
        updater.check = lambda timeout=3: {
            "version": "9.9.9", "notes": "", "url": "x", "sha256": "", "size": 1}
        self.addCleanup(lambda: setattr(updater, "check", original))

        threading.Thread(target=panel.check_update_async, daemon=True).start()
        panel.UPDATE_CHECKED.wait(3)
        port = self._serve()

        html = urllib.request.urlopen(
            "http://127.0.0.1:%d/" % port, timeout=10).read().decode()
        self.assertIn("正在更新到", html)

    def test_没有新版时首页给正常面板(self):
        original = updater.check
        updater.check = lambda timeout=3: None
        self.addCleanup(lambda: setattr(updater, "check", original))

        threading.Thread(target=panel.check_update_async, daemon=True).start()
        panel.UPDATE_CHECKED.wait(3)
        port = self._serve()

        html = urllib.request.urlopen(
            "http://127.0.0.1:%d/" % port, timeout=10).read().decode()
        self.assertIn("開始清", html)

    def test_查更新抛异常也不能拖垮启动(self):
        original = updater.check
        def boom(timeout=3):
            raise RuntimeError("网络炸了")
        updater.check = boom
        self.addCleanup(lambda: setattr(updater, "check", original))

        threading.Thread(target=panel.check_update_async, daemon=True).start()
        port = self._serve()

        html = urllib.request.urlopen(
            "http://127.0.0.1:%d/" % port, timeout=10).read().decode()
        self.assertIn("開始清", html)


if __name__ == "__main__":
    unittest.main()
