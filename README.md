<img src="icons/favicon-64.png" width="72" alt="icon">

# Workbuddy积分看板（非官方）

> WorkBuddy 账号用量菜单栏 / 托盘面板 + 完整看板。点击查看积分余额、今日/本月消耗（积分 ⇄ Token 切换），Pin 后常驻；
> 今日会话 Token 消耗榜；内置浏览器可打开的完整看板（账单详单 / 热力日历 / 会话消耗 / 来源拆分）。

非官方个人小工具，与 WorkBuddy 官方无关。
积分数据通过「网页登录会话」调用官方计费接口获取**你自己账号**的用量；
本工具使用一个**独立的浏览器配置档**（Windows：Edge/Chrome；macOS：Edge/Chrome，仅用于登录你自己的账号与自动续期），
不读取、不影响日常浏览器配置；账号凭据仅用于官方请求，账单和会话缓存保存在本机。

## 来源、许可证与本 Fork

本仓库 Fork 自 **[CerberusPhil/wb-usage-widget](https://github.com/CerberusPhil/wb-usage-widget)**。
感谢上游作者与贡献者；账单统计、会话分析、完整看板和图标均以原项目为基础。

- 本 Fork：[fayh77/wb-usage-widget](https://github.com/fayh77/wb-usage-widget)
- 修改起点：上游提交 [`487c572`](https://github.com/CerberusPhil/wb-usage-widget/commit/487c5728cb02d93f55ec93f2346624c0d7a86e0e)
- [查看相对修改起点的代码差异](https://github.com/CerberusPhil/wb-usage-widget/compare/487c5728cb02d93f55ec93f2346624c0d7a86e0e...fayh77:main)
- 许可证：沿用上游 **[MIT License](LICENSE)**，保留原有版权及许可声明。

本 Fork 用于分享 macOS 适配、跨平台交互和授权安全修改，供上游参考；
不代表这些修改已被上游接受，也不是 WorkBuddy 官方发布。

### 相对上游的主要修改

| 范围 | 修改内容与主要代码 |
| --- | --- |
| macOS 支持 | 增加 Cocoa / WebKit 状态栏窗口、浏览器发现、单实例入口、Apple Silicon 打包与证书配置；见 `scripts/macos_widget.py`、`scripts/browser_runtime.py`、`packaging/*macos*`。 |
| 跨平台弹窗 | 将固定角落挂件改为图标锚定弹窗，共用生命周期、Pin、悬停暂停与防过期计时器；Windows 原生操作调度到 UI 线程；见 `scripts/popup_controller.py`、`scripts/popup_ui.py`、`scripts/tray_icon.py`。 |
| 授权恢复 | 统一登录、重试和取消入口；隔离旧授权任务，等待浏览器端点及官方页面真正就绪；见 `scripts/billing_server.py`、`scripts/cookie_auto_renew.py`。 |
| 凭据与本地服务 | 增加系统保护存储、受校验的旧数据迁移、本地会话鉴权、一次性票据、来源校验、输出转义与请求重定向限制；见 `scripts/credential_store.py`、`scripts/local_http_security.py`。 |
| 专用浏览器 | 使用私有配置档与动态回环调试端口，校验端点身份；授权成功后关闭，只对本工具创建的子进程提供关闭兜底，不按名称终止用户浏览器。 |
| 回归验证 | 新增 `tests/`，覆盖授权恢复、存储拒绝与取消、HTTP 鉴权、注入防护、浏览器隔离、弹窗状态和多屏布局。 |

### 已验证与未验证

本次本地验证环境为 macOS / arm64、Python 3.12：

- 52 项回归用例：51 项通过，1 项 Windows 原生 DPAPI 用例跳过。
- macOS 原生冒烟及实际按钮操作通过：图标锚定、Pin、悬停、拖动缩放、自动收起和退出。
- 实际官方网页登录、保护存储和账单同步成功；授权完成后确认专用调试端口已关闭。
- Grok 参与独立静态复核；以上运行验证另行执行。静态复核不等同于完整安全审计。
- **Windows 原生 UI / DPAPI、Intel Mac 尚未真机验收。** Windows 改动不应据此视为已完成发行验证。
- macOS 本地构建仅 ad-hoc 签名，尚无 Developer ID 签名或 Apple 公证；本仓库发布源码，不包含个人运行数据或本机安装包。

## 功能

**桌面挂件（Windows `.exe` / macOS `.app`）**
- 积分余额（点击跳转成长计划页）
- 今日 / 本月消耗 —— 点击卡片切换 **积分 ⇄ Token** 双口径
- **今日会话消耗 TOP 榜** —— 按 Token 排行，柱状图直观对比
- 字号与主题调节；Pin 固定后可拖动、缩放
- 点击菜单栏 / 托盘图标在其附近弹出，闲置 5 秒自动收起，操作时暂停
- 右键菜单提供显示/隐藏、登录/重新授权、同步、完整版与退出
- 「✕」退出应用及内嵌服务；「−」或 Esc 仅收起
- 有效会话无需反复打开登录浏览器；凭据失效或官方撤销后可重新授权

**完整看板**（点击面板或托盘菜单中的「完整版」，通过一次性本机授权打开）
- 顶部统计卡片 —— 积分口径看余额与消耗；点击卡片切成 **Token 口径**，并把 Token 拆成
  **积分 Token / 免费额度 / 外部 API（BYOK）** 三色构成条
- 模型消耗（积分 / 次数 / Token 三口径）—— 可切 **近7天（默认）/ 近30天 / 总账**，含环比上一窗口
- 30 天热力日历 —— 积分 / 请求数 / Token 三档；Token 档底部琥珀色细条为该天**非积分 Token 占比**
- 会话消耗 —— 默认列**近7天活跃会话**（可切近30天 / 总账），按 Token 降序，
  点会话**展开窗口内模型明细**（Token / 积分 / 计费归属）
- 消耗详单 —— 固定按天：时间 / 模型 / 积分 / 来源 / 摘要
- 来源拆分 —— 本机 / 非本机 / 网页版 / APP 四类（页尾）
- 全局字号缩放（五档，自动记忆）· 6 套主题

## macOS 版本

本分支在上游基础上增加 macOS 支持，保留原有账单、Token 聚合和 Windows 入口。
当前构建产物面向 **Apple Silicon（M 系列）**，使用系统 Cocoa / WebKit，不需要 WebView2。

### 直接使用

1. 先按下方说明从源码构建，再打开本机生成的 `dist/WBCreditWidget.app`，也可复制到「应用程序」。应用已包含 Python 和依赖；GitHub 源码仓库不包含这个构建产物。
2. 首次使用点击 **「登录 / 重新授权」**，在独立 Edge 或 Chrome 窗口完成官方网页登录。
   电脑需要安装其中一个浏览器；不读取日常浏览器配置档，也不要求导出客户端加密令牌。
3. 完成登录后自动同步。挂件、完整看板和菜单栏 `WB` 都有重新授权入口；
   等待期间可取消，失败后可再次授权，不需要重启程序。
4. 点击菜单栏 `WB`，面板在图标旁弹出；闲置 **5 秒**自动收起，鼠标停留或输入时暂停。
   **Pin** 固定后可拖动、缩放；取消固定会回到图标旁。Pin 仅在本次运行有效。
   `Esc` /「−」收起；Mac 未固定时失焦也收起。右键 `WB` 可登录、同步、打开完整版或退出。
   「×」关闭应用及其内嵌服务。收起不取消后台同步或授权等待，重新打开可查看结果。

> 这是本地 ad-hoc 签名构建，尚未使用 Apple Developer ID 签名或公证。
> 对外分发前需要签名、公证；不要通过关闭 Gatekeeper 或 SIP 来运行。
> WorkBuddy 客户端已登录，不等于本工具能够读取其凭据。客户端凭据不可读或加密时，
> 请使用上述网页登录入口。没有凭据时不会自动启动后台浏览器反复续期。

### 源码启动与打包

需要 Python 3.10+（本机验证使用 Python 3.12）和 Git。获取本 Fork 后，可直接源码启动：

```sh
git clone https://github.com/fayh77/wb-usage-widget.git
cd wb-usage-widget
bash start_macos.command
```

`start_macos.command` 首次会在项目 `.venv` 安装依赖，之后复用环境；也可在 Finder 双击运行。
生成独立 `.app`：

```sh
# 在仓库根目录、原生 Apple Silicon Python 环境中执行
bash packaging/build_macos.sh
# 输出 dist/WBCreditWidget.app（构建产物不提交到 Git）
```

macOS 数据位置：

| 内容 | 路径 |
|------|------|
| 账单缓存 | `~/.workbuddy/server_usage_cache.json` |
| 网页登录凭据 / 显式导入的令牌 | macOS 钥匙串，服务名 `cn.workbuddy.usage-widget.credentials` |
| 独立登录浏览器配置档 | `~/Library/Application Support/WBCreditWidget/browser_cookie_profile` |
| 应用日志 | `~/Library/Logs/WBCreditWidget/widget.log` |

本地服务只监听 `127.0.0.1:8790`，现在需要会话鉴权；不要手输 `/full` 或收藏一次性启动链接。
请从面板/菜单打开完整版。Mac、Windows 的专用登录浏览器均使用动态回环调试端口，
并校验专用配置档中的端点身份；成功保存授权后关闭该专用浏览器，不关闭日常浏览器。
协议关闭失败时，只终止本次挂件创建且仍存活的子进程；不会按浏览器名称搜索或杀进程。
取消等待不会关闭专用登录窗口；不再登录时请手动关闭它。

### 开发验证

```sh
.venv/bin/python -m unittest discover -s tests -v
dist/WBCreditWidget.app/Contents/MacOS/WBCreditWidget --tray-smoke
```

回归覆盖授权取消/重试、旧任务隔离、端点身份、同源鉴权、票据过期/重放、脚本注入、
凭据迁移/拒绝/取消回滚、浏览器关闭隔离、静止光标悬停，以及四边任务栏和负坐标显示器布局。
原生冒烟检查锚点弹出、操作暂停、Pin、拖动缩放、取消固定与自动收起；
「×」退出另需实际点击并检查进程和端口释放。Windows DPAPI 原生用例仅在 Windows 执行；
在 macOS 通过跨平台测试不代表 Windows 原生 UI 已验收。

## Windows 使用（本地构建）

1. 在 Windows 上用 `packaging/build.bat` 构建本分支，运行生成的 `WBCreditWidget.exe`。
   上游 Releases 的现有产物不一定包含本分支的弹窗与安全改造。
2. 首次点击 **「登录 / 重新授权」**，在专用浏览器完成官方登录。
3. 点击托盘图标查看临时面板；Pin 后保持显示。凭据失效时可重新授权。

> - **不需要运行 WorkBuddy 客户端**——积分/账单走网页登录会话，客户端开不开都行；
> - 「今日会话消耗 TOP 榜」需要本机装有 WorkBuddy 客户端并使用过（读本机会话记录）；
> - 换电脑：在新电脑重复一次步骤 2 即可（登录态按机器独立，不可拷贝）；
> - 更多细节（操作、隐私说明、FAQ）见 [`packaging/使用说明.txt`](packaging/使用说明.txt)。

## 从源码运行（可选）

| 依赖 | 说明 |
|------|------|
| Windows 10/11 | 窗口依赖 WebView2 运行时（Win11 与新版 Win10 自带） |
| Python 3.10+ | 仅源码模式需要 |
| pywebview | `pip install -r requirements.txt`（仅源码模式需要） |

```bat
:: 方式一：静默启动（推荐日常使用，无控制台窗口）
scripts\start_billing_widget_silent.vbs

:: 方式二：带控制台启动（排障用）
scripts\start_billing_widget.bat

:: 单独刷新静态看板 HTML（不常驻窗口）
scripts\refresh_billing.bat

:: 停止挂件与后台服务
scripts\stop_billing_widget.bat
```

## 数据与缓存

| 数据 | 来源 | 位置 |
|------|------|------|
| 积分账单（余额 / 逐笔） | 官方计费接口（31 天滚动窗口，本机自动累积） | `%USERPROFILE%\.workbuddy\server_usage_cache.json` |
| 登录会话（cookie） | 本工具专用 Edge / Chrome 登录窗口 | macOS 钥匙串；Windows `%LOCALAPPDATA%\WBCreditWidget\credentials\*.dpapi`（当前用户 DPAPI） |
| Token / 会话明细 | 本机 WorkBuddy 会话记录（只读） | 不复制、不外传 |

## 目录结构

```
.
├── scripts/                  # 源码（本地服务 + 挂件壳 + 数据聚合）
│   ├── wb_widget_app.py      # 跨平台一键入口：单实例、内嵌服务、窗口与续期线程
│   ├── billing_server.py     # 本地服务（默认 http://127.0.0.1:8790；含 /api/login 一键登录）
│   ├── billing_widget.py     # 跨平台窗口桥接；缺少 pywebview 时降级到系统浏览器
│   ├── macos_widget.py      # Cocoa 状态栏与窗口适配
│   ├── tray_icon.py         # Windows 托盘与工作区适配
│   ├── popup_controller.py / popup_ui.py  # 弹窗状态与 Pin 控件
│   ├── browser_runtime.py / desktop_browser.py  # 浏览器发现与系统 URL 交接
│   ├── credential_store.py  # Keychain / DPAPI 及受保护的数据迁移
│   ├── local_http_security.py  # 本地鉴权、启动票据与输出编码
│   ├── server_usage_sync.py  # 账单同步（网页会话 cookie；UA 与签发会话绑定）
│   ├── cookie_auto_renew.py  # 登录会话自动续期（专用 Edge / Chrome 配置档 + CDP）
│   ├── save_cookie.py        # 手动 cookie 兜底（剪贴板 → 校验 → 保存）
│   ├── billing_dashboard_data.py
│   ├── token_sessions.py     # 本机会话 Token 聚合与对账
│   ├── billing_widget_template.html / billing_template.html
│   └── *.bat / *.vbs         # 启动 / 停止 / 刷新 / 开机自启
├── icons/                    # 沿用上游应用图标（favicon / .ico 多尺寸）
├── packaging/
│   ├── WBCreditWidget.spec / WBCreditWidget-macOS.spec
│   ├── build.bat / build_macos.sh  # 对应平台打包入口
│   ├── macos_env.sh / macos_certificates.py / generate_macos_icon.sh
│   ├── version_info.txt      # Windows 版本资源
│   └── 使用说明.txt
├── tests/                    # 隔离的回归测试，不需要真实账号凭据
├── start_macos.command       # macOS 源码启动入口
└── dist/                     # 本机构建产物，未提交到本仓库
```

## 从源码打包 exe

```bat
cd packaging
build.bat
:: 产物：packaging\dist\WBCreditWidget.exe
```

## 登录与凭据安全

- 默认通过独立浏览器完成官方网页登录；不扫描 WorkBuddy 客户端或其他工具的凭据。
- macOS 使用系统钥匙串，Windows 使用当前用户 DPAPI；保护存储不可用时不降级写明文。
  本工具自己的旧 `server_usage.json` / `server_usage_rt.json` / `server_usage_at.json`
  仅在保护存储写入、读回校验成功后迁移删除；与已保存账号不一致的旧记录保留并限制权限。
- 后台查询不弹钥匙串授权窗，也不等待正在进行的交互式存储。主动重新授权时可能出现系统提示；
  应用重签名/重新构建后可能需要重新允许访问。拒绝后可以重试，不关闭系统安全保护。
- 本地 HTTP 使用每进程随机能力值、短期一次性启动票据、HttpOnly / SameSite=Strict 会话，
  严格校验 Host / Origin；变更操作只接受 POST。绑定回环地址本身不等于安全。
- 面板数据做 HTML / 脚本上下文转义，使用 CSP 和禁止嵌入策略；`/widget` 为兼容 pywebview
  桥接允许 `unsafe-eval`，但使用精确脚本哈希而非任意内联脚本。凭据不会返回给页面。
- 官方授权请求不跟随重定向；日志不打印 Cookie、令牌片段、请求头或一次性启动 URL。
  外部浏览器使用系统 URL 交接，而不是把票据拼进命令行子进程参数。
- 专用浏览器配置档、账单缓存和本机能力文件限制为当前用户访问。CDP 开启期间仍是敏感接口；
  成功授权即关闭，取消后若保留登录窗口，请自行关闭。
- 这些措施不防同一用户权限下的恶意程序、管理员/root、已解锁浏览器或内存读取。
  迁移删除不擦除历史备份；手工导入文件和剪贴板由用户自行清理。不要提交或分享账号缓存、
  浏览器配置档、日志、运行时能力文件或带个人账单的静态 HTML。

高级用户可显式导入自己的 refresh token；不承诺凭据永久有效，官方撤销或风控后需重新登录：

```bat
python scripts\server_usage_sync.py --rt-info
python scripts\server_usage_sync.py --setup-rt
:: 在隐藏输入提示中输入，禁止把令牌放在命令参数中
python scripts\server_usage_sync.py --setup-rt-file <本机私有文件>
python scripts\server_usage_sync.py --forget-rt
python scripts\save_cookie.py --check
```

Token / 会话统计仍只读本机会话记录，不读取客户端的登录授权。

## 免责声明

- 非官方工具；官方接口与字段可能调整，导致部分功能失效；
- 请在遵守 WorkBuddy 服务条款的前提下使用；
- 凭据仅用于官方授权/账单请求；不向本项目作者的服务器发送账号凭据或会话内容。
