"""
SQLite 历史数据存储模块
Author: WorkBuddy SRE
Version: 1.0.0

设计要点：
- 使用 aiosqlite 异步写入，不阻塞订阅回调
- 按 node_id 分表存储（每节点一张表），避免单表过大
- 自动清理过期数据（默认保留 7 天）
- 每次写入带 buffer，批量 INSERT 提升性能
"""

import aiosqlite
import os
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path

import structlog

logger = structlog.get_logger()

# 默认配置
DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "history.db")
DEFAULT_RETENTION_DAYS = 7
DEFAULT_FLUSH_INTERVAL = 5  # 秒，buffer 满 N 秒后写入
DEFAULT_FLUSH_SIZE = 100    # 条，buffer 满 N 条后写入


class HistoryDB:
    """SQLite 异步历史数据库"""

    DEFAULT_DB_PATH = DEFAULT_DB_PATH
    DEFAULT_RETENTION_DAYS = DEFAULT_RETENTION_DAYS

    def __init__(
        self,
        db_path: Optional[str] = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
        flush_size: int = DEFAULT_FLUSH_SIZE,
    ):
        self.db_path = db_path or self.DEFAULT_DB_PATH
        self.retention_days = retention_days
        self.flush_interval = flush_interval
        self.flush_size = flush_size

        self._db: Optional[aiosqlite.Connection] = None
        self._buffer: List[tuple] = []
        self._last_flush = time.monotonic()
        self._tables_created: set = set()

    # ------------------------------------------------------------------ #
    #  连接 / 初始化                                                        #
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        """初始化数据库连接和目录"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

        # 性能优化：WAL 模式 + 同步 NORMAL
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA cache_size=-20000")  # 20MB 缓存

        logger.info("history_db_connected", path=self.db_path)

    async def close(self) -> None:
        """关闭连接前刷新 buffer"""
        await self.flush()
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("history_db_closed")

    # ------------------------------------------------------------------ #
    #  表管理                                                              #
    # ------------------------------------------------------------------ #

    def _safe_table_name(self, node_id: str) -> str:
        """将 node_id 转为安全的 SQLite 表名"""
        # 替换特殊字符
        safe = node_id.replace("=", "_eq_").replace(";", "_sc_").replace(".", "_dot_")
        # 加前缀避免关键字冲突
        return f"h_{safe}"

    async def _ensure_table(self, node_id: str) -> str:
        """确保节点的历史表存在"""
        safe_name = self._safe_table_name(node_id)
        if safe_name in self._tables_created:
            return safe_name

        sql = f"""
            CREATE TABLE IF NOT EXISTS [{safe_name}] (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                value REAL,
                quality TEXT,
                source_timestamp TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_{safe_name}_ts ON [{safe_name}](timestamp);
        """
        await self._db.executescript(sql)
        self._tables_created.add(safe_name)
        return safe_name

    # ------------------------------------------------------------------ #
    #  写入（带 buffer）                                                    #
    # ------------------------------------------------------------------ #

    async def write(
        self,
        node_id: str,
        value: Any,
        quality: str,
        timestamp: str,
        source_timestamp: Optional[str] = None,
    ) -> None:
        """写入一条历史数据（先进 buffer，满足条件后批量刷盘）"""
        self._buffer.append((node_id, timestamp, value, quality, source_timestamp))

        # 检查是否该 flush
        now = time.monotonic()
        if (
            len(self._buffer) >= self.flush_size
            or (now - self._last_flush) >= self.flush_interval
        ):
            await self.flush()

    async def flush(self) -> None:
        """将 buffer 中的数据批量写入数据库"""
        if not self._buffer:
            return

        buffer_copy = self._buffer[:]
        self._buffer.clear()
        self._last_flush = time.monotonic()

        if not self._db:
            logger.warning("history_db_not_connected")
            return

        try:
            # 按 node_id 分组，批量写入各自表
            by_node: Dict[str, List[tuple]] = {}
            for node_id, ts, val, quality, src_ts in buffer_copy:
                by_node.setdefault(node_id, []).append((ts, val, quality, src_ts))

            for node_id, rows in by_node.items():
                table_name = await self._ensure_table(node_id)
                await self._db.executemany(
                    f"INSERT INTO [{table_name}] (timestamp, value, quality, source_timestamp) VALUES (?, ?, ?, ?)",
                    rows,
                )

            await self._db.commit()
            logger.debug("history_db_flushed", count=len(buffer_copy))

        except Exception as e:
            logger.error("history_db_flush_failed", error=str(e), count=len(buffer_copy))
            # 写回 buffer，下次重试
            self._buffer.extend(buffer_copy)

    # ------------------------------------------------------------------ #
    #  查询                                                                #
    # ------------------------------------------------------------------ #

    async def read_history(
        self,
        node_id: str,
        start_time: str,
        end_time: str,
        max_points: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        查询节点的历史数据
        - start_time / end_time: ISO 格式时间字符串
        - max_points: 最大返回条数
        """
        await self.flush()  # 先刷新 buffer 确保最新数据

        table_name = self._safe_table_name(node_id)
        # 检查表是否存在
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if not await cursor.fetchone():
            return []

        sql = f"""
            SELECT timestamp, value, quality, source_timestamp
            FROM [{table_name}]
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            LIMIT ?
        """
        cursor = await self._db.execute(sql, (start_time, end_time, max_points))
        rows = await cursor.fetchall()

        return [
            {
                "timestamp": row["timestamp"],
                "value": round(row["value"], 2) if row["value"] is not None else None,
                "quality": row["quality"],
                "source_timestamp": row["source_timestamp"],
            }
            for row in rows
        ]

    async def read_latest(self, node_id: str) -> Optional[Dict[str, Any]]:
        """读取节点的最新一条历史记录"""
        await self.flush()

        table_name = self._safe_table_name(node_id)
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if not await cursor.fetchone():
            return None

        cursor = await self._db.execute(
            f"SELECT timestamp, value, quality FROM [{table_name}] ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if not row:
            return None

        return {
            "timestamp": row["timestamp"],
            "value": round(row["value"], 2) if row["value"] is not None else None,
            "quality": row["quality"],
        }

    async def get_node_stats(self, node_id: str) -> Dict[str, Any]:
        """获取节点的历史数据统计"""
        table_name = self._safe_table_name(node_id)
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if not await cursor.fetchone():
            return {"count": 0, "first": None, "last": None}

        cursor = await self._db.execute(
            f"SELECT COUNT(*) as cnt, MIN(timestamp) as first_ts, MAX(timestamp) as last_ts FROM [{table_name}]"
        )
        row = await cursor.fetchone()
        return {
            "count": row["cnt"],
            "first": row["first_ts"],
            "last": row["last_ts"],
        }

    async def list_stored_nodes(self) -> List[Dict[str, Any]]:
        """列出所有有历史数据的节点"""
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'h_%'"
        )
        tables = await cursor.fetchall()

        nodes = []
        for row in tables:
            table_name = row["name"]
            cursor2 = await self._db.execute(
                f"SELECT COUNT(*) as cnt FROM [{table_name}]"
            )
            cnt_row = await cursor2.fetchone()
            # 反向解析 node_id（去掉 h_ 前缀）
            nodes.append({
                "table_name": table_name,
                "count": cnt_row["cnt"],
            })

        return sorted(nodes, key=lambda x: x["count"], reverse=True)

    # ------------------------------------------------------------------ #
    #  维护                                                                #
    # ------------------------------------------------------------------ #

    async def cleanup(self) -> int:
        """清理过期数据（超过 retention_days）"""
        cutoff = (datetime.now() - timedelta(days=self.retention_days)).isoformat()
        total_deleted = 0

        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'h_%'"
        )
        tables = await cursor.fetchall()

        for row in tables:
            table_name = row["name"]
            cursor2 = await self._db.execute(
                f"SELECT COUNT(*) as cnt FROM [{table_name}] WHERE timestamp < ?",
                (cutoff,),
            )
            cnt_row = await cursor2.fetchone()
            if cnt_row["cnt"] > 0:
                await self._db.execute(
                    f"DELETE FROM [{table_name}] WHERE timestamp < ?",
                    (cutoff,),
                )
                total_deleted += cnt_row["cnt"]

        await self._db.commit()
        if total_deleted > 0:
            logger.info("history_db_cleanup", deleted=total_deleted, cutoff=cutoff)

        return total_deleted

    async def get_db_size(self) -> int:
        """获取数据库文件大小（字节）"""
        try:
            return os.path.getsize(self.db_path)
        except FileNotFoundError:
            return 0
