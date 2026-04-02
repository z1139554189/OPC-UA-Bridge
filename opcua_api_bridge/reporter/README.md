# OPC UA 报表模块使用指南

## 目录结构

```
reporter/
├── config.py          ← ⭐ 你只需要改这一个文件（节点列表、调度间隔）
├── excel_report.py    ← Excel 报表生成器（单 Sheet，每分钟追加一行）
├── history_report.py  ← 一次性历史报表（从 SQLite 读过去 N 小时数据，含折线图）
├── scheduler.py       ← 定时自动调度脚本（直读 SQLite，不走 HTTP API）
└── output/            ← 报表输出目录（自动创建）
```

---

## 第一步：安装依赖

```powershell
cd C:\Users\Administrator\WorkBuddy\20260326125244\opcua_api_bridge
C:\Python314\python.exe -m pip install openpyxl
```

---

## 第二步：修改配置

打开 `reporter/config.py`，修改以下内容：

```python
# 1. 填你实际的 OPC UA 节点 ID（在 Swagger /api/v1/nodes 里可以查到）
"fixed_nodes": [
    "ns=1;s=FIT_05R301F01.PV",
    "ns=1;s=FIT_05R302F01.PV",
    # ...
],

# 2. 采集间隔（分钟）
"schedule_interval_minutes": 1,

# 3. 报表文件名（固定文件名，持续追加）
"output_filename": "opcua_report.xlsx",
```

---

## 第三步A：定时自动采集（推荐，已注册为 Windows 服务）

```powershell
# 启动服务
net start OPCUAScheduler

# 停止服务
net stop OPCUAScheduler
```

手动运行（调试用）：
```powershell
cd C:\Users\Administrator\WorkBuddy\20260326125244\opcua_api_bridge

# 按 config.py 配置运行（默认每 1 分钟一次）
C:\Python314\python.exe reporter/scheduler.py

# 单次运行后退出
C:\Python314\python.exe reporter/scheduler.py --once
```

报表文件保存在 `reporter/output/opcua_report.xlsx`，每分钟追加一行。

---

## 第三步B：生成历史报表（一次性）

```powershell
cd C:\Users\Administrator\WorkBuddy\20260326125244\opcua_api_bridge

# 默认读取过去 1 小时数据
C:\Python314\python.exe reporter/history_report.py

# 读取过去 6 小时
C:\Python314\python.exe reporter/history_report.py --hours 6
```

---

## 数据流架构

```
OPC UA 服务器
    ↓（订阅推送）
桥接器（FastAPI :8000）
    ↓（写入）
SQLite 历史库（data/history.db）
    ↑（直读 SQLite，不走 HTTP）
调度器 scheduler.py → Excel 报表
```

调度器直读 SQLite，即使桥接器挂了也不影响报表生成。

---

## 常见问题

**Q: 提示 `No module named 'openpyxl'`**
```powershell
C:\Python314\python.exe -m pip install openpyxl
```

**Q: 打开 Excel 导致调度器写入失败（Permission denied）**
- 复制一份报表到其他位置查看，不要直接打开 output 目录下的文件

**Q: 节点 ID 不知道填什么**
- 先启动桥接器，访问 `http://localhost:8000/docs`
- 调用 `GET /api/v1/nodes` 查看节点树，找到你需要的节点 ID

**Q: 报表里没有数据**
- 确认桥接器和 OPC UA 服务器连接正常：访问 `http://localhost:8000/health`
- 确认 SQLite 历史库有数据：检查 `data/history.db`
