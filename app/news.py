from __future__ import annotations

import html
import re
from urllib import error, request


NEWS_PAGES = [
    ("https://finance.eastmoney.com/yaowen.html", "东方财富焦点"),
    ("https://www.eastmoney.com/", "东方财富首页"),
]

POSITIVE_WORDS = [
    "利好",
    "大涨",
    "走强",
    "上涨",
    "增长",
    "反弹",
    "回暖",
    "修复",
    "翻倍",
    "配置价值",
    "催化",
    "超预期",
    "新高",
]

NEGATIVE_WORDS = [
    "收跌",
    "下跌",
    "走弱",
    "回落",
    "失守",
    "风险",
    "关税",
    "威胁",
    "受阻",
    "蒸发",
    "下滑",
    "大跌",
    "承压",
    "亏",
    "警告",
]


def fetch_text(url: str) -> str:
    req = request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": "https://www.eastmoney.com/",
        },
    )
    try:
        with request.urlopen(req, timeout=12) as response:
            return response.read().decode("utf-8", errors="ignore")
    except error.URLError:
        return ""


def clean_text(value: str) -> str:
    value = re.sub(r"<.*?>", "", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_link(href: str) -> str:
    href = href.strip()
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"https://www.eastmoney.com{href}"
    return href


def extract_articles(page_html: str, source_name: str) -> list[dict]:
    articles = []
    seen = set()
    for href, title in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page_html, re.S):
        title_text = clean_text(title)
        href_text = normalize_link(href)
        if len(title_text) < 8 or len(title_text) > 80:
            continue
        if href_text.startswith("javascript:"):
            continue
        if not any(domain in href_text for domain in ("eastmoney.com/a/", "caifuhao.eastmoney.com/news/")):
            continue
        key = (title_text, href_text)
        if key in seen:
            continue
        seen.add(key)
        articles.append({"title": title_text, "link": href_text, "source": source_name})
    return articles


def keyword_profile(fund: dict) -> dict:
    text = f"{fund['name']} {fund['category']}"
    keywords = []
    fallback = ["A股", "市场", "指数", "资金", "券商", "机构"]
    theme = "broad"

    mappings = [
        (["白酒", "消费"], ["白酒", "消费", "食品饮料", "茅台", "以旧换新"], "consumer"),
        (["医疗", "医药", "创新药"], ["医疗", "医药", "创新药", "制药", "药品"], "healthcare"),
        (["红利", "高股息"], ["红利", "高股息", "分红", "银行"], "dividend"),
        (["沪深300"], ["沪深300", "A股", "大盘", "主力资金", "指数"], "index"),
        (["QDII", "海外", "纳斯达克", "标普", "美股"], ["美股", "美元", "美联储", "纳指", "海外"], "global"),
        (["科技", "芯片", "半导体", "CPO", "算力"], ["科技", "芯片", "半导体", "CPO", "算力"], "tech"),
        (["新能源", "锂电", "光伏"], ["新能源", "锂电", "光伏", "储能"], "new_energy"),
    ]

    for triggers, matched_keywords, matched_theme in mappings:
        if any(item in text for item in triggers):
            keywords.extend(matched_keywords)
            theme = matched_theme

    if not keywords:
        if "混合" in text:
            keywords.extend(["A股", "机构", "市场", "资金", "指数"])
        elif "指数" in text:
            keywords.extend(["指数", "A股", "市场", "主力资金"])
        else:
            keywords.extend(fallback)

    keywords = list(dict.fromkeys(keywords))
    return {"theme": theme, "keywords": keywords}


def article_sentiment(title: str) -> str:
    for word in NEGATIVE_WORDS:
        if word in title:
            return "negative"
    for word in POSITIVE_WORDS:
        if word in title:
            return "positive"
    return "neutral"


def score_article(title: str, keywords: list[str], theme: str) -> int:
    score = 0
    for keyword in keywords:
        if keyword in title:
            score += 4
    if theme == "global" and any(word in title for word in ["美股", "美元", "美联储", "海外", "纳指"]):
        score += 3
    if theme == "index" and any(word in title for word in ["A股", "沪深300", "主力资金", "指数"]):
        score += 3
    if theme == "consumer" and any(word in title for word in ["消费", "白酒", "茅台", "食品饮料"]):
        score += 3
    if theme == "healthcare" and any(word in title for word in ["创新药", "医药", "医疗", "制药"]):
        score += 3
    if theme == "dividend" and any(word in title for word in ["红利", "高股息", "银行", "分红"]):
        score += 3
    if theme == "tech" and any(word in title for word in ["科技", "芯片", "半导体", "CPO", "算力"]):
        score += 3
    return score


def fetch_relevant_news(fund: dict, limit: int = 6) -> list[dict]:
    profile = keyword_profile(fund)
    all_articles = []
    for url, source_name in NEWS_PAGES:
        page_html = fetch_text(url)
        if not page_html:
            continue
        all_articles.extend(extract_articles(page_html, source_name))

    ranked = []
    seen_titles = set()
    for article in all_articles:
        if article["title"] in seen_titles:
            continue
        seen_titles.add(article["title"])
        score = score_article(article["title"], profile["keywords"], profile["theme"])
        if score <= 0:
            continue
        ranked.append(
            {
                **article,
                "score": score,
                "sentiment": article_sentiment(article["title"]),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:limit]
