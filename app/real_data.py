from __future__ import annotations

import html
import json
import re
from datetime import datetime
from functools import lru_cache
from urllib import error, request


FUND_CODE_URL = "https://fund.eastmoney.com/js/fundcode_search.js"
HISTORY_URL = (
    "https://fund.eastmoney.com/f10/F10DataApi.aspx?type=lsjz&code={code}&page={page}&per=49"
)


class RealDataError(RuntimeError):
    pass


def fetch_text(url: str) -> str:
    req = request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": "https://fund.eastmoney.com/",
        },
    )
    try:
        with request.urlopen(req, timeout=12) as response:
            return response.read().decode("utf-8", errors="ignore")
    except error.URLError as exc:
        raise RealDataError(f"拉取真实基金数据失败：{exc}") from exc


@lru_cache(maxsize=1)
def fetch_fund_catalog() -> dict[str, dict]:
    raw = fetch_text(FUND_CODE_URL).lstrip("\ufeff")
    match = re.search(r"var\s+r\s*=\s*(\[.*\]);?\s*$", raw, re.S)
    if not match:
        raise RealDataError("无法解析基金代码列表。")
    items = json.loads(match.group(1))
    catalog = {}
    for item in items:
        if len(item) < 4:
            continue
        code, short_name, name, category = item[:4]
        catalog[code] = {
            "code": code,
            "short_name": short_name,
            "name": name,
            "category": category,
        }
    return catalog


def clean_cell(raw: str) -> str:
    text = re.sub(r"<.*?>", "", raw)
    text = html.unescape(text)
    return text.strip().replace("--", "")


def parse_history_page(payload: str) -> tuple[list[dict], int]:
    match = re.search(
        r'content:"(?P<content>.*)",records:(?P<records>\d+),pages:(?P<pages>\d+),curpage:(?P<curpage>\d+)',
        payload,
        re.S,
    )
    if not match:
        raise RealDataError("无法解析历史净值响应。")

    content = match.group("content")
    pages = int(match.group("pages"))
    body_match = re.search(r"<tbody>(?P<body>.*)</tbody>", content, re.S)
    if not body_match:
        return [], pages

    rows: list[dict] = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", body_match.group("body"), re.S):
        cells = [clean_cell(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)]
        if len(cells) < 4 or not cells[0] or not cells[1]:
            continue
        growth = cells[3].replace("%", "")
        rows.append(
            {
                "nav_date": cells[0],
                "unit_nav": float(cells[1]),
                "daily_return": float(growth) / 100 if growth else None,
            }
        )
    return rows, pages


def infer_risk_level(category: str) -> str:
    if "货币" in category or "债券" in category:
        return "低"
    if "指数" in category:
        return "中"
    if "混合" in category:
        return "中高"
    if "股票" in category or "QDII" in category:
        return "高"
    return "中"


def fetch_fund_history(code: str) -> list[dict]:
    page = 1
    pages = 1
    rows: list[dict] = []
    while page <= pages:
        payload = fetch_text(HISTORY_URL.format(code=code, page=page))
        page_rows, pages = parse_history_page(payload)
        rows.extend(page_rows)
        page += 1

    if not rows:
        raise RealDataError(f"基金 {code} 没有返回历史净值数据。")

    rows.sort(key=lambda item: item["nav_date"])
    previous_nav = None
    for row in rows:
        if row["daily_return"] is None:
            row["daily_return"] = 0.0 if previous_nav is None else row["unit_nav"] / previous_nav - 1
        previous_nav = row["unit_nav"]
    return rows


def fetch_fund_snapshot(code: str, name_hint: str | None = None) -> dict:
    catalog = fetch_fund_catalog()
    item = catalog.get(code)
    if not item:
        item = {
            "code": code,
            "name": name_hint or f"基金 {code}",
            "category": "未分类",
        }

    history = fetch_fund_history(code)
    return {
        "fund": {
            "code": code,
            "name": item["name"],
            "category": item["category"],
            "manager": "东财公开数据",
            "risk_level": infer_risk_level(item["category"]),
            "description": "真实净值来自天天基金公开页面接口，适合做走势分析与策略辅助，不构成投资建议。",
            "data_source": "real",
            "last_synced_at": datetime.now().isoformat(timespec="seconds"),
        },
        "history": history,
    }


def sync_fund_data(connection, code: str, name_hint: str | None = None) -> dict:
    snapshot = fetch_fund_snapshot(code, name_hint=name_hint)
    fund = snapshot["fund"]
    history = snapshot["history"]

    connection.execute(
        """
        INSERT INTO funds (code, name, category, manager, risk_level, description, data_source, last_synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            name = excluded.name,
            category = excluded.category,
            manager = excluded.manager,
            risk_level = excluded.risk_level,
            description = excluded.description,
            data_source = excluded.data_source,
            last_synced_at = excluded.last_synced_at
        """,
        (
            fund["code"],
            fund["name"],
            fund["category"],
            fund["manager"],
            fund["risk_level"],
            fund["description"],
            fund["data_source"],
            fund["last_synced_at"],
        ),
    )
    connection.execute("DELETE FROM fund_nav_history WHERE fund_code = ?", (code,))
    connection.executemany(
        """
        INSERT INTO fund_nav_history (fund_code, nav_date, unit_nav, daily_return)
        VALUES (?, ?, ?, ?)
        """,
        [(code, row["nav_date"], row["unit_nav"], row["daily_return"]) for row in history],
    )
    return {
        "code": code,
        "name": fund["name"],
        "history_count": len(history),
        "last_synced_at": fund["last_synced_at"],
        "data_source": fund["data_source"],
    }
