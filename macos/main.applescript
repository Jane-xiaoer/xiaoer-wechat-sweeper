-- 小耳微信清扫器 · 自包含启动器
set appPath to POSIX path of (path to me)
set toolDir to appPath & "Contents/Resources/app"
try
	do shell script "cd " & quoted form of toolDir & " && /usr/bin/python3 panel.py > /dev/null 2>&1 &"
on error errMsg
	display dialog "启动失败：" & errMsg buttons {"知道了"} default button 1 with icon caution
end try
