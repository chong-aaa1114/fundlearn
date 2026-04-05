from __future__ import annotations

import math
from statistics import mean, pstdev


def pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 100, 2)


def moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    segment = values[-window:]
    return sum(segment) / len(segment)


def period_return(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    base = values[-period - 1]
    if base == 0:
        return None
    return values[-1] / base - 1


def annualized_volatility(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    return pstdev(daily_returns) * math.sqrt(252)


def max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = value / peak - 1
        worst = min(worst, drawdown)
    return abs(worst)


def range_position(values: list[float], lookback: int = 90) -> float:
    if not values:
        return 0.0
    segment = values[-lookback:] if len(values) >= lookback else values
    low = min(segment)
    high = max(segment)
    if math.isclose(high, low):
        return 0.5
    return (segment[-1] - low) / (high - low)


def compute_base_metrics(history: list[dict]) -> dict:
    nav_values = [row["unit_nav"] for row in history]
    daily_returns = [row["daily_return"] for row in history[1:]]
    latest_nav = nav_values[-1]
    metrics = {
        "latest_nav": latest_nav,
        "latest_date": history[-1]["nav_date"],
        "return_1m": period_return(nav_values, 20),
        "return_3m": period_return(nav_values, 60),
        "return_6m": period_return(nav_values, 120),
        "return_12m": period_return(nav_values, 240),
        "volatility": annualized_volatility(daily_returns),
        "max_drawdown": max_drawdown(nav_values),
        "ma20": moving_average(nav_values, 20),
        "ma60": moving_average(nav_values, 60),
        "range_position": range_position(nav_values, 90),
        "recent_10d": period_return(nav_values, 10),
        "history_points": len(nav_values),
    }
    return metrics


def recommendation_from_metrics(
    fund: dict,
    metrics: dict,
    category_avg_3m: float | None,
    position: dict | None = None,
) -> dict:
    score = 50
    reasons: list[str] = []
    ma20 = metrics["ma20"]
    ma60 = metrics["ma60"]
    ret_3m = metrics["return_3m"]
    drawdown = metrics["max_drawdown"]
    volatility = metrics["volatility"]
    position_ratio = metrics["range_position"]

    if ma20 and ma60:
        if ma20 > ma60:
            score += 12
            reasons.append("20 日均线在 60 日均线上方，中期趋势保持偏强。")
        else:
            score -= 10
            reasons.append("20 日均线跌破 60 日均线，短中期走势转弱。")

    if ret_3m is not None:
        if ret_3m > 0.08:
            score += 10
            reasons.append("近 3 个月收益表现较好，资金关注度偏强。")
        elif ret_3m < -0.08:
            score -= 8
            reasons.append("近 3 个月回撤较明显，需要控制节奏。")

    if category_avg_3m is not None and ret_3m is not None:
        if ret_3m > category_avg_3m + 0.02:
            score += 6
            reasons.append(f"跑赢同类平均约 {pct(ret_3m - category_avg_3m)}%，相对强弱更优。")
        elif ret_3m < category_avg_3m - 0.02:
            score -= 6
            reasons.append(f"落后同类平均约 {pct(category_avg_3m - ret_3m)}%，需要继续观察。")

    if drawdown < 0.15:
        score += 8
        reasons.append("历史最大回撤控制较稳，适合做底仓观察。")
    elif drawdown > 0.25:
        score -= 10
        reasons.append("历史最大回撤偏大，仓位不宜过重。")

    if volatility < 0.16:
        score += 4
        reasons.append("波动率处于可接受区间，策略执行难度较低。")
    elif volatility > 0.24:
        score -= 5
        reasons.append("波动率较高，更适合分批操作而不是一次性重仓。")

    if position_ratio < 0.3 and ma20 and ma60 and ma20 > ma60:
        score += 7
        reasons.append("当前处于近阶段偏低位置，若继续看好可考虑分批布局。")
    elif position_ratio > 0.8 and ma20 and ma60 and ma20 < ma60:
        score -= 7
        reasons.append("价格靠近阶段高位但趋势转弱，追高性价比不足。")

    action = "继续观察"
    tag = "neutral"
    if score >= 78:
        action = "适合继续定投"
        tag = "positive"
    elif score >= 66:
        action = "可以分批加仓"
        tag = "positive"
    elif score >= 54:
        action = "继续观察"
        tag = "neutral"
    elif score >= 42:
        action = "暂缓操作"
        tag = "warning"
    else:
        action = "注意风险"
        tag = "danger"

    holding = None
    if position:
        cost_basis = position["cost_basis"]
        current_return = metrics["latest_nav"] / cost_basis - 1 if cost_basis else 0.0
        current_value = position["shares"] * metrics["latest_nav"]
        cost_value = position["shares"] * cost_basis
        holding = {
            "shares": position["shares"],
            "cost_basis": cost_basis,
            "buy_date": position["buy_date"],
            "current_return": current_return,
            "current_value": current_value,
            "cost_value": cost_value,
        }
        if current_return > 0.15 and ma20 and ma60 and ma20 < ma60:
            action = "考虑止盈"
            tag = "warning"
            reasons.insert(0, f"当前浮盈约 {pct(current_return)}%，且近期趋势转弱，可考虑落袋一部分利润。")
        elif current_return < -0.12 and score < 50:
            action = "控制仓位"
            tag = "danger"
            reasons.insert(0, f"当前浮亏约 {pct(abs(current_return))}%，且趋势未企稳，优先控制风险。")
        elif current_return > 0.05 and action == "适合继续定投":
            reasons.insert(0, f"当前仍有约 {pct(current_return)}% 浮盈，适合延续定投而不是追涨加满。")

    confidence = min(0.93, max(0.45, 0.5 + abs(score - 50) / 100))
    return {
        "score": round(score, 1),
        "action": action,
        "tag": tag,
        "confidence": round(confidence, 2),
        "reasons": reasons[:4],
        "metrics": {
            "latest_nav": round(metrics["latest_nav"], 4),
            "latest_date": metrics["latest_date"],
            "return_1m": pct(metrics["return_1m"]),
            "return_3m": pct(metrics["return_3m"]),
            "return_6m": pct(metrics["return_6m"]),
            "return_12m": pct(metrics["return_12m"]),
            "volatility": pct(metrics["volatility"]),
            "max_drawdown": pct(metrics["max_drawdown"]),
            "range_position": pct(metrics["range_position"]),
            "category_avg_3m": pct(category_avg_3m),
        },
        "holding": holding,
        "fund": {
            "code": fund["code"],
            "name": fund["name"],
            "category": fund["category"],
            "manager": fund["manager"],
            "risk_level": fund["risk_level"],
            "description": fund["description"],
            "data_source": fund.get("data_source", "real"),
            "last_synced_at": fund.get("last_synced_at"),
        },
    }


def category_averages(funds: list[dict], metrics_by_code: dict[str, dict]) -> dict[str, float | None]:
    grouped: dict[str, list[float]] = {}
    for fund in funds:
        ret_3m = metrics_by_code[fund["code"]]["return_3m"]
        if ret_3m is None:
            continue
        grouped.setdefault(fund["category"], []).append(ret_3m)
    return {category: mean(values) for category, values in grouped.items()}
