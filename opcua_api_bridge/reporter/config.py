"""
报表模块配置
修改这里来定制你的采集目标和调度参数
"""

from pathlib import Path

REPORT_CONFIG = {
    # ── 桥接器地址 ──────────────────────────────
    "api_url": "http://localhost:8000",

    # ── 固定采集节点（实时值） ───────────────────
    # 填写你 OPC UA 服务器上的实际节点 ID
    # 格式通常是 "ns=命名空间;s=节点名"
    "fixed_nodes": [
        "ns=1;s=FIT_05R201F01.PV",
        "ns=1;s=FIT_05R210F01.PV",
        "ns=1;s=FIT_05R201F01.FLAG",
        "ns=1;s=FIT_05R210F01.FLAG",
        "ns=1;s=FIT_05R201F01.AOF",
        "ns=1;s=FIT_05R210F01.AOF",
        "ns=1;s=FIT_05R201F01.HFV",
        "ns=1;s=FIT_05R210F01.HFV",
    ],

    # ── 历史趋势节点 ─────────────────────────────
    # 从 SQLite 历史库查询，需要桥接器先订阅过这些节点
    "history_nodes": [
        "ns=1;s=FIT_05R201F01.PV",
        "ns=1;s=FIT_05R210F01.PV",
        "ns=1;s=FIT_05R201F01.FLAG",
        "ns=1;s=FIT_05R210F01.FLAG",
    ],

    # ── 历史回溯时间（小时） ─────────────────────
    # 注意：历史数据来自桥接器本地 SQLite，只有桥接器运行期间订阅过的数据
    "history_hours": 1,  # 查最近 1 小时

    # ── 定时调度间隔（分钟） ─────────────────────
    "schedule_interval_minutes": 30,   # 每 30 分钟

    # ── 报表输出目录 ─────────────────────────────
    "output_dir": str(Path(__file__).parent / "output"),

    # ── 最多保留报表数量 ─────────────────────────
    "keep_reports": 72,   # 保留最近 72 份（每30分钟一次 = 保留 1.5 天）
}
