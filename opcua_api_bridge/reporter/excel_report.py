"""
Excel 报表生成器
将采集到的 OPC UA 数据写入 Excel，包含 4 个 Sheet：
  Sheet1 - 报表摘要（生成时间、连接状态、节点统计）
  Sheet2 - 实时值（所有节点当前值快照）
  Sheet3 - 历史趋势（时间序列展宽表）
  Sheet4 - 历史原始（长表格式，方便数据透视）
"""

import openpyxl
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, numbers
)
from openpyxl.chart import LineChart, Reference
from openpyxl.utils import get_column_letter
from datetime import datetime
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# ── 辅助函数 ───────────────────────────────────────

def _short_node(node_id: str) -> str:
    """ns=1;s=FIT_05R201F01.PV → FIT_05R201F01.PV"""
    # 先去掉 ns=X;s= 前缀
    if ";" in node_id:
        node_id = node_id.split(";", 1)[1]
    # 再去掉 s= 或 i= 等标识符前缀
    for prefix in ("s=", "i=", "g=", "b="):
        if node_id.startswith(prefix):
            node_id = node_id[len(prefix):]
            break
    return node_id


def _fmt_ts(ts: str) -> str:
    """截断 ISO 时间戳到秒：2026-04-01T13:35:43.123456 → 2026-04-01 13:35:43"""
    if not ts:
        return ""
    # 去掉时区后缀
    ts = ts.split("+")[0].split("Z")[0]
    # 替换 T 为空格
    ts = ts.replace("T", " ")
    # 截断到秒
    if "." in ts:
        ts = ts[:ts.index(".")]
    return ts


def _fmt_val(val) -> str:
    """数值保留两位小数，非数值原样返回"""
    if val is None:
        return ""
    try:
        return str(round(float(val), 2))
    except (ValueError, TypeError):
        return str(val)


# ── 颜色常量 ───────────────────────────────────────
COLOR_HEADER_BG   = "1F4E79"   # 深蓝
COLOR_HEADER_FONT = "FFFFFF"   # 白字
COLOR_GOOD        = "C6EFCE"   # 绿（Good 质量）
COLOR_BAD         = "FFC7CE"   # 红（Bad/Error）
COLOR_WARN        = "FFEB9C"   # 黄（Unknown）
COLOR_TITLE_BG    = "2E75B6"   # 标题行蓝
COLOR_ALT_ROW     = "EBF3FB"   # 隔行浅蓝


def _header_style(cell, *, bg=COLOR_HEADER_BG, font_color=COLOR_HEADER_FONT):
    cell.font = Font(bold=True, color=font_color, size=11)
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _border_all(ws, min_row, min_col, max_row, max_col):
    thin = Side(style="thin")
    for row in ws.iter_rows(min_row=min_row, min_col=min_col,
                             max_row=max_row, max_col=max_col):
        for cell in row:
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)


def _quality_fill(cell, quality: str):
    q = (quality or "").lower()
    if q == "good":
        cell.fill = PatternFill("solid", fgColor=COLOR_GOOD)
    elif q in ("bad", "error"):
        cell.fill = PatternFill("solid", fgColor=COLOR_BAD)
    else:
        cell.fill = PatternFill("solid", fgColor=COLOR_WARN)


# ══════════════════════════════════════════════════
# Sheet 1 — 摘要
# ══════════════════════════════════════════════════
def _write_summary(ws, data: dict):
    ws.title = "摘要"
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 40

    # 大标题
    ws.merge_cells("A1:B1")
    title = ws["A1"]
    title.value = "OPC UA 数据采集报表"
    title.font = Font(bold=True, size=16, color=COLOR_HEADER_FONT)
    title.fill = PatternFill("solid", fgColor=COLOR_TITLE_BG)
    title.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    headers = ["项目", "值"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col, value=h)
        _header_style(cell)

    health = data.get("health", {})
    realtime = data.get("realtime", [])
    history = data.get("history", {})

    rows = [
        ("报表生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("数据采集时间", _fmt_ts(data.get("collected_at", "")) or "-"),
        ("OPC UA 连接状态", "✅ 正常" if health.get("opcua_connected") else "❌ 断开"),
        ("服务内存占用 (MB)", health.get("memory_mb", "-")),
        ("实时节点数量", len(realtime)),
        ("历史数据节点数量", len(history)),
        ("历史记录总条数",
         sum(len(v) for v in history.values() if isinstance(v, list))),
        ("报表包含 Sheet", "摘要 / 实时值 / 历史趋势 / 历史原始"),
    ]

    for i, (k, v) in enumerate(rows, start=3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=str(v))
        if i % 2 == 0:
            for col in (1, 2):
                ws.cell(row=i, column=col).fill = PatternFill(
                    "solid", fgColor=COLOR_ALT_ROW
                )

    _border_all(ws, 2, 1, 2 + len(rows), 2)


# ══════════════════════════════════════════════════
# Sheet 2 — 实时值
# ══════════════════════════════════════════════════
def _write_realtime(ws, realtime: list[dict]):
    ws.title = "实时值"
    cols = ["节点 ID", "当前值", "数据类型", "质量", "时间戳", "错误信息"]
    widths = [36, 16, 14, 10, 26, 36]

    for col, (h, w) in enumerate(zip(cols, widths), 1):
        ws.column_dimensions[get_column_letter(col)].width = w
        cell = ws.cell(row=1, column=col, value=h)
        _header_style(cell)
    ws.row_dimensions[1].height = 24

    for i, node in enumerate(realtime, start=2):
        error = node.get("error", "")
        row_data = [
            _short_node(node.get("node_id", "")),
            _fmt_val(node.get("value")),
            node.get("data_type", ""),
            node.get("quality", "Unknown" if not error else "Error"),
            _fmt_ts(node.get("timestamp", "")),
            error,
        ]
        for col, val in enumerate(row_data, 1):
            cell = ws.cell(row=i, column=col, value=val)
            cell.alignment = Alignment(vertical="center")
            if i % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=COLOR_ALT_ROW)

        # 质量列着色
        _quality_fill(ws.cell(row=i, column=4), row_data[3])

    if realtime:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}1"
        ws.freeze_panes = "A2"

    _border_all(ws, 1, 1, max(len(realtime), 1) + 1, len(cols))


# ══════════════════════════════════════════════════
# Sheet 3 — 历史趋势（宽表 + 折线图）
# ══════════════════════════════════════════════════
def _write_history_wide(ws, history: dict):
    ws.title = "历史趋势"
    if not history:
        ws["A1"] = "暂无历史数据"
        return

    node_ids = list(history.keys())
    short_ids = [_short_node(n) for n in node_ids]

    # 收集所有时间戳，去重排序
    all_ts: set[str] = set()
    for records in history.values():
        for r in (records or []):
            ts = r.get("timestamp") or r.get("source_timestamp") or r.get("time")
            if ts:
                all_ts.add(_fmt_ts(ts))
    all_ts_sorted = sorted(all_ts)

    # 表头
    header_row = ["时间戳"] + short_ids
    col_widths = [20] + [max(20, len(n) + 4) for n in short_ids]
    for col, (h, w) in enumerate(zip(header_row, col_widths), 1):
        ws.column_dimensions[get_column_letter(col)].width = w
        cell = ws.cell(row=1, column=col, value=h)
        _header_style(cell)

    # 构建值查找表  node_id -> {ts -> value}
    lookup: dict[str, dict] = {}
    for nid, records in history.items():
        lookup[nid] = {}
        for r in (records or []):
            ts = r.get("timestamp") or r.get("source_timestamp") or r.get("time")
            if ts:
                lookup[nid][_fmt_ts(ts)] = _fmt_val(r.get("value"))

    # 写数据行
    for row_i, ts in enumerate(all_ts_sorted, start=2):
        ws.cell(row=row_i, column=1, value=ts)
        if row_i % 2 == 0:
            ws.cell(row=row_i, column=1).fill = PatternFill(
                "solid", fgColor=COLOR_ALT_ROW
            )
        for col_i, nid in enumerate(node_ids, start=2):
            val = lookup[nid].get(ts)
            cell = ws.cell(row=row_i, column=col_i, value=val)
            if row_i % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=COLOR_ALT_ROW)

    total_rows = len(all_ts_sorted) + 1

    # 冻结 + 自动筛选
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(header_row))}1"

    _border_all(ws, 1, 1, total_rows, len(header_row))

    # ── 折线图（最多画前 6 个节点，避免太拥挤）──
    if len(all_ts_sorted) < 2:
        return
    chart_nodes = node_ids[:6]
    chart = LineChart()
    chart.title = "历史趋势"
    chart.style = 10
    chart.y_axis.title = "数值"
    chart.x_axis.title = "时间"
    chart.width = 24
    chart.height = 14

    for col_i, nid in enumerate(chart_nodes, start=2):
        data_ref = Reference(ws, min_col=col_i, min_row=1,
                              max_row=total_rows)
        chart.add_data(data_ref, titles_from_data=True)

    ts_ref = Reference(ws, min_col=1, min_row=2, max_row=total_rows)
    chart.set_categories(ts_ref)
    chart.shape = 4

    # 放在数据右边
    anchor_col = get_column_letter(len(header_row) + 2)
    ws.add_chart(chart, f"{anchor_col}2")


# ══════════════════════════════════════════════════
# Sheet 4 — 历史原始（长表）
# ══════════════════════════════════════════════════
def _write_history_raw(ws, history: dict):
    ws.title = "历史原始"
    cols = ["节点 ID", "时间戳", "值", "质量"]
    widths = [36, 26, 16, 10]

    for col, (h, w) in enumerate(zip(cols, widths), 1):
        ws.column_dimensions[get_column_letter(col)].width = w
        cell = ws.cell(row=1, column=col, value=h)
        _header_style(cell)

    row_i = 2
    for nid, records in history.items():
        for r in (records or []):
            ts = r.get("timestamp") or r.get("source_timestamp") or r.get("time", "")
            val = r.get("value")
            quality = r.get("quality", "")
            row_data = [_short_node(nid), _fmt_ts(ts), _fmt_val(val), quality]
            for col, v in enumerate(row_data, 1):
                cell = ws.cell(row=row_i, column=col, value=v)
                if row_i % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor=COLOR_ALT_ROW)
            _quality_fill(ws.cell(row=row_i, column=4), quality)
            row_i += 1

    if row_i > 2:
        ws.auto_filter.ref = f"A1:D1"
        ws.freeze_panes = "A2"
        _border_all(ws, 1, 1, row_i - 1, 4)


# ══════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════
def generate_excel(
    data: dict,
    output_path: Optional[str] = None,
    append: bool = False,
) -> str:
    """
    根据采集数据生成 Excel 报表。

    参数:
        data        - OpcUaCollector.collect_all() 返回的字典
        output_path - 输出路径（默认在当前目录生成带时间戳的文件名）
        append      - 是否追加模式（追加到"历史原始" Sheet）

    返回:
        生成的文件绝对路径
    """
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"opcua_report_{ts}.xlsx"

    output_path = str(Path(output_path).resolve())

    if append and Path(output_path).exists():
        # 追加模式：加载现有文件，刷新所有 Sheet
        wb = openpyxl.load_workbook(output_path)
        ws_summary  = wb["摘要"]
        ws_realtime = wb["实时值"]
        ws_wide     = wb["历史趋势"]
        ws_raw      = wb["历史原始"]

        # 刷新摘要（保留结构，更新内容）
        for row in ws_summary.iter_rows():
            for cell in row:
                cell.value = None
        _write_summary(ws_summary, data)

        # 刷新实时值
        for row in ws_realtime.iter_rows(min_row=2):
            for cell in row:
                cell.value = None
        ws_realtime.max_row = 1
        _write_realtime(ws_realtime, data.get("realtime", []))

        # 刷新历史趋势（清空后重建）
        for row in ws_wide.iter_rows():
            for cell in row:
                cell.value = None
        ws_wide.max_row = 1
        ws_wide.max_column = 1
        _write_history_wide(ws_wide, data.get("history", {}))
    else:
        # 新建模式：创建新文件
        wb = openpyxl.Workbook()
        # 删除默认 Sheet
        wb.remove(wb.active)

        ws_summary  = wb.create_sheet("摘要")
        ws_realtime = wb.create_sheet("实时值")
        ws_wide     = wb.create_sheet("历史趋势")
        ws_raw      = wb.create_sheet("历史原始")

        _write_summary(ws_summary, data)
        _write_realtime(ws_realtime, data.get("realtime", []))
        _write_history_wide(ws_wide, data.get("history", {}))
        _write_history_raw(ws_raw, data.get("history", {}))

    # 追加新数据到"历史原始" Sheet
    if append and data.get("realtime"):
        # 将实时值转换为历史格式（添加时间戳）
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in data["realtime"]:
            next_row = ws_raw.max_row + 1
            ws_raw.cell(row=next_row, column=1, value=_short_node(item.get("node_id")))
            ws_raw.cell(row=next_row, column=2, value=timestamp)
            ws_raw.cell(row=next_row, column=3, value=_fmt_val(item.get("value")))
            ws_raw.cell(row=next_row, column=4, value=item.get("quality"))

        # 更新摘要中的采集次数
        ws_summary = wb["摘要"]
        for row in ws_summary.iter_rows(min_row=3, max_col=2):
            if row[0].value == "数据采集次数":
                row[1].value = row[1].value + 1 if row[1].value else 1
                break

    wb.save(output_path)
    logger.info(f"报表已生成: {output_path}")
    if not append:
        print(f"[OK] 报表已生成: {output_path}")
    return output_path
