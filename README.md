<img src="icons/favicon-64.png" width="72" alt="icon">

# Workbuddy积分看板（非官方）

> WorkBuddy 账号用量桌面挂件 + 完整看板。常驻桌面实时查看积分余额、今日/本月消耗（积分 ⇄ Token 切换）、
> 今日会话 Token 消耗榜；内置浏览器可打开的完整看板（账单详单 / 热力日历 / 会话消耗 / 来源拆分）。

非官方个人小工具，与 WorkBuddy 官方无关。
积分数据通过「网页登录会话」调用官方计费接口获取**你自己账号**的用量；
本工具使用一个**独立的 Edge 配置档**（仅用于登录你自己的账号与自动续期），
不读取、不影响你日常浏览器的任何数据；所有数据仅保存在本机，不上传不外传。

## 功能

**桌面挂件（`WBCreditWidget.exe`）**
- 积分余额（点击跳转成长计划页）
- 今日 / 本月消耗 —— 点击卡片切换 **积分 ⇄ Token** 双口径
- **今日会话消耗 TOP 榜** —— 按 Token 排行，柱状图直观对比
- 字号调节（A− / A+ 四档）· 三主题（WorkBuddy / EVA-01 / EVA-02）· 拖拽移动与缩放
- **登录会话自动续期** —— 每次打开挂件自动续 7 天，日常使用永不掉线

**完整看板**（点击「↗ 完整版」，或浏览器打开 `http://127.0.0.1:8790/full`）
- 顶部统计卡片 —— 积分口径看余额与消耗；点击卡片切成 **Token 口径**，并把 Token 拆成
  **积分 Token / 免费额度 / 外部 API（BYOK）** 三色构成条
- 模型消耗（积分 / 次数 / Token 三口径）—— 可切 **近7天（默认）/ 近30天 / 总账**，含环比上一窗口
- 30 天热力日历 —— 积分 / 请求数 / Token 三档；Token 档底部琥珀色细条为该天**非积分 Token 占比**
- 会话消耗 —— 默认列**近7天活跃会话**（可切近30天 / 总账），按 Token 降序，
  点会话**展开窗口内模型明细**（Token / 积分 / 计费归属）
- 消耗详单 —— 固定按天：时间 / 模型 / 积分 / 来源 / 摘要
- 来源拆分 —— 本机 / 非本机 / 网页版 / APP 四类（页尾）
- 全局字号缩放（五档，自动记忆）· 6 套主题

## 快速开始（免安装）

1. 从 [**Releases**](https://github.com/CerberusPhil/wb-usage-widget/releases/latest) 下载最新版 zip
   （`WBCreditWidget_vX.Y.zip`），解压后双击里面的 `WBCreditWidget.exe`
2. 首次使用：点窗口里的 **「🔑 登录 WorkBuddy 账号」**，在弹出的浏览器窗口完成登录
   （密码/扫码均可）——约 10~30 秒后自动出数据，**只需登录一次**
3. 之后每次打开挂件自动续期（登录会话 7 天滑动），无需再管

> - **不需要运行 WorkBuddy 客户端**——积分/账单走网页登录会话，客户端开不开都行；
> - 「今日会话消耗 TOP 榜」需要本机装有 WorkBuddy 客户端并使用过（读本机会话记录）；
> - 换电脑：在新电脑重复一次步骤 2 即可（登录态按机器独立，不可拷贝）；
> - 更多细节（操作、隐私说明、FAQ）见 [`packaging/使用说明.txt`](packaging/使用说明.txt)。

## 从源码运行（可选）

| 依赖 | 说明 |
|------|------|
| Windows 10/11 | 窗口依赖 WebView2 运行时（Win11 与新版 Win10 自带） |
| Python 3.8+ | 仅源码模式需要 |
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
| 登录会话（cookie） | 专用 Edge 配置档（首次一键登录，自动滑动续期） | `%USERPROFILE%\.workbuddy\server_usage.json` + `%LOCALAPPDATA%\WBCreditWidget\edge_cookie_profile` |
| Token / 会话明细 | 本机 WorkBuddy 会话记录（只读） | 不复制、不外传 |

## 目录结构

```
.
├── scripts/                  # 源码（本地服务 + 挂件壳 + 数据聚合）
│   ├── wb_widget_app.py      # 一键入口（exe 打包入口：互斥锁 + 内嵌服务 + 窗口 + 续期线程）
│   ├── billing_server.py     # 本地服务（默认 http://127.0.0.1:8790；含 /api/login 一键登录）
│   ├── billing_widget.py     # 挂件窗口壳（pywebview；缺则 Edge --app 降级）
│   ├── server_usage_sync.py  # 账单同步（网页会话 cookie；UA 与签发会话绑定）
│   ├── cookie_auto_renew.py  # 登录会话自动续期（专用 Edge 配置档 + CDP，纯标准库）
│   ├── save_cookie.py        # 手动 cookie 兜底（剪贴板 → 校验 → 保存）
│   ├── billing_dashboard_data.py
│   ├── token_sessions.py     # 本机会话 Token 聚合与对账
│   ├── billing_widget_template.html / billing_template.html
│   └── *.bat / *.vbs         # 启动 / 停止 / 刷新 / 开机自启
├── icons/                    # 应用图标（favicon / .ico 多尺寸，原创）
├── packaging/
│   ├── WBCreditWidget.spec   # PyInstaller 打包配置
│   ├── version_info.txt      # Windows 版本资源（产品名/描述 = Workbuddy积分看板）
│   ├── build.bat             # 一键重新打包
│   └── 使用说明.txt
└── dist/                     # 构建产物目录（exe 不入库，仅随 Releases 分发）
    └── WBCreditWidget.exe    # 免安装可执行文件（git 忽略）
```

## 从源码打包 exe

```bat
cd packaging
build.bat
:: 产物：packaging\dist\WBCreditWidget.exe
```

## 登录与会话机制（v1.8 起）

新版 WorkBuddy 客户端把本机登录态里的 `accessToken` 从**明文 JWT** 改成了
**加密信封**（`{"$wbEncrypted": 1, "envelope": "..."}`；密钥由客户端运行时持有，
本地无法解密），因此不可能再从客户端登录态直接取令牌。

本工具的应对：**积分数据改走「网页登录会话」通道**，且全自动——

```
首次使用   占位页点「🔑 登录」→ 弹出专用 Edge 窗口 → 正常登录一次
日常使用   每次打开挂件，后台自动访问一次成长计划页（会话滑动续期 7 天）
           → CDP 读取该配置档的登录 cookie（可读 HttpOnly）→ 校验 → 保存 → 同步
```

- **专用配置档**：`%LOCALAPPDATA%\WBCreditWidget\edge_cookie_profile`，与你的日常
  浏览器完全隔离；CDP 调试口只绑定该配置档，不触碰日常浏览器；
- **UA 一致性**：登录会话与签发时的 User-Agent 绑定（服务端风控），续期时 headless
  模式会强制回放登录时的 UA；
- **登录会话 7 天滑动**：只要 7 天内打开过一次挂件就永不过期；连续 7 天未打开才会
  过期，重新点一次「🔑 登录」即恢复；
- **登录态不可跨机器拷贝**（浏览器安全设计），每台电脑各自登录一次；
- **手动兜底**（一般用不到）：

```bat
:: 1) 浏览器登录 https://www.codebuddy.cn/profile/plans-usage
:: 2) F12 -> Network -> 点任一请求 -> 复制请求头里整行 Cookie 的值
:: 3) 运行（读剪贴板、自动校验并保存）
python scripts\_tools\save_cookie.py

:: 查看当前 cookie 是否有效、还剩多久
python scripts\_tools\save_cookie.py --check
```

- **Token / 会话明细不受影响** —— 读的是本机会话记录文件（`~/.workbuddy/projects/*.jsonl`），
  完全不经过登录态；
- 完整看板的「凭据自检」区会显示当前实际使用的凭据类型。

## 免责声明

- 非官方工具；官方接口与字段可能调整，导致部分功能失效；
- 请在遵守 WorkBuddy 服务条款的前提下使用；
- 本项目不收集、不上传任何用户数据。
