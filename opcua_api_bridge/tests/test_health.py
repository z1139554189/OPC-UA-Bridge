"""
测试：健康检查模块
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.monitoring.health import HealthCheck


@pytest.fixture
def mock_opcua_client():
    client = MagicMock()
    client.endpoint = "opc.tcp://localhost:4840"
    client.is_connected.return_value = True
    return client


@pytest.fixture
def disconnected_opcua_client():
    client = MagicMock()
    client.endpoint = "opc.tcp://localhost:4840"
    client.is_connected.return_value = False
    return client


class TestHealthCheck:

    @pytest.mark.asyncio
    async def test_all_healthy(self, mock_opcua_client):
        """正常情况：全部检查通过"""
        hc = HealthCheck(mock_opcua_client)

        with patch("src.monitoring.health.HealthCheck._check_memory",
                   new_callable=AsyncMock,
                   return_value={"status": "ok", "rss_mb": 100.0}):
            result = await hc.check_all()

        assert result["status"] == "healthy"
        assert result["checks"]["opcua"]["status"] == "ok"
        assert "uptime_seconds" in result
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_opcua_disconnected(self, disconnected_opcua_client):
        """OPC UA 断线时状态为 degraded"""
        hc = HealthCheck(disconnected_opcua_client)

        with patch("src.monitoring.health.HealthCheck._check_memory",
                   new_callable=AsyncMock,
                   return_value={"status": "ok", "rss_mb": 100.0}):
            result = await hc.check_all()

        assert result["status"] == "degraded"
        assert result["checks"]["opcua"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_no_client(self):
        """未传入客户端时"""
        hc = HealthCheck(None)

        with patch("src.monitoring.health.HealthCheck._check_memory",
                   new_callable=AsyncMock,
                   return_value={"status": "ok", "rss_mb": 80.0}):
            result = await hc.check_all()

        assert result["status"] == "degraded"
        assert result["checks"]["opcua"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_memory_warning(self, mock_opcua_client):
        """内存超过 512 MB 时返回 warning（整体仍为 degraded）"""
        hc = HealthCheck(mock_opcua_client)

        with patch("src.monitoring.health.HealthCheck._check_memory",
                   new_callable=AsyncMock,
                   return_value={"status": "warning", "rss_mb": 600.0}):
            result = await hc.check_all()

        # warning != ok，整体应为 degraded
        assert result["status"] == "degraded"
        assert result["checks"]["memory"]["rss_mb"] == 600.0
