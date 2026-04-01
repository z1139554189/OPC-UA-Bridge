# OPC UA 报表模块使用指南

## 目录结构

```
reporter/
├── config.py          ← ⭐ 你只需要改这一个文件
├── collector.py       ← 数据采集器（HTTP 客户端）
├── excel_report.py    ← Excel 报表生成器
├── run_report.py      ← 手动一键运行脚本
├── scheduler.py       ← 定时自动调度脚本
└── output/            ← 报表输出目录（自动创建）
```

---

## 第一步：安装依赖

```powershell
cd C:\Users\Administrator\WorkBuddy\20260326125244\opcua_api_bridge
C:\Python314\python.exe -m pip install openpyxl httpx
```

---

## 第二步：修改配置

打开 `reporter/config.py`，修改以下内容：

```python
# 1. 改成你的桥接器地址
"api_url": "http://localhost:8000",

# 2. 填你实际的 OPC UA 节点 ID（在 Swagger /api/v1/nodes 里可以查到）
"fixed_nodes": [
    "ns=2;s=Temperature",
    "ns=2;s=Pressure",
    # ...
],

# 3. 选择要看历史趋势的节点
"history_nodes": [
    "ns=2;s=Temperature",
],

# 4. 历史数据回溯多少小时
"history_hours": 24,
```

---

## 第三步A：手动触发（生成一次报表）

```powershell
cd C:\Users\Administrator\WorkBuddy\20260326125244\opcua_api_bridge
C:\Python314\python.exe reporter/run_report.py
```

可选参数：
```powershell
# 指定历史回溯 48 小时
C:\Python314\python.exe reporter/run_report.py --hours 48

# 指定输出路径
C:\Python314\python.exe reporter/run_report.py --out D:\reports\my_report.xlsx
```

---

## 第三步B：定时自动采集

```powershell
cd C:\Users\Administrator\WorkBuddy\20260326125244\opcua_api_bridge

# 按 config.py 配置运行（默认每 60 分钟一次）
C:\Python314\python.exe reporter/scheduler.py

# 每 30 分钟采集一次
C:\Python314\python.exe reporter/scheduler.py --interval 30

# 每小时采集，历史 48h，保留最近 100 份报表
C:\Python314\python.exe reporter/scheduler.py --interval 60 --hours 48 --keep 100
```

按 **Ctrl+C** 停止调度器。

报表文件自动保存在 `reporter/output/` 目录，文件名格式：
```
opcua_report_20260327_083000.xlsx
```

---

## 报表内容说明

| Sheet | 内容 |
|-------|------|
| **摘要** | 生成时间、OPC UA 连接状态、各类数据统计 |
| **实时值** | 所有节点的当前值、数据类型、质量、时间戳 |
| **历史趋势** | 时间序列宽表 + 自动折线图 |
| **历史原始** | 长表格式，方便数据透视分析 |

质量单元格颜色：🟩 绿=Good，🟨 黄=Unknown，🟥 红=Error

---

## 常见问题

**Q: 提示 `No module named 'openpyxl'`**
```powershell
C:\Python314\python.exe -m pip install openpyxl httpx
```

**Q: 历史数据全是空的**
- 桥接器的 OPC UA 服务器需要支持 Historical Access（HA）
- 确认节点 ID 正确

**Q: 节点 ID 不知道填什么**
- 先启动桥接器，访问 `http://localhost:8000/docs`
- 调用 `GET /api/v1/nodes` 查看节点树，找到你需要的节点 ID
