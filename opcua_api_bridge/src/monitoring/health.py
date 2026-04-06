"""
健康检查模块（自适应采集模式 + 推送超时检测）
Author: WorkBuddy SRE
Version: 3.0.0

适配 client.py v7.0.0 的状态：
- active: 正常采集（推送正常）
- yielded: Session 已让出（客户端在用）
- reconnecting: 正在重连
- push_timeout: 推送超时（连接异常）
- waiting_first_push: 已连接但未收到首条推送
"""

import time
from datetime import datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.opcua_client.client import OPCUAClient

logger = structlog.get_logger()

# 缓存数据过期阈值（秒）
CACHE_STALE_TIMEOUT = 120


class HealthCheck:
    """系统健康检查"""

    def __init__(self, opcua_client: "OPCUAClient"):
        self.client = opcua_client

    async def check_all(self) -> dict:
        """执行所有健康检查"""
        uptime = getattr(self.client, '_start_time', None)
        uptime_seconds = int(time.time() - uptime) if uptime else 0

        opc_check = self._check_opcua()
        memory_check = self._check_memory()

        checks = {
            "opcua": opc_check,
            "memory": memory_check,
        }

        # 整体状态：所有检查都 OK 才算 healthy
        all_ok = all(c["status"] == "ok" for c in checks.values())
        status = "healthy" if all_ok else "unhealthy"

        return {
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "uptime_seconds": uptime_seconds,
            "opcua_connected": self.client.is_connected(),
            "collect_status": self.client.get_collect_status(),
            "cache_freshness_seconds": self.client.get_cache_freshness(),
            "push_age_seconds": self.client.get_push_age(),
            "memory_mb": memory_check.get("value_mb", 0),
            "checks": checks,
        }

    def _check_opcua(self) -> dict:
        """检查 OPC UA 采集状态"""
        collect_status = self.client.get_collect_status()
        push_age = self.client.get_push_age()
        freshness = self.client.get_cache_freshness()

        if collect_status == "active":
            return {
                "status": "ok",
                "endpoint": self.client.endpoint,
                "collect_status": "active",
                "push_age_seconds": push_age,
                "cache_freshness_seconds": freshness,
                "cache_size": self.client.get_cache_size(),
            }
        elif collect_status == "waiting_first_push":
            return {
                "status": "degraded",
                "endpoint": self.client.endpoint,
                "collect_status": "waiting_first_push",
                "detail": "已连接 OPC 服务器，等待首条数据推送",
                "push_age_seconds": push_age,
                "cache_freshness_seconds": freshness,
                "cache_size": self.client.get_cache_size(),
            }
        elif collect_status == "push_timeout":
            return {
                "status": "error",
                "endpoint": self.client.endpoint,
                "collect_status": "push_timeout",
                "detail": f"推送超时（{push_age} 秒未收到数据），可能 OPC 服务器异常断开",
                "push_age_seconds": push_age,
                "cache_freshness_seconds": freshness,
                "cache_size": self.client.get_cache_size(),
            }
        elif collect_status == "yielded":
            return {
                "status": "degraded",
                "endpoint": self.client.endpoint,
                "collect_status": "yielded",
                "detail": "Session 已让出给 OPC 客户端，等待恢复",
                "cache_freshness_seconds": freshness,
                "cache_size": self.client.get_cache_size(),
            }
        else:
            # reconnecting
            is_stale = freshness > CACHE_STALE_TIMEOUT or freshness < 0
            return {
                "status": "degraded" if not is_stale else "error",
                "endpoint": self.client.endpoint,
                "collect_status": collect_status,
                "cache_freshness_seconds": freshness,
                "cache_size": self.client.get_cache_size(),
                "detail": "正在重连" if not is_stale else "缓存数据已过期",
            }

    def _check_memory(self) -> dict:
        """检查内存使用"""
        try:
            import psutil
            process = psutil.Process()
            mem_mb = round(process.memory_info().rss / 1024 / 1024, 1)
            return {
                "status": "ok",
                "value_mb": mem_mb,
            }
        except ImportError:
            return {
                "status": "ok",
                "detail": "psutil 未安装，跳过内存检查",
                "value_mb": 0,
            }
