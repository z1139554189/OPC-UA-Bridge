#!/usr/bin/env python3
"""
磺化釜搅拌电机预测性维护周报 (V3 — 退化趋势感知)
======================================================
基于 ISO 20958:2013 / MCSA / CUSUM-EWMA 方法，叠加多维退化趋势感知，
从全量历史稳态数据中检测暗病、感知微观退化、给出分级维护建议。

算法依据:
  - CUSUM 累积漂移检测 (ISO 20958 / Montgomery SPC)
  - EWMA 指数加权移动平均 (TU Wien 2024)
  - 超值峰度 (MCSA 冲击脉冲分析)
  - 威布尔剩余寿命估计 (IEC 61650, beta=3.5)
  - 自适应基线 (数据驱动中位数学习)

V3 新增 — 多维退化趋势感知:
  - 窗口化多特征提取 (1小时窗口 x 10特征)
  - 样本熵 — 感知信号规律性变化
  - 零交叉率 — 感知高频抖动成分
  - 自相关衰减 — 感知信号"记忆力"流失
  - Mann-Kendall 趋势检验 + Sen 斜率估计
  - 综合退化指数 (DI) 融合 + 退化速率预估

数据范围:
  - 累积全量历史数据 (每周自动增长: 7天 -> 14天 -> 21天 -> ...)
  - 三阶段状态机自动识别并排除停机段，仅分析稳态运行时段电机电流
"""

import argparse
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
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
# V3 退化趋势分析配置
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 窗口参数
WINDOW_SECONDS = 3600      # 1小时窗口
WINDOW_MIN_POINTS = 100    # 窗口最少数据点 (少于则跳过)

# 样本熵参数
SAMPEN_M = 2               # 嵌入维数
SAMPEN_R_FACTOR = 0.2      # 相似容限 = 0.2 * std

# 退化指数融合权重 (总和=1)
DI_WEIGHTS = {
    "mean_trend": 0.18,       # 均值漂移趋势
    "mean_accel": 0.10,       # 均值漂移加速度
    "std_trend": 0.16,        # 标准差趋势
    "skewness_trend": 0.08,   # 偏度趋势
    "sample_entropy_trend": 0.12,  # 样本熵趋势
    "zero_cross_trend": 0.08,      # 零交叉率趋势
    "rms_trend": 0.12,        # RMS趋势
    "autocorr_decay_trend": 0.10,  # 自相关衰减趋势
    "outlier_freq_trend": 0.06,    # 异常点频率趋势
}

# DI 分级阈值
DI_THRESHOLDS = {
    "healthy": 15,    # DI < 15 健康
    "note": 30,       # DI < 30 注意
    "warning": 50,    # DI < 50 警告
    # DI >= 50 危险
}

# 特征方向判定: 对于每个特征, ↑(向上)是恶化还是改善?
# up_bad: mk_trend > 0 是坏方向（退化）
# down_bad: mk_trend < 0 是坏方向（退化）
FEATURE_DIRECTION = {
    "mean_trend": "up_bad",            # 均值↑ = 电流漂移增大 = 坏
    "std_trend": "up_bad",             # 标准差↑ = 波动增大 = 坏
    "rms_trend": "up_bad",             # RMS↑ = 同均值
    "skewness_trend": "up_bad",        # 偏度↑ = 冲击脉冲可能增多 = 坏
    "sample_entropy_trend": "up_bad",  # 样本熵↑ = 信号更不规则 = 坏
    "zero_cross_trend": "up_bad",      # 零交叉率↑ = 高频噪声增多 = 坏
    "autocorr_decay_trend": "down_bad", # 自相关↓(mk_trend<0) = "记忆力"流失 = 坏
    "outlier_freq_trend": "up_bad",    # 异常点频率↑ = 坏
    "mean_accel": "up_bad",            # 均值漂移加速度↑ = 坏
}

# 弱趋势检测阈值: MK不显著但Sen斜率/基线 > 此值, 标记为"弱趋势"
WEAK_TREND_THRESHOLD_PCT_PER_DAY = 0.15  # 0.15%/天 ≈ 4.5%/月

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 退化趋势 → 维护建议映射
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _degradation_maintenance_suggestions(feature_trends, di, di_label):
    """根据退化特征生成维护建议"""
    suggestions = []
    
    for ft in feature_trends:
        if not ft.get("has_trend"):
            continue
        name = ft["name"]
        is_bad = ft.get("is_bad_direction", True)
        if not is_bad:
            continue  # 好方向的变化不需要维护建议
        
        if name in ("mean_trend", "rms_trend"):
            suggestions.append({
                "feature": ft["label"],
                "suggestion": (
                    "电流均值持续上升，可能反映负载变化、绝缘老化或机械效率下降。"
                    "建议：① 检查电机实际负载是否匹配铭牌额定值；"
                    "② 测量绕组绝缘电阻（IEEE 43 标准）；"
                    "③ 关注运行温升变化。"
                ),
            })
        elif name == "std_trend":
            suggestions.append({
                "feature": ft["label"],
                "suggestion": (
                    "电流波动持续增大，常见原因包括机械松动、润滑不良或负载不稳定。"
                    "建议：① 检查联轴器对中与紧固件扭矩；"
                    "② 补充/更换轴承润滑脂；"
                    "③ 进行振动频谱分析确认频域异常成分。"
                ),
            })
        elif name == "sample_entropy_trend":
            suggestions.append({
                "feature": ft["label"],
                "suggestion": (
                    "信号规律性下降（样本熵上升），系统随机成分增加。"
                    "常见于轴承早期磨损、电磁异常或转子条裂纹。"
                    "建议：① 监测振动信号高频段变化趋势；"
                    "② 条件允许时进行 MCSA（电机电流信号分析）频谱检测。"
                ),
            })
        elif name == "zero_cross_trend":
            suggestions.append({
                "feature": ft["label"],
                "suggestion": (
                    "高频抖动成分增多（零交叉率上升），可能反映轴承滚道点蚀或电磁谐波。"
                    "建议：① 进行振动频谱分析，关注高频段异常；"
                    "② 检查电源谐波畸变率（THD）。"
                ),
            })
        elif name == "autocorr_decay_trend":
            suggestions.append({
                "feature": ft["label"],
                "suggestion": (
                    "信号自相关性下降（\"记忆力\"流失），系统动态特性在变化。"
                    "可能原因：转子气隙不均、转子导条缺陷或轴承游隙增大。"
                    "建议：检查转子条完整性（可用振动频谱边带分析）。"
                ),
            })
        elif name == "outlier_freq_trend":
            suggestions.append({
                "feature": ft["label"],
                "suggestion": (
                    "异常点频率上升，瞬时冲击事件增多。"
                    "可能原因：机械碰摩、碎屑撞击或间歇性电气故障。"
                    "建议：① 加强实时报警监控阈值；② 采集高频振动数据分析冲击事件特征。"
                ),
            })
        elif name == "skewness_trend":
            suggestions.append({
                "feature": ft["label"],
                "suggestion": (
                    "电流分布偏斜度变化，可能反映单向冲击脉冲增多。"
                    "建议：结合峭度指标综合判断是否为轴承点蚀早期特征。"
                ),
            })
    
    # DI 分级建议
    if di_label in ("警告", "危险"):
        suggestions.append({
            "feature": "综合退化指数",
            "suggestion": (
                f"综合退化指数 DI={di}（{di_label}级别），多个维度同时出现恶化趋势。"
                "强烈建议：① 安排近期计划性检修（2周内）；"
                "② 增加巡检频次至每日1次；③ 准备备件（轴承、密封等常用件）。"
            ),
        })
    elif di_label == "注意":
        suggestions.append({
            "feature": "综合退化指数",
            "suggestion": (
                f"综合退化指数 DI={di}（{di_label}级别），个别维度出现早期退化迹象。"
                "建议：① 安排月度巡检时重点关注；② 可在下次计划停机时进行检查。"
            ),
        })
    
    return suggestions


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
    触发后自动重置，防止长序列无限累积导致所有电机压到同一低分。
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
        # 触发后重置，防止无限累积
        if cp > h:
            triggered_pos.append(i)
            cp = 0.0
        if cn > h:
            triggered_neg.append(i)
            cn = 0.0
        c_pos.append(cp)
        c_neg.append(cn)
        max_pos = max(max_pos, cp)
    return c_pos, c_neg, max_pos, h, triggered_pos, triggered_neg


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 稳态数据提取 + 运行时长计算
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STOP_CURRENT_A = 1.0     # 停机判定电流 (A)，远低于正常运行 12~16A
START_CONSECUTIVE = 10   # 连续 >1A 的点数（确认启动）
SURGE_REMOVE = 50        # 启动浪涌剔除窗口
STOP_CONSECUTIVE = 10    # 连续 <1A 的点数（确认停机）
PRE_STOP_REMOVE = 70     # 停机前过渡剔除窗口
RUNTIME_START_N = 5      # 运行时长 — 开始计时连续点数
RUNTIME_STOP_N = 5       # 运行时长 — 停止计时连续点数
MK_WINDOW = 60           # MK 稳态检测窗口 (点数，60=1min@1s采样)
MK_P_THRESHOLD = 0.1     # MK p>=0.1 → 无显著单调趋势 → 判定稳态


def extract_steady_segments(data_all):
    """四阶段状态机 — 从全量数据中提取所有稳态段电流值。

    规则:
      停机→启动: 连续 {START_CONSECUTIVE} 点 > 1A
      启动→过渡: 启动点后剔除 {SURGE_REMOVE} 个浪涌点
      过渡→稳态: MK 滑动窗口 ({MK_WINDOW}点=1min) 检测，p>={MK_P_THRESHOLD} → 无显著趋势 → 稳态
      稳态→停机: 连续 {STOP_CONSECUTIVE} 点 < 1A
      稳态终点 = 停机点 - {PRE_STOP_REMOVE}（停机前过渡剔除）

    返回: [float, ...]  — 全部稳态段的电流值（只用于分析预测）
    """.format(START_CONSECUTIVE=START_CONSECUTIVE, SURGE_REMOVE=SURGE_REMOVE,
               MK_WINDOW=MK_WINDOW, MK_P_THRESHOLD=MK_P_THRESHOLD,
               STOP_CONSECUTIVE=STOP_CONSECUTIVE, PRE_STOP_REMOVE=PRE_STOP_REMOVE)

    STOPPED, STARTING, TRANSITION, STEADY = 0, 1, 2, 3
    state = STOPPED
    above_count = 0
    below_count = 0
    cnt_since_start = 0
    steady_start_idx = None
    steady_ranges = []      # [(start_idx, end_idx), ...]
    transition_buffer = []  # [(value, index), ...] for MK sliding window

    for i, d in enumerate(data_all):
        v = d["value"]
        if v is None:
            continue

        if state == STOPPED:
            if v > STOP_CURRENT_A:
                above_count += 1
                if above_count >= START_CONSECUTIVE:
                    state = STARTING
                    cnt_since_start = 0
                    above_count = 0
            else:
                above_count = 0

        elif state == STARTING:
            cnt_since_start += 1
            if cnt_since_start >= SURGE_REMOVE:
                state = TRANSITION
                transition_buffer = []
                below_count = 0

        elif state == TRANSITION:
            # 先检测停机
            if v < STOP_CURRENT_A:
                below_count += 1
                if below_count >= STOP_CONSECUTIVE:
                    state = STOPPED
                    below_count = 0
                    transition_buffer = []
                # 低电流点不加入缓冲，避免干扰 MK
                continue
            below_count = 0

            # 加入过渡缓冲
            transition_buffer.append((v, i))
            if len(transition_buffer) >= MK_WINDOW:
                recent = [p[0] for p in transition_buffer[-MK_WINDOW:]]
                mk_result = _mann_kendall(recent)
                if mk_result["p_value"] >= MK_P_THRESHOLD:
                    # 最近 1min 无显著单调趋势 → 进入稳态
                    steady_start_idx = transition_buffer[-MK_WINDOW][1]
                    state = STEADY
                    transition_buffer = []
                    below_count = 0

        elif state == STEADY:
            if v < STOP_CURRENT_A:
                below_count += 1
                if below_count >= STOP_CONSECUTIVE:
                    steady_end_idx = i - PRE_STOP_REMOVE
                    if steady_start_idx is not None and steady_end_idx >= steady_start_idx:
                        steady_ranges.append((steady_start_idx, steady_end_idx))
                    state = STOPPED
                    below_count = 0
                    steady_start_idx = None
            else:
                below_count = 0

    # 末尾仍在稳态（电机运行中，未检测到停机）
    if state == STEADY and steady_start_idx is not None:
        steady_ranges.append((steady_start_idx, len(data_all) - 1))
    # TRANSITION 状态下到达末尾 → 不记录稳态段（还没判定进入稳态）

    # 提取值
    result = []
    for start, end in steady_ranges:
        for j in range(start, end + 1):
            if j < len(data_all) and data_all[j]["value"] is not None:
                result.append(data_all[j]["value"])
    return result


def calc_runtime(data_all):
    """计算累计运行时长 — 与稳态提取完全独立。

    规则:
      开始计时: 连续 {RUNTIME_START_N} 点 > 1A
      停止计时: 连续 {RUNTIME_STOP_N} 点 < 1A
      时长 = 从计时开始到停止的时间差

    返回: float (小时)
    """.format(RUNTIME_START_N=RUNTIME_START_N, RUNTIME_STOP_N=RUNTIME_STOP_N)

    RUNNING = "running"
    STOPPED = "stopped"

    state = STOPPED
    above_count = 0
    below_count = 0
    run_start_ts = None
    total_seconds = 0.0

    for d in data_all:
        v = d["value"]
        if v is None:
            continue
        try:
            ts = datetime.fromisoformat(d["timestamp"])
        except (ValueError, TypeError):
            continue

        if state == STOPPED:
            if v > STOP_CURRENT_A:
                above_count += 1
                if above_count >= RUNTIME_START_N:
                    state = RUNNING
                    run_start_ts = ts
                    above_count = 0
            else:
                above_count = 0

        elif state == RUNNING:
            if v < STOP_CURRENT_A:
                below_count += 1
                if below_count >= RUNTIME_STOP_N:
                    state = STOPPED
                    if run_start_ts is not None:
                        total_seconds += max(0.0, (ts - run_start_ts).total_seconds())
                    run_start_ts = None
                    below_count = 0
            else:
                below_count = 0

    # 末尾仍在运行
    if state == RUNNING and run_start_ts is not None:
        try:
            last_ts = datetime.fromisoformat(data_all[-1]["timestamp"])
            total_seconds += max(0.0, (last_ts - run_start_ts).total_seconds())
        except (ValueError, TypeError, IndexError):
            pass

    return round(total_seconds / 3600.0, 1)


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
    """短期波动评分。ratio<1 表示波动降低（变好），但也给区分度；ratio>1 波动增加（变坏）按比例扣分。"""
    if baseline_std < 1e-9:
        return 50.0
    ratio = recent_rolling_std / baseline_std
    if ratio <= 0.5:
        return 100.0
    elif ratio < 0.8:
        return round(100.0 - (ratio - 0.5) * 16.7, 1)   # 100 ~ 95
    elif ratio <= 1.0:
        return round(95.0 - (ratio - 0.8) * 25.0, 1)    # 95 ~ 90
    elif ratio < 1.3:
        return round(90.0 - (ratio - 1.0) * 33.3, 1)    # 90 ~ 80
    elif ratio < 2.0:
        return round(80.0 - (ratio - 1.3) * 58.6, 1)    # 80 ~ 39
    elif ratio < 3.0:
        return round(39.0 - (ratio - 2.0) * 30.0, 1)    # 39 ~ 9
    else:
        return max(5, round(9.0 - (ratio - 3.0) * 4.0, 1))


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
    """峭度变化评分 — 基于比值 (ratio = cur / bl)，而非绝对差值。

    峭度是四阶距统计量，120K 数据点下 delta 轻松达到 ±50，
    用绝对差值评分会导致分数全部压在 5 或 99 两端。
    改用比值法后，1.5x 增长和 5x 增长有明确的分数梯度。

    ratio < 1 → 峭度下降（分布变正态）→ 改善，加分
    ratio > 1 → 峭度上升（厚尾加剧）  → 恶化，扣分
    """
    if baseline_kurtosis is None or baseline_kurtosis < 1e-9:
        baseline_kurtosis = 1.0
    ratio = current_kurtosis / max(baseline_kurtosis, 1.0)

    # ── 峭度下降 (ratio < 1) → 加分 ──
    if ratio <= 0.2:
        return 100.0                                           # 改善 >80%
    elif ratio <= 0.5:
        return round(95.0 + (0.5 - ratio) / 0.3 * 5.0, 1)    # 95~100
    elif ratio <= 0.8:
        return round(90.0 + (0.8 - ratio) / 0.3 * 5.0, 1)    # 90~95
    elif ratio <= 1.0:
        return round(85.0 + (1.0 - ratio) / 0.2 * 5.0, 1)    # 85~90

    # ── 峭度上升 (ratio > 1) → 扣分 ──
    elif ratio <= 1.2:
        return round(85.0 - (ratio - 1.0) / 0.2 * 5.0, 1)    # 80~85
    elif ratio <= 1.5:
        return round(80.0 - (ratio - 1.2) / 0.3 * 15.0, 1)   # 65~80
    elif ratio <= 2.5:
        return round(65.0 - (ratio - 1.5) / 1.0 * 25.0, 1)   # 40~65
    elif ratio <= 5.0:
        return round(40.0 - (ratio - 2.5) / 2.5 * 25.0, 1)   # 15~40
    else:
        return round(max(5.0, 15.0 - (ratio - 5.0) * 2.0), 1)  # 5~15


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
    if vol_ratio is not None and vol_ratio > 2.0:
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
# V3 — 退化趋势分析核心算法
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _sample_entropy(values, m=SAMPEN_M, r_factor=SAMPEN_R_FACTOR):
    """样本熵 — 衡量信号规律性/复杂度。

    熵值越高 = 信号越不规则 = 系统中随机成分越多。
    健康电机信号规律性强(低熵)，退化电机随机噪声增加(高熵)。

    Args:
        values: 信号序列 (需有时间顺序)
        m: 嵌入维数 (默认2)
        r_factor: 相似容限因子 (默认0.2, r = r_factor * std)
    Returns:
        float: 样本熵值, 数据不足返回 -1
    """
    n = len(values)
    if n < m + 2:
        return -1.0

    # 计算容限 r
    mean_v = sum(values) / n
    std_v = math.sqrt(sum((v - mean_v) ** 2 for v in values) / n)
    r = r_factor * max(std_v, 1e-9)

    # 构建模板向量
    def _count_matches(m_len):
        """统计长度为 m_len 的模板匹配对数"""
        count = 0
        for i in range(n - m_len):
            for j in range(i + 1, n - m_len):
                # Chebyshev 距离 (无穷范数)
                match = True
                for k in range(m_len):
                    if abs(values[i + k] - values[j + k]) > r:
                        match = False
                        break
                if match:
                    count += 1
        return count

    # 长度为 m 的匹配数
    a = _count_matches(m)
    # 长度为 m+1 的匹配数
    b = _count_matches(m + 1)

    if a < 1 or b < 1:
        return -1.0

    return -math.log(b / a)


def _zero_crossing_rate(values, mean_val=None):
    """零交叉率 — 信号穿越均值的频率。

    需要先减去均值（直流分量），统计交流分量穿越零点的频率。
    交叉率升高 = 信号中高频抖动成分增加。

    Args:
        values: 信号序列
        mean_val: 均值 (可选，不传则计算)
    Returns:
        float: 零交叉率 (次/秒, 基于1Hz采样)
    """
    if len(values) < 2:
        return 0.0

    if mean_val is None:
        mean_val = sum(values) / len(values)

    centered = [v - mean_val for v in values]
    crosses = 0
    for i in range(1, len(centered)):
        if (centered[i-1] >= 0 and centered[i] < 0) or \
           (centered[i-1] < 0 and centered[i] >= 0):
            crosses += 1

    # 归一化到次/秒 (采样率1Hz)
    return crosses / max(1, len(centered) - 1)


def _auto_correlation(values, max_lag=60):
    """自相关系数 (多个滞后) — 衡量信号的"时间依赖性"。

    健康电机: 自相关系数在滞后1/5/10秒处较高 (惯性/工艺关联)
    退化电机: 自相关系数下降更快 = "记忆力"在流失

    Args:
        values: 信号序列
        max_lag: 最大滞后步数 (默认60秒)
    Returns:
        dict: {lag: corr_coef, ...}
    """
    n = len(values)
    if n < max_lag + 2:
        return {}

    mean_v = sum(values) / n
    var_v = sum((v - mean_v) ** 2 for v in values) / n
    if var_v < 1e-9:
        return {lag: 0.0 for lag in [1, 5, 10, 30, 60] if lag <= n // 2}

    result = {}
    lags = [lag for lag in [1, 5, 10, 30, 60] if lag < n // 2]
    for lag in lags:
        numerator = sum(
            (values[i] - mean_v) * (values[i + lag] - mean_v)
            for i in range(n - lag)
        )
        result[lag] = numerator / ((n - lag) * var_v) if var_v > 0 else 0.0

    return result


def _mann_kendall(values):
    """Mann-Kendall 趋势检验 — 非参数单调趋势检测。

    不假设数据分布，只看方向: 后面的比前面大就+1，小就-1。
    对所有两两配对做统计，判断是否存在显著单调趋势。

    Returns:
        dict: {
            "trend": 1/0/-1 (上升/无/下降),
            "p_value": float,
            "statistic": float (Z统计量),
            "significant": bool (p<0.05),
        }
    """
    n = len(values)
    if n < 10:
        return {"trend": 0, "p_value": 1.0, "statistic": 0.0, "significant": False}

    # 计算 S 统计量
    s = 0
    for k in range(n - 1):
        for j in range(k + 1, n):
            diff = values[j] - values[k]
            if diff > 0:
                s += 1
            elif diff < 0:
                s -= 1

    # 方差 (考虑结修正)
    # 先计算结
    unique_vals = {}
    for v in values:
        unique_vals[v] = unique_vals.get(v, 0) + 1

    var_s = n * (n - 1) * (2 * n + 5) / 18.0
    for count in unique_vals.values():
        if count > 1:
            var_s -= count * (count - 1) * (2 * count + 5) / 18.0
    var_s = max(var_s, 1e-9)

    # Z 统计量
    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0

    # 双尾 p-value (正态近似)
    # 使用标准正态 CDF 近似
    abs_z = abs(z)
    if abs_z < 0.01:
        p_val = 1.0
    else:
        # 近似: 2 * (1 - Phi(|z|))
        p_val = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs_z / math.sqrt(2.0))))
    p_val = max(0.0, min(1.0, p_val))

    if p_val < 0.05:
        trend = 1 if s > 0 else (-1 if s < 0 else 0)
    else:
        trend = 0

    return {
        "trend": trend,
        "p_value": round(p_val, 4),
        "statistic": round(z, 3),
        "significant": p_val < 0.05,
    }


def _sen_slope(values):
    """Sen 斜率估计 — 所有两点连线斜率的中位数。

    比最小二乘更稳健，不受异常点影响。
    斜率单位: 变化量/窗口索引 (每个索引 ≈ 1小时)

    Returns:
        dict: {"slope": float} — 每窗口索引的变化量
    """
    n = len(values)
    if n < 2:
        return {"slope": 0.0}

    slopes = []
    for i in range(n - 1):
        for j in range(i + 1, n):
            dt = j - i
            if dt > 0:
                slopes.append((values[j] - values[i]) / dt)

    if not slopes:
        return {"slope": 0.0}

    slopes.sort()
    mid = len(slopes) // 2
    if len(slopes) % 2 == 1:
        return {"slope": slopes[mid]}
    else:
        return {"slope": (slopes[mid - 1] + slopes[mid]) / 2.0}


def _compute_window_features(steady_values, window_seconds=WINDOW_SECONDS):
    """将稳态数据按时长窗口分段，每窗口计算10个特征。

    Args:
        steady_values: 稳态电流值列表 (按时间顺序)
        window_seconds: 窗口时长(秒), 默认3600

    Returns:
        list[dict]: 每个窗口的特征字典, 按时间顺序排列
    """
    wp = max(WINDOW_MIN_POINTS, window_seconds)  # 最少1小时=3600点
    if window_seconds < wp:
        wp = window_seconds

    windows = []
    start = 0
    total = len(steady_values)

    while start < total:
        end = min(start + wp, total)
        chunk = steady_values[start:end]

        if len(chunk) < WINDOW_MIN_POINTS:
            start = end
            continue

        # 基础统计
        n = len(chunk)
        mean_v = sum(chunk) / n
        # 标准差
        variance = sum((v - mean_v) ** 2 for v in chunk) / n
        std_v = math.sqrt(max(variance, 1e-12))
        # RMS
        rms_v = math.sqrt(sum(v ** 2 for v in chunk) / n)
        # 偏度
        if std_v > 1e-9:
            skew_num = sum((v - mean_v) ** 3 for v in chunk) / n
            skew_v = skew_num / (std_v ** 3)
        else:
            skew_v = 0.0
        # 峰峰值
        p2p = max(chunk) - min(chunk)
        # 异常点频率 (偏离均值 > 2sigma 的点)
        threshold_2s = 2.0 * max(std_v, 0.01)
        outlier_count = sum(1 for v in chunk if abs(v - mean_v) > threshold_2s)
        outlier_rate = outlier_count / n
        # 零交叉率
        zcr = _zero_crossing_rate(chunk, mean_v)
        # 样本熵 (窗口内采样: 取前500点加速, 对熵估计影响很小)
        samp_vals = chunk[:500] if len(chunk) > 500 else chunk
        entropy = _sample_entropy(samp_vals)
        # 自相关 (lag=10 的系数)
        if n >= 20:
            auto_corr = _auto_correlation(chunk, max_lag=10)
            ac_lag10 = auto_corr.get(10, 0.0)
        else:
            ac_lag10 = 0.0

        features = {
            "start_idx": start,
            "end_idx": end,
            "n_points": n,
            "mean": round(mean_v, 4),
            "std": round(std_v, 4),
            "rms": round(rms_v, 4),
            "skewness": round(skew_v, 4),
            "peak_to_peak": round(p2p, 4),
            "outlier_rate": round(outlier_rate, 4),
            "zero_cross_rate": round(zcr, 6),
            "sample_entropy": round(entropy, 4) if entropy >= 0 else None,
            "autocorr_lag10": round(ac_lag10, 4),
        }
        windows.append(features)
        start = end

    return windows


def _analyze_degradation_trends(window_features, steady_values, data_span_days):
    """对窗口特征序列进行趋势分析，输出退化指数。

    对每个特征序列做 MK 检验 + Sen 斜率，判断是否在退化。
    多特征融合为综合退化指数 DI。

    Returns:
        dict: 完整的退化分析结果
    """
    if len(window_features) < 20:
        return {
            "di": 0.0,
            "di_label": "数据不足",
            "di_color": "#999",
            "di_explanation": "窗口数不足20个，无法进行可靠的趋势分析。",
            "features_trend": [],
            "has_trends": False,
            "has_weak_trends": False,
            "maintenance_suggestions": [],
            "window_count": len(window_features),
        }

    # ── 提取各特征序列 ──
    feature_names = [
        ("mean", "mean_trend", "均值 (A)", 1),
        ("std", "std_trend", "标准差 (A)", 2),
        ("rms", "rms_trend", "RMS (A)", 3),
        ("skewness", "skewness_trend", "偏度", 4),
        ("sample_entropy", "sample_entropy_trend", "样本熵", 5),
        ("zero_cross_rate", "zero_cross_trend", "零交叉率 (Hz)", 6),
        ("peak_to_peak", "rms_trend", "峰峰值 (A)", 7),
        ("autocorr_lag10", "autocorr_decay_trend", "自相关 lag10", 8),
        ("outlier_rate", "outlier_freq_trend", "异常点频率", 9),
    ]

    # 均值序列单独保存 (用于加速度分析)
    mean_series = None

    feature_trends = []
    for fname, di_key, label, order in feature_names:
        series = [w[fname] for w in window_features if w.get(fname) is not None]
        if len(series) < 20:
            continue

        if fname == "mean":
            mean_series = series

        # MK 检验
        mk = _mann_kendall(series)
        # Sen 斜率 (per窗口索引, 每个窗口 ≈ 1小时)
        sen = _sen_slope(series)
        # 估算每天窗口数
        windows_per_day = len(window_features) / max(1, data_span_days)
        slope_per_day = sen["slope"] * windows_per_day

        # 对 autocorr_decay: 相关系数下降 = 退化 (所以取负斜率 sign翻转)
        if fname == "autocorr_lag10":
            slope_per_day = -slope_per_day

        # 趋势强度: slope / 基线值
        baseline_val = series[0] if series[0] != 0 else (1e-6 if series[0] >= 0 else -1e-6)
        trend_strength = abs(slope_per_day) / abs(baseline_val) * 100.0  # 百分比

        # ── 趋势判定 (三级: strong/weak/none) ──
        # 1. MK显著 + 有方向 → strong trend
        # 2. MK不显著但Sen斜率有意义(>弱趋势阈值) → weak trend (回答用户"整体有趋势但MK不显著"的疑问)
        # 3. 其他 → none
        weak_threshold_abs = WEAK_TREND_THRESHOLD_PCT_PER_DAY  # 0.15%/天
        has_meaningful_slope = trend_strength >= weak_threshold_abs

        if mk["significant"] and mk["trend"] != 0:
            trend_level = "strong"
            has_trend = True
            effective_trend = mk["trend"]  # 用MK判断的方向
            p_display = f"p={mk['p_value']:.3f}"  # 保留3位有效数字
        elif has_meaningful_slope and slope_per_day != 0:
            trend_level = "weak"
            has_trend = True
            effective_trend = 1 if slope_per_day > 0 else -1  # 用Sen斜率判断方向
            p_display = f"p={mk['p_value']:.3f} (弱趋势)"
        else:
            trend_level = "none"
            has_trend = False
            effective_trend = 0
            p_display = f"p={mk['p_value']:.3f}"

        # ── 方向判定: ↑是好事还是坏事? ──
        dir_type = FEATURE_DIRECTION.get(di_key, "up_bad")
        if dir_type == "down_bad":
            # autocorr: mk_trend < 0 才是坏方向
            is_bad_direction = (effective_trend < 0) if has_trend else False
        else:
            # up_bad: mk_trend > 0 是坏方向
            is_bad_direction = (effective_trend > 0) if has_trend else False

        # 子分数: 只在有显著趋势(strong)时才扣分, weak趋势给轻度分数
        if trend_level == "strong":
            if trend_strength < 0.5:
                sub_score = max(0, round(20 - trend_strength * 40, 1))
            elif trend_strength < 2.0:
                sub_score = round(15 + (trend_strength - 0.5) / 1.5 * 35, 1)
            elif trend_strength < 5.0:
                sub_score = round(50 + (trend_strength - 2.0) / 3.0 * 30, 1)
            else:
                sub_score = round(80 + min(20, (trend_strength - 5.0) * 2), 1)
        elif trend_level == "weak":
            sub_score = min(10.0, round(trend_strength / weak_threshold_abs * 5, 1))
        else:
            sub_score = 0.0

        # 最近1周 vs 前1周的变化
        weeks_windows = min(168, len(series) // 2)
        if len(series) >= weeks_windows * 2:
            last_week_avg = sum(series[-weeks_windows:]) / weeks_windows
            prev_week_avg = sum(series[-2*weeks_windows:-weeks_windows]) / weeks_windows
            week_change = round((last_week_avg - prev_week_avg) / max(abs(prev_week_avg), 1e-9) * 100, 2)
        else:
            last_week_avg = sum(series[-min(24, len(series)):]) / min(24, len(series))
            prev_week_avg = last_week_avg
            week_change = 0.0

        ft = {
            "name": di_key,
            "label": label,
            "order": order,
            "mk_trend": mk["trend"],
            "mk_p_value": mk["p_value"],
            "mk_significant": mk["significant"],
            "sen_slope_per_day": round(slope_per_day, 6),
            "trend_strength_pct": round(trend_strength, 4),
            "effective_trend": effective_trend,
            "trend_level": trend_level,
            "has_trend": has_trend,
            "is_bad_direction": is_bad_direction,
            "direction_type": dir_type,
            "p_display": p_display,
            "sub_score": round(sub_score, 1),
            "week_change_pct": week_change,
            "values": series,
            "first_val": round(series[0], 4),
            "last_val": round(series[-1], 4),
            "total_change_pct": round((series[-1] - series[0]) / max(abs(series[0]), 1e-9) * 100, 2),
        }
        feature_trends.append(ft)

    if not feature_trends:
        return {
            "di": 0.0,
            "di_label": "无法分析",
            "di_color": "#999",
            "di_explanation": "未能提取到有效的窗口特征序列。",
            "features_trend": [],
            "has_trends": False,
            "has_weak_trends": False,
            "maintenance_suggestions": [],
            "window_count": len(window_features),
        }

    # ── 均值漂移加速度 ──
    mean_accel_score = 0.0
    if mean_series is not None and len(mean_series) >= 40:
        # 对 Sen 斜率序列再做 MK
        slopes_per_window = []
        step = max(1, len(mean_series) // 10)
        for i in range(0, len(mean_series) - step, step):
            seg = mean_series[i:i + step * 3]
            if len(seg) >= 10:
                s = _sen_slope(seg)
                slopes_per_window.append(s["slope"])

        if len(slopes_per_window) >= 5:
            accel_mk = _mann_kendall(slopes_per_window)
            if accel_mk["significant"] and accel_mk["trend"] != 0:
                accel_sen = _sen_slope(slopes_per_window)
                accel_strength = abs(accel_sen["slope"]) / max(abs(slopes_per_window[0]), 1e-12) * 100
                if accel_strength < 1:
                    mean_accel_score = 10.0
                elif accel_strength < 5:
                    mean_accel_score = round(10 + (accel_strength - 1) / 4 * 40, 1)
                else:
                    mean_accel_score = round(50 + min(30, (accel_strength - 5) * 3), 1)

    # ── DI 融合 ──
    # 只统计坏方向的趋势（好方向的变化不算退化）
    di = 0.0
    active_features = 0
    for ft in feature_trends:
        w = DI_WEIGHTS.get(ft["name"], 0.0)
        if w > 0 and ft.get("is_bad_direction", True):
            di += ft["sub_score"] * w
            if ft["sub_score"] > 0:
                active_features += 1

    # 均值加速度额外加分
    if mean_accel_score > 0:
        di += mean_accel_score * DI_WEIGHTS.get("mean_accel", 0.10)

    # 联动加权: 多个特征同时报警 → 额外加分
    if active_features >= 5:
        di = di * 1.3
    elif active_features >= 3:
        di = di * 1.15

    di = round(min(100, di), 1)

    # ── DI 分级 ──
    if di < DI_THRESHOLDS["healthy"]:
        di_label = "健康"
        di_color = "#2e7d32"
    elif di < DI_THRESHOLDS["note"]:
        di_label = "注意"
        di_color = "#e65100"
    elif di < DI_THRESHOLDS["warning"]:
        di_label = "警告"
        di_color = "#ef6c00"
    else:
        di_label = "危险"
        di_color = "#c62828"

    # ── 退化速率 / RUL ──
    degradation_rate = 0.0
    if mean_series and len(mean_series) >= 20:
        sen_all = _sen_slope(mean_series)
        windows_per_day = len(window_features) / max(1, data_span_days)
        degradation_rate = abs(sen_all["slope"]) * windows_per_day

    # ── DI=0 解释 ──
    strong_count = sum(1 for ft in feature_trends if ft["trend_level"] == "strong")
    weak_count = sum(1 for ft in feature_trends if ft["trend_level"] == "weak")
    if di == 0:
        if weak_count > 0:
            di_explanation = (
                f"DI=0 是因为所有维度均未通过MK显著性检验（p≥0.05），"
                f"但检测到 {weak_count} 个弱趋势（Sen斜率有意义但MK不显著）。"
                f"这通常意味着数据存在方向性迹象，但波动较大尚未达到统计显著性。"
                f"建议持续观察后续趋势发展。"
            )
        else:
            di_explanation = (
                f"DI=0 是因为所有维度均未检测到显著单调趋势（MK p≥0.05），"
                f"且Sen斜率也未超过弱趋势阈值（{WEAK_TREND_THRESHOLD_PCT_PER_DAY}%/天）。"
                f"窗口特征序列呈随机波动，电机运行状态稳定，无明显退化迹象。"
            )
    else:
        di_explanation = (
            f"DI={di}，其中 {strong_count} 个强趋势（MK显著）+ {weak_count} 个弱趋势。"
        )

    # ── 维护建议 ──
    has_weak_trends = weak_count > 0
    maintenance_suggestions = _degradation_maintenance_suggestions(feature_trends, di, di_label)

    return {
        "di": di,
        "di_label": di_label,
        "di_color": di_color,
        "di_explanation": di_explanation,
        "features_trend": feature_trends,
        "has_trends": strong_count > 0,
        "has_weak_trends": has_weak_trends,
        "strong_trend_count": strong_count,
        "weak_trend_count": weak_count,
        "maintenance_suggestions": maintenance_suggestions,
        "active_features_count": active_features,
        "mean_accel_score": round(mean_accel_score, 1),
        "degradation_rate": round(degradation_rate, 6),
        "window_count": len(window_features),
    }


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

    # ── 稳态数据提取 ──
    # 三阶段状态机：停机→启动(10点>1A)→浪涌剔除(50点)→稳态→停机前剔除(70点)→停机(10点<1A)
    values_steady = extract_steady_segments(all_data)
    # 用于统计的"运行数据"用稳态数据；同时统计 1A 以上的全量运行点数
    values_running_simple = [v for v in values_raw if v is not None and v >= STOP_CURRENT_A]
    running_count_raw = len(values_running_simple)
    pct_stopped = round((total_points - running_count_raw) / max(1, total_points) * 100, 1)

    if not values_steady or len(values_steady) < 10:
        return {
            "node_id": node_id,
            "display_name": dn,
            "total_points": total_points,
            "running_points": len(values_steady),
            "pct_stopped": pct_stopped,
            "data_insufficient": True,
            "error": (
                f"稳态数据不足（{len(values_steady)} 点），停机占比 {pct_stopped}%。"
                "当前可能处于停机状态或数据积累不充分。"
            ),
        }

    running_count = len(values_steady)

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
        baseline_values = values_steady[:split]
        analysis_values = values_steady[split:]
        baseline_source = f"前 {data_span_days // 2} 天（{len(baseline_values)} 点）"
    elif len(values_steady) >= 100:
        # 中等数据: 前半基线，后半分析
        split = running_count // 2
        baseline_values = values_steady[:split]
        analysis_values = values_steady[split:]
        baseline_source = f"前半段（{len(baseline_values)} 点，拆分）"
    else:
        # 数据少: 全部做基线，CUSUM 不可靠
        baseline_values = values_steady
        analysis_values = values_steady
        baseline_source = f"全量数据（{len(baseline_values)} 点，基线不稳定）"

    baseline_is_same = (baseline_values is analysis_values)

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
    if not data_sufficient or baseline_is_same:
        # 基线未拆分或数据不足时，CUSUM 可能误报，限制最低分并给提示
        cusum_score = max(cusum_score, 40)

    # ── 2. 短期波动增幅 ──
    if baseline_is_same:
        # 基线未拆分：近期波动与基线来自同一数据源，比较无意义
        vol_score = None
        vol_ratio = None
    else:
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
    # ewma_end 改用 analysis 期中位数，避免 λ=0.15 对尾端过于敏感
    ewma_values = _ewma(analysis_values, lam=0.15)  # 保留供报告展示
    ewma_end = sorted(analysis_values)[len(analysis_values) // 2]
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
        if r["value"] is None or r["value"] < STOP_CURRENT_A:
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
    # 有 None 的维度不参与加权，剩余权重重新归一化
    valid_weights = {k: SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS if dim_scores[k] is not None}
    weight_sum = sum(valid_weights.values())
    if weight_sum > 0:
        overall = sum(dim_scores[k] * (valid_weights[k] / weight_sum) for k in valid_weights)
    else:
        overall = 50.0
    if any(dim_scores[k] is not None and dim_scores[k] < 40 for k in SCORE_WEIGHTS):
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
    # 规则: 连续5点>1A开始计时, 连续5点<1A停止计时 (与稳态提取完全独立)
    cumulative_hours = calc_runtime(all_data)

    # ── 日均统计 ──
    daily_stats = {}
    for r in all_data:
        if r["value"] is None or r["value"] < STOP_CURRENT_A:
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

    # ── 五维度评分明细（健康/关注/告警都需要展开原因）──
    dim_detail = {}
    dim_names_map = {
        "cusum": ("CUSUM 累积漂移", "检测微小持续偏移"),
        "volatility": ("电流波动增幅", "短期滚动标准差 vs 基线"),
        "ewma": ("EWMA 趋势偏移", "指数加权移动平均偏离基线程度"),
        "excursion": ("异常偏移频次", "近期超出2σ范围的频率"),
        "kurtosis": ("峭度变化", "分布厚尾特征（冲击脉冲）"),
    }
    for key, (label, desc) in dim_names_map.items():
        score = dim_scores.get(key)
        if score is None:
            continue
        if score >= 85:
            level = "优秀"
        elif score >= 70:
            level = "正常"
        elif score >= 50:
            level = "偏低"
        else:
            level = "告警"
        dim_detail[key] = {
            "label": label,
            "desc": desc,
            "score": score,
            "level": level,
        }

    # ── 趋势图数据 ──
    # 取分析期数据用于图表（降采样到最多 500 点）
    chart_raw = analysis_values
    chart_ewma = ewma_values
    if len(chart_raw) > 500:
        step = len(chart_raw) // 500
        chart_raw = chart_raw[::step]
        chart_ewma = chart_ewma[::step]

    # ── V3: 退化趋势分析 ──
    degradation = None
    try:
        windows = _compute_window_features(values_steady)
        if windows and len(windows) >= 10:
            degradation = _analyze_degradation_trends(windows, values_steady, data_span_days)
    except Exception as e:
        degradation = {
            "di": 0.0, "di_label": "分析错误", "di_color": "#999",
            "di_explanation": f"分析异常: {e}",
            "features_trend": [], "has_trends": False,
            "has_weak_trends": False,
            "strong_trend_count": 0, "weak_trend_count": 0,
            "maintenance_suggestions": [],
            "window_count": 0, "error": str(e),
        }

    return {
        "node_id": node_id,
        "display_name": dn,
        "total_points": total_points,
        "running_points": running_count,
        "pct_stopped": pct_stopped,
        "data_span_days": data_span_days,
        "data_sufficient": data_sufficient,
        "baseline_source": baseline_source,
        "cumulative_hours": round(cumulative_hours, 1),
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
        "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "kurt_delta": round(kurt_delta, 3),
        "drift_per_day": round(drift_per_day, 4),
        "alerts": alerts,
        "dim_detail": dim_detail,
        "daily_avg": daily_avg,
        "daily_max": daily_max,
        "chart_raw": chart_raw,
        "chart_ewma": chart_ewma,
        "chart_cusum_pos": c_pos,
        "chart_cusum_neg": c_neg,
        "chart_cusum_h": h_val,
        # V3 退化趋势
        "degradation": degradation,
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
/* V3 — 退化趋势面板 */
.degradation-panel { background:white; border-radius:10px; margin-bottom:20px;
                    box-shadow:0 1px 3px rgba(0,0,0,0.08); overflow:hidden; }
.degradation-panel .panel-header { padding:16px 20px; display:flex; align-items:center;
                                   justify-content:space-between; border-bottom:1px solid #eee; }
.degradation-di-badge { display:inline-block; padding:4px 14px; border-radius:16px;
                        font-size:14px; font-weight:700; color:white; }
.degradation-feature-card { background:#f8f9fa; border-radius:8px; padding:14px;
                            margin-bottom:8px; border-left:3px solid #ccc; }
.degradation-feature-card.trend-up { border-left-color:#c62828; }
.degradation-feature-card.trend-down { border-left-color:#1565c0; }
.degradation-feature-card.trend-none { border-left-color:#ccc; }
.feature-trend-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }
.feature-trend-header .ft-name { font-size:13px; font-weight:600; }
.feature-trend-header .ft-slope { font-size:12px; color:#666; }
.feature-trend-chart { height:200px; margin-top:8px; }
.di-overview-chart { height:320px; padding:12px; }
.trend-direction-up { color:#c62828; }
.trend-direction-down { color:#1565c0; }
.trend-direction-none { color:#999; }
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
            f'<td style="color:{"#c62828" if (ds["volatility"] is not None and ds["volatility"]<60) else "#333"}">{ds["volatility"] if ds["volatility"] is not None else "—"}</td>'
            f'<td style="color:{"#c62828" if ds["ewma"]<60 else "#333"}">{ds["ewma"]}</td>'
            f'<td style="color:{"#c62828" if ds["excursion"]<60 else "#333"}">{ds["excursion"]}</td>'
            f'<td style="color:{"#c62828" if ds["kurtosis"]<60 else "#333"}">{ds["kurtosis"]}</td>'
            f'<td>{rul_display}</td>'
            f'<td>{r["cumulative_hours"]} h</td>'
            f'<td>{r["running_points"]:,} 点</td>'
            f'</tr>'
        )

    html += "</tbody></table></div>"

    # ── V3: 退化指数 DI 总览 ──
    di_healthy = sum(1 for r in results if r.get("degradation") and r["degradation"]["di"] < 15)
    di_note = sum(1 for r in results if r.get("degradation") and 15 <= r["degradation"]["di"] < 30)
    di_warn = sum(1 for r in results if r.get("degradation") and 30 <= r["degradation"]["di"] < 50)
    di_danger = sum(1 for r in results if r.get("degradation") and r["degradation"]["di"] >= 50)

    html += f"""
<div class="degradation-panel">
<div class="panel-header">
  <h3 style="margin:0;font-size:16px;">多维退化指数 (DI) 总览</h3>
  <span style="font-size:12px;color:#999;">基于全量稳态数据窗口特征趋势分析</span>
</div>
<div style="padding:16px 20px;">
  <div class="summary-grid" style="margin-bottom:16px;">
    <div class="summary-card"><div class="num" style="color:#2e7d32">{di_healthy}</div><div class="lbl">健康 (DI&lt;15)</div></div>
    <div class="summary-card"><div class="num" style="color:#e65100">{di_note}</div><div class="lbl">注意</div></div>
    <div class="summary-card"><div class="num" style="color:#ef6c00">{di_warn}</div><div class="lbl">警告</div></div>
    <div class="summary-card"><div class="num" style="color:#c62828">{di_danger}</div><div class="lbl">危险 (DI&gt;=50)</div></div>
  </div>
  <div class="di-overview-chart">
    <canvas id="di_overview_chart"></canvas>
  </div>
</div>
</div>
"""

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
    <div class="val" style="color:{'#c62828' if (ds['volatility'] is not None and ds['volatility']<60) else '#2e7d32'}">{ds['volatility'] if ds['volatility'] is not None else '—'}</div>
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
<tr><td>波动比</td><td>{r['vol_ratio'] if r['vol_ratio'] is not None else '—'}×</td><td>短期滚动标准差 / 基线标准差</td></tr>
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

        # ── 五维度评分明细 ──
        dim_detail = r.get("dim_detail", {})
        if dim_detail:
            low_dims = {k: v for k, v in dim_detail.items() if v["score"] < 70}
            if low_dims:
                html += """
<div style="margin-top:16px; background:#fff8e1; border-radius:8px; padding:16px; border-left:4px solid #e65100;">
<h3 style="margin:0 0 12px 0; font-size:14px; color:#e65100;">健康度偏低原因分析</h3>
<p style="font-size:12px; color:#666; margin-bottom:12px;">以下维度评分偏低导致综合健康度降至 {0}%：</p>
<table style="width:100%; border-collapse:collapse; font-size:12px;">
<tr style="background:#ffecb3;"><th style="padding:6px 10px; text-align:left;">维度</th><th style="padding:6px 10px;">评分</th><th style="padding:6px 10px;">等级</th><th style="padding:6px 10px; text-align:left;">说明</th></tr>
""".format(r["overall"])
                for key, info in sorted(low_dims.items(), key=lambda x: x[1]["score"]):
                    color = "#c62828" if info["score"] < 50 else "#e65100"
                    html += (
                        f'<tr><td style="padding:6px 10px; font-weight:600;">{info["label"]}</td>'
                        f'<td style="padding:6px 10px; text-align:center; color:{color}; font-weight:700;">{info["score"]}</td>'
                        f'<td style="padding:6px 10px; text-align:center;">{info["level"]}</td>'
                        f'<td style="padding:6px 10px; color:#666;">{info["desc"]}</td></tr>'
                    )
                html += "</table></div>"
            else:
                # 所有维度都60-70但综合偏低(因为分散拖累)
                html += f"""
<div style="margin-top:16px; background:#e8f5e9; border-radius:8px; padding:14px; border-left:4px solid #2e7d32;">
  <span style="font-size:12px; color:#333;">各维度评分均处于正常范围（最低 {min((v['score'] for v in dim_detail.values()), default=70)} 分），"
  综合健康度为 {r['overall']}% 属合理范围。</span>
</div>"""

        # ── V3: 退化趋势分析 ──
        degradation = r.get("degradation")
        if degradation and degradation.get("window_count", 0) >= 10:
            dg = degradation
            trend_parts = []
            for ft in dg.get("features_trend", []):
                if ft.get("has_trend"):
                    sym = "&#8593;" if ft["effective_trend"] > 0 else "&#8595;"
                    level_mark = "" if ft["trend_level"] == "strong" else "~"
                    trend_parts.append(f'{ft["label"]} {sym}{level_mark}')
            trend_summary = "、".join(trend_parts[:3]) or "无明显趋势"

            # DI 解释
            di_expl = dg.get("di_explanation", "")
            arrow_note = (
                "箭头方向基于<strong>全量历史数据的MK趋势检验 + Sen斜率估计</strong>"
                "（非与上周/昨日对比），&#8593; 表示特征值随时间单调上升，&#8595; 表示下降。"
                "标注 ~ 为弱趋势（Sen斜率有意义但MK不显著）。"
            )

            html += f"""
<div style="border-top:1px solid #eee; padding:16px 24px;">
  <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
    <h3 style="margin:0;">退化趋势分析</h3>
    <span class="degradation-di-badge" style="background:{dg['di_color']}">DI {dg['di']} — {dg['di_label']}</span>
  </div>
  <div style="font-size:12px; color:#888; margin-bottom:16px; background:#f5f5f5; border-radius:6px; padding:10px 14px;">
    {arrow_note}
  </div>
  <div style="font-size:12px; color:#666; margin-bottom:12px;">{di_expl}</div>
  <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:8px; margin-bottom:12px;">
"""
            for ft in sorted(dg.get("features_trend", []), key=lambda x: x.get("order", 99)):
                if not ft.get("has_trend"):
                    continue
                eff_trend = ft["effective_trend"]
                is_bad = ft.get("is_bad_direction", True)
                trend_cls = "trend-up" if eff_trend > 0 else "trend-down"
                sym = "&#8593;" if eff_trend > 0 else "&#8595;"
                level_label = "" if ft["trend_level"] == "strong" else " ~弱趋势~"
                
                # 方向标记
                if is_bad:
                    dir_badge = '<span style="font-size:11px;color:#c62828;font-weight:600;">⚠ 坏方向</span>'
                else:
                    dir_badge = '<span style="font-size:11px;color:#2e7d32;font-weight:600;">✅ 好方向</span>'
                
                # 弱趋势降低视觉权重
                opacity = 'opacity:0.7;' if ft["trend_level"] == "weak" else ''
                
                html += f"""
    <div class="degradation-feature-card {trend_cls}" style="{opacity}">
      <div class="feature-trend-header">
        <span class="ft-name">{ft['label']} {sym}{level_label}</span>
        <span class="ft-slope">{ft['sen_slope_per_day']:+.6f}/天</span>
      </div>
      <div style="font-size:12px; color:#888; margin-bottom:2px;">
        {ft['p_display']} | 趋势强度 {ft['trend_strength_pct']:.2f}%
      </div>
      <div style="font-size:11px; margin-bottom:4px;">
        {dir_badge}
        <span style="color:#999; margin-left:6px;">全周期: {ft['first_val']:.4f} → {ft['last_val']:.4f} ({ft['total_change_pct']:+.2f}%)</span>
      </div>
    </div>
"""
            if not any(ft.get("has_trend") for ft in dg.get("features_trend", [])):
                html += f"""
    <div style="grid-column:1/-1; padding:16px; color:#999; font-size:13px;">
      各维度特征均未检测到显著单调趋势（含弱趋势检测），电机运行状态稳定。
    </div>
"""

            # ── 退化维护建议 ──
            maint = dg.get("maintenance_suggestions", [])
            if maint:
                html += """
  </div>
  <div style="margin-top:12px; background:#e8eaf6; border-radius:8px; padding:14px;">
    <h4 style="margin:0 0 10px 0; font-size:13px; color:#1a237e;">退化趋势维护建议</h4>
"""
                for ms in maint:
                    html += (
                        f'<div style="margin-bottom:8px; font-size:12px; line-height:1.6;">'
                        f'<strong style="color:#283593;">[{ms["feature"]}]</strong> '
                        f'{ms["suggestion"]}</div>'
                    )
                html += '</div>'

            html += f"""
  </div>
  <div style="font-size:11px; color:#999; margin-top:8px;">
    分析窗口数: {dg.get("window_count", 0)} | 强趋势: {dg.get("strong_trend_count", 0)} | 弱趋势: {dg.get("weak_trend_count", 0)} | 算法: MK趋势检验 + Sen斜率估计 + 样本熵
  </div>
</div>
"""

        html += '</div></div>'

    # ── Chart.js 数据 ──
    # V3: DI 总览数据
    di_overview = {}
    for r in results:
        if r.get("data_insufficient"):
            continue
        dn = r["display_name"]
        dg = r.get("degradation")
        if dg:
            di_overview[dn] = {
                "di": dg["di"],
                "label": dg["di_label"],
                "color": dg["di_color"],
            }

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
const diData = {json.dumps(di_overview, ensure_ascii=False)};

// ── V3: DI 总览柱状图 ──
(function() {{
  const ctx = document.getElementById('di_overview_chart');
  if (!ctx || !diData) return;
  const motors = Object.keys(diData);
  if (motors.length === 0) return;
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: motors,
      datasets: [{{
        label: '退化指数 (DI)',
        data: motors.map(m => diData[m].di),
        backgroundColor: motors.map(m => diData[m].color),
        borderWidth: 0,
        borderRadius: 4,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      animation: false,
      scales: {{
        y: {{
          beginAtZero: true, max: 100,
          title: {{ display: true, text: '退化指数 (DI)' }},
          ticks: {{ callback: v => v.toFixed(0) }}
        }},
        x: {{ ticks: {{ font: {{ size: 10 }} }} }}
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: ctx => diData[motors[ctx.dataIndex]].label + ' (DI=' + ctx.raw.toFixed(1) + ')'
          }}
        }}
      }}
    }}
  }});
}})();


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
<div class="footer">由 OPC UA Bridge 自动生成 — {generated_at} | V3 多维退化感知 | 算法: CUSUM+EWMA+峭度+威布尔+MK趋势+样本熵</div>
</body>
</html>"""

    return html


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 主入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    parser = argparse.ArgumentParser(
        description="磺化釜电机预测性维护报告 (V3 · 退化趋势感知)"
    )
    parser.add_argument("--output", type=str, default=None, help="输出文件路径（默认自动生成）")
    args = parser.parse_args()

    now = datetime.now()
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S")

    print(f"═" * 60)
    print(f"磺化釜电机预测性维护报告 (V3 — 退化趋势感知)")
    print(f"生成时间: {generated_at}")
    print(f"数据库: {DB_PATH}")
    print(f"分析: 一级异常检测 (CUSUM+EWMA+峭度+威布尔) + 二级退化感知 (MK趋势+Sen斜率+样本熵)")
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
