# OPC UA REST API Bridge

轻量级 **OPC UA → REST API** 桥接器，让任何 HTTP 客户端（包括 WorkBuddy）都能安全地读取工业设备数据。

## 架构总览

```
[OPC UA Server]
      │  opc.tcp://
      ▼
[opcua-bridge]  ──── /health /metrics
      │  REST / JSON
      ▼
[WorkBuddy 分析 / 其他客户端]

辅助服务: Redis (缓存)  Prometheus + Grafana (监控)
```

## 快速启动

### 方式一：本地开发（推荐先用这个验证）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制并编辑配置
cp .env.example .env
# 修改 OPCUA_ENDPOINT 指向你的 OPC UA 服务器

# 3. 启动
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

浏览器打开 http://localhost:8000/docs 即可看到交互式 API 文档。

### 方式二：Docker Compose（生产推荐）

```bash
# 复制配置
cp .env.example .env
# 编辑 .env 中的 OPCUA_ENDPOINT、SECRET_KEY 等

# 启动所有服务
docker compose up -d

# 查看日志
docker compose logs -f opcua-bridge
```

| 服务 | 地址 |
|------|------|
| API  | http://localhost:8000 |
| API 文档 | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin/admin) |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/metrics` | Prometheus 指标 |
| GET | `/api/v1/nodes` | 浏览节点树 |
| GET | `/api/v1/nodes/{node_id}/value` | 读取当前值 |
| POST | `/api/v1/nodes/batch-read` | 批量读取 |
| GET | `/api/v1/nodes/{node_id}/history` | 读取历史数据 |

### 读取示例

```bash
# 读取单个节点
curl http://localhost:8000/api/v1/nodes/ns=2;s=Temperature/value

# 批量读取
curl -X POST http://localhost:8000/api/v1/nodes/batch-read \
  -H "Content-Type: application/json" \
  -d '["ns=2;s=Temperature", "ns=2;s=Pressure"]'
```

响应格式：

```json
{
  "node_id": "ns=2;s=Temperature",
  "value": 25.6,
  "timestamp": "2026-03-26T14:00:00Z",
  "quality": "Good",
  "data_type": "Double"
}
```

## 运行测试

```bash
# 安装测试依赖
pip install pytest pytest-asyncio httpx

# 运行全部测试
pytest

# 带覆盖率
pip install pytest-cov
pytest --cov=src --cov-report=term-missing
```

## 目录结构

```
opcua_api_bridge/
├── src/
│   ├── api/
│   │   └── main.py          # FastAPI 应用、路由
│   ├── opcua_client/
│   │   └── client.py        # OPC UA 异步客户端
│   ├── monitoring/
│   │   ├── health.py        # 健康检查
│   │   └── metrics.py       # Prometheus 指标
│   └── config/
│       └── settings.py      # 配置（pydantic-settings）
├── tests/
│   ├── test_health.py
│   ├── test_opcua_client.py
│   └── test_api.py
├── deployment/
│   └── prometheus.yml
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## 安全说明

- **只读**：桥接器不对 OPC UA 服务器执行任何写操作
- **网络隔离**：建议将桥接器部署在 OPC UA 网络与办公网之间的 DMZ
- **JWT**：生产环境请配置有效的 `SECRET_KEY`
- **TLS**：生产环境建议在 nginx/traefik 前置层配置 HTTPS

## 常见问题

**Q: 连接 OPC UA 服务器失败？**
- 检查防火墙是否开放 4840 端口（TCP）
- 确认 `OPCUA_ENDPOINT` 格式：`opc.tcp://ip:port`
- 如服务器需要认证，填写 `OPCUA_USERNAME` / `OPCUA_PASSWORD`

**Q: 历史数据接口返回空？**
- 需要服务器支持 OPC UA Historical Access（HA）
- 在 `src/opcua_client/client.py` 的 `read_history` 方法中按服务器类型实现具体调用

**Q: Docker 容器无法访问宿主机上的 OPC UA 服务器？**
- 将 `OPCUA_ENDPOINT` 中的 `localhost` 改为 `host.docker.internal`（Windows/Mac）
- 或使用宿主机实际 IP 地址
