"""
报表模块配置
修改这里来定制你的采集目标和调度参数
"""

from pathlib import Path

REPORT_CONFIG = {
    # ── 采集节点（实时值，每分钟记录一次） ───────
    "fixed_nodes": [
        "ns=1;s=FIT_05R301F01.PV",
        "ns=1;s=FIT_05R302F01.PV",
        "ns=1;s=FIT_05R303F01.PV",
        "ns=1;s=FIT_05R304F01.PV",
        "ns=1;s=FIT_05R305F01.PV",
    ],

    # ── 定时调度间隔（分钟） ─────────────────────
    "schedule_interval_minutes": 1,

    # ── 报表输出目录 ─────────────────────────────
    "output_dir": str(Path(__file__).parent / "output"),

    # ── 报表文件名（固定文件名，持续追加） ────────
    "output_filename": "opcua_report.xlsx",
}
