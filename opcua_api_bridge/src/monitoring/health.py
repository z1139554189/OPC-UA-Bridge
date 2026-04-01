"""
健康检查模块
Author: WorkBuddy SRE
Version: 2.0.0
"""

import time
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any

import structlog

logger = structlog.get_logger()

# 全局启动时间（模块加载时记录，uptime 准确）
_START_TIME = time.time()


class HealthCheck:
    """系统健康检查"""

    def __init__(self, opcua_client=None):
        self.opcua_client = opcua_client

    async def check_all(self) -> Dict[str, Any]:
        """执行全部健康检查，返回汇总结果"""
        checks = await asyncio.gather(
            self._check_opcua(),
            self._check_memory(),
            return_exceptions=True
        )

        opcua_result  = checks[0] if not isinstance(checks[0], Exception) else {"status": "error", "detail": str(checks[0])}
        memory_result = checks[1] if not isinstance(checks[1], Exception) else {"status": "error", "detail": str(checks[1])}

        overall = "healthy" if all(
            r.get("status") == "ok" for r in [opcua_result, memory_result]
        ) else "degraded"

        opcua_ok = opcua_result.get("status") == "ok"
        memory_mb = memory_result.get("rss_mb", 0)

        return {
            "status": overall,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "uptime_seconds": round(time.time() - _START_TIME, 1),
            "opcua_connected": opcua_ok,
            "memory_mb": memory_mb,
            "checks": {
                "opcua": opcua_result,
                "memory": memory_result,
            }
        }

    async def _check_opcua(self) -> Dict[str, Any]:
        """检查 OPC UA 连接"""
        if self.opcua_client is None:
            return {"status": "error", "detail": "客户端未初始化"}

        connected = self.opcua_client.is_connected()
        if connected:
            return {"status": "ok", "endpoint": self.opcua_client.endpoint}
        else:
            return {"status": "error", "detail": "OPC UA 服务器未连接",
                    "endpoint": self.opcua_client.endpoint}

    async def _check_memory(self) -> Dict[str, Any]:
        """检查进程内存"""
        try:
            import psutil, os
            proc = psutil.Process(os.getpid())
            mem_mb = proc.memory_info().rss / 1024 / 1024
            status = "ok" if mem_mb < 512 else "warning"
            return {"status": status, "rss_mb": round(mem_mb, 1)}
        except ImportError:
            # psutil 未安装时退化为简单报告
            return {"status": "ok", "detail": "psutil 未安装，跳过内存检查"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}
