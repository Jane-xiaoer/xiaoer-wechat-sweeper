# 自动更新设计

2026-08-16 · 目标版本 v2.4.0

## 病灶

`/Applications/小耳微信清扫器.app` 停在 v2.2.0（8/15 装的），线上已经 v2.3.0。
不是更新失败，是**从来没有更新这条路**：

- `panel.py` 449 行里一个 version 字都没有，app 完全没有版本感知
- `VERSION` 文件只在 `scripts/build-macos-app.sh` 里被读过一次，写进 Info.plist 就没了，没进过 `Resources/app/`
- README 的下载按钮指向 `releases/latest`，所以网页看着永远是新的；用户手上那份则永远停在下载那天

## 决策

**静默自动更新，打开时就装好（C1）。** 用户双击 → 面板显示「正在更新到 vX.Y.Z…」→ 装完自动重开 → 看到的就是新版。全程不点任何按钮。

### 硬约束：只能整包替换，不能热更新脚本

`build-macos-app.sh:52` 用 `codesign --force --deep` 签的，`Contents/Resources/app/` 里的
py/html/css 全在 `CodeResources` 覆盖范围内。改任何一个文件都会让签名校验失败，
Gatekeeper 判定 app 损坏，公证票据也对不上，用户看到「已损坏，请移到废纸篓」。

所以 macOS 上唯一正确的路径是：**下载整包 zip → 验证 → 整个 .app 替换**。

### 硬约束：验签链一步都不能省

静默更新下用户不点确认，全靠代码把关。这条链从「重要」升级为「绝对必要」。
其中**校验 TeamIdentifier == `3DP32PZ62M`** 是最容易漏、也最要命的一步：
签名有效只证明「某个 Apple 开发者签了它」，不证明是我们。少了这步，
中间人拿自己的 Developer ID 签一个公证过的恶意 app 进来，照样能过签名校验和 spctl。

### 铁律：更新失败绝不能变成「工具打不开」

这是一个会搬运和删除用户文件的工具。更新链路上任何一环出问题——无网、
超时、下载中断、校验不过、目录没权限——一律**静默放弃，照常进旧版面板**。
宁可不更新，不可打不开。

## 架构

新建 `updater.py`，职责单一。`panel.py` 只调三个函数，不往里塞更新逻辑
（它已经 449 行，再涨就该拆了）。

```python
current_version() -> str
    # 读 Resources/app/VERSION，读不到返回 "0.0.0"

check(timeout=3) -> dict | None
    # GET api.github.com/repos/Jane-xiaoer/xiaoer-wechat-sweeper/releases/latest
    # 比对版本号，无新版或任何异常返回 None
    # -> {"version", "notes", "url", "sha256", "size"}

install(info) -> bool
    # 下载 → 校验 → 替换，见下方安全链。任何失败返回 False，不留残迹
```

版本比对用 `tuple(int(x) for x in v.split("."))`，不引第三方库
（app 跑在系统自带 `/usr/bin/python3`，不能有依赖）。

注意 GitHub 的 `tag_name` 带 `v` 前缀（`v2.3.0`），本地 `VERSION` 不带（`2.3.0`），
比对前统一 `lstrip("v")`。

### 为什么能替换「正在运行的自己」

`macos/main.applescript` 是 `do shell script "... python3 panel.py ... &"` —— 起完
后台 python 就退出了，`.app` 自身进程根本不在。真正在跑的是独立的 python 进程，
py 文件已读进内存。所以整包替换 `.app` 不会打断当前运行。
唯一的活引用是 `panel.html`（每次请求都重新 `read_text`），装完立即重开，
新旧混搭的窗口只有几百毫秒。

## 启动流程

`panel.py main()` 开头，mac / Windows 通用：

```
双击
 ├─ updater.check(timeout=3)
 │    ├─ 无网 / 超时 / 无新版 / 任何异常 ──→ 直接进面板（旧版正常用）
 │    └─ 有新版 ↓
 ├─ 开面板，显示「正在更新到 v2.3.0…」水彩小页
 ├─ 后台线程 updater.install(info)
 │    ├─ 成功 → 自动重开 app（mac: open -a 新路径；Win: start bat），当前进程退出
 │    └─ 失败 → 静默关掉更新页，照常进旧版面板
```

3 秒超时是硬上限。GitHub API 在国内经常慢，超时就当没有新版，下次打开再说。

## 安全链（install 内部）

| # | 步骤 | 失败动作 |
|---|------|---------|
| 1 | 下载 zip 到 `~/Library/Caches/xiaoer-wechat-cleaner/update/` | 中止 |
| 2 | 校验 sha256 == `asset.digest`（GitHub API 直接给，白捡的） | 中止 |
| 3 | `ditto -x -k` 解压到临时目录 | 中止 |
| 4 | `codesign --verify --deep --strict` | 中止 |
| 5 | `codesign -dv` 读 TeamIdentifier，必须 == `3DP32PZ62M` | 中止 |
| 6 | `spctl -a -t execute` 公证有效 | 中止 |
| 7 | 旧 app 改名 `.old` → 新 app `ditto` 就位 → 删 `.old` | 回滚 `.old` |

「中止」= 清掉临时目录，返回 False，一个字节都不写进 `/Applications`。

第 7 步先改名再就位，是为了替换过程中断电/被杀时还能靠 `.old` 救回来。
启动时若发现 `.old` 存在而主体缺失，自动回滚。

### 权限

`/Applications` 通常当前用户可写（app 是用户自己拖进去的）。
不可写就静默跳过更新，不弹权限框、不提权——一个清扫工具不该要管理员密码。

## Windows 分支

没有签名，链短一半：

```
下载 zip → 校验 sha256 → 解压临时目录 → 覆盖程序目录 → 重开 bat
```

- 正在跑的 `.py` 文件可以被覆盖（Python 已读进内存），不像 exe 被锁
- 程序目录不可写（例如装在 `Program Files`）→ 静默跳过
- Windows 版资产名 `Xiaoer-WeChat-Cleaner-vX.Y.Z-Windows.zip`，靠 `IS_WIN` 选

## 打包与发布改动

`scripts/build-macos-app.sh`：

```bash
cp "$ROOT/VERSION" "$ROOT/updater.py" "$APP/Contents/Resources/app/"
```

Windows zip 打包同样带上 `VERSION` 和 `updater.py`。

发布流程不变，仍是 `gh release create` 传两个 zip。
**不自建 `latest.json`** —— GitHub Releases API 已经提供 `tag_name`、`body`、
`assets[].digest`，够用；而且 zip 本来就托管在 GitHub，自建索引解决不了下载那一步。

## 失败路径矩阵

| 情况 | 行为 |
|------|------|
| 无网 / GitHub 不通 | 3 秒超时 → 正常进旧版面板 |
| 已是最新 | 不做任何事 |
| 下载中断 | 清临时目录 → 旧版面板 |
| sha256 对不上 | 中止 → 旧版面板 |
| 签名 / TeamID / 公证任一不过 | 中止 → 旧版面板 |
| `/Applications` 不可写 | 跳过 → 旧版面板 |
| 替换到一半崩了 | 下次启动检测 `.old` → 自动回滚 |

一栏共同点：**任何异常的兜底都是「旧版正常打开」**。

## 测试

`dist/` 里现成躺着两个真·签名公证包，是完美素材：

- `Xiaoer-WeChat-Cleaner-v2.2.1.zip`
- `Xiaoer-WeChat-Cleaner-v2.3.0.zip`

1. **版本比对**：`2.2.0 < 2.3.0`、`2.10.0 > 2.9.0`（字符串比较会错，必须元组）、脏数据不崩
2. **check() 降级**：断网、超时、GitHub 返回 500 / 畸形 JSON → 一律 None，不抛异常
3. **安全链正例**：v2.3.0 真包走完 1–7 步全过
4. **安全链反例**（关键）：
   - 篡改包内任意一个字节 → 第 2 步拦下
   - 重新用别的证书签一个包 → 第 5 步拦下（这条验证的正是最容易漏的那步）
   - 去掉公证票据 → 第 6 步拦下
5. **回滚**：第 7 步中途 kill → 下次启动能靠 `.old` 救回
6. **端到端**：拿 `/Applications` 里那份 2.2.0 当小白鼠，真升一次到 2.3.0

安全链的测试在沙盒目录里跑，不碰 `/Applications`，只有第 6 项碰。

## 不做（YAGNI）

- 国内镜像 / 自建 latest.json —— GitHub 直连 + 超时降级够用
- 差分 / 增量更新 —— 全量才 8MB
- 更新频率设置项、"跳过此版本"、后台常驻轮询 —— 只在启动时查一次
- 版本回滚 UI —— `.old` 只用于崩溃自救，不做面向用户的降级功能
