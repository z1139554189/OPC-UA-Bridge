# 长期记忆

## 用户习惯偏好

- **报表时间戳**：精确到秒（格式 `YYYY-MM-DD HH:MM:SS`），不需要毫秒/微秒
- **报表数值**：浮点数保留 2 位小数（`round(value, 2)`）
- **报表节点显示**：只显示位号部分（去掉 `ns=1;s=` 前缀），如 `FIT_05R201F01.PV`
- **适用范围**：所有 Excel 报表、数据表格输出
- **工作流程**：任务完成后先自行检查/验证一遍再交付给用户，减少来回修改。交付前必须验证核心功能是否正常（如数据是否真的在写入、报表是否真的有新数据），不能只看"代码改了/服务启动了"就交差
- **先想后做**：执行任何可能影响系统环境的大操作（卸载软件、改系统配置等）前，必须先评估影响范围，列出可能受影响的依赖和服务，不要用户说什么就无脑执行
- **遇错先查**：遇到库兼容性报错，第一时间查 GitHub Issues / 官方文档确认是否已知问题，而不是先尝试写 monkey patch 绕过
- **软件安装路径**：默认装到 **D 盘**（`D:\`），除非软件本身有特殊要求（如系统组件、需要装在 C 盘的）
- **专家协作**：架构选型、代码质量、部署运维等任务应主动召唤相关领域专家（Skill），不要闷头干
- **新增节点必须预热订阅**：桥接器 client.py 是按需订阅模式（只有 API 请求过的节点才会被订阅并写入 SQLite）。调度器直读 SQLite，所以任何新增到 config.py 的节点都必须通过 API 预热订阅一次，否则 SQLite 里不会有新数据。scheduler.py 已有 `warmup_subscriptions()` 处理此问题，但修改 config.py 加节点后必须重启调度器才能触发预热
- **锁屏自动 git push**：已配置开机自启的 PowerShell 后台服务（`auto_git_push.vbs` → `auto_git_push.ps1`），监听 Win32_SessionSwitchEvent（SessionSwitchType=7 即锁屏），自动执行 `git add -A && git commit && git push`。日志在 `.workbuddy/auto_git_push.log`。注册表位置 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\AutoGitPush`

- **领域背景**：工业自动化 / 工控系统，熟悉 OPC UA 协议
- **技术偏好**：倾向于可落地的架构方案，不只是理论
- **工作风格**：问题导向，直接切入技术细节

## 进行中的项目

### OPC UA REST API 桥接器
- **目录**：`c:\Users\Administrator\WorkBuddy\20260326125244\opcua_api_bridge\`
- **状态**：骨架已创建，待续
- **技术栈**：FastAPI + opcua-asyncio + Redis + Prometheus + JWT
- **已完成**：
  - `architecture.md`：完整架构文档
  - `requirements.txt`：Python 依赖
  - `src/api/main.py`：FastAPI 主应用
  - `src/opcua_client/client.py`：OPC UA 异步客户端
  - `src/config/settings.py`：配置管理
- **项目状态：骨架完整，可启动开发**
- **已完成**（2026-03-26 补全）：
  - `src/monitoring/health.py`：健康检查（OPC连接 + 内存）
  - `src/monitoring/metrics.py`：Prometheus 指标注册
  - `src/*/__init__.py`：所有包初始化文件
  - `Dockerfile`：多阶段构建，非root用户
  - `docker-compose.yml`：桥接器 + Redis + Prometheus + Grafana
  - `deployment/prometheus.yml`：抓取配置
  - `tests/`：test_health、test_opcua_client、test_api 三个测试文件
  - `.env.example`：配置模板
  - `README.md`：完整启动文档
- **已完成**（2026-03-27 新增）：
  - `reporter/` 子模块：完整的数据采集+Excel报表生成系统
    - `reporter/config.py`：用户配置文件（节点列表、调度间隔等）
    - `reporter/collector.py`：HTTP 数据采集器（实时值/批量/历史）
    - `reporter/excel_report.py`：Excel 生成器（4 Sheet：摘要/实时值/历史趋势/历史原始，含折线图）
    - `reporter/run_report.py`：手动一键采集脚本
- **已完成**（2026-04-02 报表重构）：
  - **调度器直读 SQLite**：`scheduler.py` 重写，去掉 httpx/HTTP API，直接用 sqlite3 读历史库最新值
  - 架构评审（FastAPI 专家确认）：减少故障点，桥接器挂了不影响报表
  - `reporter/config.py`：5 个 PV 节点，1 分钟间隔，固定文件名 `opcua_report.xlsx`
  - `reporter/excel_report.py`：单 Sheet 简洁版（时间列 + 5 节点列），每分钟追加一行
  - `reporter/history_report.py`：一次性历史报表（从 SQLite 读过去 N 小时数据，含折线图）
  - **SQLite 表名前缀注意**：实际为 `h_ns_eq_1_sc_s_eq_`（HistoryDB._safe_table_name 生成的是 `h_ns_eq_1_sc_`，两者不同）
  - **Excel 文件锁问题**：打开报表会导致调度器写入失败（Permission denied），建议复制后查看
  - **容错加固**（2026-04-02）：
    - scheduler.py：失败重试 3 次 + alert.log 告警 + 连续失败阈值告警
    - main.py：OPC UA 断连自动重连（30 秒间隔，无限重试）
- **NSSM Windows 服务**（2026-04-02）：
  - 安装路径：`D:\Tools\nssm.exe`
  - 服务名：`OPCUABridge`（端口 8000）、`OPCUAScheduler`
  - 管理命令：`net stop/start OPCUABridge`、`net stop/start OPCUAScheduler`
  - **注意**：NSSM `AppParameters` 引号问题会导致参数错乱，改用 Python `winreg` 直接写注册表更可靠
- **已完成**（2026-03-30 ~ 31 OPC UA 真实连接调试 + 读值修复）：
  - OPC UA 服务器：`opc.tcp://172.30.0.254:18950`（工业防火墙后，Ping 不通但 TCP 可达）
  - 本机 IP：`192.168.10.10`（以太网），路由：`route add 172.30.0.0 mask 255.255.255.0 192.168.10.1`
  - **根本原因**：服务器不支持直接 Read 请求（返回 Bad），必须用 Subscription 订阅模式获取数据
  - **库兼容性**：同步 `opcua` 库订阅回调返回 None（不兼容），必须用 `asyncua` 异步库
  - **解决方案**：`client.py` 完全重写为 asyncua + 按需订阅缓存（v3.0.0）
    - 启动时只创建空订阅通道（秒级完成）
    - 节点首次请求时按需 subscribe_data_change，值推送后写入缓存
    - 后续请求直接从缓存命中，无网络延迟
  - **Session 限制**：服务器最大 Session 数很少（约 2-3 个），调试时频繁重启会耗尽 Session，需等 60-120 秒自动释放
  - **API 验证通过**：`/health` → healthy，`/api/v1/nodes/batch-read` → 真实值（AOF=false, FLAG=4, HFV=-0.1）
  - **依赖新增**：`asyncua`（pip install asyncua）
- **历史数据结论**（2026-03-31 确认）：
  - 服务器 `Bad_HistoryOperationUnsupported`，OPC UA HA 协议不可用
  - `Historizing=True` 只是节点标记，DCS 内部有历史库但不对外暴露
  - **方案：桥接器自建 SQLite 历史库**（订阅推送写入，`read_history()` 查 SQLite）
- **已完成**（2026-04-01）：
  - `src/storage/__init__.py`：HistoryDB 异步 SQLite 历史存储模块
    - WAL 模式、buffer 批量写入、按 node_id 分表、7 天自动清理
    - 数据库路径：`opcua_api_bridge/data/history.db`
  - `client.py` v4.0.0：`_SubHandler` 推送时写 SQLite，`read_history()` 查 SQLite
  - 新增依赖：`aiosqlite`
  - **验证通过**：订阅→写入→查询全链路 OK
- **待处理**：
  - Grafana 仪表板 JSON 配置
  - 生产环境 JWT 鉴权接入
  - reporter/ 定时调度部署（可用 `python reporter/scheduler.py` 或 cron）
- **云端部署**（2026-04-01 进行中）：
  - **TDSQL-C MySQL**（上海）：`sh-cynosdbmysql-grp-4f512ckw.sql.tencentcdb.com:21397`，公网可达
  - VPC：`vpc-dh9ul32a`，子网：`subnet-ob3fhqkf`
  - 数据库：`opcua_db`，用户：`opcua_user`，密码在 `.env` 文件中
  - **SQLite→云端推送**：1288 条成功（脚本 `cloud/sqlite_to_cloud.py`）
  - **SCF 云函数**：事件函数版（不支持 API 网关触发器）→ 已删除
  - **SCF Web 函数**：Flask 版代码已写好（`cloud/scf/index.py`），待在控制台创建
  - **用户建议**：考虑 MQTT 协议（腾讯云 IoT Hub），工业数据上传的事实标准

## 工作区信息

- **Claw 工作区**：`c:\Users\Administrator\WorkBuddy\Claw`
- **OPC 工作区**：`c:\Users\Administrator\WorkBuddy\20260326125244`
- **记忆同步**：两个仓库都保存 MEMORY.md，保持同步

## 记忆方案（已配置）

| 层级 | 组件 | 状态 | 用途 |
|------|------|------|------|
| Layer 1 | SESSION-STATE.md | ✅ 运行中 | 当前任务热存储 |
| Layer 4 | MEMORY.md + daily/ | ✅ 运行中 | 长期记忆 + 日志 |
| Git 同步 | GitHub 仓库 | ✅ 已配置 | 跨设备备份 |

- **GitHub 仓库**：
  - `workbuddy-claw-memory`：Claw 工作区记忆
  - `opcua-api-bridge`：OPC 项目代码 + 记忆
- **同步方式**：手动 `git push` 或锁屏自动 push

## 系统环境

- **OS**：Windows 10 22H2 (Build 19045)
- **用户**：Administrator
- **GitHub**：用户名 `z1139554189`，已有仓库 `workbuddy-memory`
- **Python**：3.13.3（系统版 `C:\Program Files\Python313\python.exe`）；3.14 已卸载（asyncua 不兼容 Python 3.14，GitHub issue #1880 open）；managed 还有 3.13.12
- **Node.js**：20.19.0（`C:\Program Files\nodejs\node.exe`）
- **Playwright**：Python 版已安装，Chromium 145.0 已下载（`C:\Users\Administrator\AppData\Local\ms-playwright\chromium-1208`）
- **Google Chrome**：已安装（`C:\Program Files\Google\Chrome\Application\chrome.exe`）
- **playwriter CLI**：v0.0.102（`npm i -g playwriter`），通过 Chrome 扩展连接已有浏览器，保留登录态
- **浏览器自动化方案**：
  - playwriter（推荐）：连接已有 Chrome，保留登录态，适合操作腾讯云控制台
  - Python Playwright：独立 Chromium，适合通用自动化
- **已安装 Skill**：find-skills, clawdhub（CLI 坏了，undici 缺失）

## OPC UA 节点结构（已探明）
- 服务器节点命名空间：**ns=1**（不是 ns=2）
- 节点结构：树形，设备名是父节点（Object），属性是子节点（Variable）
  - 父节点：`ns=1;s=FIT_05R210F01`（node_class=1, Object）
  - 子节点格式：`ns=1;s=设备名.属性名`，如 `ns=1;s=FIT_05R210F01.PV`
- **`.VALUE` 在 DCS 里对应 OPC UA 里的 `.PV`（Process Value）**
- FIT 类节点的 36 个子属性：PV(过程值), FLAG, AOF, HFV, HPV, HSV, HTV, HWF, H_B, HH_B, HHH_B, L_B, LL_B, ORH, ORL, ERR, OOS, IOP, NR, PR, REVSCL, SAFESTA, SWAM_B, HPV_STA, HFV_STA, HSV_STA, HTV_STA, CFGERR, ENHART, DPV_B, I_HH, I_LL, I_H, I_L, SIMUL, LLL_B
- 节点总数：约 3500+ 根节点，每个根节点可能有 30+ 子节点
- **首次订阅延迟**：子属性节点（如 .PV）首次订阅后服务器推送约需 5-10 秒（超过了默认 5s 超时），建议延长到 15s

## 历史操作记录（摘要）

- 2026-03-26：清理 C 盘，释放约 220 MB
- 2026-03-26：探讨 WorkBuddy 玩法、技能安装、OPC UA 数据分析
- 2026-03-27：新增 reporter/ 子模块，实现自动采集 + Excel 报表生成（含定时调度）
