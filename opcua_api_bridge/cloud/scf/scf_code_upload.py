"""
SCF 云函数入口文件
提供 OPC UA 数据只读查询 API

部署到腾讯云 SCF 后，由 API 网关触发。

环境变量（在 SCF 控制台设置）：
  DB_HOST=172.17.0.2
  DB_PORT=3306
  DB_NAME=opcua_db
  DB_USER=opcua_user
  DB_PASSWORD=Admin_00
"""

import json
import os
import logging
from datetime import datetime, timedelta
from decimal import Decimal

import pymysql
from pymysql.cursors import DictCursor

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _get_conn():
    """获取 MySQL 连接"""
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "172.17.0.2"),
        port=int(os.environ.get("DB_PORT", "3306")),
        database=os.environ.get("DB_NAME", "opcua_db"),
        user=os.environ.get("DB_USER", "opcua_user"),
        password=os.environ.get("DB_PASSWORD", ""),
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=5,
    )


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(obj, Decimal):
        return round(float(obj), 2)
    if isinstance(obj, bytes):
        return obj.decode("utf-8")
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _response(data, status_code=200):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(data, ensure_ascii=False, default=_json_default),
    }


def handle_health(params):
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT NOW() as now, COUNT(*) as total FROM node_latest")
            row = cur.fetchone()
        conn.close()
        return _response({
            "status": "healthy",
            "db_time": row["now"],
            "tracked_nodes": row["total"],
        })
    except Exception as e:
        return _response({"status": "unhealthy", "error": str(e)}, 500)


def handle_latest(params):
    node_id = params.get("node_id")
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            if node_id:
                cur.execute("SELECT * FROM node_latest WHERE node_id = %s", (node_id,))
            else:
                cur.execute("SELECT * FROM node_latest ORDER BY timestamp DESC LIMIT 1000")
            rows = cur.fetchall()
        conn.close()

        result = []
        for row in rows:
            entry = dict(row)
            nid = entry["node_id"]
            entry["display_name"] = nid.split("=", 1)[-1].replace(";", ".", 1) if "=" in nid else nid
            entry.pop("id", None)
            result.append(entry)

        return _response({"data": result, "count": len(result)})
    except Exception as e:
        return _response({"error": str(e)}, 500)


def handle_history(params):
    node_id = params.get("node_id")
    if not node_id:
        return _response({"error": "node_id is required"}, 400)

    end_time = params.get("end_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if params.get("start_time"):
        start_time = params["start_time"]
    else:
        hours = int(params.get("hours", "1"))
        start_time = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

    max_points = min(int(params.get("max_points", "1000")), 10000)

    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT timestamp, value, quality, source_timestamp
                   FROM node_history
                   WHERE node_id = %s AND timestamp BETWEEN %s AND %s
                   ORDER BY timestamp ASC LIMIT %s""",
                (node_id, start_time, end_time, max_points),
            )
            rows = cur.fetchall()
        conn.close()

        return _response({
            "data": rows, "count": len(rows),
            "node_id": node_id,
            "time_range": {"start": start_time, "end": end_time},
        })
    except Exception as e:
        return _response({"error": str(e)}, 500)


def handle_nodes(params):
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT node_id,
                          SUBSTRING_INDEX(node_id, ';', -1) as display_name,
                          value, quality, timestamp, updated_at
                   FROM node_latest ORDER BY timestamp DESC"""
            )
            rows = cur.fetchall()

            cur.execute(
                """SELECT node_id, COUNT(*) as history_count
                   FROM node_history GROUP BY node_id ORDER BY history_count DESC LIMIT 100"""
            )
            history_counts = {row["node_id"]: row["history_count"] for row in cur.fetchall()}
        conn.close()

        result = []
        for row in rows:
            entry = dict(row)
            entry["history_count"] = history_counts.get(row["node_id"], 0)
            result.append(entry)

        return _response({"data": result, "count": len(result)})
    except Exception as e:
        return _response({"error": str(e)}, 500)


def handle_stats(params):
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM node_latest")
            node_count = cur.fetchone()["cnt"]

            cur.execute("SELECT COUNT(*) as cnt FROM node_history")
            history_count = cur.fetchone()["cnt"]

            cur.execute("SELECT MAX(timestamp) as latest FROM node_history")
            latest = cur.fetchone()["latest"]

            cur.execute("SELECT * FROM push_log ORDER BY push_time DESC LIMIT 10")
            recent_pushes = cur.fetchall()

            cur.execute(
                """SELECT SUM(data_length + index_length) as total_size
                   FROM information_schema.TABLES
                   WHERE table_schema = %s
                   AND table_name IN ('node_history', 'node_latest', 'push_log')""",
                (os.environ.get("DB_NAME", "opcua_db"),),
            )
            db_size_bytes = cur.fetchone()["total_size"] or 0
        conn.close()

        return _response({
            "tracked_nodes": node_count,
            "total_history_records": history_count,
            "latest_data_time": latest,
            "db_size_mb": round(db_size_bytes / 1024 / 1024, 2),
            "recent_pushes": recent_pushes,
        })
    except Exception as e:
        return _response({"error": str(e)}, 500)


def handle_batch_latest(body):
    node_ids = body.get("node_ids", [])
    if not node_ids:
        return _response({"error": "node_ids cannot be empty"}, 400)

    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(node_ids))
            cur.execute(
                f"SELECT * FROM node_latest WHERE node_id IN ({placeholders})",
                tuple(node_ids),
            )
            rows = cur.fetchall()
        conn.close()

        data_map = {}
        for row in rows:
            entry = dict(row)
            nid = entry["node_id"]
            entry["display_name"] = nid.split("=", 1)[-1].replace(";", ".", 1) if "=" in nid else nid
            entry.pop("id", None)
            data_map[nid] = entry

        result = []
        for nid in node_ids:
            if nid in data_map:
                result.append(data_map[nid])
            else:
                result.append({
                    "node_id": nid,
                    "display_name": nid.split("=", 1)[-1].replace(";", ".", 1) if "=" in nid else nid,
                    "value": None, "quality": "N/A", "timestamp": None,
                })

        return _response({"data": result, "count": len(result)})
    except Exception as e:
        return _response({"error": str(e)}, 500)


ROUTE_MAP = {
    "/health": ("GET", handle_health),
    "/api/latest": ("GET", handle_latest),
    "/api/history": ("GET", handle_history),
    "/api/nodes": ("GET", handle_nodes),
    "/api/stats": ("GET", handle_stats),
    "/api/batch-latest": ("POST", handle_batch_latest),
}


def main_handler(event, context):
    http_method = event.get("httpMethod", "GET")
    if http_method == "OPTIONS":
        return _response({"message": "OK"})

    path = event.get("path", "/").rstrip("/")

    handler = None
    for route_path, (method, route_handler) in ROUTE_MAP.items():
        if path == route_path or path.startswith(route_path + "/"):
            if method == http_method:
                handler = route_handler
            else:
                return _response({"error": f"Method {http_method} not allowed"}, 405)
            break

    if not handler:
        return _response({
            "name": "OPC UA Cloud API",
            "version": "1.0.0",
            "endpoints": {
                "GET /health": "health check",
                "GET /api/latest?node_id=xxx": "query realtime values",
                "GET /api/history?node_id=xxx&hours=1": "query history data",
                "GET /api/nodes": "list all tracked nodes",
                "GET /api/stats": "statistics overview",
                "POST /api/batch-latest": "batch query (body: {node_ids: [...]})",
            },
        })

    if http_method == "GET":
        params = event.get("queryStringParameters") or {}
        return handler(params)
    elif http_method == "POST":
        body = event.get("body", "")
        try:
            body_data = json.loads(body) if body else {}
        except (json.JSONDecodeError, TypeError):
            body_data = {}
        return handler(body_data)
    else:
        return _response({"error": "Unsupported method"}, 405)
