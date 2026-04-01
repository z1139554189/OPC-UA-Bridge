"""
定时自动采集调度器
用法：
    python scheduler.py                   # 按 config.py 中的 schedule_interval_minutes 定时运行
    python scheduler.py --interval 30     # 每 30 分钟跑一次
    python scheduler.py --interval 60 --keep 72  # 每小时，保留最近 72 个文件
"""

import argparse
import sys
import time
import logging
import signal
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from reporter.collector import OpcUaCollector
from reporter.excel_report import generate_excel
from reporter.config import REPORT_CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            ROOT / "reporter" / "scheduler.log", encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)

_running = True


def _handle_signal(sig, frame):
    global _running
    logger.info("收到停止信号，调度器即将退出...")
    _running = False


signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _cleanup_old_reports(output_dir: Path, keep: int):
    """保留最新的 keep 个报表文件，删除旧的"""
    files = sorted(output_dir.glob("opcua_report_*.xlsx"), key=lambda f: f.stat().st_mtime)
    to_delete = files[:-keep] if len(files) > keep else []
    for f in to_delete:
        try:
            f.unlink()
            logger.info(f"清理旧报表: {f.name}")
        except Exception as e:
            logger.warning(f"清理失败 {f}: {e}")


def run_once(collector: OpcUaCollector, output_dir: Path, hours: int, append_to_file: str | None = None) -> str | None:
    """执行一次采集+生成，返回文件路径或 None"""
    if append_to_file:
        out_path = Path(append_to_file)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = output_dir / f"opcua_report_{ts}.xlsx"

    try:
        logger.info(f"开始采集（历史 {hours}h）...")
        data = collector.collect_all(
            fixed_nodes=REPORT_CONFIG["fixed_nodes"],
            history_nodes=REPORT_CONFIG["history_nodes"],
            history_hours=hours,
        )
        path = generate_excel(data, output_path=str(out_path), append=True if append_to_file else False)
        logger.info(f"报表生成成功: {path}")
        return path
    except Exception as e:
        logger.error(f"采集/生成失败: {e}", exc_info=True)
        return None


def main():
    parser = argparse.ArgumentParser(description="OPC UA 报表定时调度器")
    parser.add_argument("--url",      default=REPORT_CONFIG["api_url"])
    parser.add_argument("--interval", type=float,
                        default=REPORT_CONFIG["schedule_interval_minutes"],
                        help="采集间隔（分钟，支持小数），默认 60")
    parser.add_argument("--hours",    type=int,
                        default=REPORT_CONFIG["history_hours"],
                        help="历史回溯小时数，默认 24")
    parser.add_argument("--outdir",   default=REPORT_CONFIG["output_dir"],
                        help="报表输出目录")
    parser.add_argument("--keep",     type=int,
                        default=REPORT_CONFIG["keep_reports"],
                        help="保留最近 N 个报表文件，默认 72")
    parser.add_argument("--once",     action="store_true",
                        help="只跑一次后退出（与手动 run_report.py 等效）")
    parser.add_argument("--duration", type=int, default=None,
                        help="运行总时长（分钟），运行完成后自动停止")
    args = parser.parse_args()

    output_dir = Path(args.outdir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    collector = OpcUaCollector(base_url=args.url)

    if args.once:
        run_once(collector, output_dir, args.hours)
        return

    # 追加模式：只生成一个文件
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"opcua_report_{ts}.xlsx"
    logger.info(
        f"调度器启动 | 间隔: {args.interval}min | 运行时长: {args.duration or '无限'}min | "
        f"输出文件: {output_file}"
    )
    logger.info("按 Ctrl+C 停止")

    # 首次运行（创建新文件）
    run_once(collector, output_dir, args.hours, append_to_file=None)
    # 后续运行（追加到同一个文件）
    _cleanup_old_reports(output_dir, args.keep)

    start_time = time.time()
    while _running:
        run_once(collector, output_dir, args.hours, append_to_file=str(output_file))

        # 检查是否到达运行时长
        if args.duration:
            elapsed = (time.time() - start_time) / 60  # 分钟
            if elapsed >= args.duration:
                logger.info(f"已运行 {elapsed:.1f} 分钟，达到预设时长，停止调度器")
                break

        # 等待下一次，每秒检查一次 _running 标志
        wait_seconds = int(args.interval * 60)
        for _ in range(wait_seconds):
            if not _running:
                break
            time.sleep(1)

    logger.info("调度器已停止")


if __name__ == "__main__":
    main()
