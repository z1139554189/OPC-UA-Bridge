"""
测试：OPC UA 客户端核心
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from src.opcua_client.client import OPCUAClient, OPCQuality


@pytest.fixture
def client():
    return OPCUAClient(
        endpoint="opc.tcp://localhost:4840",
        username="user",
        password="pass"
    )


class TestOPCUAClientInit:

    def test_default_values(self, client):
        assert client.endpoint == "opc.tcp://localhost:4840"
        assert client.username == "user"
        assert client._connected is False
        assert client._max_reconnect_attempts == 3

    def test_cache_enabled_by_default(self, client):
        assert client._cache_enabled is True
        assert isinstance(client._node_cache, dict)


class TestOPCUAClientConnection:

    @pytest.mark.asyncio
    async def test_connect_success(self, client):
        mock_sync = MagicMock()

        with patch("src.opcua_client.client.SyncClient", return_value=mock_sync), \
             patch.object(client, "_update_node_count"):
            result = await client.connect()

        assert result is True
        assert client._connected is True
        mock_sync.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_failure(self, client):
        mock_sync = MagicMock()
        mock_sync.connect.side_effect = ConnectionRefusedError("拒绝连接")

        with patch("src.opcua_client.client.SyncClient", return_value=mock_sync):
            result = await client.connect()

        assert result is False
        assert client._connected is False

    @pytest.mark.asyncio
    async def test_disconnect(self, client):
        mock_sync = MagicMock()
        client._client = mock_sync
        client._connected = True

        await client.disconnect()

        mock_sync.disconnect.assert_called_once()
        assert client._connected is False


class TestOPCUAClientReadValue:

    @pytest.mark.asyncio
    async def test_read_value_not_connected(self, client):
        """未连接时应抛出 ConnectionError"""
        client._connected = False
        client._client = None

        with pytest.raises(ConnectionError):
            await client.read_value("ns=2;s=Temperature")

    @pytest.mark.asyncio
    async def test_read_value_caches_result(self, client):
        """成功读取后节点数据应被缓存"""
        client._connected = True

        mock_node = MagicMock()
        dv = MagicMock()
        dv.Value.Value = 25.6
        dv.SourceTimestamp.isoformat.return_value = "2026-03-26T14:00:00"
        dv.ServerTimestamp.isoformat.return_value = "2026-03-26T14:00:00"
        dv.StatusCode.is_good.return_value = True
        dv.Value.VariantType = "Double"
        mock_node.get_value.return_value = dv

        with patch.object(client, "_ensure_connected", return_value=True), \
             patch.object(client, "_get_node_by_id", return_value=mock_node):
            result = await client.read_value("ns=2;s=Temperature")

        assert result["value"] == 25.6
        assert result["quality"] == OPCQuality.GOOD.value
        assert "ns=2;s=Temperature" in client._node_cache


class TestOPCQuality:

    def test_quality_values(self):
        assert OPCQuality.GOOD.value == "Good"
        assert OPCQuality.BAD.value == "Bad"
        assert OPCQuality.UNCERTAIN.value == "Uncertain"
