"""
报表模块配置
修改这里来定制你的采集目标和调度参数
"""

from pathlib import Path

REPORT_CONFIG = {
    # ── 采集节点（实时值，每分钟记录一次） ───────
    "fixed_nodes": [
        # 瞬时流量 FIT
        "ns=1;s=FIT_05R301F01.PV",
        "ns=1;s=FIT_05R302F01.PV",
        "ns=1;s=FIT_05R303F01.PV",
        "ns=1;s=FIT_05R304F01.PV",
        "ns=1;s=FIT_05R305F01.PV",
        "ns=1;s=FIT_05R306F01.PV",
        "ns=1;s=FIT_05R307F01.PV",
        "ns=1;s=FIT_05R308F01.PV",
        "ns=1;s=FIT_05R309F01.PV",
        "ns=1;s=FIT_05R310F01.PV",
        # 累积流量 FIQ
        "ns=1;s=FIQ_05R301F01.OUT",
        "ns=1;s=FIQ_05R302F01.OUT",
        "ns=1;s=FIQ_05R303F01.OUT",
        "ns=1;s=FIQ_05R304F01.OUT",
        "ns=1;s=FIQ_05R305F01.OUT",
        "ns=1;s=FIQ_05R306F01.OUT",
        "ns=1;s=FIQ_05R307F01.OUT",
        "ns=1;s=FIQ_05R308F01.OUT",
        "ns=1;s=FIQ_05R309F01.OUT",
        "ns=1;s=FIQ_05R310F01.OUT",
        # 错误状态 ERR
        "ns=1;s=FIT_05R301F01.ERR",
        "ns=1;s=FIT_05R302F01.ERR",
        "ns=1;s=FIT_05R303F01.ERR",
        "ns=1;s=FIT_05R304F01.ERR",
        "ns=1;s=FIT_05R305F01.ERR",
        "ns=1;s=FIT_05R306F01.ERR",
        "ns=1;s=FIT_05R307F01.ERR",
        "ns=1;s=FIT_05R308F01.ERR",
        "ns=1;s=FIT_05R309F01.ERR",
        "ns=1;s=FIT_05R310F01.ERR",
        # 搅拌电机电流 IIAS（磺化釜 05A102~05A111，量程 0-50A）
        "ns=1;s=IIAS_05A102.PV",
        "ns=1;s=IIAS_05A103.PV",
        "ns=1;s=IIAS_05A104.PV",
        "ns=1;s=IIAS_05A105.PV",
        "ns=1;s=IIAS_05A106.PV",
        "ns=1;s=IIAS_05A107.PV",
        "ns=1;s=IIAS_05A108.PV",
        "ns=1;s=IIAS_05A109.PV",
        "ns=1;s=IIAS_05A110.PV",
        "ns=1;s=IIAS_05A111.PV",
        # 搅拌电机电流故障 ERR（磺化釜 05A102~05A111）
        "ns=1;s=IIAS_05A102.ERR",
        "ns=1;s=IIAS_05A103.ERR",
        "ns=1;s=IIAS_05A104.ERR",
        "ns=1;s=IIAS_05A105.ERR",
        "ns=1;s=IIAS_05A106.ERR",
        "ns=1;s=IIAS_05A107.ERR",
        "ns=1;s=IIAS_05A108.ERR",
        "ns=1;s=IIAS_05A109.ERR",
        "ns=1;s=IIAS_05A110.ERR",
        "ns=1;s=IIAS_05A111.ERR",
    ],

    # ── 定时调度间隔（分钟） ─────────────────────
    "schedule_interval_minutes": 1,

    # ── 报表输出目录 ─────────────────────────────
    "output_dir": str(Path(__file__).parent / "output"),

    # ── 报表文件名（固定文件名，持续追加） ────────
    "output_filename": "opcua_report.xlsx",
}
