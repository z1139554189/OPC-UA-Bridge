"""
SQLite → 云 MySQL 历史数据迁移/推送脚本
从本地 SQLite 分表读取历史数据，批量写入云 MySQL

用法：
  python cloud/sqlite_to_cloud.py              # 推送所有数据
  python cloud/sqlite_to_cloud.py --limit 100  # 每个节点只推送最新 100 条
"""

import asyncio
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pymysql
from cloud.cloud_config import CLOUD_MYSQL

SQLITE_DB = Path(__file__).parent.parent / "data" / "history.db"


def read_sqlite_tables():
    """获取所有 h_ 前缀的分表"""
    conn = sqlite3.connect(str(SQLITE_DB))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'h_%'")
    tables = [row[0] for row in cur.fetchall()]
    conn.close()
    return tables


def read_sqlite_data(table_name: str, limit: int = 0):
    """从 SQLite 分表读取数据"""
    conn = sqlite3.connect(str(SQLITE_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if limit > 0:
        sql = f"SELECT timestamp, value, quality, source_timestamp FROM [{table_name}] ORDER BY id DESC LIMIT ?"
        cur.execute(sql, (limit,))
        rows = list(reversed(cur.fetchall()))  # 翻转回时间正序
    else:
        sql = f"SELECT timestamp, value, quality, source_timestamp FROM [{table_name}] ORDER BY timestamp ASC"
        cur.execute(sql)
        rows = cur.fetchall()

    conn.close()
    return rows


def table_name_to_node_id(table_name: str) -> str:
    """将 SQLite 安全表名还原为 node_id"""
    # h_ns_eq_1_sc_s_eq_FIT_05R201F01_dot_PV -> ns=1;s=FIT_05R201F01.PV
    node = table_name[2:]  # 去掉 h_ 前缀
    node = node.replace("_eq_", "=").replace("_sc_", ";").replace("_dot_", ".")
    return node


def push_to_cloud(records: list) -> bool:
    """批量推送到云 MySQL"""
    if not records:
        return True

    try:
        conn = pymysql.connect(
            host=CLOUD_MYSQL["host"],
            port=CLOUD_MYSQL["port"],
            user=CLOUD_MYSQL["user"],
            password=CLOUD_MYSQL["password"],
            database=CLOUD_MYSQL["database"],
            connect_timeout=10,
        )
        cur = conn.cursor()

        # 推送到 node_history
        history_sql = """
            INSERT IGNORE INTO node_history (node_id, timestamp, value, quality, source_timestamp)
            VALUES (%s, %s, %s, %s, %s)
        """

        # 推送到 node_latest（UPSERT）
        latest_sql = """
            INSERT INTO node_latest (node_id, value, quality, timestamp)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                value = VALUES(value),
                quality = VALUES(quality),
                timestamp = VALUES(timestamp),
                updated_at = NOW(3)
        """

        history_rows = []
        latest_rows = {}  # 按 node_id 去重，保留最新

        for node_id, ts, value, quality, source_ts in records:
            history_rows.append((node_id, ts, value, quality, source_ts))
            # latest 只保留每个 node_id 的最新一条
            if node_id not in latest_rows or ts > latest_rows[node_id][2]:
                latest_rows[node_id] = (node_id, value, quality, ts)

        # 批量写入 node_history
        if history_rows:
            cur.executemany(history_sql, history_rows)

        # 写入 node_latest
        for row in latest_rows.values():
            cur.execute(latest_sql, row)

        conn.commit()

        # 记录推送日志
        cur.execute("""
            INSERT INTO push_log (node_count, record_count, status, duration_ms)
            VALUES (%s, %s, %s, %s)
        """, (len(latest_rows), len(history_rows), "success", 0))

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        print(f"  [ERROR] 推送失败: {e}")
        # 记录失败日志
        try:
            conn2 = pymysql.connect(
                host=CLOUD_MYSQL["host"],
                port=CLOUD_MYSQL["port"],
                user=CLOUD_MYSQL["user"],
                password=CLOUD_MYSQL["password"],
                database=CLOUD_MYSQL["database"],
                connect_timeout=10,
            )
            cur2 = conn2.cursor()
            cur2.execute("""
                INSERT INTO push_log (node_count, record_count, status, error_msg, duration_ms)
                VALUES (%s, %s, %s, %s, %s)
            """, (0, 0, "failed", str(e)[:500], 0))
            conn2.commit()
            conn2.close()
        except:
            pass
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SQLite → 云 MySQL 数据推送")
    parser.add_argument("--limit", type=int, default=0, help="每节点推送条数限制，0=全部")
    args = parser.parse_args()

    if not SQLITE_DB.exists():
        print(f"[ERROR] SQLite 数据库不存在: {SQLITE_DB}")
        sys.exit(1)

    print("=" * 60)
    print("SQLite → 云 MySQL 数据推送")
    print(f"SQLite: {SQLITE_DB}")
    print(f"MySQL:  {CLOUD_MYSQL['host']}:{CLOUD_MYSQL['port']}/{CLOUD_MYSQL['database']}")
    print("=" * 60)

    # 1. 测试 MySQL 连接
    print("\n[1/3] 测试 MySQL 连接...")
    try:
        conn = pymysql.connect(
            host=CLOUD_MYSQL["host"],
            port=CLOUD_MYSQL["port"],
            user=CLOUD_MYSQL["user"],
            password=CLOUD_MYSQL["password"],
            database=CLOUD_MYSQL["database"],
            connect_timeout=10,
        )
        conn.close()
        print("  [OK] MySQL 连接成功")
    except Exception as e:
        print(f"  [FAIL] MySQL 连接失败: {e}")
        sys.exit(1)

    # 2. 读取 SQLite 数据
    print("\n[2/3] 读取 SQLite 数据...")
    tables = read_sqlite_tables()
    if not tables:
        print("  没有历史数据")
        sys.exit(0)

    all_records = []
    for table_name in tables:
        node_id = table_name_to_node_id(table_name)
        rows = read_sqlite_data(table_name, limit=args.limit)
        for row in rows:
            all_records.append((
                node_id,
                row["timestamp"],
                round(row["value"], 2) if row["value"] is not None else None,
                row["quality"] or "Good",
                row["source_timestamp"],
            ))
        print(f"  {node_id}: {len(rows)} 条")

    print(f"  合计: {len(all_records)} 条记录")

    # 3. 推送到云 MySQL
    print(f"\n[3/3] 推送到云 MySQL...")
    start = datetime.now()
    success = push_to_cloud(all_records)
    elapsed = (datetime.now() - start).total_seconds() * 1000

    if success:
        print(f"  [OK] 推送成功! {len(all_records)} 条记录, 耗时 {elapsed:.0f}ms")
    else:
        print(f"  [FAIL] 推送失败")
        sys.exit(1)

    # 4. 验证
    print("\n[验证] 查询云端数据...")
    try:
        conn = pymysql.connect(
            host=CLOUD_MYSQL["host"],
            port=CLOUD_MYSQL["port"],
            user=CLOUD_MYSQL["user"],
            password=CLOUD_MYSQL["password"],
            database=CLOUD_MYSQL["database"],
            connect_timeout=10,
        )
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM node_history")
        print(f"  node_history 总记录: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM node_latest")
        print(f"  node_latest 节点数:  {cur.fetchone()[0]}")
        cur.execute("SELECT node_id, value, quality, timestamp FROM node_latest LIMIT 5")
        print("  最新数据（前5条）:")
        for row in cur.fetchall():
            print(f"    {row[0]}: value={row[1]}, quality={row[2]}, ts={row[3]}")
        cur.execute("SELECT * FROM push_log ORDER BY id DESC LIMIT 1")
        log = cur.fetchone()
        print(f"  推送日志: status={log[5]}, records={log[3]}, nodes={log[2]}")
        conn.close()
    except Exception as e:
        print(f"  验证查询失败: {e}")


if __name__ == "__main__":
    main()
