<img src="icons/favicon-64.png" width="72" alt="icon">

# Workbuddy积分看板（非官方）

> WorkBuddy 账号用量桌面挂件 + 完整看板。常驻桌面实时查看积分余额、今日/本月消耗（积分 ⇄ Token 切换）、
> 今日会话 Token 消耗榜；内置浏览器可打开的完整看板（账单详单 / 热力日历 / 会话消耗 / 来源拆分）。

非官方个人小工具，与 WorkBuddy 官方无关。
**只读取你本机 WorkBuddy 客户端的登录态**（只读、不落盘、不外传），调用官方计费接口获取
「你自己账号」的用量数据；所有数据仅保存在本机，不使用任何浏览器自动化。

## 功能

**桌面挂件（`WBCreditWidget.exe`）**
- 积分余额（点击跳转成长计划页）
- 今日 / 本月消耗 —— 点击卡片切换 **积分 ⇄ Token** 双口径
- **今日会话消耗 TOP 榜** —— 按 Token 排行，柱状图直观对比
- 字号调节（A− / A+ 四档）· 三主题（WorkBuddy / EVA-01 / EVA-02）· 拖拽移动与缩放

**完整看板**（点击「↗ 完整版」，或浏览器打开 `http://127.0.0.1:8790/full`）
- 顶部统计卡片 —— 积分口径看余额与消耗；点击卡片切成 **Token 口径**，并把 Token 拆成
  **积分 Token / 免费额度 / 外部 API（BYOK）** 三色构成条
- 模型消耗（积分 / 次数 / Token 三口径）
- 30 天热力日历 —— 积分 / 请求数 / Token 三档；Token 档底部琥珀色细条为该天**非积分 Token 占比**
- 会话消耗 —— 默认总消耗，可切「按天」；按天榜以当天最高 Token 归一画柱，点击某天**展开模型明细**
- 消耗详单 —— 固定按天：时间 / 模型 / 积分 / 来源 / 摘要
- 来源拆分 —— 本机 / 非本机 / 网页版 / APP 四类（页尾）
- 全局字号缩放（五档，自动记忆）· 6 套主题

## 快速开始（免安装）

1. 从 [**Releases**](https://github.com/CerberusPhil/wb-usage-widget/releases/latest) 下载最新版 zip
   （`WBCreditWidget_vX.Y.zip`），解压后双击里面的 `WBCreditWidget.exe`
2. 前提：本机已安装 WorkBuddy 客户端并保持登录
3. 首次启动等待 10~60 秒完成首次同步（窗口自动刷新）

> 更多细节（操作、隐私说明、FAQ）见 [`packaging/使用说明.txt`](packaging/使用说明.txt)。

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
| Token / 会话明细 | 本机 WorkBuddy 会话记录（只读） | 不复制、不外传 |
| 登录凭据 | 本机 WorkBuddy 客户端登录态 | 仅内存只读使用，**不落盘** |

## 目录结构

```
.
├── scripts/                  # 源码（本地服务 + 挂件壳 + 数据聚合）
│   ├── wb_widget_app.py      # 一键入口（exe 打包入口：互斥锁 + 内嵌服务 + 窗口）
│   ├── billing_server.py     # 本地服务（默认 http://127.0.0.1:8790）
│   ├── billing_widget.py     # 挂件窗口壳（pywebview；缺则 Edge --app 降级）
│   ├── server_usage_sync.py  # 账单同步（桌面登录态优先 / cookie 兜底）
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

## 已知问题

### 积分数据不刷新，日志出现 `HTTP 401`

较新版本的 WorkBuddy 客户端把本机登录态里的 `accessToken` 从**明文 JWT** 改成了
**加密信封**（`{"$wbEncrypted": 1, "envelope": "..."}`）。本工具需要明文令牌才能调用
官方计费接口，因此会拿到 401：

- 表现：积分余额 / 账单**停在最后一次成功同步的缓存**，不再更新；
- 与网络、账号状态无关，重新登录客户端不一定能解决；
- **Token / 会话明细不受影响** —— 它读的是本机会话记录文件（`~/.workbuddy/projects/*.jsonl`），不经过登录态；
- 积分真值请以网页端为准。

诊断方法：对比登录态文件的 `auth.accessToken` 字段——是字符串（JWT）还是
`{"$wbEncrypted": ...}` 对象，即可判断是否命中该问题。

## 免责声明

- 非官方工具；官方接口与字段可能调整，导致部分功能失效；
- 请在遵守 WorkBuddy 服务条款的前提下使用；
- 本项目不收集、不上传任何用户数据。
