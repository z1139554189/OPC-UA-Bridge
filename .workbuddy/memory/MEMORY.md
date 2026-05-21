# 长期记忆

> 更新时间：2026-05-21 16:20

## 系统环境

| 项目 | 值 |
|------|-----|
| OS | Windows 10 22H2，用户 Administrator |
| Python venv | D:\opcua_venv（Python 3.13.12，managed 版创建） |
| Git | D:\Program Files\Git，需绝对路径调用 |
| NSSM | D:\Tools\nssm.exe（v2.24 64-bit） |
| 永久路由 | 172.30.0.0/24 → 192.168.10.1（接口 192.168.10.10） |

## OPC-UA-Bridge 项目

| 项目 | 值 |
|------|-----|
| 项目路径 | C:\Users\Administrator\WorkBuddy\2026-05-21-10-09-10\OPC-UA-Bridge\opcua_api_bridge |
| GitHub | https://github.com/z1139554189/OPC-UA-Bridge（已改公有） |
| OPC UA 服务器 | opc.tcp://172.30.0.254:18950（无安全策略，只支持 1 Session） |
| Bridge 服务 | OPCUABridge（AUTO_START，端口 8000，退出码 0/2 不重启） |
| Scheduler 服务 | OPCUAScheduler（AUTO_START，退出码 0 不重启） |
| Dashboard | http://localhost:8000/dashboard（实时+历史+导出） |
| 存储 | SQLite（./data/history.db，WAL 模式，7天保留） |

## Dashboard 功能

| 功能 | 说明 |
|------|------|
| 实时看板 | 3秒轮询 `/api/v1/cache/stats`，20 卡片（FIT 绿色 kg/h，FIQ 橙色） |
| 历史查询 | `POST /api/v1/history/query`，时间桶采样，Chart.js 趋势图 |
| Excel 导出 | `POST /api/v1/history/export`，固定时间桶对齐，每行一个间隔 |

## 用户偏好

| 规则 | 说明 |
|------|------|
| 软件安装路径 | 默认装到 D 盘 |
| git push | 手动触发，不自动推送 |
| 报表数值 | 保留 2 位小数 |
| 报表节点 | 只显示位号部分（去掉 ns=1;s= 前缀） |
| 订阅节点 | R301~R310 共 10 反应器，每家仅 F01：10×FIT.PV + 10×FIQ.OUT |
| 节点权威来源 | reporter/config.py 中的 REPORT_NODES 列表（唯一标准） |
| 数字格式 | 保留 2 位小数 |

## 核心教训（2026-05-21 项目实施总结）

### 1. 节点列表：永远找权威来源
- `reporter/config.py` 的 `REPORT_NODES` 是唯一标准
- 凭空捏造会导致遗漏节点或包含不存在的节点
- 所有地方（main.py、dashboard.html、config.py）必须对齐同一来源

### 2. Bridge 启动顺序：add_nodes() 必须在 start() 之前
- 订阅发生在 `_connect_opc()` 阶段
- `_collect_nodes` 为空 → 0 节点订阅 → 15s 超时 → 退避 499s
- 修复：lifespan 中先 `add_nodes()` 再 `start()`

### 3. Chart.js 两个坑
- **ctx 冲突**：`destroy()` 后 canvas 保留 2D 上下文 → 销毁后必须 `remove()` + `createElement('canvas')` 重建
- **时间轴依赖**：v4 的 `type: 'time'` 需外部适配器 → 改用 `type: 'linear'` + 毫秒时间戳 + 手动格式化 tick，零外部依赖

### 4. 多源数据对齐：时间桶优于精确匹配
- 不同节点的采集时间戳毫秒不可能相同
- `p["timestamp"] == ts` 永远匹配失败 → 改用固定间隔时间桶，每桶取最新值
- 时间桶 = `start_time + bucket_index × interval_seconds`

### 5. NSSM 服务修正
- AppParameters 不需要 `-m uvicorn` 前缀
- 退出码 0/2 不重启（正常退出 + 参数错误），1 重启（运行异常）
- `Restart-Service` 可能不够快，需 `sc query` 确认后等待 OPC 重连
