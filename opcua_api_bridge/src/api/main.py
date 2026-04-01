"""
OPC UA REST API 桥接器 - 主应用入口
Author: WorkBuddy SRE
Version: 1.0.0
"""

import os
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import structlog
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.monitoring.metrics import metrics_registry
from src.monitoring.health import HealthCheck
from src.opcua_client.client import OPCUAClient

# 配置结构化日志
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ]
)
logger = structlog.get_logger()

# OPC UA 客户端实例
opcua_client: Optional[OPCUAClient] = None

# 认证
security = HTTPBearer()

# 应用生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    global opcua_client
    logger.info("opcua_bridge_starting", version="1.0.0")
    
    try:
        # 初始化 OPC UA 客户端（不使用安全策略）
        opcua_client = OPCUAClient(
            endpoint=settings.OPCUA_ENDPOINT,
            username=settings.OPCUA_USERNAME,
            password=settings.OPCUA_PASSWORD,
            security_policy=None,  # 无安全策略
            security_mode=None      # 无安全模式
        )
        await opcua_client.connect()
        logger.info("opcua_connected", endpoint=settings.OPCUA_ENDPOINT)
    except Exception as e:
        logger.error("opcua_connection_failed", error=str(e))
        # 应用可以启动，但 OPC UA 功能不可用
    
    yield
    
    # 关闭时
    if opcua_client:
        await opcua_client.disconnect()
    logger.info("opcua_bridge_shutdown")

# 创建 FastAPI 应用
app = FastAPI(
    title="OPC UA REST API Bridge",
    description="轻量级 OPC UA 到 REST API 的桥接器",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """全局异常处理"""
    logger.error("unexpected_error", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"}
    )

# 健康检查路由
@app.get("/health", tags=["监控"])
async def health_check():
    """系统健康检查"""
    health = HealthCheck(opcua_client)
    status_result = await health.check_all()
    
    if status_result["status"] == "healthy":
        return status_result
    else:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=status_result
        )

# 节点管理路由
@app.get("/api/v1/nodes", tags=["节点管理"])
async def get_nodes(
    node_id: Optional[str] = Query(None, description="起始节点ID，不填则从 Objects 根节点开始"),
    namespace: Optional[int] = Query(None, description="命名空间过滤，如 1 只返回 ns=1 的节点"),
    recursive: bool = Query(False, description="是否递归（默认关闭，数据量大时容易超时）"),
    limit: int = Query(200, ge=1, le=1000, description="单次返回最大节点数"),
    offset: int = Query(0, ge=0, description="分页偏移量")
):
    """
    获取 OPC UA 节点列表（分页）
    - 不传 node_id 默认从 Objects 节点（ns=0;i=85）开始
    - 服务器共有 3000+ 节点，建议使用 offset/limit 分页
    """
    if not opcua_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPC UA 客户端未连接"
        )

    try:
        nodes = await opcua_client.browse_nodes(
            node_id=node_id,
            namespace=namespace,
            recursive=recursive,
            max_nodes=limit,
            offset=offset
        )
        return {"nodes": nodes, "count": len(nodes), "offset": offset, "limit": limit}
    except Exception as e:
        logger.error("get_nodes_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取节点失败: {str(e)}"
        )

# 读取节点值
@app.get("/api/v1/nodes/{node_id}/value", tags=["数据读取"])
async def read_node_value(node_id: str):
    """
    读取指定节点的当前值
    """
    if not opcua_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPC UA 客户端未连接"
        )
    
    try:
        value = await opcua_client.read_value(node_id)
        return {
            "node_id": node_id,
            "value": value["value"],
            "timestamp": value["timestamp"],
            "quality": value["quality"],
            "data_type": value["data_type"]
        }
    except Exception as e:
        logger.error("read_node_value_failed", node_id=node_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"读取节点值失败: {str(e)}"
        )

# 批量读取节点值
@app.post("/api/v1/nodes/batch-read", tags=["数据读取"])
async def batch_read_values(node_ids: list[str]):
    """批量读取多个节点的值（通过订阅缓存）"""
    if not opcua_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPC UA 客户端未连接"
        )

    try:
        results = await opcua_client.batch_read(node_ids)
        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.error("batch_read_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"批量读取失败: {str(e)}"
        )

# 查看订阅缓存状态
@app.get("/api/v1/cache/stats", tags=["监控"])
async def cache_stats():
    """查看订阅缓存统计"""
    if not opcua_client:
        raise HTTPException(status_code=503, detail="OPC UA 客户端未连接")
    return {
        "cache_size": opcua_client.get_cache_size(),
        "subscribed_count": opcua_client.get_subscribed_count(),
        "connected": opcua_client.is_connected(),
    }

# 读取历史数据
@app.get("/api/v1/nodes/{node_id}/history", tags=["历史数据"])
async def read_history(
    node_id: str,
    start_time: str = Query(..., description="开始时间 (ISO格式)"),
    end_time: str = Query(..., description="结束时间 (ISO格式)"),
    max_points: int = Query(1000, description="最大数据点数")
):
    """读取节点的历史数据"""
    if not opcua_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPC UA 客户端未连接"
        )
    
    try:
        history = await opcua_client.read_history(
            node_id=node_id,
            start_time=start_time,
            end_time=end_time,
            max_points=max_points
        )
        return {
            "node_id": node_id,
            "history": history,
            "count": len(history)
        }
    except Exception as e:
        logger.error("read_history_failed", node_id=node_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"读取历史数据失败: {str(e)}"
        )

# Prometheus 指标端点
@app.get("/metrics", tags=["监控"])
async def metrics():
    """Prometheus 指标端点"""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from starlette.responses import Response
    
    return Response(
        content=generate_latest(metrics_registry),
        media_type=CONTENT_TYPE_LATEST
    )

# 根路由
@app.get("/")
async def root():
    """API 根路径"""
    return {
        "name": "OPC UA REST API Bridge",
        "version": "1.0.0",
        "status": "operational" if opcua_client else "degraded",
        "docs": "/docs" if settings.DEBUG else None,
        "health": "/health"
    }

# 启动说明
if __name__ == "__main__":
    import uvicorn
    
    logger.info("starting_opcua_bridge")
    uvicorn.run(
        "src.api.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="info"
    )