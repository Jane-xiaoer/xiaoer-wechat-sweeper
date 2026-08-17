-- 小耳微信清扫器 · 自包含启动器
--
-- 以前这里直接跑 /usr/bin/python3 并把输出丢进 /dev/null。可那个路径
-- 根本不是 Python，只是 xcode-select 的转发壳，真身在 Command Line Tools 里；
-- 没装过 Xcode 的电脑上它跑不起来，而错误又全被丢弃，用户只看到
-- Dock 图标闪一下就没了——那就是反馈里说的「双击闪退」。
--
-- 现在交给 launcher.sh 去找一个真能跑的 python3。它以 20 退出就代表
-- 这台电脑上确实没有 Python，那就明明白白告诉用户，并直接把下载页打开。
set appPath to POSIX path of (path to me)
set toolDir to appPath & "Contents/Resources/app"
set launcher to toolDir & "/launcher.sh"

try
	do shell script "/bin/bash " & quoted form of launcher
on error errMsg number errNum
	if errNum is 20 then
		set msg to "这台电脑上还没有 Python，小耳微信清扫器需要它才能跑。

去 python.org 下载安装（免费，几分钟），装完再双击我一次就行。"
		display dialog msg buttons {"我知道了", "去下载"} default button "去下载" with icon note
		if button returned of result is "去下载" then
			open location "https://www.python.org/downloads/macos/"
		end if
	else
		display dialog "启动失败：" & errMsg & "

日志在 ~/Library/Logs/小耳微信清扫器.log，可以发给小耳。" buttons {"知道了"} default button 1 with icon caution
	end if
end try
