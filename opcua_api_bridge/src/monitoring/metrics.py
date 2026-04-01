"""
Prometheus 指标注册
Author: WorkBuddy SRE
Version: 1.0.0
"""

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    REGISTRY,
)

# 使用默认注册表（与 /metrics 端点集成）
metrics_registry = REGISTRY

# ─── 应用信息 ────────────────────────────────────────────────
app_info = Info("opcua_bridge_app", "OPC UA Bridge 应用信息")
app_info.info({
    "version": "1.0.0",
    "service": "opcua-api-bridge",
})

# ─── OPC UA 连接状态 ─────────────────────────────────────────
opcua_connection_status = Gauge(
    "opcua_connection_status",
    "OPC UA 连接状态（1=已连接，0=断开）",
)

opcua_reconnect_total = Counter(
    "opcua_reconnect_total",
    "OPC UA 重连次数",
)

opcua_node_count = Gauge(
    "opcua_node_count",
    "当前浏览到的节点数量",
)

# ─── API 请求指标 ────────────────────────────────────────────
http_requests_total = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# ─── OPC UA 操作指标 ─────────────────────────────────────────
opcua_operations_total = Counter(
    "opcua_operations_total",
    "OPC UA 操作总数",
    ["operation", "status"],   # operation: read/browse/history; status: ok/error
)

opcua_operation_duration_seconds = Histogram(
    "opcua_operation_duration_seconds",
    "OPC UA 操作耗时（秒）",
    ["operation"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

opcua_data_quality = Counter(
    "opcua_data_quality_total",
    "OPC UA 数据质量计数",
    ["quality"],   # Good / Uncertain / Bad
)

# ─── 缓存指标 ────────────────────────────────────────────────
cache_hits_total = Counter(
    "cache_hits_total",
    "缓存命中次数",
    ["cache_type"],
)

cache_misses_total = Counter(
    "cache_misses_total",
    "缓存未命中次数",
    ["cache_type"],
)

cache_size = Gauge(
    "cache_size",
    "当前缓存条目数",
    ["cache_type"],
)

# ─── 便捷函数 ────────────────────────────────────────────────

def record_http_request(method: str, endpoint: str, status_code: int, duration: float):
    """记录一次 HTTP 请求"""
    http_requests_total.labels(
        method=method,
        endpoint=endpoint,
        status_code=str(status_code),
    ).inc()
    http_request_duration_seconds.labels(
        method=method,
        endpoint=endpoint,
    ).observe(duration)


def record_opcua_operation(operation: str, status: str, duration: float):
    """记录一次 OPC UA 操作"""
    opcua_operations_total.labels(operation=operation, status=status).inc()
    opcua_operation_duration_seconds.labels(operation=operation).observe(duration)


def record_data_quality(quality: str):
    """记录数据质量"""
    opcua_data_quality.labels(quality=quality).inc()
