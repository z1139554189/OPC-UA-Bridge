# -*- coding: utf-8 -*-
"""
SCF Web 函数 - OPC UA 数据查询 API
使用 Flask 框架，SCF 会自动分配 HTTP 访问地址

环境变量（在 SCF 控制台设置）：
  DB_HOST=sh-cynosdbmysql-grp-4f512ckw.sql.tencentcdb.com
  DB_PORT=21397
  DB_NAME=opcua_db
  DB_USER=opcua_user
  DB_PASSWORD=（控制台设置）
"""

import os
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Flask, request, jsonify
import pymysql
from pymysql.cursors import DictCursor

app = Flask(__name__)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _get_conn():
    """获取 MySQL 连接"""
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "sh-cynosdbmysql-grp-4f512ckw.sql.tencentcdb.com"),
        port=int(os.environ.get("DB_PORT", "3306")),
        database=os.environ.get("DB_NAME", "opcua_db"),
        user=os.environ.get("DB_USER", "opcua_user"),
        password=os.environ.get("DB_PASSWORD", ""),
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=5,
    )


class MyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(obj, Decimal):
            return round(float(obj), 2)
        if isinstance(obj, bytes):
            return obj.decode("utf-8")
        return super().default(obj)

app.json_encoder = MyEncoder
app.json.default_encoder = MyEncoder


def _display_name(node_id):
    """ns=1;s=FIT_05R201F01.PV -> FIT_05R201F01.PV"""
    if "=" in node_id:
        return node_id.split("=", 1)[-1].replace(";", ".", 1)
    return node_id


@app.after_request
def _add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# ====================================================================== #
#  API 路由
# ====================================================================== #

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "name": "OPC UA Cloud API",
        "version": "2.0.0 (Web Function)",
        "endpoints": {
            "GET /health": "health check",
            "GET /api/latest?node_id=xxx": "query realtime values",
            "GET /api/history?node_id=xxx&hours=1": "query history data",
            "GET /api/nodes": "list all tracked nodes",
            "GET /api/stats": "statistics overview",
            "POST /api/batch-latest": "batch query (body: {node_ids: [...]})",
        },
    })


@app.route("/health", methods=["GET"])
def handle_health():
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT NOW() as now, COUNT(*) as total FROM node_latest")
            row = cur.fetchone()
        conn.close()
        return jsonify({
            "status": "healthy",
            "db_time": row["now"],
            "tracked_nodes": row["total"],
        })
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


@app.route("/api/latest", methods=["GET"])
def handle_latest():
    node_id = request.args.get("node_id")
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
            entry["display_name"] = _display_name(entry["node_id"])
            entry.pop("id", None)
            result.append(entry)

        return jsonify({"data": result, "count": len(result)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/history", methods=["GET"])
def handle_history():
    node_id = request.args.get("node_id")
    if not node_id:
        return jsonify({"error": "node_id is required"}), 400

    end_time = request.args.get("end_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if request.args.get("start_time"):
        start_time = request.args["start_time"]
    else:
        hours = int(request.args.get("hours", "1"))
        start_time = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

    max_points = min(int(request.args.get("max_points", "1000")), 10000)

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

        return jsonify({
            "data": rows,
            "count": len(rows),
            "node_id": node_id,
            "time_range": {"start": start_time, "end": end_time},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/nodes", methods=["GET"])
def handle_nodes():
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

        return jsonify({"data": result, "count": len(result)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stats", methods=["GET"])
def handle_stats():
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

        return jsonify({
            "tracked_nodes": node_count,
            "total_history_records": history_count,
            "latest_data_time": latest,
            "db_size_mb": round(db_size_bytes / 1024 / 1024, 2),
            "recent_pushes": recent_pushes,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/batch-latest", methods=["POST"])
def handle_batch_latest():
    body = request.get_json(silent=True) or {}
    node_ids = body.get("node_ids", [])
    if not node_ids:
        return jsonify({"error": "node_ids cannot be empty"}), 400

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
            entry["display_name"] = _display_name(nid)
            entry.pop("id", None)
            data_map[nid] = entry

        result = []
        for nid in node_ids:
            if nid in data_map:
                result.append(data_map[nid])
            else:
                result.append({
                    "node_id": nid,
                    "display_name": _display_name(nid),
                    "value": None,
                    "quality": "N/A",
                    "timestamp": None,
                })

        return jsonify({"data": result, "count": len(result)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000)
