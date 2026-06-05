#!/usr/bin/env python3
"""
磺化釜搅拌电机预测性维护周报 (V2)
=====================================
基于 ISO 20958:2013 / MCSA / CUSUM-EWMA 方法，从全量历史运行电流中
检测暗病、预测退化趋势、给出分级维护建议。

算法依据:
  - CUSUM 累积漂移检测 (ISO 20958 / Montgomery SPC)
  - EWMA 指数加权移动平均 (TU Wien 2024)
  - 超值峰度 (MCSA 冲击脉冲分析)
  - 威布尔剩余寿命估计 (IEC 61650, β=3.5)
  - 自适应基线 (数据驱动中位数学习)

数据范围:
  - 累积全量历史数据（每周自动增长: 7天 → 14天 → 21天 → ...）
  - 自动识别并排除停机段，仅分析运行时段电机电流
"""

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timedelta


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 配置
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
DB_PATH = os.path.join(PROJECT_DIR, "data", "history.db")
REPORT_DIR = os.path.join(PROJECT_DIR, "reports")

# 10 台磺化釜搅拌电机（完整 NodeID，匹配 SQLite 表名）
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

# 评分权重 (ISO 20958 + 工程经验)
SCORE_WEIGHTS = {
    "cusum": 0.30,      # CUSUM 漂移 — 最高权重，检测微小持续偏移
    "volatility": 0.25, # 波动增幅 — 机械松动/润滑不良
    "ewma": 0.20,       # EWMA 趋势 — 长期缓慢退化
    "excursion": 0.15,  # 异常频次 — 运行稳定性
    "kurtosis": 0.10,   # 峭度变化 — 轴承冲击脉冲
}

MOTOR_RATED_CURRENT_A = 22.0  # 铭牌额定电流 (仅回退使用)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SQLite 工具
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _safe_table(node_id: str) -> str:
    """将 NodeID 转为 SQLite 安全表名 (与 storage/__init__.py 一致)"""
    return "h_" + node_id.replace("=", "_eq_").replace(";", "_sc_").replace(".", "_dot_")


def display_name(node_id: str) -> str:
    """ns=1;s=IIAS_05A102.PV → IIAS_05A102.PV"""
    if ";s=" in node_id:
        return node_id.split(";s=", 1)[-1]
    if "s=" in node_id:
        return node_id.rsplit("s=", 1)[-1]
    return node_id


def short_code(node_id: str) -> str:
    """ns=1;s=IIAS_05A102.PV → 05A102"""
    dn = display_name(node_id)
    if "05A" in dn:
        return dn.split("05A")[-1].split(".")[0]
    return dn


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 统计工具函数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _median(sorted_values):
    """中位数 (ISO 20958 推荐的稳健中心估计)"""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n % 2 == 1:
        return sorted_values[n // 2]
    return (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2.0


def _pop_std(values, mean):
    """总体标准差"""
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _excess_kurtosis(values, mean, std):
    """超值峰度 (excess kurtosis = Pearson - 3)
    正值 → 厚尾分布（冲击脉冲多）→ 轴承早期故障特征"""
    if len(values) < 4 or std < 1e-9:
        return 0.0
    n = len(values)
    m4 = sum((v - mean) ** 4 for v in values) / n
    m2 = std ** 2
    return m4 / (m2 ** 2) - 3.0


def _ewma(values, lam=0.15):
    """指数加权移动平均 (TU Wien 2024: 对平均漂移检测更优)"""
    if not values:
        return []
    result = [values[0]]
    for v in values[1:]:
        result.append(lam * v + (1 - lam) * result[-1])
    return result


def _two_sided_cusum(values, target, std, k_factor=0.5, h_factor=5.0):
    """双侧 CUSUM 累积和检测 (ISO 20958 / Montgomery SPC)
    
    TU Wien 2024: CUSUM 对极小漂移 (<0.5σ) 检测能力优于 EWMA
    """
    if not values or std < 1e-9:
        return [], [], 0.0, 1.0, [], []
    k = k_factor * std
    h = h_factor * std
    c_pos, c_neg = [0.0], [0.0]
    triggered_pos, triggered_neg = [], []
    max_pos = 0.0
    for i, v in enumerate(values):
        deviation = v - target
        cp = max(0, c_pos[-1] + deviation - k)
        cn = max(0, c_neg[-1] - deviation - k)
        c_pos.append(cp)
        c_neg.append(cn)
        if cp > h:
            triggered_pos.append(i)
        if cn > h:
            triggered_neg.append(i)
        max_pos = max(max_pos, cp)
    return c_pos, c_neg, max_pos, h, triggered_pos, triggered_neg


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 停机数据过滤
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _find_stopped_threshold(values, bin_width=0.5):
    """自动发现停机/运行分界线 — 基于直方图谷底检测。

    对双峰分布，找到两峰之间计数最少的桶（谷底）作为阈值。
    当谷底区域极宽（>5A）时，改用两峰中点，避免将运行峰左尾误判为谷底。
    对单峰分布（只有运行数据），返回保守阈值 5.0A。
    """
    if not values:
        return 0.0

    valid = [v for v in values if v is not None and v > 0.01]
    if len(valid) < 50:
        return 5.0

    from collections import Counter

    # 1. 构建直方图（bin_width A 宽桶）
    hist = Counter()
    for v in valid:
        b = round(v / bin_width) * bin_width   # round 避免浮点误差
        hist[b] += 1

    sorted_bins = sorted(hist.keys())
    if len(sorted_bins) < 3:
        return 5.0

    # 2. 识别两个峰
    #    停机峰：<2A 范围内计数最高的桶
    stopped_peak_bin, stopped_peak_cnt = None, 0
    for b in sorted_bins:
        if b < 2.0 and hist[b] > stopped_peak_cnt:
            stopped_peak_cnt = hist[b]
            stopped_peak_bin = b

    #    运行峰：>5A 范围内计数最高的桶
    running_peak_bin, running_peak_cnt = None, 0
    for b in sorted_bins:
        if b > 5.0 and hist[b] > running_peak_cnt:
            running_peak_cnt = hist[b]
            running_peak_bin = b

    # 3. 单峰分布处理
    if stopped_peak_bin is None or stopped_peak_cnt < len(valid) * 0.005:
        return 5.0

    if running_peak_bin is None:
        return 0.5

    # 4. 找两峰之间的谷底区域
    valley_bins = [
        (b, hist[b])
        for b in sorted_bins
        if stopped_peak_bin < b < running_peak_bin
    ]

    if not valley_bins:
        return round((stopped_peak_bin + running_peak_bin) / 2.0, 2)

    # 5. 谷底区域极宽（>5A）时，用两峰中点
    #   注意：用两峰桶的距离（完整范围），而非有数据桶的极值
    valley_span = running_peak_bin - stopped_peak_bin
    if valley_span > 5.0:
        return round((stopped_peak_bin + running_peak_bin) / 2.0, 2)

    # 6. 谷底窄，用计数最少的桶（验证不在峰边缘）
    valley_bin, valley_cnt = min(valley_bins, key=lambda x: x[1])

    if (valley_bin - stopped_peak_bin < 1.0 or
            running_peak_bin - valley_bin < 1.0):
        return round((stopped_peak_bin + running_peak_bin) / 2.0, 2)

    # 返回谷底桶的上沿（偏运行侧）
    return round(valley_bin + bin_width, 2)


def _filter_running(values, threshold, min_consecutive=10):
    """过滤停机数据段 — 仅返回运行时段电流"""
    if not values or threshold <= 0:
        return values, 0
    running, buffer = [], []
    dropped = 0
    for v in values:
        if v is None:
            continue
        if v < threshold:
            buffer.append(v)
            if len(buffer) >= min_consecutive:
                dropped += len(buffer)
                buffer = []
        else:
            if len(buffer) < min_consecutive:
                running.extend(buffer)
            else:
                dropped += len(buffer)
            buffer = []
            running.append(v)
    if buffer and len(buffer) < min_consecutive:
        running.extend(buffer)
    else:
        dropped += len(buffer)
    return running, dropped


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 自适应基线
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _compute_adaptive_baseline(values_sorted):
    """从运行数据自适应计算基线"""
    if not values_sorted or len(values_sorted) < 10:
        return None, None, None
    median_val = _median(values_sorted)
    mean_val = sum(values_sorted) / len(values_sorted)
    std_val = _pop_std(values_sorted, mean_val)
    kurt_val = _excess_kurtosis(values_sorted, mean_val, std_val)
    return median_val, std_val, kurt_val


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 评分函数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _score_cusum(cusum_max, h_value, triggered_count=0, data_points=0):
    """CUSUM 漂移评分 — 基于触发次数 + 最大累积比
    触发次数更能反映真实退化程度，避免多台电机"全打40分"问题。
    """
    if h_value < 1e-9 or data_points < 10:
        return 100.0

    rate = triggered_count / max(1, data_points)

    # 零触发 → 满分
    if triggered_count == 0:
        ratio = cusum_max / h_value
        if ratio < 0.1:
            return 100.0
        elif ratio < 0.3:
            return round(98.0 - ratio * 30, 1)
        elif ratio < 0.5:
            return round(89.0 - (ratio - 0.3) * 70, 1)
        else:
            return round(75.0 - (ratio - 0.5) * 60, 1)

    # 有触发 → 按触发率 + max比 扣分
    score = 90.0
    # 触发率惩罚 (0.1% → -2, 1% → -15, 5%+ → -40)
    if rate < 0.001:
        score -= 2
    elif rate < 0.01:
        score -= round(2 + (rate - 0.001) / 0.009 * 13, 1)
    elif rate < 0.05:
        score -= round(15 + (rate - 0.01) / 0.04 * 25, 1)
    else:
        score -= 40
    # CUSUM max 惩罚
    ratio = cusum_max / h_value
    if ratio > 1.5:
        score -= round(min(30, (ratio - 1.5) * 20), 1)
    elif ratio > 1.0:
        score -= round((ratio - 1.0) * 15, 1)
    return round(max(10, score), 1)


def _score_volatility(recent_rolling_std, baseline_std):
    """短期波动评分。波动降低（ratio<1）给满分100，波动增加按比例扣分。"""
    if baseline_std < 1e-9:
        return 50.0
    ratio = recent_rolling_std / baseline_std
    if ratio <= 1.0:
        return 100.0  # 波动未增大 → 满分
    elif ratio < 1.3:
        return round(100 - (ratio - 1.0) * 40, 1)   # 100~88
    elif ratio < 2.0:
        return round(88 - (ratio - 1.3) * 70, 1)    # 88~39
    elif ratio < 3.0:
        return round(39 - (ratio - 2.0) * 30, 1)    # 39~9
    else:
        return max(5, round(9 - (ratio - 3.0) * 4, 1))


def _score_ewma(ewma_end, baseline_mean, baseline_std):
    """EWMA 趋势偏移评分 — 偏移量÷基线标准差。
    标准差极小 (<0.05A) 的电机本质上极其稳定，直接给高分，不因除小量而误判。
    """
    if baseline_std < 0.05:
        return 98.0
    shift_sigmas = abs(ewma_end - baseline_mean) / baseline_std
    if shift_sigmas < 0.2:
        return round(98.0 - shift_sigmas * 15, 1)   # 98.0~95.0
    elif shift_sigmas < 0.5:
        return round(95.0 - (shift_sigmas - 0.2) * 50, 1)  # 95.0~80.0
    elif shift_sigmas < 1.0:
        return round(80.0 - (shift_sigmas - 0.5) * 70, 1)  # 80.0~45.0
    elif shift_sigmas < 2.0:
        return round(45.0 - (shift_sigmas - 1.0) * 30, 1)  # 45.0~15.0
    else:
        return round(max(5.0, 15.0 - (shift_sigmas - 2.0) * 3), 1)


def _score_excursion(recent_excursion_rate, baseline_excursion_rate):
    """偏移频次评分 — 近期偏移率 ÷ 基线偏移率。
    偏移率很低 (<0.005) 说明几乎无异常，给高分。
    """
    if baseline_excursion_rate is None or baseline_excursion_rate < 1e-9:
        base = 0.02
    else:
        base = baseline_excursion_rate

    # 如果近期偏移率本身就极低，直接给高分
    if recent_excursion_rate < 0.005:
        return 98.0
    if recent_excursion_rate < 0.01:
        return 92.0

    ratio = recent_excursion_rate / max(base, 0.005)
    if ratio < 1.0:
        return round(90.0 - ratio * 10, 1)          # 90.0~80.0
    elif ratio < 1.5:
        return round(80.0 - (ratio - 1.0) * 50, 1)  # 80.0~55.0
    elif ratio < 2.5:
        return round(55.0 - (ratio - 1.5) * 40, 1)  # 55.0~15.0
    elif ratio < 4.0:
        return round(15.0 - (ratio - 2.5) * 6, 1)   # 15.0~6.0
    else:
        return round(max(3.0, 6.0 - (ratio - 4.0) * 1.5), 1)


def _score_kurtosis(current_kurtosis, baseline_kurtosis):
    """峭度变化评分 — 只有峭度显著上升（冲击增多）才扣分。
    峭度下降（分布变得更正态）是改善，不扣分，幅度越大加分越多。
    用连续公式避免评分聚集在少数几个固定值。
    """
    if baseline_kurtosis is None:
        baseline_kurtosis = 0.0
    delta = current_kurtosis - baseline_kurtosis

    # ── 峭度下降或持平 → 加分 ──
    if delta <= -2.0:
        return round(min(99, 97 + abs(delta) * 0.5), 1)     # 97~99
    if delta <= -1.0:
        return round(95 + (abs(delta) - 1.0) * 2, 1)        # 95~97
    if delta <= -0.5:
        return round(92 + (abs(delta) - 0.5) * 6, 1)        # 92~95
    if delta <= -0.2:
        return round(88 + (abs(delta) - 0.2) * 13.3, 1)     # 88~92
    if delta <= 0.0:
        return round(85 + abs(delta) * 15, 1)                # 85~88

    # ── 峭度上升 → 扣分 ──
    if delta < 0.3:
        return round(85 - delta * 45, 1)                     # 85~71.5
    if delta < 0.8:
        return round(71.5 - (delta - 0.3) * 50, 1)           # 71.5~46.5
    if delta < 1.5:
        return round(46.5 - (delta - 0.8) * 35, 1)           # 46.5~22
    if delta < 3.0:
        return round(22 - (delta - 1.5) * 8, 1)              # 22~10
    else:
        import math
        log_delta = math.log1p(delta)
        return round(max(5, 10 - log_delta * 3), 1)          # 最高10，对数衰减


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 预警生成
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _generate_alerts_v2(dim_scores, cusum_triggered, ewma_shift_sigmas,
                         vol_ratio, exc_ratio, kurt_delta, remaining_days):
    alerts = []
    if dim_scores.get("cusum", 100) < 40:
        ccount = len(cusum_triggered)
        alerts.append({
            "level": "warning",
            "title": "CUSUM 累积漂移告警",
            "message": (
                f"CUSUM 检测到 {ccount} 次异常触发，电流存在持续性微小偏移（≥0.5σ）。"
                f"可能原因：轴承早期磨损、负载缓慢变化或绝缘渐进劣化。建议安排近期巡检。"
            ),
        })
    if vol_ratio > 2.0:
        alerts.append({
            "level": "warning",
            "title": "电流波动异常增大",
            "message": (
                f"短期电流波动幅度为基线的 {vol_ratio:.1f} 倍，波动显著增大。"
                f"可能反映机械松动、润滑不良或负载不稳定，建议检查联轴器和轴承润滑。"
            ),
        })
    if ewma_shift_sigmas > 2.0:
        alerts.append({
            "level": "warning",
            "title": "EWMA 长期趋势偏移",
            "message": (
                f"EWMA 趋势偏移 {ewma_shift_sigmas:.1f}σ，长期趋势持续偏离基线。"
                f"可能存在绝缘老化或机械效率下降，建议安排绝缘检测。"
            ),
        })
    if exc_ratio > 3.0:
        alerts.append({
            "level": "info",
            "title": "异常偏移频次上升",
            "message": (
                f"电流异常偏移频次增至基线的 {exc_ratio:.1f} 倍，运行稳定性下降。"
                f"建议关注后续趋势变化。"
            ),
        })
    if kurt_delta > 1.0:
        alerts.append({
            "level": "info",
            "title": "峭度增加 — 冲击脉冲",
            "message": (
                f"电流分布峭度增加 {kurt_delta:.1f}，出现厚尾特征。"
                f"可能意味着冲击型事件增多（轴承点蚀、碎屑撞击），建议持续监测。"
            ),
        })
    if remaining_days is not None and remaining_days < 60:
        alerts.append({
            "level": "warning",
            "title": "剩余寿命不足 60 天",
            "message": (
                f"按当前退化趋势，预计 {remaining_days} 天内达到维护阈值。"
                f"建议在 {max(7, remaining_days - 15)} 天内安排计划性检修。"
            ),
        })
    if not alerts:
        alerts.append({
            "level": "info",
            "title": "运行状态正常",
            "message": "各维度健康指标均在正常范围内，建议按常规周期执行预防性维护。",
        })
    return alerts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 数据读取
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def read_cumulative_data(db: sqlite3.Connection, node_id: str) -> list:
    """读取累积全量历史数据（从最早记录到当前时刻）
    
    返回: [{"timestamp": "...", "value": float}, ...]
    """
    table = _safe_table(node_id)
    cursor = db.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    )
    if not cursor.fetchone():
        return []

    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    cursor.execute(
        f"""SELECT timestamp, value FROM [{table}]
            WHERE timestamp >= '2000-01-01' AND timestamp < ?
            ORDER BY timestamp ASC""",
        (now_str + "Z",)
    )
    rows = cursor.fetchall()
    return [
        {"timestamp": r[0], "value": float(r[1])}
        for r in rows
        if r[1] is not None
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 单电机全量分析
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def analyze_motor(node_id: str, all_data: list, now: datetime) -> dict:
    """对单台电机执行完整的 V2 预测性维护分析
    
    基线策略 (累积数据):
      - 数据 < 7 天: 前半段做基线，后半段做分析
      - 数据 >= 7 天: 最早 30 天做基线（或最大可用），其余做分析
      - CUSUM 在分析期内运行
    """
    dn = display_name(node_id)

    # 提取原始电流值
    values_raw = [r["value"] for r in all_data if r["value"] is not None]
    total_points = len(values_raw)

    if total_points < 10:
        return {
            "node_id": node_id,
            "display_name": dn,
            "total_points": total_points,
            "data_insufficient": True,
            "error": f"数据点不足 ({total_points} 点)，无法进行可靠分析。至少需要 10 个数据点。",
        }

    # ── 停机过滤 ──
    stopped_threshold = _find_stopped_threshold(values_raw)
    values_running, stopped_count = _filter_running(values_raw, stopped_threshold)
    pct_stopped = round(stopped_count / max(1, total_points) * 100, 1)

    if not values_running or len(values_running) < 10:
        return {
            "node_id": node_id,
            "display_name": dn,
            "total_points": total_points,
            "running_points": len(values_running),
            "pct_stopped": pct_stopped,
            "data_insufficient": True,
            "error": (
                f"运行数据不足（{len(values_running)} 点），停机占比 {pct_stopped}%。"
                "当前可能处于停机状态或数据积累不充分。"
            ),
        }

    running_count = len(values_running)

    # ── 数据时间跨度 ──
    try:
        first_ts = datetime.fromisoformat(all_data[0]["timestamp"])
        last_ts = datetime.fromisoformat(all_data[-1]["timestamp"])
        data_span_days = max(1, (last_ts - first_ts).days + 1)
    except (ValueError, TypeError, IndexError):
        data_span_days = 1

    data_sufficient = data_span_days >= 3

    # ── 基线/分析拆分 ──
    if data_span_days >= 14:
        # 充足数据: 最早 50% 做基线
        split = running_count // 2
        baseline_values = values_running[:split]
        analysis_values = values_running[split:]
        baseline_source = f"前 {data_span_days // 2} 天（{len(baseline_values)} 点）"
    elif len(values_running) >= 100:
        # 中等数据: 前半基线，后半分析
        split = running_count // 2
        baseline_values = values_running[:split]
        analysis_values = values_running[split:]
        baseline_source = f"前半段（{len(baseline_values)} 点，拆分）"
    else:
        # 数据少: 全部做基线，CUSUM 不可靠
        baseline_values = values_running
        analysis_values = values_running
        baseline_source = f"全量数据（{len(baseline_values)} 点，基线不稳定）"

    # ── 自适应基线计算 ──
    baseline_sorted = sorted(baseline_values)
    bl_median, bl_std, bl_kurtosis = _compute_adaptive_baseline(baseline_sorted)
    if bl_median is None:
        bl_median = sum(baseline_values) / len(baseline_values)
        bl_std = 0.05 * bl_median
        bl_kurtosis = 0.0

    # ── 1. CUSUM 累积漂移 ──
    c_pos, c_neg, cusum_max, h_val, trig_pos, trig_neg = _two_sided_cusum(
        analysis_values, bl_median, max(bl_std, 0.01),
        k_factor=0.5, h_factor=5.0
    )
    cusum_triggered = trig_pos + trig_neg
    cusum_score = _score_cusum(cusum_max, h_val, len(cusum_triggered), len(analysis_values))
    if not data_sufficient:
        cusum_score = max(cusum_score, 40)

    # ── 2. 短期波动增幅 ──
    window_size = max(10, min(100, len(analysis_values) // 10))
    rolling_stds = []
    for i in range(0, len(analysis_values) - window_size + 1, window_size // 2):
        chunk = analysis_values[i:i + window_size]
        if len(chunk) >= 5:
            m = sum(chunk) / len(chunk)
            rolling_stds.append(_pop_std(chunk, m))
    recent_rolling_std = _median(sorted(rolling_stds[-6:])) if rolling_stds else bl_std
    vol_ratio = recent_rolling_std / max(bl_std, 0.01)
    vol_score = _score_volatility(recent_rolling_std, bl_std)

    # ── 3. EWMA 趋势偏移 ──
    ewma_values = _ewma(analysis_values, lam=0.15)
    ewma_end = ewma_values[-1] if ewma_values else bl_median
    ewma_shift_sigmas = abs(ewma_end - bl_median) / max(bl_std, 0.01)
    ewma_score = _score_ewma(ewma_end, bl_median, bl_std)

    # ── 4. 异常偏移频次 ──
    normal_band = 2.0 * max(bl_std, 0.01)
    baseline_excursions = sum(1 for v in baseline_values if abs(v - bl_median) > normal_band)
    baseline_excursion_rate = baseline_excursions / max(1, len(baseline_values))

    recent_excursions = 0
    recent_running_count = 0
    day_7_ago = now - timedelta(days=7)
    for r in all_data:
        if r["value"] is None or r["value"] < stopped_threshold:
            continue
        recent_running_count += 1
        try:
            ts = datetime.fromisoformat(r["timestamp"])
            if ts >= day_7_ago and abs(r["value"] - bl_median) > normal_band:
                recent_excursions += 1
        except (ValueError, TypeError):
            continue
    recent_excursion_rate = recent_excursions / max(1, recent_running_count)
    exc_score = _score_excursion(recent_excursion_rate, baseline_excursion_rate)

    # ── 5. 峭度变化 ──
    analysis_mean = sum(analysis_values) / len(analysis_values) if analysis_values else bl_median
    analysis_std = _pop_std(analysis_values, analysis_mean) if analysis_values else bl_std
    current_kurtosis = _excess_kurtosis(analysis_values, analysis_mean, analysis_std)
    kurt_delta = current_kurtosis - bl_kurtosis  # 保留方向：+上升→变坏，-下降→变好
    kurt_score = _score_kurtosis(current_kurtosis, bl_kurtosis)

    # ── 6. EWMA 漂移率（用于寿命估计）──
    if len(ewma_values) >= 2:
        drift_total = abs(ewma_values[-1] - ewma_values[0])
        drift_per_day = drift_total / max(1, data_span_days)
    else:
        drift_per_day = 0.0

    # ── 综合健康度 ──
    dim_scores = {
        "cusum": cusum_score,
        "volatility": vol_score,
        "ewma": ewma_score,
        "excursion": exc_score,
        "kurtosis": kurt_score,
    }
    overall = sum(dim_scores[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)
    if any(dim_scores[k] < 40 for k in SCORE_WEIGHTS):
        overall = min(overall, 60)
    overall = round(overall, 1)

    # ── 风险等级 ──
    if overall >= 85:
        risk_level = "healthy"
        risk_label = "健康"
        risk_color = "#2e7d32"
    elif overall >= 70:
        risk_level = "normal"
        risk_label = "正常"
        risk_color = "#1565c0"
    elif overall >= 50:
        risk_level = "attention"
        risk_label = "关注"
        risk_color = "#e65100"
    else:
        risk_level = "warning"
        risk_label = "告警"
        risk_color = "#c62828"

    # ── 剩余寿命 (Weibull: IEC 61650, β=3.5) ──
    # 只有存在明确退化趋势时才做威布尔估计
    # 稳定电机（drift_per_day <= 0.0005）无退化趋势，返回 None
    if data_span_days >= 14 and drift_per_day > 0.0005 and ewma_values and bl_std > 0:
        alarm_threshold = bl_median + 3.0 * bl_std
        gap = max(0.01, alarm_threshold - ewma_end)
        eta = gap / max(drift_per_day, 1e-9)
        beta = 3.5
        R_target = 0.90
        remaining_days_weibull = eta * ((-math.log(R_target)) ** (1.0 / beta))
        remaining_days = max(7, min(3650, round(remaining_days_weibull)))
    else:
        remaining_days = None  # 稳定无退化趋势，不给出固定寿命值

    # ── 累计运行时间 ──
    # 正确做法：按"连续运行段"计算，每段 = 段内最后数据点 - 段内第一数据点。
    # 段结束条件：相邻运行数据点间隔 > 60 秒（OPC UA 值不变时不推送，
    # 超过 60 秒无推送说明要么停机要么网络异常，不计入运行时间）。
    cumulative_hours = 0.0
    seg_start_ts = None   # 当前运行段的第一条数据时间戳
    seg_last_ts  = None   # 当前运行段最后一条数据时间戳
    SEGMENT_GAP_LIMIT = 60  # 秒，超过此间隔认为段已结束

    for r in all_data:
        if r["value"] is None or r["value"] < stopped_threshold:
            # 停机：结束当前段，累加段时长
            if seg_start_ts is not None and seg_last_ts is not None:
                dur_h = (seg_last_ts - seg_start_ts).total_seconds() / 3600
                if dur_h > 0:
                    cumulative_hours += dur_h
            seg_start_ts = None
            seg_last_ts  = None
            continue
        try:
            cur_ts = datetime.fromisoformat(r["timestamp"])
        except (ValueError, TypeError):
            continue

        if seg_start_ts is None:
            # 新运行段开始
            seg_start_ts = cur_ts
            seg_last_ts  = cur_ts
        else:
            gap_s = (cur_ts - seg_last_ts).total_seconds()
            if gap_s > SEGMENT_GAP_LIMIT:
                # 间隔过长，结束当前段，开始新段
                dur_h = (seg_last_ts - seg_start_ts).total_seconds() / 3600
                if dur_h > 0:
                    cumulative_hours += dur_h
                seg_start_ts = cur_ts
                seg_last_ts  = cur_ts
            else:
                # 同一段内，更新最后时间戳
                seg_last_ts = cur_ts

    # 循环结束后，处理最后一个未关闭的段
    if seg_start_ts is not None and seg_last_ts is not None:
        dur_h = (seg_last_ts - seg_start_ts).total_seconds() / 3600
        if dur_h > 0:
            cumulative_hours += dur_h

    # ── 日均统计 ──
    daily_stats = {}
    for r in all_data:
        if r["value"] is None or r["value"] < stopped_threshold:
            continue
        try:
            day_key = r["timestamp"][:10]
        except (TypeError, IndexError):
            continue
        if day_key not in daily_stats:
            daily_stats[day_key] = []
        daily_stats[day_key].append(r["value"])

    daily_avg = []
    daily_max = []
    for day in sorted(daily_stats.keys()):
        vals = daily_stats[day]
        daily_avg.append({"date": day, "value": round(sum(vals) / len(vals), 2)})
        daily_max.append({"date": day, "value": round(max(vals), 2)})

    # ── 预警 ──
    exc_ratio_val = recent_excursion_rate / max(baseline_excursion_rate, 0.005)
    alerts = _generate_alerts_v2(
        dim_scores, cusum_triggered, ewma_shift_sigmas,
        vol_ratio, exc_ratio_val, kurt_delta, remaining_days
    )
    if not data_sufficient:
        alerts.insert(0, {
            "level": "info",
            "title": "数据积累中",
            "message": (
                f"当前仅覆盖 {data_span_days} 天运行数据，基线尚不稳定。"
                "各指标评分会随数据积累逐步精确，建议至少积累 7 天数据后参考本报告。"
            ),
        })

    # ── 趋势图数据 ──
    # 取分析期数据用于图表（降采样到最多 500 点）
    chart_raw = analysis_values
    chart_ewma = ewma_values
    if len(chart_raw) > 500:
        step = len(chart_raw) // 500
        chart_raw = chart_raw[::step]
        chart_ewma = chart_ewma[::step]

    return {
        "node_id": node_id,
        "display_name": dn,
        "total_points": total_points,
        "running_points": running_count,
        "pct_stopped": pct_stopped,
        "data_span_days": data_span_days,
        "data_sufficient": data_sufficient,
        "baseline_source": baseline_source,
        "stopped_threshold": round(stopped_threshold, 2),
        "bl_median": round(bl_median, 2),
        "bl_std": round(bl_std, 3),
        "bl_kurtosis": round(bl_kurtosis, 3),
        "analysis_mean": round(analysis_mean, 2),
        "analysis_std": round(analysis_std, 3),
        "current_kurtosis": round(current_kurtosis, 3),
        "dim_scores": dim_scores,
        "overall": overall,
        "risk_level": risk_level,
        "risk_label": risk_label,
        "risk_color": risk_color,
        "remaining_days": remaining_days,
        "cumulative_hours": round(cumulative_hours, 1),
        "cusum_max": round(cusum_max, 3),
        "cusum_h": round(h_val, 3),
        "cusum_triggered_count": len(cusum_triggered),
        "ewma_shift_sigmas": round(ewma_shift_sigmas, 2),
        "ewma_end": round(ewma_end, 2),
        "vol_ratio": round(vol_ratio, 2),
        "kurt_delta": round(kurt_delta, 3),
        "drift_per_day": round(drift_per_day, 4),
        "alerts": alerts,
        "daily_avg": daily_avg,
        "daily_max": daily_max,
        "chart_raw": chart_raw,
        "chart_ewma": chart_ewma,
        "chart_cusum_pos": c_pos,
        "chart_cusum_neg": c_neg,
        "chart_cusum_h": h_val,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HTML 报告生成
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #f5f7fa; color: #333; line-height:1.6; }
.container { max-width:1200px; margin:0 auto; padding:20px; }
.header { background: linear-gradient(135deg, #1a237e, #283593); color: white;
          border-radius:12px; padding:30px; margin-bottom:24px; }
.header h1 { font-size:24px; margin-bottom:8px; }
.header .sub { opacity:0.85; font-size:14px; margin-bottom:4px; }
.summary-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
                gap:12px; margin-bottom:24px; }
.summary-card { background:white; border-radius:10px; padding:16px;
                box-shadow:0 1px 3px rgba(0,0,0,0.08); text-align:center; }
.summary-card .num { font-size:28px; font-weight:700; }
.summary-card .lbl { font-size:12px; color:#666; margin-top:4px; }
.fleet-table { background:white; border-radius:10px; overflow:hidden;
               box-shadow:0 1px 3px rgba(0,0,0,0.08); margin-bottom:24px; }
.fleet-table table { width:100%; border-collapse:collapse; }
.fleet-table th { background:#e8eaf6; padding:12px 14px; text-align:left;
                  font-size:13px; font-weight:600; color:#37474f; }
.fleet-table td { padding:10px 14px; border-bottom:1px solid #f0f0f0; font-size:13px; }
.fleet-table tr:hover { background:#f8f9ff; }
.badge { display:inline-block; padding:3px 10px; border-radius:12px;
         font-size:12px; font-weight:600; color:white; }
.motor-section { background:white; border-radius:10px; margin-bottom:20px;
                 box-shadow:0 1px 3px rgba(0,0,0,0.08); overflow:hidden; }
.motor-header { padding:18px 24px; display:flex; align-items:center;
                justify-content:space-between; border-bottom:1px solid #eee; }
.motor-header h2 { font-size:18px; }
.motor-body { padding:20px 24px; }
.score-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:12px;
              margin-bottom:20px; }
.score-item { background:#f8f9fa; border-radius:8px; padding:14px; text-align:center; }
.score-item .val { font-size:22px; font-weight:700; }
.score-item .key { font-size:11px; color:#666; margin-top:2px; }
.score-item .wt { font-size:10px; color:#999; }
.chart-row { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }
.chart-box { background:#fafafa; border-radius:8px; padding:12px; position:relative;
             height:280px; }
.chart-box.full { grid-column:1/-1; }
.chart-box h4 { font-size:13px; color:#555; margin-bottom:8px; }
.alert-list { margin-top:12px; }
.alert-item { padding:10px 14px; border-radius:8px; margin-bottom:8px;
              font-size:13px; line-height:1.5; }
.alert-warning { background:#fff3e0; border-left:4px solid #e65100; }
.alert-info { background:#e3f2fd; border-left:4px solid #1565c0; }
.metrics-table { width:100%; border-collapse:collapse; margin-top:16px; }
.metrics-table th { background:#f5f5f5; padding:8px 12px; text-align:left;
                    font-size:12px; color:#555; }
.metrics-table td { padding:8px 12px; border-bottom:1px solid #f0f0f0; font-size:12px; }
.footer { text-align:center; color:#999; font-size:12px; padding:20px; }
h3 { font-size:16px; margin-bottom:12px; color:#37474f; }
"""


def _health_bar(score):
    """生成彩色健康度条形 HTML"""
    if score >= 85:
        color = "#2e7d32"
    elif score >= 70:
        color = "#1565c0"
    elif score >= 50:
        color = "#e65100"
    else:
        color = "#c62828"
    return (
        f'<div style="display:flex;align-items:center;gap:8px">'
        f'<div style="flex:1;height:8px;background:#e0e0e0;border-radius:4px;overflow:hidden">'
        f'<div style="width:{score}%;height:100%;background:{color};border-radius:4px"></div>'
        f'</div><span style="font-weight:700;color:{color};min-width:42px">{score}%</span></div>'
    )


def generate_html_report(results: list, period_desc: str, generated_at: str,
                          data_start: str, data_end: str) -> str:
    """生成完整的 HTML 预测性维护报告"""

    # ── 汇总统计 ──
    healthy_count = sum(1 for r in results if r.get("risk_level") == "healthy")
    normal_count = sum(1 for r in results if r.get("risk_level") == "normal")
    attention_count = sum(1 for r in results if r.get("risk_level") == "attention")
    warning_count = sum(1 for r in results if r.get("risk_level") == "warning")
    insufficient = sum(1 for r in results if r.get("data_insufficient"))
    total_running_points = sum(r.get("running_points", 0) for r in results)
    avg_health = sum(r.get("overall", 0) for r in results if not r.get("data_insufficient"))
    valid_count = sum(1 for r in results if not r.get("data_insufficient"))
    avg_health_val = round(avg_health / max(1, valid_count), 1)

    alerts_all = []
    for r in results:
        for a in r.get("alerts", []):
            if a.get("level") == "warning":
                alerts_all.append(f"{r['display_name']}: {a['title']}")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>磺化釜电机预测性维护周报</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>磺化釜搅拌电机 — 预测性维护周报</h1>
  <div class="sub">分析周期：{period_desc}</div>
  <div class="sub">算法依据：ISO 20958:2013 / MCSA / CUSUM-EWMA / 威布尔寿命估计 (IEC 61650, β=3.5)</div>
  <div class="sub">数据范围：{data_start} ~ {data_end}（累积全量历史，仅含运行时段）</div>
</div>

<div class="summary-grid">
  <div class="summary-card">
    <div class="num" style="color:#2e7d32">{healthy_count}</div>
    <div class="lbl">健康</div>
  </div>
  <div class="summary-card">
    <div class="num" style="color:#1565c0">{normal_count}</div>
    <div class="lbl">正常</div>
  </div>
  <div class="summary-card">
    <div class="num" style="color:#e65100">{attention_count}</div>
    <div class="lbl">需关注</div>
  </div>
  <div class="summary-card">
    <div class="num" style="color:#c62828">{warning_count}</div>
    <div class="lbl">告警</div>
  </div>
  <div class="summary-card">
    <div class="num">{total_running_points:,}</div>
    <div class="lbl">运行数据点</div>
  </div>
  <div class="summary-card">
    <div class="num">{avg_health_val}%</div>
    <div class="lbl">平均健康度</div>
  </div>
</div>
"""

    # ── 告警汇总 ──
    if alerts_all:
        html += '<div class="motor-section"><div class="motor-body">'
        html += '<h3 style="color:#c62828;">告警汇总</h3>'
        for a in alerts_all:
            html += f'<div class="alert-item alert-warning">{a}</div>'
        html += '</div></div>'

    # ── 机队总览表 ──
    html += """<div class="fleet-table"><table>
<thead><tr>
  <th>电机</th><th>健康度</th><th>风险</th>
  <th>CUSUM</th><th>波动</th><th>EWMA</th><th>偏移</th><th>峭度</th>
  <th>剩余寿命</th><th>累计运行(h)</th><th>运行数据</th>
</tr></thead><tbody>"""

    for r in results:
        dn = r.get("display_name", "?")
        if r.get("data_insufficient"):
            html += (
                f'<tr><td>{dn}</td>'
                f'<td colspan="9" style="color:#999;font-size:12px">{r.get("error","数据不足")}</td></tr>'
            )
            continue
        ds = r["dim_scores"]
        # 剩余寿命显示处理
        if r["remaining_days"] is None:
            rul_display = "稳定无退化"
        else:
            rul_display = f"{r['remaining_days']} 天"
        html += (
            f'<tr>'
            f'<td><strong>{dn}</strong></td>'
            f'<td>{_health_bar(r["overall"])}</td>'
            f'<td><span class="badge" style="background:{r["risk_color"]}">{r["risk_label"]}</span></td>'
            f'<td style="color:{"#c62828" if ds["cusum"]<60 else "#333"}">{ds["cusum"]}</td>'
            f'<td style="color:{"#c62828" if ds["volatility"]<60 else "#333"}">{ds["volatility"]}</td>'
            f'<td style="color:{"#c62828" if ds["ewma"]<60 else "#333"}">{ds["ewma"]}</td>'
            f'<td style="color:{"#c62828" if ds["excursion"]<60 else "#333"}">{ds["excursion"]}</td>'
            f'<td style="color:{"#c62828" if ds["kurtosis"]<60 else "#333"}">{ds["kurtosis"]}</td>'
            f'<td>{rul_display}</td>'
            f'<td>{r["cumulative_hours"]} h</td>'
            f'<td>{r["running_points"]:,} 点</td>'
            f'</tr>'
        )

    html += "</tbody></table></div>"

    # ── 每台电机详细分析 ──
    for i, r in enumerate(results):
        dn = r.get("display_name", "?")
        if r.get("data_insufficient"):
            html += (
                f'<div class="motor-section">'
                f'<div class="motor-header"><h2>{dn}</h2>'
                f'<span style="color:#999">数据不足</span></div>'
                f'<div class="motor-body"><p style="color:#999">{r.get("error","")}</p></div>'
                f'</div>'
            )
            continue

        ds = r["dim_scores"]
        chart_id = dn.replace(".", "_")

        if r["remaining_days"] is None:
            rul_display_detail = "剩余寿命 — 稳定无退化趋势"
        else:
            rul_display_detail = f"剩余寿命 {r['remaining_days']} 天"

        html += f"""
<div class="motor-section">
<div class="motor-header">
  <h2>{dn}</h2>
  <div style="display:flex;align-items:center;gap:16px">
    <span class="badge" style="background:{r['risk_color']}">{r['risk_label']}</span>
    <span style="font-size:14px;color:#666">健康度 {r['overall']}% | {rul_display_detail}</span>
  </div>
</div>
<div class="motor-body">

<!-- 五维度评分卡片 -->
<div class="score-grid">
  <div class="score-item">
    <div class="val" style="color:{'#c62828' if ds['cusum']<60 else '#2e7d32'}">{ds['cusum']}</div>
    <div class="key">CUSUM 漂移</div>
    <div class="wt">权重 30%</div>
  </div>
  <div class="score-item">
    <div class="val" style="color:{'#c62828' if ds['volatility']<60 else '#2e7d32'}">{ds['volatility']}</div>
    <div class="key">波动增幅</div>
    <div class="wt">权重 25%</div>
  </div>
  <div class="score-item">
    <div class="val" style="color:{'#c62828' if ds['ewma']<60 else '#2e7d32'}">{ds['ewma']}</div>
    <div class="key">EWMA 趋势</div>
    <div class="wt">权重 20%</div>
  </div>
  <div class="score-item">
    <div class="val" style="color:{'#c62828' if ds['excursion']<60 else '#2e7d32'}">{ds['excursion']}</div>
    <div class="key">偏移频次</div>
    <div class="wt">权重 15%</div>
  </div>
  <div class="score-item">
    <div class="val" style="color:{'#c62828' if ds['kurtosis']<60 else '#2e7d32'}">{ds['kurtosis']}</div>
    <div class="key">峭度变化</div>
    <div class="wt">权重 10%</div>
  </div>
</div>

<!-- 趋势图 -->
<div class="chart-row">
  <div class="chart-box">
    <h4>电流趋势（运行数据 · 降采样）</h4>
    <canvas id="chart_current_{chart_id}"></canvas>
  </div>
  <div class="chart-box">
    <h4>CUSUM 累积漂移监测</h4>
    <canvas id="chart_cusum_{chart_id}"></canvas>
  </div>
</div>

<!-- 关键指标 -->
<table class="metrics-table">
<tr><th>指标</th><th>值</th><th>说明</th></tr>
<tr><td>自适应基线电流</td><td>{r['bl_median']} A</td><td>从历史运行数据中位数学习（{r['baseline_source']}）</td></tr>
<tr><td>基线标准差</td><td>{r['bl_std']} A</td><td>正常运行波动范围</td></tr>
<tr><td>基线峭度</td><td>{r['bl_kurtosis']}</td><td>基线分布形态（0=正态，正值=厚尾）</td></tr>
<tr><td>分析期均值</td><td>{r['analysis_mean']} A</td><td>近期运行电流平均值</td></tr>
<tr><td>分析期峭度</td><td>{r['current_kurtosis']}</td><td>当前分布形态 — 高于基线表示冲击事件增多</td></tr>
<tr><td>EWMA 终点</td><td>{r['ewma_end']} A</td><td>指数加权移动平均终值（λ=0.15）</td></tr>
<tr><td>EWMA 偏移</td><td>{r['ewma_shift_sigmas']} σ</td><td>偏离基线的程度</td></tr>
<tr><td>波动比</td><td>{r['vol_ratio']}×</td><td>短期滚动标准差 / 基线标准差</td></tr>
<tr><td>峭度变化</td><td>Δ={r['kurt_delta']}</td><td>当前峭度与基线的差值</td></tr>
<tr><td>退化速率</td><td>{r['drift_per_day']} A/天</td><td>EWMA 日均漂移量（用于寿命估计）</td></tr>
<tr><td>累计运行时间</td><td>{r['cumulative_hours']} 小时</td><td>仅统计实际运行时段</td></tr>
<tr><td>停机占比</td><td>{r['pct_stopped']}%</td><td>全量数据中停机段占比（已排除分析）</td></tr>
<tr><td>数据跨度</td><td>{r['data_span_days']} 天</td><td>数据覆盖的自然天数</td></tr>
</table>

<!-- 预警 -->
"""
        if r["alerts"]:
            html += '<div class="alert-list"><h3>维护建议</h3>'
            for a in r["alerts"]:
                cls = "alert-warning" if a["level"] == "warning" else "alert-info"
                html += (
                    f'<div class="alert-item {cls}">'
                    f'<strong>{a["title"]}</strong><br>{a["message"]}</div>'
                )
            html += '</div>'

        html += '</div></div>'

    # ── Chart.js 数据 ──
    charts_data = {}
    for r in results:
        if r.get("data_insufficient"):
            continue
        dn = r["display_name"]
        charts_data[dn] = {
            "raw": r["chart_raw"],
            "ewma": r["chart_ewma"],
            "cusum_pos": r["chart_cusum_pos"],
            "cusum_neg": r["chart_cusum_neg"],
            "cusum_h": r["chart_cusum_h"],
            "bl_median": r["bl_median"],
            "bl_std": r["bl_std"],
        }

    html += f"""
</div>  <!-- container -->

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
const data = {json.dumps(charts_data, ensure_ascii=False)};

Object.keys(data).forEach(name => {{
  const d = data[name];
  const id = name.replace(/[.]/g, '_');

  // 电流趋势图
  const ctx1 = document.getElementById('chart_current_' + id);
  if (ctx1) {{
    new Chart(ctx1, {{
      type: 'line',
      data: {{
        labels: d.raw.map((_, i) => i),
        datasets: [
          {{
            label: '运行电流 (A)',
            data: d.raw,
            borderColor: '#90caf9',
            backgroundColor: 'transparent',
            borderWidth: 0.5, pointRadius: 0,
          }},
          {{
            label: 'EWMA (λ=0.15)',
            data: d.ewma,
            borderColor: '#e65100',
            borderWidth: 2, pointRadius: 0,
          }},
          {{
            label: '基线 (中位数)',
            data: Array(d.raw.length).fill(d.bl_median),
            borderColor: '#2e7d32',
            borderWidth: 1.5, borderDash: [6,3], pointRadius: 0,
          }},
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        animation: false,
        scales: {{
          y: {{ title: {{ display: true, text: '电流 (A)' }} }},
          x: {{ display: false }},
        }},
        plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth:12, font:{{size:10}} }} }} }}
      }}
    }});
  }}

  // CUSUM 图
  const ctx2 = document.getElementById('chart_cusum_' + id);
  if (ctx2) {{
    new Chart(ctx2, {{
      type: 'line',
      data: {{
        labels: d.cusum_pos.map((_, i) => i),
        datasets: [
          {{
            label: 'CUSUM⁺',
            data: d.cusum_pos,
            borderColor: '#c62828', borderWidth: 1.5, pointRadius: 0,
          }},
          {{
            label: 'CUSUM⁻',
            data: d.cusum_neg,
            borderColor: '#1565c0', borderWidth: 1.5, pointRadius: 0,
          }},
          {{
            label: '判决阈值 h',
            data: Array(d.cusum_pos.length).fill(d.cusum_h),
            borderColor: '#ff6f00', borderWidth: 1, borderDash: [4,2], pointRadius: 0,
          }},
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        animation: false,
        scales: {{
          y: {{ title: {{ display: true, text: '累积和' }} }},
          x: {{ display: false }},
        }},
        plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth:12, font:{{size:10}} }} }} }}
      }}
    }});
  }}
}});
</script>
<div class="footer">由 OPC UA Bridge 自动生成 — {generated_at} | 算法: CUSUM+EWMA+峭度+威布尔 (ISO 20958 / IEC 61650)</div>
</body>
</html>"""

    return html


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 主入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    parser = argparse.ArgumentParser(
        description="磺化釜电机预测性维护报告 (V2 · CUSUM+EWMA+峭度+威布尔)"
    )
    parser.add_argument("--output", type=str, default=None, help="输出文件路径（默认自动生成）")
    args = parser.parse_args()

    now = datetime.now()
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S")

    print(f"═" * 60)
    print(f"磺化釜电机预测性维护报告 (V2)")
    print(f"生成时间: {generated_at}")
    print(f"数据库: {DB_PATH}")
    print(f"算法: ISO 20958 CUSUM + EWMA + 峭度 + 威布尔 (IEC 61650)")
    print(f"═" * 60)

    if not os.path.exists(DB_PATH):
        print(f"\n❌ 错误: 数据库不存在 — {DB_PATH}")
        sys.exit(1)

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    # ── 确定数据范围 ──
    all_start = None
    all_end = None

    results = []
    for node_id in MOTOR_NODES:
        dn = display_name(node_id)
        print(f"\n分析 {dn}...", end=" ", flush=True)

        all_data = read_cumulative_data(db, node_id)
        if all_data:
            try:
                ts_first = all_data[0]["timestamp"][:19]
                ts_last = all_data[-1]["timestamp"][:19]
                if all_start is None or ts_first < all_start:
                    all_start = ts_first
                if all_end is None or ts_last > all_end:
                    all_end = ts_last
            except (IndexError, KeyError):
                pass

        result = analyze_motor(node_id, all_data, now)
        results.append(result)

        if result.get("data_insufficient"):
            print(f"⚠ {result.get('error', '数据不足')}")
        else:
            rul_str = "稳定" if result['remaining_days'] is None else f"RUL {result['remaining_days']}天"
            print(f"✓ 健康度 {result['overall']}% | {result['risk_label']} | "
                  f"运行 {result['running_points']} 点 | {rul_str}")

    db.close()

    # ── 数据范围 ──
    period_desc = f"累积全量（{all_start or '?'} ~ {all_end or '?'}）"
    if all_start and all_end:
        try:
            s = datetime.fromisoformat(all_start)
            e = datetime.fromisoformat(all_end)
            span = (e - s).days + 1
            period_desc = f"累积全量 {span} 天（{all_start} ~ {all_end}）"
        except (ValueError, TypeError):
            pass

    # ── 生成报告 ──
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = args.output or os.path.join(
        REPORT_DIR, f"motor_predictive_report_{now.strftime('%Y%m%d_%H%M%S')}.html"
    )
    html = generate_html_report(results, period_desc, generated_at,
                                 all_start or "?", all_end or "?")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html)

    # ── 摘要 ──
    valid = [r for r in results if not r.get("data_insufficient")]
    warning_motors = [r for r in valid if r.get("risk_level") == "warning"]
    attention_motors = [r for r in valid if r.get("risk_level") == "attention"]

    print(f"\n{'═' * 60}")
    print(f"报告摘要")
    print(f"{'═' * 60}")
    print(f"  电机总数: {len(MOTOR_NODES)}")
    print(f"  可分析: {len(valid)} | 数据不足: {len(results) - len(valid)}")
    if warning_motors:
        print(f"  ⚠ 告警电机 ({len(warning_motors)}): {', '.join(r['display_name'] for r in warning_motors)}")
    if attention_motors:
        print(f"  🔶 需关注 ({len(attention_motors)}): {', '.join(r['display_name'] for r in attention_motors)}")
    print(f"\n✅ 报告: {report_file}")

    return report_file


if __name__ == "__main__":
    main()
