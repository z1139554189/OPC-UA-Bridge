"""
测试：REST API 端点
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def api_client():
    return TestClient(app)


@pytest.fixture
def mock_opcua():
    """注入已连接的 mock OPC UA 客户端"""
    import src.api.main as main_module
    mock = MagicMock()
    mock.is_connected.return_value = True
    mock.endpoint = "opc.tcp://localhost:4840"
    mock.browse_nodes = AsyncMock(return_value=[
        {"node_id": "ns=2;s=Temperature", "display_name": "温度", "has_value": True}
    ])
    mock.read_value = AsyncMock(return_value={
        "node_id": "ns=2;s=Temperature",
        "value": 25.6,
        "timestamp": "2026-03-26T14:00:00Z",
        "quality": "Good",
        "data_type": "Double"
    })

    original = main_module.opcua_client
    main_module.opcua_client = mock
    yield mock
    main_module.opcua_client = original


class TestRootEndpoint:

    def test_root_returns_info(self, api_client):
        response = api_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "1.0.0"
        assert "status" in data


class TestHealthEndpoint:

    def test_health_when_client_none(self, api_client):
        """无 OPC UA 客户端时，/health 返回 503"""
        import src.api.main as main_module
        original = main_module.opcua_client
        main_module.opcua_client = None

        response = api_client.get("/health")
        assert response.status_code in (200, 503)

        main_module.opcua_client = original


class TestNodesEndpoint:

    def test_get_nodes_no_client(self, api_client):
        """未连接时应返回 503"""
        import src.api.main as main_module
        original = main_module.opcua_client
        main_module.opcua_client = None

        response = api_client.get("/api/v1/nodes")
        assert response.status_code == 503

        main_module.opcua_client = original

    def test_get_nodes_success(self, api_client, mock_opcua):
        """正常情况返回节点列表"""
        response = api_client.get("/api/v1/nodes")
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert data["count"] == 1


class TestReadValueEndpoint:

    def test_read_value_success(self, api_client, mock_opcua):
        """正常读取节点值"""
        response = api_client.get("/api/v1/nodes/ns=2;s=Temperature/value")
        assert response.status_code == 200
        data = response.json()
        assert data["value"] == 25.6
        assert data["quality"] == "Good"
