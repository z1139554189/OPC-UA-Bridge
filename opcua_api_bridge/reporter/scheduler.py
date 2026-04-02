"""
定时自动采集调度器（直读 SQLite 版）
每分钟从 SQLite 历史库读取 5 个节点的最新值，追加一行到 Excel。
不再依赖 HTTP API，桥接器挂了也不影响报表。

容错机制：
- 单次失败自动重试（最多 3 次，间隔 5 秒）
- 连续失败超过阈值写入 alert.log
- SQLite 连接失败时自动重连

用法：
    python scheduler.py                   # 按 config.py 中的间隔运行
    python scheduler.py --interval 5      # 每 5 分钟一次（调试用）
    python scheduler.py --once            # 只跑一次后退出
"""

import argparse
import sqlite3
import sys
import time
import logging
import signal
import urllib.request
import urllib.error
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from reporter.excel_report import generate_excel
from reporter.config import REPORT_CONFIG

# ── 桥接器 API（用于启动时预热订阅） ───────────────
BRIDGE_API_BASE = "http://localhost:8000"

# ── 日志配置 ───────────────────────────────────────

LOG_DIR = ROOT / "reporter"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "scheduler.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# 告警日志（独立文件，只记告警）
alert_handler = logging.FileHandler(LOG_DIR / "alert.log", encoding="utf-8")
alert_handler.setLevel(logging.WARNING)
alert_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(alert_handler)

# ── 配置 ───────────────────────────────────────────

MAX_RETRIES = 3          # 单次采集最大重试次数
RETRY_INTERVAL = 5       # 重试间隔（秒）
ALERT_THRESHOLD = 3      # 连续失败多少次写告警

_running = True


def _handle_signal(sig, frame):
    global _running
    logger.info("收到停止信号，调度器即将退出...")
    _running = False


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── SQLite 直读 ──────────────────────────────────

def _safe_table_name(node_id: str) -> str:
    """将 node_id 转为 SQLite 表名（与 HistoryDB._safe_table_name 一致）"""
    safe = node_id.replace("=", "_eq_").replace(";", "_sc_").replace(".", "_dot_")
    return f"h_{safe}"


def read_latest_values(db_path: str, nodes: list[str]) -> list[dict]:
    """
    从 SQLite 读取每个节点的最新一条记录。
    返回格式与 batch_read API 一致：[{"node_id": ..., "value": ..., "quality": ...}, ...]
    """
    results = []
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        raise RuntimeError(f"无法连接 SQLite ({db_path}): {e}")

    try:
        for node_id in nodes:
            table_name = _safe_table_name(node_id)
            try:
                # 检查表是否存在
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                )
                if not cursor.fetchone():
                    results.append({"node_id": node_id, "value": None, "quality": "Bad"})
                    continue

                # 读最新一条
                cursor = conn.execute(
                    f"SELECT value, quality, timestamp FROM [{table_name}] ORDER BY id DESC LIMIT 1"
                )
                row = cursor.fetchone()
                if row:
                    results.append({
                        "node_id": node_id,
                        "value": round(float(row["value"]), 2) if row["value"] is not None else None,
                        "quality": row["quality"],
                    })
                else:
                    results.append({"node_id": node_id, "value": None, "quality": "Bad"})
            except Exception as e:
                logger.warning(f"读取 {node_id} 失败: {e}")
                results.append({"node_id": node_id, "value": None, "quality": "Bad"})
    finally:
        conn.close()

    return results


# ── 主逻辑 ────────────────────────────────────────

def warmup_subscriptions(nodes: list[str], timeout: int = 15) -> bool:
    """启动时通过桥接器 API 触发订阅，确保 SQLite 历史库持续写入。"""
    logger.info(f"预热订阅: 向 {BRIDGE_API_BASE} 发送 {len(nodes)} 个节点...")
    try:
        req = urllib.request.Request(
            f"{BRIDGE_API_BASE}/api/v1/nodes/batch-read",
            data=json.dumps(nodes).encode(),
            headers={"Content-Type": "application/json"},
        )
        r = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(r.read())
        good_count = sum(1 for item in data.get("results", []) if item.get("quality") == "Good")
        logger.info(f"预热订阅完成: {good_count}/{len(nodes)} 个节点已收到数据")
        return good_count > 0
    except urllib.error.URLError as e:
        logger.warning(f"预热订阅失败（桥接器不可用，将直读 SQLite）: {e.reason}")
        return False
    except Exception as e:
        logger.warning(f"预热订阅异常: {e}")
        return False


def run_once(db_path: str, nodes: list[str], output_file: Path) -> bool:
    """执行一次 SQLite 读取 + Excel 追加，返回是否成功"""
    try:
        values = read_latest_values(db_path, nodes)
        generate_excel(
            nodes=nodes,
            values=values,
            output_path=str(output_file),
        )
        return True
    except PermissionError as e:
        logger.warning(f"文件被占用，跳过本次: {e}")
        return False
    except Exception as e:
        logger.error(f"报表生成失败: {e}", exc_info=True)
        return False


def run_once_with_retry(db_path: str, nodes: list[str], output_file: Path) -> bool:
    """带重试的单次采集"""
    for attempt in range(1, MAX_RETRIES + 1):
        ok = run_once(db_path, nodes, output_file)
        if ok:
            return True
        if attempt < MAX_RETRIES:
            logger.info(f"第 {attempt}/{MAX_RETRIES} 次失败，{RETRY_INTERVAL}秒后重试...")
            time.sleep(RETRY_INTERVAL)
    return False


def main():
    parser = argparse.ArgumentParser(description="OPC UA 报表调度器（直读 SQLite 版）")
    parser.add_argument("--db", default=str(ROOT / "data" / "history.db"),
                        help="SQLite 数据库路径")
    parser.add_argument("--interval", type=float,
                        default=REPORT_CONFIG["schedule_interval_minutes"],
                        help="采集间隔（分钟），默认 1")
    parser.add_argument("--outdir", default=REPORT_CONFIG["output_dir"])
    parser.add_argument("--once", action="store_true",
                        help="只跑一次后退出")
    args = parser.parse_args()

    nodes = REPORT_CONFIG["fixed_nodes"]
    db_path = str(Path(args.db).resolve())
    output_dir = Path(args.outdir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / REPORT_CONFIG["output_filename"]

    # 检查数据库是否存在
    if not Path(db_path).exists():
        logger.error(f"SQLite 数据库不存在: {db_path}")
        logger.error("请先启动桥接器以创建历史数据库")
        sys.exit(1)

    logger.info(
        f"调度器启动 | 间隔: {args.interval}min | 节点: {len(nodes)}个 | "
        f"文件: {output_file} | 数据源: SQLite"
    )
    logger.info(f"数据库: {db_path}")
    logger.info(f"容错: 最多重试 {MAX_RETRIES} 次, 连续失败 {ALERT_THRESHOLD} 次告警")
    logger.info("按 Ctrl+C 停止")

    # 启动时预热订阅（确保桥接器持续写入 SQLite）
    warmup_subscriptions(nodes)

    if args.once:
        ok = run_once_with_retry(db_path, nodes, output_file)
        if not ok:
            logger.error("单次采集失败（已重试）")
            sys.exit(1)
        return

    start_time = time.time()
    success_count = 0
    fail_count = 0
    consecutive_fails = 0  # 连续失败计数

    while _running:
        ok = run_once_with_retry(db_path, nodes, output_file)
        if ok:
            success_count += 1
            if consecutive_fails > 0:
                logger.info(f"恢复正常（之前连续失败 {consecutive_fails} 次）")
            consecutive_fails = 0
        else:
            fail_count += 1
            consecutive_fails += 1

            # 连续失败告警
            if consecutive_fails == ALERT_THRESHOLD:
                logger.warning(
                    f"⚠️ 连续失败 {ALERT_THRESHOLD} 次！"
                    f"可能原因: SQLite 不可用 / Excel 被锁定 / 磁盘满"
                )
            elif consecutive_fails > ALERT_THRESHOLD and consecutive_fails % 10 == 0:
                # 每 10 次再告警一次，避免刷屏
                logger.warning(f"⚠️ 连续失败已持续 {consecutive_fails} 次，仍未恢复")

        # 等待下一次
        wait_seconds = int(args.interval * 60)
        for _ in range(wait_seconds):
            if not _running:
                break
            time.sleep(1)

    elapsed = (time.time() - start_time) / 60
    logger.info(
        f"调度器已停止 | 运行 {elapsed:.1f}min | "
        f"成功 {success_count} | 失败 {fail_count}"
    )


if __name__ == "__main__":
    main()
