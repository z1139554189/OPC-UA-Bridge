"""
OPC UA 客户端核心模块（自适应采集模式 + 推送超时检测）
Author: WorkBuddy SRE
Version: 7.1.0

关键设计：
- 默认保持长连接 + 订阅，数据持续推送（秒级），全部写入 SQLite
- 检测到 Session 被占用（BadTooManySessions）时自动让出
- TCP 端口预检：区分网络未就绪（30s 短退避）和真正连接失败（1800s 长退避）
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

# ── 云端 MySQL（TDSQL-C）实时同步配置 ──────────────────────────────
_CLOUD_MYSQL = None

def _get_cloud_mysql():
    """延迟加载云端 MySQL 连接配置（从上层 cloud_config 读取）"""
    global _CLOUD_MYSQL
    if _CLOUD_MYSQL is None:
        import os
        from pathlib import Path
        _cfg = {}
        # 尝试从环境变量或上层 cloud_config 读取
        try:
            cfg_path = Path(__file__).parent.parent.parent / "20260402160254" / "cloud" / "cloud_config.py"
            if cfg_path.exists():
                import importlib.util
                spec = importlib.util.spec_from_file_location("cloud_config", cfg_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                CLOUD_MYSQL = getattr(mod, 'CLOUD_MYSQL', {})
                _CLOUD_MYSQL = {
                    "host": CLOUD_MYSQL.get("host", os.environ.get("CLOUD_MYSQL_HOST", "")),
                    "port": int(CLOUD_MYSQL.get("port", os.environ.get("CLOUD_MYSQL_PORT", "21397"))),
                    "user": CLOUD_MYSQL.get("user", os.environ.get("CLOUD_MYSQL_USER", "opcua_user")),
                    "password": CLOUD_MYSQL.get("password", os.environ.get("CLOUD_MYSQL_PASSWORD", "")),
                    "database": CLOUD_MYSQL.get("database", os.environ.get("CLOUD_MYSQL_DATABASE", "opcua_db")),
                }
            else:
                _CLOUD_MYSQL = {
                    "host": os.environ.get("CLOUD_MYSQL_HOST", ""),
                    "port": int(os.environ.get("CLOUD_MYSQL_PORT", "21397")),
                    "user": os.environ.get("CLOUD_MYSQL_USER", "opcua_user"),
                    "password": os.environ.get("CLOUD_MYSQL_PASSWORD", ""),
                    "database": os.environ.get("CLOUD_MYSQL_DATABASE", "opcua_db"),
                }
        except Exception:
            _CLOUD_MYSQL = {
                "host": os.environ.get("CLOUD_MYSQL_HOST", ""),
                "port": int(os.environ.get("CLOUD_MYSQL_PORT", "21397")),
                "user": os.environ.get("CLOUD_MYSQL_USER", "opcua_user"),
                "password": os.environ.get("CLOUD_MYSQL_PASSWORD", ""),
                "database": os.environ.get("CLOUD_MYSQL_DATABASE", "opcua_db"),
            }
    return _CLOUD_MYSQL


def _sync_values_to_cloud(cache_snapshot: dict, cloud_cfg: dict):
    """
    将缓存快照批量 UPSERT 到云端 node_latest 表。
    在线程池中执行（run_in_executor），不阻塞事件循环。
    """
    if not cache_snapshot:
        return

    try:
        import pymysql

        conn = pymysql.connect(
            host=cloud_cfg.get("host", ""),
            port=int(cloud_cfg.get("port", 21397)),
            user=cloud_cfg.get("user", ""),
            password=cloud_cfg.get("password", ""),
            database=cloud_cfg.get("database", "opcua_db"),
            connect_timeout=5,
        )
        cur = conn.cursor()

        sql = """
            INSERT INTO node_latest (node_id, value, quality, timestamp, updated_at)
            VALUES (%s, %s, %s, NOW(3), NOW(3))
            ON DUPLICATE KEY UPDATE
                value = VALUES(value),
                quality = VALUES(quality),
                timestamp = VALUES(timestamp),
                updated_at = NOW(3)
        """

        for node_id, cached in cache_snapshot.items():
            val = cached.get("value")
            if val is None:
                continue
            try:
                numeric_val = round(float(val), 2) if val is not None else None
            except (ValueError, TypeError):
                numeric_val = val

            cur.execute(sql, (
                node_id,
                numeric_val,
                cached.get("quality", "Good"),
            ))

        conn.commit()
        conn.close()
    except Exception:
        pass  # 云端同步失败不影响主流程，静默忽略

# 缓存数据过期时间（秒），超过此时间缓存数据视为过期
CACHE_STALE_TIMEOUT = 120

# 推送超时：N 秒内未收到任何 datachange_notification → 视为连接异常
PUSH_TIMEOUT = 300

# 普通连接失败后退避时间（秒）
CONNECT_FAIL_BACKOFF = 1800

# 连接成功后等待首次推送的宽限期（秒）
INITIAL_GRACE = 15

# 推送超时/首次推送失败后断开重连的等待时间（秒）
# 设为 500s：给 OPC 服务器足够时间释放旧 Session，避免重连后又触发 Session 满
PUSH_FAIL_BACKOFF = 500

# 网络未就绪时的退避时间（秒），区别于正常连接失败的 1800s
# 用于开机后网络栈未就绪 / 路由未建立 / 网线未插等场景
NETWORK_UNREACHABLE_BACKOFF = 30

# TCP 预检超时（秒）
NETWORK_CHECK_TIMEOUT = 5

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
        self._cloud_sync_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_collect_time: Optional[float] = None

        # 推送超时检测：最后一次收到 datachange_notification 的时间
        self._last_push_time: float = 0  # 0 = 从未收到过推送

        # 连接时间（用于首次推送宽限期检测）
        self._connected_at: float = 0

        # 退避解禁时间：time.time() > _retry_after 才允许重连（0=立即可连）
        self._retry_after: float = 0

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

        # 启动云端实时同步任务（每秒写一次最新值到 TDSQL-C）
        self._cloud_sync_task = asyncio.create_task(self._cloud_sync_loop())

        # 启动采集循环
        self._collect_task = asyncio.create_task(self._collect_loop())
        logger.info("adaptive_collect_started", mode="default_subscribe",
                    push_timeout=PUSH_TIMEOUT,
                    heartbeat_interval=HEARTBEAT_INTERVAL)
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

        # 取消云端同步任务
        if self._cloud_sync_task and not self._cloud_sync_task.done():
            self._cloud_sync_task.cancel()
            try:
                await self._cloud_sync_task
            except asyncio.CancelledError:
                pass
            self._cloud_sync_task = None

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

    async def _check_network_reachable(self) -> bool:
        """
        TCP 级别检查 OPC UA 端点是否可达（异步，5s 超时）。
        用于区分"网络未就绪"（短退避 30s）和"真正的连接失败"（长退避 1800s）。
        开机后 Windows 网络栈和静态路由可能需要几十秒才能就绪，
        此预检确保不会因为 WinError 1232 等临时网络错误进入 30 分钟长退避。
        """
        import re
        try:
            m = re.match(r'opc\.tcp://([^:]+):(\d+)', self.endpoint)
            if not m:
                return True  # 无法解析 URL，假定可达
            host, port = m.group(1), int(m.group(2))
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=NETWORK_CHECK_TIMEOUT,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def _connect_opc(self) -> bool:
        """建立 OPC UA 连接并创建订阅。"""
        # ── 网络可达性预检（避免 WinError 1232 触发 1800s 长退避）──
        if not await self._check_network_reachable():
            self._retry_after = time.time() + NETWORK_UNREACHABLE_BACKOFF
            logger.warning("opc_network_unreachable",
                           endpoint=self.endpoint,
                           retry_after=NETWORK_UNREACHABLE_BACKOFF,
                           message=f"OPC UA 服务器网络不可达，{NETWORK_UNREACHABLE_BACKOFF}秒后重试")
            self._client = None
            self._subscription = None
            OPC_CONNECTION_STATUS.set(0)
            return False

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
            is_session_full = "session" in err_str and "max" in err_str

            if is_session_full:
                # Session已满：服务器上已创建幽灵Session，无法自动恢复。
                # 以退出码 2 终止进程（NSSM 配置该退出码为 ExitAction=None 不重启），
                # 等待人工排查后手动重启服务。
                logger.error("session_full_fatal",
                           error=str(e),
                           message="OPC UA Session已满，无法连接，以退出码2终止进程，禁止自动重启")
                import os
                os._exit(2)
            else:
                # 防御层：即使 TCP 预检通过，实际连接仍可能因网络瞬断失败
                err_str = str(e).lower()
                is_network_error = any(kw in err_str for kw in [
                    "winerror 1232", "network location", "cannot reach",
                    "no route to host", "network is unreachable",
                ])
                if is_network_error:
                    self._retry_after = time.time() + NETWORK_UNREACHABLE_BACKOFF
                    logger.warning("opc_connect_network_error",
                                   error=str(e)[:100],
                                   retry_after=NETWORK_UNREACHABLE_BACKOFF,
                                   message=f"连接时网络错误，{NETWORK_UNREACHABLE_BACKOFF}秒后重试")
                else:
                    self._retry_after = time.time() + CONNECT_FAIL_BACKOFF
                    logger.error("opc_connect_failed",
                               error=str(e),
                               retry_after=CONNECT_FAIL_BACKOFF,
                               message=f"连接失败，{CONNECT_FAIL_BACKOFF}秒后重试")

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
        自适应采集循环（极简退避版）：
        - _retry_after 统一控制重连时机，无多余状态变量
        - 普通连接失败：等 CONNECT_FAIL_BACKOFF 秒后重试
        - Session满：直接终止进程，等人工重启
        - 推送超时/首次推送宽限期到：断开后等 PUSH_FAIL_BACKOFF 秒重连
        """
        while self._running:
            try:
                # ── 1. 退避等待 ────────────────────────────────────────
                remaining = self._retry_after - time.time()
                if remaining > 0:
                    logger.info("backoff_waiting",
                               remaining=int(remaining),
                               message=f"退避中，{int(remaining)}秒后重试")
                    await asyncio.sleep(min(remaining, 5))
                    continue

                # ── 2. 未连接 → 尝试连接 ───────────────────────────────
                if self._client is None:
                    success = await self._connect_opc()
                    if success:
                        self._connected_at = time.time()
                        self._last_push_time = 0
                        logger.info("opc_connected",
                                   registered_nodes=len(self._collect_nodes))
                    # 失败时 _connect_opc 已设好 _retry_after 或终止进程
                    continue

                # ── 3. 已连接 → 每2秒巡检推送状态 ────────────────────
                await asyncio.sleep(2)
                if not self._running:
                    break

                now = time.time()
                if self._last_push_time > 0:
                    # 已收到过推送，检查是否超时
                    push_age = now - self._last_push_time
                    if push_age > PUSH_TIMEOUT:
                        logger.warning("push_timeout",
                                      push_age=int(push_age),
                                      message=f"{PUSH_TIMEOUT}秒无推送，断开重连")
                        OPC_PUSH_TIMEOUT_COUNT.inc()
                        await self._disconnect_opc()
                        self._retry_after = time.time() + PUSH_FAIL_BACKOFF
                        continue
                else:
                    # 从未收到推送，检查宽限期
                    if now - self._connected_at > INITIAL_GRACE:
                        logger.warning("push_never_received",
                                      grace=INITIAL_GRACE,
                                      message=f"连接后{INITIAL_GRACE}秒未收到推送，断开重连")
                        OPC_PUSH_TIMEOUT_COUNT.inc()
                        await self._disconnect_opc()
                        self._retry_after = time.time() + PUSH_FAIL_BACKOFF
                        continue

                # 一切正常
                OPC_COLLECT_COUNT.labels("active").inc()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("collect_loop_error", error=str(e))
                OPC_COLLECT_COUNT.labels("error").inc()
                await self._disconnect_opc()
                self._retry_after = time.time() + CONNECT_FAIL_BACKOFF

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
            if self.is_yielded() or self._client is None:
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
    #  云端实时同步（秒级延迟）                                             #
    # ------------------------------------------------------------------ #

    # 云端同步间隔（秒）
    CLOUD_SYNC_INTERVAL = 1

    async def _cloud_sync_loop(self):
        """
        每 CLOUD_SYNC_INTERVAL 秒读取最新缓存值，实时写入云端 node_latest 表。
        延迟约 1 秒，比 SQLite 历史推送快 59 倍。
        """
        while self._running:
            try:
                await asyncio.sleep(self.CLOUD_SYNC_INTERVAL)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            # 读取当前缓存中的最新值
            cache_snapshot = dict(self._value_cache)
            if not cache_snapshot:
                continue

            # 在线程池里执行同步（避免阻塞事件循环）
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(
                    None,
                    _sync_values_to_cloud,
                    cache_snapshot,
                    _get_cloud_mysql(),
                )
            except Exception as e:
                logger.warning("cloud_sync_failed", error=str(e))
            else:
                # 打印最新一条的 updated_at 做验证
                sample = next(iter(cache_snapshot.values()), {})
                logger.debug("cloud_sync_ok", node=next(iter(cache_snapshot)), 
                             updated_at=sample.get("timestamp","")[:23])

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
        if self._client is None:
            return False
        if self._last_push_time > 0:
            if (time.time() - self._last_push_time) > PUSH_TIMEOUT:
                return False
        return True

    def is_yielded(self) -> bool:
        """兼容旧接口：退避期间视为 yielded。"""
        return self._client is None and (self._retry_after - time.time()) > 0

    def get_cache_freshness(self) -> int:
        """返回上次数据更新距今的秒数。"""
        if not self._last_collect_time:
            return -1
        return int(time.time() - self._last_collect_time)

    def get_collect_status(self) -> str:
        """返回采集状态描述。"""
        if self._client is None:
            remaining = self._retry_after - time.time()
            if remaining > 0:
                return f"backoff({int(remaining)}s)"
            return "reconnecting"
        if self._last_push_time > 0:
            if (time.time() - self._last_push_time) > PUSH_TIMEOUT:
                return "push_timeout"
        elif (time.time() - self._connected_at) <= INITIAL_GRACE:
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
