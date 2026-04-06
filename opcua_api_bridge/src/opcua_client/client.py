"""
OPC UA 客户端核心模块（自适应采集模式 + 推送超时检测）
Author: WorkBuddy SRE
Version: 7.0.0

关键设计：
- 默认保持长连接 + 订阅，数据持续推送（秒级），全部写入 SQLite
- 检测到 Session 被占用（BadTooManySessions）时自动让出
- 每 YIELD_RECHECK_INTERVAL 秒尝试重连，客户端断开后自动恢复
- 推送超时检测：N 秒内未收到任何推送 → 视为连接异常 → 断开重连
- 心跳写入：定期将缓存值写入 SQLite（source=heartbeat），保证时间序列连续
- API 层只从内存缓存读取，无阻塞
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Set

from asyncua import Client as AsyncClient, ua
import structlog
from prometheus_client import Counter, Histogram, Gauge

from src.config.settings import settings
from src.storage import HistoryDB

logger = structlog.get_logger()

# 缓存数据过期时间（秒），超过此时间缓存数据视为过期
CACHE_STALE_TIMEOUT = 120

# Session 被占用后的重试间隔（秒）
YIELD_RECHECK_INTERVAL = 30

# 推送超时：N 秒内未收到任何 datachange_notification → 视为连接异常
PUSH_TIMEOUT = 300

# 心跳间隔：每 N 秒将缓存值强制写入 SQLite（保证时间序列连续）
HEARTBEAT_INTERVAL = 10

# 订阅推送数据写入 SQLite 的批量 buffer 配置
WRITE_BUFFER_SIZE = 50
WRITE_BUFFER_TIMEOUT = 2  # 秒

# 浏览节点临时连接的超时（秒）
BROWSE_TIMEOUT = 15

# Prometheus 指标
OPC_CONNECTION_STATUS = Gauge(
    'opc_connection_status',
    'OPC UA 连接状态 (1=已连接, 0=断开)'
)
OPC_REQUEST_COUNT = Counter(
    'opc_request_total',
    'OPC UA 请求总数',
    ['operation']
)
OPC_REQUEST_DURATION = Histogram(
    'opc_request_duration_seconds',
    'OPC UA 请求耗时',
    ['operation']
)
OPC_NODE_COUNT = Gauge(
    'opc_node_count',
    'OPC UA 已订阅节点数量'
)
OPC_COLLECT_COUNT = Counter(
    'opc_collect_total',
    'OPC UA 采集循环次数',
    ['status']
)
OPC_YIELD_COUNT = Counter(
    'opc_yield_total',
    'OPC UA Session 让出次数'
)
OPC_RESUME_COUNT = Counter(
    'opc_resume_total',
    'OPC UA 采集恢复次数'
)
OPC_PUSH_TIMEOUT_COUNT = Counter(
    'opc_push_timeout_total',
    'OPC UA 推送超时次数（连接异常）'
)
OPC_HEARTBEAT_COUNT = Counter(
    'opc_heartbeat_total',
    'OPC UA 心跳写入次数'
)


class OPCQuality:
    GOOD = "Good"
    UNCERTAIN = "Uncertain"
    BAD = "Bad"
    ERROR = "Error"


class _SubHandler:
    """asyncua 订阅回调：持续收集推送值，写入 buffer 待批量落库。"""

    def __init__(self, buffer: List[Dict[str, Any]], client_ref: 'OPCUAClient'):
        self._buffer = buffer
        self._client_ref = client_ref

    async def datachange_notification(self, node, val, data):
        node_id = node.nodeid.to_string()

        try:
            sc = data.monitored_item.Value.StatusCode
            is_good = sc.is_good()
        except Exception:
            is_good = val is not None

        if not is_good:
            return  # 只缓存 Good 数据

        now_iso = datetime.now().isoformat()
        quality = OPCQuality.GOOD

        self._buffer.append({
            "node_id": node_id,
            "value": val,
            "quality": quality,
            "timestamp": now_iso,
            "source": "push",  # 标记为推送数据
        })

        # 更新最后推送时间（核心：用于判断连接是否真正存活）
        self._client_ref._last_push_time = time.time()


class OPCUAClient:
    """
    OPC UA 客户端（自适应采集模式 + 推送超时检测）。

    - 默认保持长连接 + 订阅，数据持续推送（秒级）
    - 检测到 Session 被占用时自动让出，客户端断开后自动恢复
    - 推送超时 N 秒 → 视为连接异常 → 断开重连
    - 心跳写入：每 N 秒将缓存值写入 SQLite（source=heartbeat）
    - API 层只从内存缓存读取
    """

    def __init__(
        self,
        endpoint: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        security_policy: Optional[str] = None,
        security_mode: Optional[str] = None,
        history_db_path: Optional[str] = None,
        history_retention_days: int = 7,
    ):
        self.endpoint = endpoint
        self.username = username or ""
        self.password = password or ""

        # 缓存（最新值，API 层只读）
        self._value_cache: Dict[str, Dict[str, Any]] = {}

        # 需要采集的节点列表
        self._collect_nodes: Set[str] = set()

        # OPC UA 客户端实例
        self._client: Optional[AsyncClient] = None
        self._subscription = None

        # 采集循环控制
        self._collect_task: Optional[asyncio.Task] = None
        self._flush_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_collect_time: Optional[float] = None

        # 推送超时检测：最后一次收到 datachange_notification 的时间
        self._last_push_time: float = 0  # 0 = 从未收到过推送

        # 让出 / 恢复状态
        self._yielded = False  # 是否因 Session 被占用而让出
        self._consecutive_failures = 0
        self._max_failures_before_yield = 3  # 连续失败 N 次后让出

        # 推送数据 buffer（批量写入 SQLite）
        self._write_buffer: List[Dict[str, Any]] = []
        self._buffer_lock = asyncio.Lock()
        self._buffer_event = asyncio.Event()

        # SQLite 历史库
        self.history_db = HistoryDB(
            db_path=history_db_path or HistoryDB.DEFAULT_DB_PATH,
            retention_days=history_retention_days,
        )

    # ------------------------------------------------------------------ #
    #  生命周期                                                             #
    # ------------------------------------------------------------------ #

    async def start(self) -> bool:
        """启动采集（自适应模式）。"""
        try:
            await self.history_db.connect()
        except Exception as e:
            logger.error("history_db_connect_failed", error=str(e))
            return False

        self._running = True

        # 启动批量写入任务
        self._flush_task = asyncio.create_task(self._flush_loop())

        # 启动心跳写入任务
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # 启动采集循环
        self._collect_task = asyncio.create_task(self._collect_loop())
        logger.info("adaptive_collect_started", mode="default_subscribe",
                    push_timeout=PUSH_TIMEOUT,
                    heartbeat_interval=HEARTBEAT_INTERVAL,
                    yield_recheck=YIELD_RECHECK_INTERVAL)
        return True

    async def stop(self):
        """停止采集。"""
        self._running = False

        # 取消采集循环
        if self._collect_task and not self._collect_task.done():
            self._collect_task.cancel()
            try:
                await self._collect_task
            except asyncio.CancelledError:
                pass
            self._collect_task = None

        # 取消心跳循环
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # 取消 flush 循环
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        # 最终 flush
        await self._flush_buffer()

        # 断开 OPC UA
        await self._disconnect_opc()

        OPC_CONNECTION_STATUS.set(0)
        await self.history_db.close()
        logger.info("adaptive_collect_stopped")

    # ------------------------------------------------------------------ #
    #  节点注册（由 API 层调用）                                              #
    # ------------------------------------------------------------------ #

    def add_nodes(self, node_ids: List[str]):
        """注册需要采集的节点。"""
        new_ids = [nid for nid in node_ids if nid not in self._collect_nodes]
        if new_ids:
            self._collect_nodes.update(new_ids)
            OPC_NODE_COUNT.set(len(self._collect_nodes))
            logger.info("nodes_registered", count=len(new_ids), total=len(self._collect_nodes))

            # 如果已连接，动态添加新节点到现有订阅
            if self._subscription and self._client:
                asyncio.create_task(self._add_nodes_to_subscription(new_ids))

    async def _add_nodes_to_subscription(self, node_ids: List[str]):
        """动态添加节点到现有订阅（不重建连接）。"""
        try:
            nodes = [self._client.get_node(nid) for nid in node_ids]
            await self._subscription.subscribe_data_change(nodes)
            logger.info("nodes_added_to_subscription", count=len(node_ids))
        except Exception as e:
            logger.warning("add_nodes_to_sub_failed", error=str(e))

    # ------------------------------------------------------------------ #
    #  OPC UA 连接 / 断开                                                    #
    # ------------------------------------------------------------------ #

    async def _connect_opc(self) -> bool:
        """建立 OPC UA 连接并创建订阅。"""
        try:
            client = AsyncClient(self.endpoint)
            if self.username:
                client.set_user(self.username)
            if self.password:
                client.set_password(self.password)
            client.session_timeout = 60000

            await client.connect()
            self._client = client

            # 创建订阅 + handler（传入 self 引用，用于更新 _last_push_time）
            handler = _SubHandler(self._write_buffer, self)
            self._subscription = await client.create_subscription(500, handler)

            # 订阅所有已注册节点
            if self._collect_nodes:
                nodes = [client.get_node(nid) for nid in self._collect_nodes]
                await self._subscription.subscribe_data_change(nodes)
                logger.info("opc_subscribed", count=len(self._collect_nodes))

            # 连接成功，重置推送超时计时
            # 注意：不在这里设置 _last_push_time，等真正的推送来更新
            OPC_CONNECTION_STATUS.set(1)
            return True

        except Exception as e:
            err_str = str(e).lower()
            is_session_limit = "session" in err_str and ("too many" in err_str or "max" in err_str)

            if is_session_limit:
                logger.warning("session_occupied",
                             error=str(e),
                             message="客户端正在使用 Session，自动让出")
            else:
                logger.error("opc_connect_failed", error=str(e))

            self._client = None
            self._subscription = None
            OPC_CONNECTION_STATUS.set(0)
            return False

    async def _disconnect_opc(self):
        """断开 OPC UA 连接。"""
        try:
            if self._subscription:
                await self._subscription.delete()
                self._subscription = None
        except Exception:
            pass

        try:
            if self._client:
                await self._client.disconnect()
                self._client = None
        except Exception:
            pass

        OPC_CONNECTION_STATUS.set(0)

    # ------------------------------------------------------------------ #
    #  自适应采集循环                                                        #
    # ------------------------------------------------------------------ #

    async def _collect_loop(self):
        """
        自适应采集循环：
        - 默认：保持长连接 + 订阅，数据持续推送
        - 推送超时：N 秒内没收到任何推送 → 视为连接异常 → 断开重连
        - 让出：检测到 Session 被占用，断开并定期重试
        - 恢复：重试成功后恢复长连接采集
        """
        # 初始连接成功后的宽限期：给 OPC 服务器几秒时间推送第一批数据
        initial_grace = 15  # 首次连接后等 15 秒才开始检测推送超时
        connected_at = 0

        while self._running:
            try:
                # ---- 已让出状态：定期尝试重连 ----
                if self._yielded:
                    await asyncio.sleep(YIELD_RECHECK_INTERVAL)
                    if not self._running:
                        break

                    logger.info("yield_recheck",
                               message="尝试重连，检查客户端是否已断开")
                    success = await self._connect_opc()
                    if success:
                        self._yielded = False
                        self._consecutive_failures = 0
                        connected_at = time.time()
                        OPC_RESUME_COUNT.inc()
                        logger.info("collect_resumed",
                                   message="Session 已空闲，恢复高速采集")
                    continue

                # ---- 正常状态：保持连接 ----
                if self._client is None:
                    # 首次连接或连接丢失后重连
                    success = await self._connect_opc()
                    if not success:
                        self._consecutive_failures += 1

                        if self._consecutive_failures >= self._max_failures_before_yield:
                            self._yielded = True
                            OPC_YIELD_COUNT.inc()
                            logger.warning("session_yielded",
                                         consecutive_failures=self._consecutive_failures,
                                         message="连续失败，让出 Session，等待客户端释放")
                        else:
                            await asyncio.sleep(5)  # 短暂重试
                        continue

                    # 连接成功，重置计数器和宽限期
                    self._consecutive_failures = 0
                    connected_at = time.time()
                    logger.info("opc_connected",
                               registered_nodes=len(self._collect_nodes))

                # ---- 已连接：检查推送是否正常 ----
                await asyncio.sleep(2)

                if not self._running:
                    break

                # 检查 session 对象是否还在
                if not (hasattr(self._client, 'session') and self._client.session):
                    logger.warning("session_lost", message="Session 丢失，准备重连")
                    self._client = None
                    self._subscription = None
                    OPC_CONNECTION_STATUS.set(0)
                    continue

                # 检查推送超时（核心：基于真实推送数据，而不是 session 对象）
                if self._last_push_time > 0:
                    # 已经收到过推送，检查是否超时
                    push_age = time.time() - self._last_push_time
                    if push_age > PUSH_TIMEOUT:
                        OPC_PUSH_TIMEOUT_COUNT.inc()
                        logger.warning("push_timeout",
                                      push_age_seconds=int(push_age),
                                      push_timeout=PUSH_TIMEOUT,
                                      message=f"{PUSH_TIMEOUT} 秒内未收到推送，视为连接异常，断开重连")
                        # 主动断开，触发重连
                        await self._disconnect_opc()
                        self._client = None
                        self._subscription = None
                        self._last_push_time = 0  # 重置
                        continue
                else:
                    # 还没收到过推送（刚连上），给一个宽限期
                    if (time.time() - connected_at) > initial_grace:
                        logger.warning("push_never_received",
                                      grace_seconds=initial_grace,
                                      message=f"连接后 {initial_grace} 秒内未收到任何推送，断开重连")
                        OPC_PUSH_TIMEOUT_COUNT.inc()
                        await self._disconnect_opc()
                        self._client = None
                        self._subscription = None
                        continue

                # 一切正常
                OPC_COLLECT_COUNT.labels("active").inc()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("collect_loop_error", error=str(e))
                OPC_COLLECT_COUNT.labels("error").inc()
                self._client = None
                self._subscription = None
                OPC_CONNECTION_STATUS.set(0)
                await asyncio.sleep(5)

    # ------------------------------------------------------------------ #
    #  心跳写入（保证时间序列连续）                                           #
    # ------------------------------------------------------------------ #

    async def _heartbeat_loop(self):
        """
        定期将当前缓存值写入 SQLite，标记 source=heartbeat。
        保证时间序列连续，且能区分真实变化和死值。
        只在连接正常且有缓存数据时才写心跳。
        """
        while self._running:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            # 只在连接正常时才写心跳
            if self._yielded or self._client is None:
                continue

            # 推送超时期间不写心跳（说明连接已经异常了）
            if self._last_push_time > 0:
                push_age = time.time() - self._last_push_time
                if push_age > PUSH_TIMEOUT:
                    continue  # 推送已超时，不写心跳

            # 将缓存中的值写入 SQLite，标记为 heartbeat
            if not self._value_cache:
                continue

            now_iso = datetime.now().isoformat()
            heartbeat_batch = []

            async with self._buffer_lock:
                for node_id, cached in self._value_cache.items():
                    # 跳过最近 3 秒内刚被推送更新的值（避免心跳和推送重复）
                    try:
                        cached_ts = datetime.fromisoformat(cached["timestamp"])
                        if (datetime.now() - cached_ts).total_seconds() < 3:
                            continue
                    except Exception:
                        pass

                    heartbeat_batch.append({
                        "node_id": node_id,
                        "value": cached["value"],
                        "quality": cached["quality"],
                        "timestamp": now_iso,
                        "source": "heartbeat",
                    })

            if heartbeat_batch:
                async with self._buffer_lock:
                    self._write_buffer.extend(heartbeat_batch)
                    self._buffer_event.set()

                OPC_HEARTBEAT_COUNT.inc()
                logger.debug("heartbeat_written",
                           nodes=len(heartbeat_batch),
                           interval=HEARTBEAT_INTERVAL)

    # ------------------------------------------------------------------ #
    #  批量写入 SQLite                                                      #
    # ------------------------------------------------------------------ #

    async def _flush_loop(self):
        """后台定期 flush buffer 到 SQLite。"""
        while self._running:
            try:
                await asyncio.wait_for(self._buffer_event.wait(), timeout=WRITE_BUFFER_TIMEOUT)
                self._buffer_event.clear()
            except asyncio.TimeoutError:
                pass

            if self._write_buffer:
                await self._flush_buffer()

    async def _flush_buffer(self):
        """将 buffer 中的数据批量写入 SQLite 和缓存。"""
        async with self._buffer_lock:
            if not self._write_buffer:
                return

            batch = self._write_buffer[:]
            self._write_buffer.clear()

        # 更新缓存（只保留最新值，推送数据优先于心跳）
        for item in batch:
            # 推送数据总是覆盖心跳，心跳不覆盖推送
            existing = self._value_cache.get(item["node_id"])
            source = item.get("source", "push")
            existing_source = existing.get("source", "push") if existing else "push"

            if source == "push" or existing is None:
                self._value_cache[item["node_id"]] = {
                    "value": item["value"],
                    "quality": item["quality"],
                    "timestamp": item["timestamp"],
                    "source": source,
                }
            # source == "heartbeat" 且 existing 是 push → 不覆盖，跳过

        self._last_collect_time = time.time()

        # 写入 SQLite
        written = 0
        for item in batch:
            val = item.get("value")
            if val is None:
                continue
            try:
                await self.history_db.write(
                    node_id=item["node_id"],
                    value=float(val) if not isinstance(val, (int, float)) else val,
                    quality=item["quality"],
                    timestamp=item["timestamp"],
                    source=item.get("source", "push"),
                )
                written += 1
            except Exception as e:
                logger.warning("history_write_failed",
                             node_id=item["node_id"], error=str(e))

        if written > 0:
            logger.debug("history_db_flushed", count=written)

    # ------------------------------------------------------------------ #
    #  连接状态 / 缓存新鲜度                                                  #
    # ------------------------------------------------------------------ #

    def is_connected(self) -> bool:
        """返回是否有活跃的 OPC UA 连接。"""
        if self._yielded:
            return False
        if self._client is None:
            return False
        # 检查推送是否超时
        if self._last_push_time > 0:
            if (time.time() - self._last_push_time) > PUSH_TIMEOUT:
                return False
        return True

    def is_yielded(self) -> bool:
        """返回是否因 Session 被占用而让出。"""
        return self._yielded

    def get_cache_freshness(self) -> int:
        """返回上次数据更新距今的秒数。"""
        if not self._last_collect_time:
            return -1
        return int(time.time() - self._last_collect_time)

    def get_collect_status(self) -> str:
        """返回采集状态描述。"""
        if self._yielded:
            return "yielded"
        if self._client is None:
            return "reconnecting"
        # 检查推送是否超时
        if self._last_push_time > 0:
            if (time.time() - self._last_push_time) > PUSH_TIMEOUT:
                return "push_timeout"
        elif self._last_push_time == 0:
            # 已连接但还没收到过推送
            return "waiting_first_push"
        return "active"

    # ------------------------------------------------------------------ #
    #  浏览节点（临时短连接）                                                  #
    # ------------------------------------------------------------------ #

    async def browse_nodes(
        self,
        node_id: Optional[str] = None,
        namespace: Optional[int] = None,
        recursive: bool = False,
        max_nodes: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """浏览节点列表（临时短连接，用完即释放 Session）。"""
        OPC_REQUEST_COUNT.labels("browse").inc()
        t0 = time.time()

        client: Optional[AsyncClient] = None
        try:
            client = AsyncClient(self.endpoint)
            if self.username:
                client.set_user(self.username)
            if self.password:
                client.set_password(self.password)
            client.session_timeout = 60000
            await client.connect()

            start_node = (
                client.get_node(node_id)
                if node_id
                else client.get_objects_node()
            )
            children = await start_node.get_children()
            paginated = children[offset: offset + max_nodes]

            nodes = []
            for child in paginated:
                try:
                    nid = child.nodeid.to_string()
                    ns = child.nodeid.NamespaceIndex

                    if namespace is not None and ns != namespace:
                        continue

                    try:
                        browse_name = (await child.read_browse_name()).Name
                    except Exception:
                        browse_name = nid

                    try:
                        display_name = (await child.read_display_name()).Text
                    except Exception:
                        display_name = browse_name

                    try:
                        node_class = str(await child.read_node_class())
                    except Exception:
                        node_class = "Unknown"

                    cached = self._value_cache.get(nid)
                    node_info: Dict[str, Any] = {
                        "node_id": nid,
                        "namespace": ns,
                        "browse_name": browse_name,
                        "display_name": display_name,
                        "node_class": node_class,
                    }
                    if cached:
                        node_info["value"] = cached.get("value")
                        node_info["quality"] = cached.get("quality")
                        node_info["timestamp"] = cached.get("timestamp")

                    nodes.append(node_info)

                except Exception as e:
                    logger.warning("browse_child_failed", error=str(e))
                    continue

            OPC_REQUEST_DURATION.labels("browse").observe(time.time() - t0)
            return nodes

        except Exception as e:
            logger.error("browse_failed", error=str(e))
            raise
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    #  读值（从缓存读取）                                                     #
    # ------------------------------------------------------------------ #

    async def read_value(self, node_id: str) -> Dict[str, Any]:
        """读取节点当前值（从缓存读取，并注册到采集列表）。"""
        OPC_REQUEST_COUNT.labels("read_value").inc()

        self.add_nodes([node_id])

        cached = self._value_cache.get(node_id)
        if cached:
            return {
                "node_id": node_id,
                "value": cached["value"],
                "quality": cached["quality"],
                "timestamp": cached["timestamp"],
                "data_type": type(cached["value"]).__name__ if cached["value"] is not None else "None",
                "source": cached.get("source", "cache"),
            }

        return {
            "node_id": node_id,
            "value": None,
            "quality": OPCQuality.BAD,
            "timestamp": datetime.now().isoformat(),
            "data_type": "None",
            "source": "not_yet_collected",
        }

    async def batch_read(self, node_ids: List[str]) -> List[Dict[str, Any]]:
        """批量读取节点值（从缓存读取，并注册到采集列表）。"""
        OPC_REQUEST_COUNT.labels("batch_read").inc()
        self.add_nodes(node_ids)

        results = []
        for nid in node_ids:
            cached = self._value_cache.get(nid)
            if cached:
                results.append({
                    "node_id": nid,
                    "value": cached["value"],
                    "quality": cached["quality"],
                    "timestamp": cached["timestamp"],
                    "data_type": type(cached["value"]).__name__ if cached["value"] is not None else "None",
                    "source": cached.get("source", "cache"),
                    "error": None,
                })
            else:
                results.append({
                    "node_id": nid,
                    "value": None,
                    "quality": OPCQuality.BAD,
                    "timestamp": datetime.now().isoformat(),
                    "data_type": "None",
                    "source": "not_yet_collected",
                    "error": "节点尚未采集到数据",
                })
        return results

    # ------------------------------------------------------------------ #
    #  历史数据（从 SQLite 查询）                                              #
    # ------------------------------------------------------------------ #

    async def read_history(
        self,
        node_id: str,
        start_time: str,
        end_time: str,
        max_points: int = 1000,
    ) -> List[Dict[str, Any]]:
        """读取历史数据（从 SQLite 本地历史库查询）。"""
        OPC_REQUEST_COUNT.labels("read_history").inc()
        try:
            history = await self.history_db.read_history(
                node_id=node_id,
                start_time=start_time,
                end_time=end_time,
                max_points=max_points,
            )
            logger.debug("history_read", node_id=node_id, count=len(history))
            return history
        except Exception as e:
            logger.error("history_read_failed", node_id=node_id, error=str(e))
            return []

    # ------------------------------------------------------------------ #
    #  缓存查询                                                              #
    # ------------------------------------------------------------------ #

    def get_cache_snapshot(self) -> Dict[str, Any]:
        return dict(self._value_cache)

    def get_cache_size(self) -> int:
        return len(self._value_cache)

    def get_subscribed_count(self) -> int:
        return len(self._collect_nodes)

    def get_last_push_time(self) -> Optional[float]:
        """返回最后一次收到推送的时间戳，0 表示从未收到。"""
        return self._last_push_time

    def get_push_age(self) -> int:
        """返回最后一次推送距今的秒数。-1 表示从未收到。"""
        if self._last_push_time == 0:
            return -1
        return int(time.time() - self._last_push_time)
