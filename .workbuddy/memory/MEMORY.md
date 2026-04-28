# 工作区完整记忆

> 更新时间：2026-04-28 14:40
> 工作区：`c:\Users\Administrator\WorkBuddy\20260326125244\`
> 记忆仓库：Claw（WorkBuddy），不维护在 GitHub 仓库

---

## 第一部分：用户画像

| 维度 | 描述 |
|------|------|
| 领域背景 | 工业自动化 / 工控系统，熟悉 OPC UA 协议 |
| 技术偏好 | 倾向于可落地的架构方案，不只是理论 |
| 工作风格 | 问题导向，直接切入技术细节 |

---

## 第二部分：用户习惯偏好（AI 必须遵守）

| 规则 | 说明 |
|------|------|
| 报表时间戳 | 精确到秒（`YYYY-MM-DD HH:MM:SS`），不需要毫秒/微秒 |
| 报表数值 | 浮点数保留 2 位小数（`round(value, 2)`） |
| 报表节点显示 | 只显示位号部分（去掉 `ns=1;s=` 前缀），如 `FIT_05R201F01.PV` |
| 适用范围 | 所有 Excel 报表、数据表格输出 |
| 交付前验证 | 任务完成后先自行检查/验证一遍再交付，必须验证核心功能正常 |
| 先想后做 | 执行大操作前先评估影响范围，列出可能受影响的依赖和服务 |
| 遇错先查 | 库兼容性报错第一时间查 GitHub Issues / 官方文档 |
| 软件安装路径 | 默认装到 D 盘（`D:\`），除非软件本身有特殊要求 |
| 专家协作 | 架构选型、代码质量、部署运维等任务主动召唤专家 Skill |
| 新增节点预热 | 新增到 config.py 的节点必须通过 API 预热订阅，否则 SQLite 无数据 |
| git push | 由用户手动触发，不配置自动 push；用户说"推送"时默认推送所有有用内容（记忆、日志、代码等） |
| 记忆管理 | 长期记忆统一在 WorkBuddy memory/ 更新，不在 GitHub 仓库维护 |

---

## 第三部分：系统环境

| 项目 | 值 |
|------|-----|
| OS | Windows 10 22H2 (Build 19045)，用户 Administrator |
| GitHub | 用户名 `z1139554189`，仓库 `OPC-UA-Bridge` |
| Python | 3.13.3（系统，推荐）、3.13.12（managed）、3.14.3（managed） |
| Node.js | 20.19.0 |
| Chrome | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| playwriter CLI | v0.0.102，通过 Chrome 扩展连接已有浏览器 |
| Python Playwright | 已安装，Chromium 145.0 |
| NSSM | `D:\Tools\nssm.exe`（nssm 2.24），Windows 服务管理 |
| 网络路由 | 永久路由 `route -p add 172.30.0.0 mask 255.255.255.0 192.168.10.1` |

---

## 第四部分：OPC UA REST API 桥接器（核心项目）

### 项目概况

| 项目 | 值 |
|------|-----|
| 目录 | `c:\Users\Administrator\WorkBuddy\20260326125244\opcua_api_bridge\` |
| GitHub | `https://github.com/z1139554189/OPC-UA-Bridge` |
| 技术栈 | FastAPI + opcua-asyncio (asyncua) + Redis + Prometheus + JWT |
| 创建时间 | 2026-03-26 |

### OPC UA 服务器信息

| 项目 | 值 |
|------|-----|
| 服务器地址 | `opc.tcp://172.30.0.254:18950`（工业防火墙后，Ping 不通但 TCP 可达） |
| 本机 IP | `192.168.10.10`（以太网） |
| Read 支持 | ❌ 必须用 Subscription 订阅模式 |
| 历史数据 | ❌ 不支持，桥接器自建 SQLite |
| Session 限制 | 最大 **1 个**，Session 满时 os._exit(2) 终止进程（NSSM 不自动重启） |
| 首次订阅延迟 | 5-10 秒 |
| 安全策略 | 无安全连接（去掉默认 Basic256Sha256） |
| 节点结构 | ns=1，约 3500+ 根节点，每个 30+ 子属性 |
| FIT 类子属性 | PV, FLAG, AOF, HFV, HPV, HSV, HTV, HWF, H_B, HH_B, HHH_B, L_B, LL_B, ORH, ORL, ERR, OOS, IOP, NR, PR, REVSCL, SAFESTA, SWAM_B, HPV_STA, HFV_STA, HSV_STA, HTV_STA, CFGERR, ENHART, DPV_B, I_HH, I_LL, I_H, I_L, SIMUL, LLL_B（共 36 个） |

### 项目模块清单

| 模块 | 文件 | 说明 |
|------|------|------|
| 架构文档 | `architecture.md` | 完整架构设计 |
| API 主应用 | `src/api/main.py` | v3.0.0，FastAPI，自适应采集模式 |
| OPC UA 客户端 | `src/opcua_client/client.py` | v7.0.0，极简退避版：统一 _retry_after 控制重连，Session 满 os._exit(2) 终止进程，**300 秒无推送视为断连**，PUSH_FAIL_BACKOFF=500s，每 10 秒心跳写入 SQLite |
| 配置管理 | `src/config/settings.py` | pydantic-settings |
| 健康检查 | `src/monitoring/health.py` | v3.0.0，检查采集状态（active/backoff/reconnecting/push_timeout/waiting_first_push）+ 缓存新鲜度 + 推送年龄 + 内存 |
| Prometheus 指标 | `src/monitoring/metrics.py` | 指标注册 |
| 历史存储 | `src/storage/__init__.py` | HistoryDB 异步 SQLite（WAL、buffer 批量写入、7 天清理、分表） |
| 调度器 | `reporter/scheduler.py` | 直读 SQLite + warmup 预热 + 文件锁检测 + 容错重试 3 次 |
| Excel 报表 | `reporter/excel_report.py` | 追加式报表，首次新建全部 Sheet，后续只追加新行，**追加时自动检测列头匹配，不匹配则补列头** |
| 报表配置 | `reporter/config.py` | 20 个 R3xx 节点（10 FIT PV + 10 FIQ OUT），间隔 1 分钟；另有 10 个 FIT ERR 仅订阅不入报表 |
| 历史报表 | `reporter/history_report.py` | 一次性历史报表（过去 1 小时） |
| Docker | `Dockerfile` + `docker-compose.yml` | 容器化部署 |
| 测试 | `tests/` | test_health、test_opcua_client、test_api |

### Windows 服务

| 服务名 | 命令 | 端口 | 启动类型 |
|--------|------|------|----------|
| OPCUABridge | `C:\Python314\python.exe -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000` | 8000 | AUTO_START（2026-04-28 改） |
| OPCUAScheduler | `C:\Python314\python.exe reporter/scheduler.py` | — | AUTO_START |

管理命令：`net stop/start OPCUABridge`、`net stop/start OPCUAScheduler`

### NSSM Recovery 策略

| 退出码 | 含义 | OPCUABridge | OPCUAScheduler |
|--------|------|-------------|----------------|
| 0 | 正常退出 | Exit（不重启） | Restart |
| 1 | 一般错误 | Restart | Restart |
| 2 | Session 满 os._exit(2) | Exit（故意不重启，避免死循环） | — |

重启延迟：~5 秒

### 调度器行为

- 每 60 秒从 SQLite 读取 **20 个节点**最新值，追加到 `reporter/output/opcua_report.xlsx`
- 启动时 `warmup_subscriptions()` 调桥接器 API 预热订阅
- Excel 被占用时静默跳过；其他失败重试 3 次 + alert.log 告警

### SQLite 历史库

- 路径：`opcua_api_bridge/data/history.db`
- WAL + 批量 buffer（满 100 条或 5 秒 flush），按 node_id 分表，7 天自动清理
- 数据来源：OPC UA 订阅推送（秒级，自适应模式）+ 心跳写入（10 秒间隔，source=heartbeat）

### 云端部署（独立工作区 `20260402160254`）

| 项目 | 值 |
|------|-----|
| TDSQL-C MySQL | 上海，`sh-cynosdbmysql-grp-4f512ckw.sql.tencentcdb.com:21397` |
| VPC | `vpc-dh9ul32a`，子网 `subnet-ob3fhqkf` |
| 数据库 | `opcua_db`，用户 `opcua_user`，密码在 `.env` |
| 已推送 | 1288 条历史记录 + 9 个节点实时快照 |
| 代码位置 | `20260402160254/opcua_bridge_cloud/` + `scf_browser_scripts/` |

### 待处理事项

- [ ] Grafana 仪表板 JSON 配置
- [ ] 生产环境 JWT 鉴权接入
- [ ] SCF Web 函数在腾讯云控制台创建
- [ ] 端到端 API 测试验证（云端）

---

## 第五部分：经验教训

| 问题 | 根因 | 解决方案 |
|------|------|----------|
| 读值返回 Bad | 服务器不支持 Read | 必须用 Subscription 订阅 |
| 历史数据稀疏 | 值不变不推送 | 60 秒定期轮询强制写缓存 |
| 调度器成功率 48% | append 清空合并单元格报错 | 首次新建 + 后续只追加新行 |
| 报表数据不更新 | 新节点未预热订阅 | warmup_subscriptions() 启动预热 |
| asyncua Python 3.14 报错 | GitHub issue #1880 | 安装 Python 3.13 或回退 |
| Excel 文件被占用 | 用户打开时写入失败 | _is_file_locked() 静默跳过 |
| Session 断了健康检查仍报 healthy | is_connected() 只看布尔标志 | 改为检查 _last_data_time 新鲜度（120s 超时）+ 断连写 logs/alert.log |
| 桥接器和客户端抢 Session | 服务器只允许 1 个 Session | 自适应采集：默认长连接高速采集，检测到 BadTooManySessions 自动让出，30 秒重试恢复 |
| 死值和断连无法区分 | v6 只检查 session 对象是否在内存里 | v7 推送超时检测：300 秒无推送 = 断连 + 心跳写入 10 秒保证时间序列连续（source=heartbeat） |
| 推送超时 30s 误判断连 | 只订阅 5 个同类型 FIT PV，工艺稳定时值全不变 | PUSH_TIMEOUT 30s→300s + 新增 5 个节点分散相关性 |
| 报表多出无名列头列 | config 曾临时加 ERR 节点，删除后旧数据列残留（openpyxl max_column 不收缩） | 用 delete_cols 从右往左删除残留列，或重建报表 |

**工作流程教训**：遇错先查 Issues、大操作先评估影响、交付前验证核心功能、MQTT 是工业数据上传主流方案

---

## 第六部分：工作流程规则（2026-04-10 新增）

### 文件清理流程
1. **全面扫描**：扫描整个目录树，不遗漏任何角落（包括 .workbuddy/、隐藏目录）
2. **分析分类**：区分核心文件 vs 无用文件，不凭感觉
3. **列表确认**：列出所有待删除项 + 删除原因 + 保留项 + 保留原因，请用户逐项确认后再执行
4. **执行删除**：删除确认的无用文件
5. **生成记忆**：清理完成后，将结果记录到 MEMORY.md

### Git 推送流程
1. **推送前必查**：执行 `git status` 和 `git ls-files`，确保所有本地有价值的文件都已在 git 中，无遗漏
2. **特别关注**：类似 server.js 这种从未进过 git 的文件
3. **添加变更**：`git add -A`
4. **提交**：`git commit -m "..."`
5. **推送**：`git push origin main`

**核心原则**：筛选无用文件 → 用户确认删除 → 一个不落推送 git

---

## 第七部分：已安装 Skills

find-skills、python-backend、fastapi、docker-compose

---

_后续增量记录在 `memory/YYYY-MM-DD.md`，长期经验沉淀在本文件。_
