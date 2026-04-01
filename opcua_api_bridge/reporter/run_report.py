"""
一键采集并生成报表（手动触发版）
用法：
    python run_report.py
    python run_report.py --hours 48 --out D:/reports/my_report.xlsx
"""

import argparse
import sys
import logging
from pathlib import Path

# 将项目根目录加入 path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from reporter.collector import OpcUaCollector
from reporter.excel_report import generate_excel
from reporter.config import REPORT_CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def main():
    parser = argparse.ArgumentParser(description="OPC UA 数据采集 & Excel 报表生成")
    parser.add_argument("--url",   default=REPORT_CONFIG["api_url"],
                        help="桥接器 API 地址，默认 http://localhost:8000")
    parser.add_argument("--hours", type=float, default=REPORT_CONFIG["history_hours"],
                        help="历史数据回溯小时数，默认从配置读取")
    parser.add_argument("--out",   default=None,
                        help="输出文件路径，默认当前目录生成带时间戳文件名")
    args = parser.parse_args()

    collector = OpcUaCollector(base_url=args.url)

    # ── 先检查服务是否可达 ──
    print(f"[INFO] 连接桥接器: {args.url}")
    try:
        health = collector.check_health()
        opc_ok = health.get("opcua_connected", False)
        print(f"   健康状态: {'[OK] OPC UA 已连接' if opc_ok else '[WARN] OPC UA 未连接（仍将生成报表）'}")
    except Exception as e:
        print(f"[ERROR] 无法连接桥接器: {e}")
        print("   请先确认桥接器已启动（python -m uvicorn src.api.main:app --port 8000）")
        sys.exit(1)

    # ── 采集数据 ──
    print(f"[INFO] 开始采集数据（历史回溯 {args.hours}h）...")
    data = collector.collect_all(
        fixed_nodes=REPORT_CONFIG["fixed_nodes"],
        history_nodes=REPORT_CONFIG["history_nodes"],
        history_hours=args.hours,
    )
    print(f"   实时节点: {len(data['realtime'])} 个")
    print(f"   历史节点: {len(data['history'])} 个")

    # ── 生成报表 ──
    print("[INFO] 生成 Excel 报表...")
    out_path = generate_excel(data, output_path=args.out)
    print(f"\n[DONE] 完成！文件已保存至:\n   {out_path}")


if __name__ == "__main__":
    main()
