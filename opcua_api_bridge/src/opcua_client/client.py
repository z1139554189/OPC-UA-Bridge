"""
OPC UA 客户端核心模块（asyncua 版，按需订阅缓存 + SQLite 历史库）
Author: WorkBuddy SRE
Version: 4.0.0

关键设计：
- 使用 asyncua（异步库），兼容该 OPC UA 服务器
- 连接后创建一个共享订阅通道（秒级完成），节点按需动态添加
- 订阅推送的值缓存在内存字典，read_value/batch_read 均从缓存读取
- 订阅推送同时写入 SQLite 历史库，read_history 查询本地库
- 线程安全：asyncua 本身是 async，在同一 event loop 里运行
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Set

from asyncua import Client as AsyncClient, ua
import structlog
from prometheus_client import Counter, Histogram, Gauge

from src.config.settings import settings
from src.storage import HistoryDB

logger = structlog.get_logger()

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


class OPCQuality:
    GOOD = "Good"
    UNCERTAIN = "Uncertain"
    BAD = "Bad"
    ERROR = "Error"


class _SubHandler:
    """asyncua 订阅回调：把推送值写入缓存字典 + SQLite 历史库，并触发等待者。"""

    def __init__(self, cache: Dict[str, Any], notify_event: asyncio.Event,
                 history_db: Optional[HistoryDB] = None):
        self._cache = cache
        self._notify = notify_event  # 每次有新数据时 set，让等待者醒来
        self._history_db = history_db

    async def datachange_notification(self, node, val, data):
        node_id = node.nodeid.to_string()
        try:
            sc = data.monitored_item.Value.StatusCode
            is_good = sc.is_good()
        except Exception:
            is_good = val is not None

        now_iso = datetime.now().isoformat()
        quality = OPCQuality.GOOD if is_good else OPCQuality.BAD

        self._cache[node_id] = {
            "value": val,
            "quality": quality,
            "timestamp": now_iso,
        }

        # 写入 SQLite 历史库（异步，不阻塞缓存更新）
        if self._history_db and val is not None:
            try:
                # 获取服务器端时间戳（如果有）
                source_ts = None
                try:
                    src = data.monitored_item.Value.SourceTimestamp
                    if src:
                        source_ts = src.isoformat()
                except Exception:
                    pass

                await self._history_db.write(
                    node_id=node_id,
                    value=float(val) if not isinstance(val, (int, float)) else val,
                    quality=quality,
                    timestamp=now_iso,
                    source_timestamp=source_ts,
                )
            except Exception as e:
                logger.warning("history_write_failed", node_id=node_id, error=str(e))

        # 通知所有等待者
        self._notify.set()
        self._notify.clear()


class OPCUAClient:
    """
    OPC UA 客户端（asyncua 按需订阅缓存模式）。

    启动时秒级完成，节点在首次 read_value/batch_read 时按需加入订阅。
    已订阅节点的后续请求直接从内存缓存返回，无网络延迟。
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
        self.security_policy = security_policy
        self.security_mode = security_mode

        self._client: Optional[AsyncClient] = None
        self._connected = False
        self._reconnect_delay = 5
        self._max_reconnect_attempts = 3

        # 订阅
        self._subscription = None
        self._subscribed_node_ids: Set[str] = set()

        # 缓存
        self._value_cache: Dict[str, Dict[str, Any]] = {}
        self._data_event = asyncio.Event()  # 有新数据推送时被 set

        # SQLite 历史库
        self.history_db = HistoryDB(
            db_path=history_db_path or HistoryDB.DEFAULT_DB_PATH,
            retention_days=history_retention_days,
        )
        self._handler = _SubHandler(self._value_cache, self._data_event, self.history_db)

        # 定期轮询写入任务
        self._poll_task: Optional[asyncio.Task] = None
        self._last_connection_check = datetime.now()

    # ------------------------------------------------------------------ #
    #  连接 / 断开                                                          #
    # ------------------------------------------------------------------ #

    async def connect(self) -> bool:
        """连接到 OPC UA 服务器，并建立共享订阅通道。"""
        try:
            logger.info("opc_connection_attempt", endpoint=self.endpoint)

            # 初始化 SQLite 历史库
            await self.history_db.connect()

            self._client = AsyncClient(self.endpoint)

            if self.username:
                self._client.set_user(self.username)
            if self.password:
                self._client.set_password(self.password)

            self._client.session_timeout = 60000

            await self._client.connect()
            self._connected = True
            logger.info("opc_connection_success", endpoint=self.endpoint)

            # 建立共享订阅通道（500ms 推送间隔）
            self._subscription = await self._client.create_subscription(
                500, self._handler
            )
            OPC_CONNECTION_STATUS.set(1)
            logger.info("opc_subscription_ready")

            # 启动定期轮询写入（每60秒缓存→SQLite）
            self._poll_task = asyncio.create_task(self._poll_cache_to_history())
            logger.info("poll_task_started", interval_seconds=60)

            return True

        except Exception as e:
            self._connected = False
            OPC_CONNECTION_STATUS.set(0)
            logger.error("opc_connection_failed", endpoint=self.endpoint, error=str(e))
            return False

    async def disconnect(self):
        """断开连接并清理订阅。"""
        self._connected = False

        # 取消轮询任务
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
            logger.info("poll_task_stopped")

        try:
            if self._subscription:
                try:
                    await self._subscription.delete()
                except Exception:
                    pass
                self._subscription = None
            if self._client:
                await self._client.disconnect()
            # 关闭 SQLite 历史库（会自动 flush buffer）
            await self.history_db.close()
            OPC_CONNECTION_STATUS.set(0)
            logger.info("opc_disconnected")
        except Exception as e:
            logger.error("opc_disconnect_failed", error=str(e))

    # ------------------------------------------------------------------ #
    #  连接状态                                                              #
    # ------------------------------------------------------------------ #

    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    async def _ensure_connected(self) -> bool:
        if self.is_connected():
            return True
        logger.warning("opc_reconnecting")
        for attempt in range(self._max_reconnect_attempts):
            if await self.connect():
                return True
            if attempt < self._max_reconnect_attempts - 1:
                await asyncio.sleep(self._reconnect_delay)
        return False

    # ------------------------------------------------------------------ #
    #  浏览节点                                                              #
    # ------------------------------------------------------------------ #

    async def browse_nodes(
        self,
        node_id: Optional[str] = None,
        namespace: Optional[int] = None,
        recursive: bool = False,
        max_nodes: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """浏览节点列表（支持分页）。"""
        if not await self._ensure_connected():
            raise ConnectionError("OPC UA 服务器连接失败")

        OPC_REQUEST_COUNT.labels("browse").inc()

        start_node = (
            self._client.get_node(node_id)
            if node_id
            else self._client.get_objects_node()
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

        return nodes

    # ------------------------------------------------------------------ #
    #  按需订阅工具                                                           #
    # ------------------------------------------------------------------ #

    async def _ensure_subscribed(self, node_ids: List[str]) -> None:
        """确保给定节点列表已加入订阅（首次按需添加）。"""
        if not self._subscription:
            raise ConnectionError("订阅通道未就绪")

        new_ids = [nid for nid in node_ids if nid not in self._subscribed_node_ids]
        if not new_ids:
            return

        nodes = [self._client.get_node(nid) for nid in new_ids]
        await self._subscription.subscribe_data_change(nodes)
        for nid in new_ids:
            self._subscribed_node_ids.add(nid)
        OPC_NODE_COUNT.set(len(self._subscribed_node_ids))
        logger.debug("opc_nodes_subscribed", count=len(new_ids))

    # ------------------------------------------------------------------ #
    #  读值（从订阅缓存读取）                                                 #
    # ------------------------------------------------------------------ #

    async def _wait_for_nodes(self, node_ids: List[str], timeout: float = 5.0) -> None:
        """等待给定节点列表全部出现在缓存中（由订阅推送触发）。"""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if all(nid in self._value_cache for nid in node_ids):
                return
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return
            try:
                # 等待新数据通知，最多等 remaining 秒
                await asyncio.wait_for(
                    asyncio.shield(self._data_event.wait()),
                    timeout=min(remaining, 1.0)
                )
            except asyncio.TimeoutError:
                pass

    async def read_value(self, node_id: str) -> Dict[str, Any]:
        """
        读取节点当前值。
        - 已订阅：直接从缓存返回
        - 未订阅：按需订阅，等待首次推送（最多 3 秒）
        """
        if not await self._ensure_connected():
            raise ConnectionError("OPC UA 服务器连接失败")

        OPC_REQUEST_COUNT.labels("read_value").inc()

        # 已缓存则直接返回
        cached = self._value_cache.get(node_id)
        if cached:
            return {
                "node_id": node_id,
                "value": cached["value"],
                "quality": cached["quality"],
                "timestamp": cached["timestamp"],
                "data_type": type(cached["value"]).__name__ if cached["value"] is not None else "None",
                "source": "cache",
            }

        # 按需订阅，等待推送
        await self._ensure_subscribed([node_id])
        await self._wait_for_nodes([node_id], timeout=3.0)

        cached = self._value_cache.get(node_id)
        if cached:
            return {
                "node_id": node_id,
                "value": cached["value"],
                "quality": cached["quality"],
                "timestamp": cached["timestamp"],
                "data_type": type(cached["value"]).__name__ if cached["value"] is not None else "None",
                "source": "subscribed",
            }
        return {
            "node_id": node_id,
            "value": None,
            "quality": OPCQuality.BAD,
            "timestamp": datetime.now().isoformat(),
            "data_type": "None",
            "source": "timeout",
        }

    async def batch_read(self, node_ids: List[str]) -> List[Dict[str, Any]]:
        """批量读取节点值（优先缓存，未缓存的按需订阅）。"""
        if not await self._ensure_connected():
            raise ConnectionError("OPC UA 服务器连接失败")

        OPC_REQUEST_COUNT.labels("batch_read").inc()

        # 找出未缓存节点，一次性批量订阅
        uncached = [nid for nid in node_ids if nid not in self._value_cache]
        if uncached:
            await self._ensure_subscribed(uncached)
            await self._wait_for_nodes(uncached, timeout=5.0)

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
                    "source": "cache" if nid not in uncached else "subscribed",
                    "error": None,
                })
            else:
                results.append({
                    "node_id": nid,
                    "value": None,
                    "quality": OPCQuality.BAD,
                    "timestamp": datetime.now().isoformat(),
                    "data_type": "None",
                    "source": "timeout",
                    "error": "订阅超时，未收到数据",
                })
        return results

    # ------------------------------------------------------------------ #
    #  历史数据（占位）                                                       #
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
        return len(self._subscribed_node_ids)

    # ------------------------------------------------------------------ #
    #  定期轮询写入（缓存→SQLite）                                          #
    # ------------------------------------------------------------------ #

    async def _poll_cache_to_history(self) -> None:
        """
        后台任务：每60秒遍历缓存，将所有已订阅节点的当前值强制写入 SQLite。
        解决 OPC UA 服务器"值不变不推送"导致历史数据稀疏的问题。
        """
        poll_interval = 60.0
        while self._connected:
            try:
                await asyncio.sleep(poll_interval)
                if not self._connected:
                    break

                if not self._value_cache:
                    continue

                now_iso = datetime.now().isoformat()
                written = 0
                for nid, entry in self._value_cache.items():
                    val = entry.get("value")
                    if val is None:
                        continue
                    quality = entry.get("quality", OPCQuality.GOOD)
                    try:
                        await self.history_db.write(
                            node_id=nid,
                            value=float(val) if not isinstance(val, (int, float)) else val,
                            quality=quality,
                            timestamp=now_iso,
                        )
                        written += 1
                    except Exception as e:
                        logger.warning("poll_write_failed", node_id=nid, error=str(e))

                logger.debug("poll_write_done", nodes_written=written, total_cached=len(self._value_cache))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("poll_task_error", error=str(e))
                await asyncio.sleep(poll_interval)
