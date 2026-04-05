from __future__ import annotations

import json

from app.ai_provider import AIProviderError, get_current_ai_config, request_json_completion


GLOSSARY = {
    "净值": "基金净值可以理解成这只基金每一份现在值多少钱。净值上涨，通常代表基金持有资产整体涨了；净值下跌，则代表整体回落了。",
    "波动率": "波动率描述的是这只基金涨跌幅度大不大。波动率越高，说明日常上下波动更明显，拿着的时候心理压力通常也更大。",
    "最大回撤": "最大回撤是这只基金从一段时间内最高点跌到最低点，最深跌了多少。它能帮助你判断，在最难熬的时候可能会亏多深。",
    "定投": "定投就是按固定节奏、固定金额持续买入。它的核心不是追求最低点买入，而是用时间去摊平买入成本。",
    "止盈": "止盈是当收益达到你的目标后，分批卖出一部分，把已经赚到的利润先锁住，而不是一直等到回撤再后悔。",
    "止损": "止损是在风险超出你能承受范围时，主动减少仓位或退出，避免小亏拖成大亏。",
    "指数基金": "指数基金不是靠基金经理主动选股，而是尽量跟着某个指数走，比如沪深300、中证500。它更适合想先建立基础配置的新手。",
    "QDII": "QDII 基金主要投资海外市场，所以除了看海外股市本身，还要看汇率变化。它和只投 A 股的基金风险来源不太一样。",
    "夏普": "夏普比率本质是在看：你承担了每一份波动，换回来的收益值不值。数值越高，通常代表风险收益比更好。",
    "回撤": "回撤就是从阶段高点往下跌了多少。你可以把它理解成“这段时间最疼的时候有多疼”。",
}

BASE_TOPICS = [
    {
        "id": "nav-basics",
        "title": "先搞懂净值、收益率和持仓成本",
        "summary": "这是看懂基金页面的第一步，不懂这三个概念，很容易只盯着涨跌颜色做决定。",
        "why_it_matters": "你需要先知道自己到底赚了多少钱、亏了多少钱，以及这只基金当前在什么位置。",
        "suggested_question": "请像给新手上第一课一样，解释净值、收益率、持仓成本分别是什么意思。",
    },
    {
        "id": "drawdown-volatility",
        "title": "波动率和最大回撤，决定你能不能拿得住",
        "summary": "很多新手只看收益，不看波动和回撤，结果一跌就慌。",
        "why_it_matters": "这两个指标直接影响你的持有体验，也影响仓位应该有多重。",
        "suggested_question": "波动率和最大回撤有什么区别？我应该先看哪个？",
    },
    {
        "id": "index-vs-active",
        "title": "指数基金和主动基金，到底怎么选",
        "summary": "这决定了你后面是更偏“跟市场”，还是更偏“相信基金经理”。",
        "why_it_matters": "不同类型基金的预期收益、风险来源和适合人群都不一样。",
        "suggested_question": "指数基金和主动基金分别适合什么样的新手？",
    },
    {
        "id": "position-sizing",
        "title": "为什么不能一上来就重仓一只基金",
        "summary": "再看好的基金，也会有波动和回撤，仓位控制比选到一只“神基”更重要。",
        "why_it_matters": "很多亏损不是因为基金不好，而是仓位太重，导致情绪先崩掉。",
        "suggested_question": "如果我只有几万块，基金仓位应该怎么分才更稳？",
    },
    {
        "id": "take-profit",
        "title": "什么情况下考虑止盈，而不是一直死拿",
        "summary": "止盈不是看见赚钱就卖，而是看收益、趋势和你的目标是否匹配。",
        "why_it_matters": "学会止盈，能避免“账面赚很多，最后又吐回去”。",
        "suggested_question": "基金盈利以后，什么情况适合分批止盈？",
    },
    {
        "id": "dca-basics",
        "title": "定投为什么适合新手，但也不是万能",
        "summary": "定投能帮助你养成纪律，但它不等于闭眼买，也不等于永远不会亏。",
        "why_it_matters": "搞懂定投边界，才能避免在错误节奏里越投越难受。",
        "suggested_question": "定投适合什么行情？什么时候不适合盲目定投？",
    },
]


def _selected_fund_snapshot(fund_detail: dict | None) -> dict | None:
    if not fund_detail:
        return None
    fund = fund_detail["fund"]
    analysis = fund_detail["analysis"]
    latest_report = fund_detail.get("latest_report") or {}
    return {
        "code": fund["code"],
        "name": fund["name"],
        "category": fund["category"],
        "manager": fund["manager"],
        "action": analysis["action"],
        "score": analysis["score"],
        "reasons": analysis["reasons"],
        "metrics": analysis["metrics"],
        "holding": analysis.get("holding"),
        "report_summary": latest_report.get("summary"),
    }


def recommend_learning_topics(fund_detail: dict | None = None) -> list[dict]:
    topics: list[dict] = []
    fund = fund_detail["fund"] if fund_detail else {}
    analysis = fund_detail["analysis"] if fund_detail else {}
    category = fund.get("category", "")
    metrics = analysis.get("metrics", {})

    if "QDII" in category:
        topics.append(
            {
                "id": "qdii-currency",
                "title": "QDII 为什么还要看汇率",
                "summary": "QDII 基金不只受海外市场影响，人民币汇率变化也会影响净值表现。",
                "why_it_matters": "这样你就不会只看美股涨跌，却忽略汇率带来的额外波动。",
                "suggested_question": "请用新手能听懂的话解释，QDII 基金为什么还会受汇率影响？",
            }
        )
    if "指数" in category:
        topics.append(
            {
                "id": "index-tracking",
                "title": "指数基金为什么也会有差别",
                "summary": "同样叫指数基金，也会因为跟踪指数、费率和跟踪误差不同而表现不同。",
                "why_it_matters": "你会更明白为什么不是随便买一只指数基金都一样。",
                "suggested_question": "指数基金都在跟指数，为什么收益还是会不一样？",
            }
        )
    if metrics.get("volatility", 0) and metrics["volatility"] >= 20:
        topics.append(
            {
                "id": "high-volatility",
                "title": "高波动基金为什么更考验仓位",
                "summary": "涨得快和跌得快往往是一起出现的，不能只看上涨速度。",
                "why_it_matters": "你会更容易理解为什么系统会提示“分批”和“不要重仓”。",
                "suggested_question": "为什么波动率高的基金，不适合我一下子买很多？",
            }
        )
    if metrics.get("max_drawdown", 0) and metrics["max_drawdown"] >= 20:
        topics.append(
            {
                "id": "drawdown-survival",
                "title": "最大回撤大，意味着你最难熬的时候会多难",
                "summary": "回撤不是历史故事，它是在提醒你这只基金未来也可能再次大幅回落。",
                "why_it_matters": "看懂这点，你会更懂为什么选基金不能只看过去收益。",
                "suggested_question": "最大回撤很大，说明这只基金未来风险也会大吗？",
            }
        )

    deduped = []
    seen = set()
    for topic in topics + BASE_TOPICS:
        if topic["id"] in seen:
            continue
        seen.add(topic["id"])
        deduped.append(topic)
    return deduped[:6]


def _fallback_teaching_answer(question: str, fund_detail: dict | None = None, mode: str = "qa") -> dict:
    lowered = question.replace("？", "").replace("?", "").strip()
    matched = None
    for term, explanation in GLOSSARY.items():
        if term in lowered:
            matched = (term, explanation)
            break

    selected = _selected_fund_snapshot(fund_detail)
    if matched:
        term, explanation = matched
        answer = explanation
        key_points = [
            f"先记一句：{term} 不是越大越好，要结合收益和你的承受能力一起看。",
            "如果一个指标你看不懂，先问它回答的是“赚多少”、“跌多深”还是“波动大不大”。",
        ]
        if selected:
            key_points.append(
                f"放到你当前这只 {selected['name']} 上，它现在的策略建议是“{selected['action']}”，所以这个指标会直接影响你的仓位判断。"
            )
        return {
            "title": f"新手解释：{term}",
            "answer": answer,
            "key_points": key_points[:3],
            "follow_ups": [f"{term} 和收益率有什么关系？", f"怎么用 {term} 判断一只基金适不适合我？"],
            "related_topics": ["基金指标入门", "仓位管理"],
            "provider_name": "fallback",
            "provider_label": "规则讲解",
            "provider_model": "heuristic-tutor",
            "used_fallback": True,
            "fallback_reason": "当前用规则讲解兜底。",
        }

    if selected:
        answer = (
            f"如果先只看你当前选中的 {selected['name']}，系统现在给它的建议是“{selected['action']}”。"
            f"最主要的原因是：{selected['reasons'][0] if selected['reasons'] else '当前趋势和风险信号综合后偏中性'}。"
            "你可以把它理解成：先看趋势有没有走坏，再看仓位重不重，最后才决定补还是减。"
        )
        key_points = [
            f"当前策略分是 {selected['score']}，说明它不是单看涨跌，而是把趋势、回撤、波动和持仓一起算进去了。",
            "新手最容易忽略的是仓位控制，比起问“还能不能涨”，更该先问“跌了我能不能拿得住”。",
            "如果你愿意，可以继续追问某个术语，我会尽量用更白话的方式讲。",
        ]
    else:
        answer = "基金学习最好的顺序是：先看懂净值和收益，再看波动和回撤，最后再学基金类型和仓位管理。"
        key_points = [
            "先不要急着追求选到最强基金，先建立一套看懂基金页面的基本语言。",
            "新手最需要先学的是：净值、收益率、波动率、最大回撤、定投和止盈。",
            "只要你把这些概念先搞懂，后面看策略解读就会轻松很多。",
        ]

    title = "基金新手问答" if mode == "qa" else "基金学习讲解"
    return {
        "title": title,
        "answer": answer,
        "key_points": key_points,
        "follow_ups": ["什么是最大回撤？", "为什么不能重仓一只基金？", "定投什么时候更合适？"],
        "related_topics": [topic["title"] for topic in recommend_learning_topics(fund_detail)[:3]],
        "provider_name": "fallback",
        "provider_label": "规则讲解",
        "provider_model": "heuristic-tutor",
        "used_fallback": True,
        "fallback_reason": "当前用规则讲解兜底。",
    }


def _assistant_prompt(question: str, fund_detail: dict | None, mode: str) -> str:
    selected = _selected_fund_snapshot(fund_detail)
    topics = recommend_learning_topics(fund_detail)
    role_text = "基金学习教练" if mode == "learning" else "基金问答助手"
    return (
        f"你是一名非常适合新手的中文 {role_text}。"
        "请用通俗、准确、不吓人的表达来回答。"
        "如果用户问题里涉及术语，要先解释术语，再联系当前基金。"
        "如果是基金通识问题，要告诉用户为什么这个知识点重要。"
        "不要使用过度专业术语，不要假装知道上下文之外的市场消息。"
        "输出严格 JSON，字段必须包含：title, answer, key_points, follow_ups, related_topics。"
        "其中 key_points, follow_ups, related_topics 必须是字符串数组。"
        f"\n\n用户问题：{question}"
        f"\n模式：{mode}"
        f"\n当前基金上下文：{json.dumps(selected, ensure_ascii=False)}"
        f"\n推荐学习主题：{json.dumps(topics, ensure_ascii=False)}"
    )


def ask_ai_assistant(question: str, fund_detail: dict | None = None, mode: str = "qa", connection=None) -> dict:
    cleaned = question.strip()
    if not cleaned:
        raise ValueError("请输入你想问的问题。")

    prompt = _assistant_prompt(cleaned, fund_detail, mode)
    try:
        result = request_json_completion(prompt, connection)
        parsed = result["parsed"]
        return {
            "title": parsed.get("title") or ("基金学习讲解" if mode == "learning" else "基金新手问答"),
            "answer": parsed.get("answer") or _fallback_teaching_answer(cleaned, fund_detail, mode)["answer"],
            "key_points": [str(item) for item in (parsed.get("key_points") or []) if str(item).strip()][:4],
            "follow_ups": [str(item) for item in (parsed.get("follow_ups") or []) if str(item).strip()][:4],
            "related_topics": [str(item) for item in (parsed.get("related_topics") or []) if str(item).strip()][:4],
            "provider_name": result["provider"],
            "provider_label": result["provider_label"],
            "provider_model": result["model"],
            "used_fallback": False,
            "fallback_reason": "",
        }
    except AIProviderError as exc:
        fallback = _fallback_teaching_answer(cleaned, fund_detail, mode)
        config = get_current_ai_config(connection)
        fallback["provider_name"] = config["provider"]
        fallback["provider_label"] = config["provider_label"]
        fallback["provider_model"] = config["model"]
        fallback["fallback_reason"] = str(exc)
        return fallback
