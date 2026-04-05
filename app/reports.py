from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean

from app.analytics import pct
from app.ai_provider import AIProviderError, get_current_ai_config, request_json_completion
from app.config import load_env
from app.news import fetch_relevant_news


load_env()

REPORTS_DIR = Path(__file__).resolve().parent.parent / "data" / "reports"


def _safe_pct(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value * 100:.2f}%"


def period_return(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    base = values[-period - 1]
    if base == 0:
        return None
    return values[-1] / base - 1


def build_report_context(detail: dict) -> dict:
    history = detail["history"]
    analysis = detail["analysis"]
    fund = detail["fund"]
    navs = [row["unit_nav"] for row in history]
    daily_returns = [row["daily_return"] for row in history[-10:]]
    latest_return = history[-1]["daily_return"] if history else 0.0

    category_or_name = f"{fund['category']} {fund['name']}"
    style_hints = []
    for keyword, hint in [
        ("沪深300", "与大盘蓝筹风格和宽基指数走势关联较强。"),
        ("白酒", "与消费、白酒板块情绪和估值变化高度相关。"),
        ("QDII", "会同时受到海外市场和汇率波动影响。"),
        ("医疗", "容易受医药板块风险偏好和政策预期变化影响。"),
        ("红利", "偏防御和高股息风格，波动通常小于高成长板块。"),
    ]:
        if keyword in category_or_name:
            style_hints.append(hint)

    news_highlights = fetch_relevant_news(fund)
    context = {
        "report_date": datetime.now().date().isoformat(),
        "fund": {
            "code": fund["code"],
            "name": fund["name"],
            "category": fund["category"],
            "manager": fund["manager"],
            "risk_level": fund["risk_level"],
        },
        "position": analysis.get("holding"),
        "metrics": {
            "latest_nav": analysis["metrics"]["latest_nav"],
            "latest_date": analysis["metrics"]["latest_date"],
            "return_1d": pct(latest_return),
            "return_5d": pct(period_return(navs, 5)),
            "return_10d": pct(period_return(navs, 10)),
            "return_20d": analysis["metrics"]["return_1m"],
            "return_60d": analysis["metrics"]["return_3m"],
            "return_120d": analysis["metrics"]["return_6m"],
            "max_drawdown": analysis["metrics"]["max_drawdown"],
            "volatility": analysis["metrics"]["volatility"],
            "range_position": analysis["metrics"]["range_position"],
            "score": analysis["score"],
            "action": analysis["action"],
        },
        "recent_navs": history[-10:],
        "recent_avg_return": mean(daily_returns) if daily_returns else 0.0,
        "style_hints": style_hints,
        "base_reasons": analysis["reasons"],
        "news_highlights": news_highlights,
    }
    return context


def heuristic_report(context: dict) -> dict:
    metrics = context["metrics"]
    position = context.get("position") or {}
    news_highlights = context.get("news_highlights", [])
    rise_drivers = []
    fall_drivers = []
    watch_points = []
    action_plan = []
    news_reason_lines = []

    if metrics["return_5d"] not in (None, "--") and float(str(metrics["return_5d"]).replace("%", "")) > 0:
        rise_drivers.append("近 5 个交易日净值仍在修复，短线资金情绪偏暖。")
    if metrics["return_20d"] is not None and metrics["return_20d"] > 0:
        rise_drivers.append("近 1 个月收益为正，说明中短期趋势仍有支撑。")
    if metrics["range_position"] is not None and metrics["range_position"] > 75:
        rise_drivers.append("当前净值位于近阶段相对高位，说明前期上涨动能仍有残留。")
    if metrics["return_1d"] is not None and metrics["return_1d"] < 0:
        fall_drivers.append("最新一个交易日净值回落，短线情绪偏弱。")
    if metrics["return_20d"] is not None and metrics["return_20d"] < 0:
        fall_drivers.append("近 1 个月收益为负，说明这一轮调整尚未完全结束。")
    if metrics["max_drawdown"] is not None and metrics["max_drawdown"] > 20:
        fall_drivers.append("历史最大回撤偏大，说明这只基金在弱市里下跌弹性也会更明显。")

    for hint in context["style_hints"]:
        if "关联较强" in hint or "影响" in hint:
            rise_drivers.append(hint)

    if news_highlights:
        focus_titles = "；".join(item["title"] for item in news_highlights[:3])
        news_reason_lines.append(f"今天抓到的相关财经标题主要有：{focus_titles}。")
        for article in news_highlights[:4]:
            title = article["title"]
            if article["sentiment"] == "positive":
                rise_drivers.append(f"新闻《{title}》对应的主题情绪偏正面，可能对这只基金的相关板块形成支撑。")
            elif article["sentiment"] == "negative":
                fall_drivers.append(f"新闻《{title}》反映的外部扰动或风险偏好变化，可能会压制相关资产表现。")
            else:
                watch_points.append(f"新闻《{title}》值得继续跟踪，它可能决定后续板块资金是延续还是转弱。")
    else:
        news_reason_lines.append("今天没有抓到足够直接的相关新闻标题，所以当前解读仍以净值走势和风格归因为主。")

    if metrics["volatility"] is not None and metrics["volatility"] > 20:
        watch_points.append("波动率较高，日常涨跌会比较明显，仓位不宜过重。")
    if metrics["range_position"] is not None and metrics["range_position"] > 80:
        watch_points.append("净值已靠近近阶段高位，追涨性价比一般。")
    if metrics["range_position"] is not None and metrics["range_position"] < 25:
        watch_points.append("净值接近近阶段低位，若继续走弱需要关注是否跌破趋势。")

    if position:
        current_return = position.get("current_return", 0.0)
        if current_return > 0.15:
            action_plan.append("你当前已有一定浮盈，适合优先盯趋势是否继续走强，再决定是否分批止盈。")
        elif current_return < -0.08:
            action_plan.append("当前仍处于浮亏区间，更适合控制节奏，避免情绪化补仓。")
        else:
            action_plan.append("当前盈亏在可控区间，建议结合趋势再决定是否继续持有或小幅调整。")

    action_plan.append(f"策略信号当前给到“{metrics['action']}”，建议把趋势和仓位管理放在同等优先级。")

    if not rise_drivers:
        rise_drivers.append("目前没有特别强的上行共振信号，后续要继续看趋势是否增强。")
    if not fall_drivers:
        fall_drivers.append("暂时没有特别强的下跌破位信号，但仍要关注后续资金情绪变化。")
    if not watch_points:
        watch_points.append("短期最值得看的仍是近 1 个月收益和波动率是否继续恶化。")

    summary = (
        f"{context['fund']['name']} 当前最新净值 {metrics['latest_nav']:.4f}，近 1 个月收益 "
        f"{metrics['return_20d'] if metrics['return_20d'] is not None else '--'}%，"
        f"系统策略判断为“{metrics['action']}”。"
    )

    reason_analysis = [
        "这份解读同时参考了真实净值走势、回撤、波动率、持仓盈亏以及当天抓到的相关新闻标题。",
        "如果只看涨跌原因，短线更看最近 1 到 5 个交易日净值变化；加上新闻后，可以更接近市场当天的交易主线。",
        "需要注意，当前新闻归因仍然主要基于标题和主题匹配，不等同于完整事件研究。",
    ] + news_reason_lines

    return {
        "title": f"{context['fund']['name']} 每日解读",
        "summary": summary,
        "reason_analysis": reason_analysis,
        "rise_drivers": rise_drivers[:4],
        "fall_drivers": fall_drivers[:4],
        "watch_points": watch_points[:4],
        "action_plan": action_plan[:4],
        "news_highlights": news_highlights[:5],
        "model_name": "heuristic-fallback",
        "provider_name": "fallback",
        "provider_label": "规则回退",
        "provider_model": "heuristic-fallback",
        "used_fallback": True,
        "fallback_reason": "",
    }


def _coerce_lines(value, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        if items:
            return items[:4]
    return fallback[:4]


def build_ai_prompt(context: dict) -> str:
    return (
        "你是一名谨慎、专业、解释型的基金分析助手。"
        "请基于给定的真实净值数据和持仓信息，输出一份中文 JSON。"
        "重点回答：这只基金近期为什么涨、为什么跌、对当前持仓意味着什么、明天应该重点看什么。"
        "不要给绝对化承诺，不要编造新闻，不要说你看到未提供的外部信息。"
        "如果引用新闻，只能使用上下文里提供的新闻标题。"
        "JSON 必须包含这些字段：title, summary, reason_analysis, rise_drivers, fall_drivers, watch_points, action_plan。"
        "其中 reason_analysis, rise_drivers, fall_drivers, watch_points, action_plan 都必须是字符串数组。"
        f"\n\n上下文数据：\n{json.dumps(context, ensure_ascii=False)}"
    )


def _build_fallback_report(context: dict, connection=None, reason: str = "") -> dict:
    report = heuristic_report(context)
    config = get_current_ai_config(connection)
    report["provider_name"] = config["provider"]
    report["provider_label"] = config["provider_label"]
    report["provider_model"] = config["model"]
    report["model_name"] = f"{config['provider_label']} · heuristic-fallback"
    report["used_fallback"] = True
    report["fallback_reason"] = reason
    if reason:
        report["reason_analysis"] = [
            f"这次没有直接使用 {config['provider_label']} 模型输出，已自动回退到规则解读。",
            f"回退原因：{reason}",
        ] + report["reason_analysis"]
    return report


def generate_ai_report(context: dict, connection=None) -> dict:
    fallback = heuristic_report(context)
    prompt = build_ai_prompt(context)
    try:
        result = request_json_completion(prompt, connection)
    except AIProviderError as exc:
        return _build_fallback_report(context, connection, str(exc))

    parsed = result["parsed"]
    report = {
        "title": parsed.get("title") or f"{context['fund']['name']} 每日解读",
        "summary": parsed.get("summary") or fallback["summary"],
        "reason_analysis": _coerce_lines(parsed.get("reason_analysis"), fallback["reason_analysis"]),
        "rise_drivers": _coerce_lines(parsed.get("rise_drivers"), fallback["rise_drivers"]),
        "fall_drivers": _coerce_lines(parsed.get("fall_drivers"), fallback["fall_drivers"]),
        "watch_points": _coerce_lines(parsed.get("watch_points"), fallback["watch_points"]),
        "action_plan": _coerce_lines(parsed.get("action_plan"), fallback["action_plan"]),
        "news_highlights": context.get("news_highlights", [])[:5],
        "model_name": f"{result['provider_label']} · {result['model']}",
        "provider_name": result["provider"],
        "provider_label": result["provider_label"],
        "provider_model": result["model"],
        "used_fallback": False,
        "fallback_reason": "",
        "provider_payload": result.get("raw_payload", {}),
    }
    return report


def save_report(connection, fund_code: str, report: dict, report_date: str | None = None) -> dict:
    report_date = report_date or datetime.now().date().isoformat()
    generated_at = datetime.now().isoformat(timespec="seconds")
    raw_payload = json.dumps(report, ensure_ascii=False)
    connection.execute(
        """
        INSERT INTO daily_reports (
            report_date, fund_code, generated_at, model_name, title, summary,
            reason_analysis, rise_drivers, fall_drivers, watch_points, action_plan, news_highlights, raw_payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_date, fund_code) DO UPDATE SET
            generated_at = excluded.generated_at,
            model_name = excluded.model_name,
            title = excluded.title,
            summary = excluded.summary,
            reason_analysis = excluded.reason_analysis,
            rise_drivers = excluded.rise_drivers,
            fall_drivers = excluded.fall_drivers,
            watch_points = excluded.watch_points,
            action_plan = excluded.action_plan,
            news_highlights = excluded.news_highlights,
            raw_payload = excluded.raw_payload
        """,
        (
            report_date,
            fund_code,
            generated_at,
            report["model_name"],
            report["title"],
            report["summary"],
            json.dumps(report["reason_analysis"], ensure_ascii=False),
            json.dumps(report["rise_drivers"], ensure_ascii=False),
            json.dumps(report["fall_drivers"], ensure_ascii=False),
            json.dumps(report["watch_points"], ensure_ascii=False),
            json.dumps(report["action_plan"], ensure_ascii=False),
            json.dumps(report.get("news_highlights", []), ensure_ascii=False),
            raw_payload,
        ),
    )
    saved = dict(report)
    saved["generated_at"] = generated_at
    saved["report_date"] = report_date
    saved["fund_code"] = fund_code
    return saved


def load_latest_report(connection, fund_code: str) -> dict | None:
    row = connection.execute(
        """
        SELECT report_date, fund_code, generated_at, model_name, title, summary,
               reason_analysis, rise_drivers, fall_drivers, watch_points, action_plan, news_highlights, raw_payload
        FROM daily_reports
        WHERE fund_code = ?
        ORDER BY report_date DESC, generated_at DESC
        LIMIT 1
        """,
        (fund_code,),
    ).fetchone()
    if not row:
        return None
    raw_payload = json.loads(row["raw_payload"]) if row["raw_payload"] else {}
    return {
        "report_date": row["report_date"],
        "fund_code": row["fund_code"],
        "generated_at": row["generated_at"],
        "model_name": row["model_name"],
        "title": row["title"],
        "summary": row["summary"],
        "reason_analysis": json.loads(row["reason_analysis"]),
        "rise_drivers": json.loads(row["rise_drivers"]),
        "fall_drivers": json.loads(row["fall_drivers"]),
        "watch_points": json.loads(row["watch_points"]),
        "action_plan": json.loads(row["action_plan"]),
        "news_highlights": json.loads(row["news_highlights"] or "[]") or raw_payload.get("news_highlights", []),
        "provider_name": raw_payload.get("provider_name"),
        "provider_label": raw_payload.get("provider_label"),
        "provider_model": raw_payload.get("provider_model"),
        "used_fallback": bool(raw_payload.get("used_fallback")),
        "fallback_reason": raw_payload.get("fallback_reason", ""),
    }


def render_markdown_report(reports: list[dict], details_by_code: dict[str, dict]) -> str:
    lines = [f"# 每日基金解读 {datetime.now().date().isoformat()}", ""]
    for report in reports:
        detail = details_by_code[report["fund_code"]]
        lines.append(f"## {detail['fund']['name']} ({report['fund_code']})")
        lines.append("")
        lines.append(report["summary"])
        lines.append("")
        lines.append("### 涨跌原因解读")
        for item in report["reason_analysis"]:
            lines.append(f"- {item}")
        lines.append("")
        if report.get("news_highlights"):
            lines.append("### 相关新闻")
            for item in report["news_highlights"]:
                lines.append(f"- {item['title']} ({item['source']})")
            lines.append("")
        lines.append("### 可能的上涨驱动")
        for item in report["rise_drivers"]:
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### 可能的下跌驱动")
        for item in report["fall_drivers"]:
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### 接下来要看")
        for item in report["watch_points"]:
            lines.append(f"- {item}")
        lines.append("")
        lines.append("### 操作建议")
        for item in report["action_plan"]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_daily_markdown(reports: list[dict], details_by_code: dict[str, dict]) -> str:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{datetime.now().date().isoformat()}.md"
    path.write_text(render_markdown_report(reports, details_by_code), encoding="utf-8")
    return str(path)
