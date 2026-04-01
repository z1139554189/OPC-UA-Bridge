"""
云端数据推送模块
将本地 OPC UA 数据定时推送到腾讯云 TDSQL-C MySQL

集成方式：在 client.py 的 _poll_cache_to_history 中调用
也可独立运行：python -m cloud.cloud_pusher
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any, List

import structlog

logger = structlog.get_logger()


class CloudPusher:
    """
    异步推送到云 MySQL。
    
    设计要点：
    - 与本地 SQLite 写入解耦，推送失败不影响本地数据
    - 批量 INSERT + UPSERT，每 60 秒推送一次
    - 使用连接池复用连接
    """

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        push_interval: float = 60.0,
        batch_size: int = 500,
        retry_times: int = 3,
        retry_delay: float = 5.0,
    ):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.push_interval = push_interval
        self.batch_size = batch_size
        self.retry_times = retry_times
        self.retry_delay = retry_delay

        self._pool = None
        self._buffer: List[tuple] = []  # (node_id, timestamp, value, quality, source_timestamp)
        self._last_push = time.monotonic()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ #
    #  连接管理                                                            #
    # ------------------------------------------------------------------ #

    async def connect(self) -> bool:
        """创建连接池"""
        try:
            import aiomysql

            self._pool = await aiomysql.create_pool(
                host=self.host,
                port=self.port,
                db=self.database,
                user=self.user,
                password=self.password,
                minsize=1,
                maxsize=3,
                autocommit=True,
                charset='utf8mb4',
                connect_timeout=10,
            )
            logger.info("cloud_pusher_connected", host=self.host, db=self.database)
            return True
        except ImportError:
            logger.error("cloud_pusher_aiomysql_missing", hint="pip install aiomysql")
            return False
        except Exception as e:
            logger.error("cloud_pusher_connect_failed", host=self.host, error=str(e))
            return False

    async def close(self):
        """关闭前推送剩余 buffer，然后关闭连接池"""
        await self.push()
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.info("cloud_pusher_closed")

    # ------------------------------------------------------------------ #
    #  数据写入（先进 buffer）                                               #
    # ------------------------------------------------------------------ #

    def enqueue(self, node_id: str, value: Any, quality: str,
                timestamp: str, source_timestamp: Optional[str] = None):
        """将一条数据加入推送 buffer"""
        self._buffer.append((node_id, timestamp, value, quality, source_timestamp))

    async def flush(self):
        """触发推送（外部可调用，用于定时 flush）"""
        now = time.monotonic()
        if (
            len(self._buffer) >= self.batch_size
            or (now - self._last_push) >= self.push_interval
        ):
            await self.push()

    # ------------------------------------------------------------------ #
    #  批量推送                                                            #
    # ------------------------------------------------------------------ #

    async def push(self) -> bool:
        """将 buffer 中的数据批量推送到云 MySQL"""
        if not self._buffer or not self._pool:
            return True

        buffer_copy = self._buffer[:]
        self._buffer.clear()
        self._last_push = time.monotonic()

        start = time.monotonic()

        for attempt in range(self.retry_times):
            try:
                async with self._pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        # 1. 批量写入历史数据
                        history_sql = """
                            INSERT INTO node_history 
                            (node_id, timestamp, value, quality, source_timestamp)
                            VALUES (%s, %s, %s, %s, %s)
                        """
                        await cur.executemany(history_sql, buffer_copy)

                        # 2. 更新实时快照（UPSERT）
                        upsert_sql = """
                            INSERT INTO node_latest (node_id, value, quality, timestamp)
                            VALUES (%s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE 
                                value = VALUES(value),
                                quality = VALUES(quality),
                                timestamp = VALUES(timestamp),
                                updated_at = NOW(3)
                        """
                        # 去重取每个 node_id 最新的一条
                        latest_map: Dict[str, tuple] = {}
                        for nid, ts, val, quality, src_ts in buffer_copy:
                            if nid not in latest_map or ts > latest_map[nid][0]:
                                latest_map[nid] = (ts, val, quality)
                        
                        upsert_rows = [
                            (nid, val, q, ts)
                            for nid, (ts, val, q) in latest_map.items()
                        ]
                        if upsert_rows:
                            await cur.executemany(upsert_sql, upsert_rows)

                        # 3. 记录推送日志
                        duration_ms = int((time.monotonic() - start) * 1000)
                        await cur.execute(
                            """INSERT INTO push_log 
                               (node_count, record_count, status, duration_ms)
                               VALUES (%s, %s, 'success', %s)""",
                            (len(latest_map), len(buffer_copy), duration_ms),
                        )

                        logger.debug(
                            "cloud_push_success",
                            records=len(buffer_copy),
                            nodes=len(latest_map),
                            duration_ms=duration_ms,
                        )
                        return True

            except Exception as e:
                logger.warning(
                    "cloud_push_retry",
                    attempt=attempt + 1,
                    max_retry=self.retry_times,
                    error=str(e),
                )
                if attempt < self.retry_times - 1:
                    await asyncio.sleep(self.retry_delay)

        # 全部重试失败，写回 buffer
        self._buffer.extend(buffer_copy)
        logger.error("cloud_push_failed", count=len(buffer_copy))
        return False

    # ------------------------------------------------------------------ #
    #  后台推送任务                                                         #
    # ------------------------------------------------------------------ #

    async def start_background(self):
        """启动后台定时推送任务"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._background_loop())
        logger.info("cloud_pusher_started", interval=self.push_interval)

    async def stop_background(self):
        """停止后台推送任务"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("cloud_pusher_stopped")

    async def _background_loop(self):
        """后台循环：定时检查 buffer 并推送"""
        while self._running:
            try:
                await asyncio.sleep(self.push_interval)
                if not self._running:
                    break
                await self.push()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("cloud_pusher_loop_error", error=str(e))
                await asyncio.sleep(self.push_interval)
