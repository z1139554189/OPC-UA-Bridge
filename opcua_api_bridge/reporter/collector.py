"""
OPC UA 数据采集器
从 REST API 桥接器拉取数据，支持：
  - 当前值（单个 / 批量）
  - 历史趋势（指定时间范围）
  - 节点树扫描
"""

import httpx
import asyncio
from datetime import datetime, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class OpcUaCollector:
    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ──────────────────────────────────────────────
    # 健康检查
    # ──────────────────────────────────────────────
    def check_health(self) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.base_url}/health")
            resp.raise_for_status()
            return resp.json()

    # ──────────────────────────────────────────────
    # 浏览节点树
    # ──────────────────────────────────────────────
    def browse_nodes(self, parent_node_id: Optional[str] = None) -> list[dict]:
        params = {}
        if parent_node_id:
            params["node_id"] = parent_node_id
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.base_url}/api/v1/nodes", params=params)
            resp.raise_for_status()
            return resp.json()

    # ──────────────────────────────────────────────
    # 读单个节点当前值
    # ──────────────────────────────────────────────
    def read_value(self, node_id: str) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(
                f"{self.base_url}/api/v1/nodes/{node_id}/value"
            )
            resp.raise_for_status()
            return resp.json()

    # ──────────────────────────────────────────────
    # 批量读取当前值
    # ──────────────────────────────────────────────
    def batch_read(self, node_ids: list[str]) -> list[dict]:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.base_url}/api/v1/nodes/batch-read",
                json=node_ids,
            )
            resp.raise_for_status()
            return resp.json()

    # ──────────────────────────────────────────────
    # 读历史数据
    # ──────────────────────────────────────────────
    def read_history(
        self,
        node_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        hours_back: int = 24,
    ) -> list[dict]:
        """
        读取节点历史数据。
        如果不传 start_time / end_time，默认取最近 hours_back 小时。
        """
        if end_time is None:
            end_time = datetime.now()
        if start_time is None:
            start_time = end_time - timedelta(hours=hours_back)

        params = {
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(
                f"{self.base_url}/api/v1/nodes/{node_id}/history",
                params=params,
            )
            resp.raise_for_status()
            # API 返回 {"node_id": "...", "history": [...], "count": N}
            return resp.json().get("history", [])

    # ──────────────────────────────────────────────
    # 一次性采集所有数据（报表用）
    # ──────────────────────────────────────────────
    def collect_all(
        self,
        fixed_nodes: list[str],
        history_nodes: list[str],
        history_hours: int = 24,
    ) -> dict:
        """
        汇总采集，返回结构化数据供报表生成器使用。

        参数:
            fixed_nodes    - 需要读当前值的固定节点列表
            history_nodes  - 需要读历史趋势的节点列表
            history_hours  - 历史回溯小时数，默认 24h
        """
        result = {
            "collected_at": datetime.now().isoformat(),
            "health": {},
            "realtime": [],
            "history": {},
        }

        # 1. 健康检查
        try:
            result["health"] = self.check_health()
        except Exception as e:
            result["health"] = {"error": str(e)}
            logger.warning(f"健康检查失败: {e}")

        # 2. 批量读当前值
        if fixed_nodes:
            try:
                batch_result = self.batch_read(fixed_nodes)
                # API 返回 {"results": [...], "count": N}，提取 results 数组
                result["realtime"] = batch_result.get("results", [])
            except Exception as e:
                logger.warning(f"批量读取失败: {e}")
                # 降级：逐个读取
                for nid in fixed_nodes:
                    try:
                        result["realtime"].append(self.read_value(nid))
                    except Exception as e2:
                        result["realtime"].append(
                            {"node_id": nid, "error": str(e2)}
                        )

        # 3. 历史数据
        for nid in history_nodes:
            try:
                result["history"][nid] = self.read_history(
                    nid, hours_back=history_hours
                )
            except Exception as e:
                logger.warning(f"历史数据读取失败 {nid}: {e}")
                result["history"][nid] = []

        return result
