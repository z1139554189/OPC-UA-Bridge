#!/usr/bin/env python3
"""
磺化釜搅拌电机电流自动化分析报告
- 从 SQLite 全量数据读取 10 台电机电流
- 剔除停机数据（电流 < stopped_threshold）
- 生成 HTML 报告（含趋势图、统计表）
用法:
  python motor_current_report.py --days 1    # 昨日报告
  python motor_current_report.py --days 7    # 过去7天报告
  python motor_current_report.py --start 2026-06-01 --end 2026-06-05
"""

import argparse
import sqlite3
import os
import sys
from datetime import datetime, timedelta
import json

# ─── 配置 ────────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "history.db"
)

MOTOR_NODES = [
    "ns=1;s=IIAS_05A102.PV",
    "ns=1;s=IIAS_05A103.PV",
    "ns=1;s=IIAS_05A104.PV",
    "ns=1;s=IIAS_05A105.PV",
    "ns=1;s=IIAS_05A106.PV",
    "ns=1;s=IIAS_05A107.PV",
    "ns=1;s=IIAS_05A108.PV",
    "ns=1;s=IIAS_05A109.PV",
    "ns=1;s=IIAS_05A110.PV",
    "ns=1;s=IIAS_05A111.PV",
]

# 电流低于此值视为停机（A）
STOPPED_THRESHOLD = 0.5

# 报告输出目录
REPORT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "reports"
)

def _safe_table(node_id: str) -> str:
    return "h_" + node_id.replace("=", "_eq_").replace(";", "_sc_").replace(".", "_dot_")


def display_name(node_id: str) -> str:
    """从完整 NodeID 提取显示名称，如 ns=1;s=IIAS_05A102.PV → IIAS_05A102.PV"""
    if ";s=" in node_id:
        return node_id.split(";s=", 1)[-1]
    if "s=" in node_id:
        return node_id.rsplit("s=", 1)[-1]
    return node_id


def read_full_data(
    db: sqlite3.Connection,
    node_id: str,
    start_time: str,
    end_time_excl: str,
) -> list:
    """读取全量数据（不做降采样）
    end_time_excl: 独占上限，格式 YYYY-MM-DDTHH:MM:SS，用 < 比较
    """
    table = _safe_table(node_id)
    cursor = db.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    )
    if not cursor.fetchone():
        return []

    cursor.execute(
        f"""
        SELECT timestamp, value
        FROM [{table}]
        WHERE timestamp >= ? AND timestamp < ?
        ORDER BY timestamp ASC
        """,
        (start_time, end_time_excl)
    )
    rows = cursor.fetchall()
    return [
        {"timestamp": r[0], "value": r[1]}
        for r in rows
        if r[1] is not None
    ]


def filter_running(data: list) -> list:
    """剔除停机数据（电流 < STOPPED_THRESHOLD）"""
    return [d for d in data if d["value"] >= STOPPED_THRESHOLD]


def compute_stats(data: list) -> dict:
    """计算统计数据"""
    if not data:
        return {
            "count": 0, "avg": None, "max": None, "min": None,
            "std": None, "runtime_hours": 0, "data_start": None, "data_end": None,
        }

    values = [d["value"] for d in data]
    n = len(values)
    avg = sum(values) / n
    mx = max(values)
    mn = min(values)
    std = (sum((v - avg) ** 2 for v in values) / n) ** 0.5

    # 运行时长估算（数据点间隔约5s，按实际时间戳计算更准）
    t0 = data[0]["timestamp"]
    t1 = data[-1]["timestamp"]
    try:
        dt0 = datetime.fromisoformat(t0)
        dt1 = datetime.fromisoformat(t1)
        runtime_hours = (dt1 - dt0).total_seconds() / 3600
    except Exception:
        runtime_hours = n * 5 / 3600  #  fallback

    return {
        "count": n,
        "avg": round(avg, 2),
        "max": round(mx, 2),
        "min": round(mn, 2),
        "std": round(std, 2),
        "runtime_hours": round(runtime_hours, 1),
        "data_start": t0,
        "data_end": t1,
    }


def build_chart_js_data(node_id: str, data_all: list, data_running: list) -> dict:
    """构造 Chart.js 所需数据（降采样到最多 500 点用于绘图，分析仍用全量）"""
    def downsample(lst, max_pts=500):
        if len(lst) <= max_pts:
            return lst
        step = len(lst) // max_pts
        return [lst[i] for i in range(0, len(lst), step)][:max_pts]

    running = downsample(data_running)
    all_downsampled = downsample(data_all)

    labels = [d["timestamp"] for d in all_downsampled]
    all_values = [d["value"] for d in all_downsampled]

    running_labels = [d["timestamp"] for d in running]
    running_values = [d["value"] for d in running]

    return {
        "labels": labels,
        "all_values": all_values,
        "running_labels": running_labels,
        "running_values": running_values,
        "node_id": node_id,
    }


def generate_html_report(results: list, period_desc: str, generated_at: str) -> str:
    """生成 HTML 报告"""
    # 准备 Chart.js 数据
    charts_json = json.dumps(
        {r["node_id"]: r["chart_data"] for r in results},
        ensure_ascii=False
    )

    # 统计表格行
    table_rows = ""
    for r in results:
        s = r["stats"]
        node = display_name(r["node_id"])
        if s["count"] == 0:
            table_rows += f"""
            <tr>
                <td>{node}</td>
                <td colspan="7" style="color:#999">无有效数据</td>
            </tr>"""
            continue
        total = r["stats_all"]["count"]
        running = s["count"]
        stop_pct = round((total - running) / total * 100, 1) if total else 0
        table_rows += f"""
            <tr>
                <td>{node}</td>
                <td>{s['avg']}</td>
                <td>{s['max']}</td>
                <td>{s['min']}</td>
                <td>{s['std']}</td>
                <td>{s['runtime_hours']}h</td>
                <td>{stop_pct}%</td>
                <td>{s['data_start'][:19] if s['data_start'] else '-'}</td>
                <td>{s['data_end'][:19] if s['data_end'] else '-'}</td>
            </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>磺化釜搅拌电机电流分析报告 — {period_desc}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, 'Microsoft YaHei', sans-serif; background: #f0f2f5; color: #333; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .subtitle {{ color: #888; font-size: 13px; margin-bottom: 20px; }}
  .card {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.08);
             padding: 20px; margin-bottom: 20px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid #eee; text-align: center; }}
  th {{ background: #fafafa; font-weight: 600; position: sticky; top: 0; }}
  tr:hover {{ background: #f8f9ff; }}
  .chart-box {{ position: relative; height: 260px; margin-top: 10px; }}
  .motor-section {{ margin-bottom: 30px; }}
  .motor-title {{ font-size: 15px; font-weight: 600; margin-bottom: 10px; color: #1a1a1a; }}
  .summary-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 12px; margin-bottom: 20px;
  }}
  .summary-card {{
    background: #fff; border-radius: 8px; padding: 14px; box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    text-align: center;
  }}
  .summary-card .val {{ font-size: 24px; font-weight: 700; color: #1a73e8; }}
  .summary-card .lbl {{ font-size: 12px; color: #888; margin-top: 4px; }}
  .tag-running {{ background: #e8f5e9; color: #2e7d32; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
  .tag-stopped {{ background: #fce4ec; color: #c62828; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
  .footer {{ text-align: center; color: #aaa; font-size: 12px; margin-top: 30px; }}
</style>
</head>
<body>
<div class="container">
  <h1>⚡ 磺化釜搅拌电机电流分析报告</h1>
  <p class="subtitle">报告周期：{period_desc} &nbsp;|&nbsp; 生成时间：{generated_at} &nbsp;|&nbsp; 数据来源：OPC UA 全量历史数据（已剔除停机）</p>

  <!-- 汇总卡片 -->
  <div class="summary-grid">
    <div class="summary-card">
      <div class="val">{len(results)}</div>
      <div class="lbl">监控电机数</div>
    </div>
    <div class="summary-card">
      <div class="val">{sum(1 for r in results if r['stats']['count'] > 0)}</div>
      <div class="lbl">有有效数据</div>
    </div>
    <div class="summary-card">
      <div class="val">{round(sum(r['stats']['avg'] for r in results if r['stats']['avg'] is not None) / max(1, sum(1 for r in results if r['stats']['avg'] is not None)), 1)}</div>
      <div class="lbl">平均电流（A）</div>
    </div>
    <div class="summary-card">
      <div class="val">{max((r['stats']['max'] for r in results if r['stats']['max'] is not None), default=0)}</div>
      <div class="lbl">最高电流（A）</div>
    </div>
  </div>

  <!-- 统计表格 -->
  <div class="card">
    <h3 style="margin-bottom:12px">📊 电机运行统计</h3>
    <table>
      <thead>
        <tr>
          <th>电机节点</th>
          <th>平均电流 (A)</th>
          <th>最大电流 (A)</th>
          <th>最小电流 (A)</th>
          <th>标准差 (A)</th>
          <th>运行时长</th>
          <th>停机占比</th>
          <th>数据起始</th>
          <th>数据结束</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </div>

  <!-- 各电机趋势图 -->
  <div class="card">
    <h3 style="margin-bottom:12px">📈 电流趋势（全量数据，已剔除停机）</h3>
"""

    # 每个电机一张图
    for r in results:
        node = display_name(r["node_id"])
        html += f"""
    <div class="motor-section">
      <div class="motor-title">{node}</div>
      <div class="chart-box">
        <canvas id="chart_{node.replace('.', '_')}"></canvas>
      </div>
    </div>
"""

    # Chart.js 脚本
    charts_data = {r["node_id"]: r["chart_data"] for r in results}
    charts_json_escaped = json.dumps(charts_data, ensure_ascii=False)

    html += f"""
  </div>

  <div class="footer">由 OPC UA Bridge 自动生成 — {generated_at}</div>
</div>

<script>
const charts = {charts_json_escaped};

Object.keys(charts).forEach(nodeId => {{
  const d = charts[nodeId];
  const ctx = document.getElementById('chart_' + nodeId.replace('.', '_')).getContext('2d');
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: d.running_labels,
      datasets: [{{
        label: '电流 (A)',
        data: d.running_values,
        borderColor: '#1a73e8',
        backgroundColor: 'rgba(26,115,232,0.08)',
        borderWidth: 1.5,
        pointRadius: 0,
        fill: true,
        tension: 0.3,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {{
        x: {{ ticks: {{ maxTicksLimit: 8, font: {{ size: 10 }} }} }},
        y: {{ title: {{ display: true, text: '电流 (A)', font: {{ size: 11 }} }}, beginAtZero: true }}
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ mode: 'index', intersect: false }}
      }}
    }}
  }});
}});
</script>
</body>
</html>"""

    return html


def main():
    parser = argparse.ArgumentParser(description="磺化釜电机电流分析报告")
    parser.add_argument("--days", type=int, default=1, help="分析过去 N 天（默认1天，即今日）")
    parser.add_argument("--today", action="store_true", help="仅分析今天（从零点到现在）")
    parser.add_argument("--yesterday", action="store_true", help="分析昨天全天（00:00~23:59，适合定时日报）")
    parser.add_argument("--start", type=str, default=None, help="开始时间 YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="结束时间 YYYY-MM-DD")
    args = parser.parse_args()

    now = datetime.now()

    if args.start and args.end:
        start_dt = datetime.fromisoformat(args.start)
        end_dt = datetime.fromisoformat(args.end) + timedelta(days=1)
        period_desc = f"{args.start} ~ {args.end}"
    elif args.today:
        # 今天零点到当前
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        period_desc = now.strftime("%Y-%m-%d") + "（截至当前）"
    elif args.yesterday:
        # 昨天全天：昨天 00:00:00 ~ 今天 00:00:00
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = today_midnight - timedelta(days=1)
        end_dt = today_midnight
        period_desc = start_dt.strftime("%Y-%m-%d") + "（昨日全天）"
    else:
        # 过去 N 天（含今天）：从 N 天前零点 到 现在
        end_dt = now
        start_dt = (now - timedelta(days=args.days)).replace(hour=0, minute=0, second=0, microsecond=0)
        if args.days == 1:
            period_desc = now.strftime("%Y-%m-%d") + "（今日）"
        else:
            period_desc = f"过去 {args.days} 天（{start_dt.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')}）"

    start_time = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
    # end_time_excl：独占上限，传给 SQL 的 < 比较
    end_time_excl = (end_dt + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S")
    # end_time_display：仅用于日志展示
    end_time_display = end_dt.strftime("%Y-%m-%dT%H:%M:%S") if not (args.start and args.end) else end_dt.strftime("%Y-%m-%dT00:00:00")

    generated_at = now.strftime("%Y-%m-%d %H:%M:%S")

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    results = []
    for node_id in MOTOR_NODES:
        print(f"  读取 {display_name(node_id)}...", end=" ", flush=True)
        data_all = read_full_data(db, node_id, start_time, end_time_excl)
        data_running = filter_running(data_all)
        stats_all = compute_stats(data_all)
        stats = compute_stats(data_running)
        chart_data = build_chart_js_data(node_id, data_all, data_running)
        results.append({
            "node_id": node_id,
            "data_all": data_all,
            "data_running": data_running,
            "stats_all": stats_all,
            "stats": stats,
            "chart_data": chart_data,
        })
        print(f"全量 {stats_all['count']} 点，运行 {stats['count']} 点")

    db.close()

    # 生成 HTML
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = os.path.join(
        REPORT_DIR,
        f"motor_report_{now.strftime('%Y%m%d_%H%M%S')}.html"
    )
    html = generate_html_report(results, period_desc, generated_at)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ 报告已生成：{report_file}")
    return report_file


if __name__ == "__main__":
    main()
