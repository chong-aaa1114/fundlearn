from __future__ import annotations

import csv
import json
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.assistant import ask_ai_assistant, recommend_learning_topics
from app.ai_provider import AIProviderError, get_current_ai_config, save_ai_config, test_ai_provider
from app.analytics import category_averages, compute_base_metrics, recommendation_from_metrics
from app.config import load_env
from app.db import ROOT_DIR, get_connection, init_db
from app.real_data import RealDataError, sync_fund_data
from app.reports import build_report_context, generate_ai_report, load_latest_report, save_report


load_env()

STATIC_DIR = ROOT_DIR / "static"


def parse_query(path: str) -> dict[str, str]:
    parsed = urlparse(path)
    query = parse_qs(parsed.query)
    return {key: values[0] for key, values in query.items() if values}


def normalize_position_rows(content: str) -> list[dict]:
    payload = content.strip()
    if not payload:
        raise ValueError("导入内容不能为空。")

    if payload.startswith("["):
        rows = json.loads(payload)
        if not isinstance(rows, list):
            raise ValueError("JSON 导入内容必须是数组。")
        return [normalize_row(row) for row in rows]

    lines = [line for line in payload.splitlines() if line.strip()]
    if not lines:
        raise ValueError("导入内容不能为空。")

    if "," in lines[0] and not any(keyword in lines[0] for keyword in ("基金代码", "fund_code", "code")):
        rows = []
        for line in lines:
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 5:
                raise ValueError("无表头 CSV 需要每行包含 5 列：基金代码,基金名称,持有份额,持仓成本,买入日期")
            rows.append(
                normalize_row(
                    {
                        "fund_code": parts[0],
                        "fund_name": parts[1],
                        "shares": parts[2],
                        "cost_basis": parts[3],
                        "buy_date": parts[4],
                    }
                )
            )
        return rows

    reader = csv.DictReader(StringIO(payload))
    return [normalize_row(row) for row in reader]


def pick_value(row: dict, keys: list[str], default: str = "") -> str:
    for key in keys:
        if key in row and str(row[key]).strip():
            return str(row[key]).strip()
    return default


def normalize_row(row: dict) -> dict:
    fund_code = pick_value(row, ["基金代码", "fund_code", "code"])
    fund_name = pick_value(row, ["基金名称", "fund_name", "name"], default=f"待补充基金 {fund_code}")
    shares = float(pick_value(row, ["持有份额", "shares", "amount", "units"]))
    cost_basis = float(pick_value(row, ["持仓成本", "成本价", "cost_basis", "avg_cost"]))
    buy_date = pick_value(row, ["买入日期", "buy_date", "date"])
    if not fund_code or not buy_date:
        raise ValueError("每一行都需要包含基金代码和买入日期。")
    datetime.strptime(buy_date, "%Y-%m-%d")
    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "shares": shares,
        "cost_basis": cost_basis,
        "buy_date": buy_date,
    }


def parse_return_rate(value) -> float:
    text = str(value).strip().replace("%", "")
    rate = float(text)
    if rate <= -100:
        raise ValueError("持有收益率必须大于 -100%。")
    return rate / 100


def normalize_quick_position(payload: dict) -> dict:
    fund_code = pick_value(payload, ["基金代码", "fund_code", "code"])
    fund_name = pick_value(payload, ["基金名称", "fund_name", "name"])
    holding_amount = float(pick_value(payload, ["持有金额", "holding_amount", "amount", "current_value"]))
    holding_return_rate = parse_return_rate(
        pick_value(payload, ["持有收益", "holding_return_rate", "return_rate", "pnl_rate"])
    )
    buy_date = pick_value(payload, ["买入日期", "buy_date", "date"], default=datetime.now().date().isoformat())

    if not fund_code:
        raise ValueError("请填写基金代码。")
    if holding_amount <= 0:
        raise ValueError("持有金额必须大于 0。")
    datetime.strptime(buy_date, "%Y-%m-%d")
    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "holding_amount": holding_amount,
        "holding_return_rate": holding_return_rate,
        "buy_date": buy_date,
    }


def load_funds_and_histories(connection):
    funds = [dict(row) for row in connection.execute("SELECT * FROM funds ORDER BY code")]
    histories: dict[str, list[dict]] = {fund["code"]: [] for fund in funds}
    for row in connection.execute(
        "SELECT fund_code, nav_date, unit_nav, daily_return FROM fund_nav_history ORDER BY nav_date"
    ):
        histories.setdefault(row["fund_code"], []).append(dict(row))
    return funds, histories


def compute_analytics(connection):
    funds, histories = load_funds_and_histories(connection)
    positions = {
        row["fund_code"]: dict(row)
        for row in connection.execute("SELECT fund_code, shares, cost_basis, buy_date FROM positions")
    }
    funds = [fund for fund in funds if histories.get(fund["code"])]
    metrics_by_code = {fund["code"]: compute_base_metrics(histories[fund["code"]]) for fund in funds}
    category_avg = category_averages(funds, metrics_by_code)

    analyses: dict[str, dict] = {}
    for fund in funds:
        analyses[fund["code"]] = recommendation_from_metrics(
            fund,
            metrics_by_code[fund["code"]],
            category_avg.get(fund["category"]),
            positions.get(fund["code"]),
        )
    return funds, histories, positions, analyses


def refresh_codes(connection, codes: list[str], name_hints: dict[str, str] | None = None) -> dict:
    refreshed = []
    errors = []
    for code in codes:
        try:
            refreshed.append(sync_fund_data(connection, code, name_hint=(name_hints or {}).get(code)))
        except RealDataError as exc:
            errors.append({"code": code, "error": str(exc)})
    connection.commit()
    return {"refreshed": refreshed, "errors": errors}


def latest_nav_for_code(connection, code: str) -> float:
    row = connection.execute(
        """
        SELECT unit_nav
        FROM fund_nav_history
        WHERE fund_code = ?
        ORDER BY nav_date DESC
        LIMIT 1
        """,
        (code,),
    ).fetchone()
    if not row:
        raise ValueError(f"基金 {code} 还没有可用净值。")
    return float(row["unit_nav"])


def build_dashboard_payload(connection) -> dict:
    funds, histories, positions, analyses = compute_analytics(connection)

    holdings = []
    total_cost = 0.0
    total_value = 0.0

    for code, position in positions.items():
        analysis = analyses[code]
        current_value = position["shares"] * analysis["metrics"]["latest_nav"]
        cost_value = position["shares"] * position["cost_basis"]
        pnl_value = current_value - cost_value
        total_cost += cost_value
        total_value += current_value
        holdings.append(
            {
                "fund_code": code,
                "fund_name": analysis["fund"]["name"],
                "category": analysis["fund"]["category"],
                "data_source": analysis["fund"].get("data_source", "real"),
                "last_synced_at": analysis["fund"].get("last_synced_at"),
                "buy_date": position["buy_date"],
                "shares": round(position["shares"], 2),
                "cost_basis": round(position["cost_basis"], 4),
                "current_nav": analysis["metrics"]["latest_nav"],
                "current_value": round(current_value, 2),
                "cost_value": round(cost_value, 2),
                "pnl_value": round(pnl_value, 2),
                "pnl_ratio": round(pnl_value / cost_value * 100, 2) if cost_value else 0.0,
                "analysis": analysis,
            }
        )

    holdings.sort(key=lambda item: item["analysis"]["score"], reverse=True)
    all_funds = [
        {
            "fund_code": fund["code"],
            "fund_name": fund["name"],
            "category": fund["category"],
            "manager": fund["manager"],
            "data_source": fund.get("data_source", "real"),
            "last_synced_at": fund.get("last_synced_at"),
            "analysis": analyses[fund["code"]],
            "is_held": fund["code"] in positions,
        }
        for fund in funds
    ]
    all_funds.sort(key=lambda item: item["analysis"]["score"], reverse=True)

    top_signals = []
    for holding in holdings[:3]:
        top_signals.append(
            {
                "fund_code": holding["fund_code"],
                "fund_name": holding["fund_name"],
                "action": holding["analysis"]["action"],
                "score": holding["analysis"]["score"],
                "tag": holding["analysis"]["tag"],
                "reasons": holding["analysis"]["reasons"],
                "held": True,
            }
        )

    for fund in all_funds:
        if len(top_signals) >= 6:
            break
        if fund["is_held"]:
            continue
        top_signals.append(
            {
                "fund_code": fund["fund_code"],
                "fund_name": fund["fund_name"],
                "action": fund["analysis"]["action"],
                "score": fund["analysis"]["score"],
                "tag": fund["analysis"]["tag"],
                "reasons": fund["analysis"]["reasons"],
                "held": False,
            }
        )

    summary = {
        "fund_count": len(holdings),
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "pnl_value": round(total_value - total_cost, 2),
        "pnl_ratio": round((total_value - total_cost) / total_cost * 100, 2) if total_cost else 0.0,
    }
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "positions": holdings,
        "top_signals": top_signals,
        "watchlist": [fund for fund in all_funds if not fund["is_held"]][:6],
        "funds": all_funds,
    }


def build_fund_detail_payload(connection, code: str) -> dict | None:
    funds, histories, positions, analyses = compute_analytics(connection)
    fund_map = {fund["code"]: fund for fund in funds}
    if code not in fund_map:
        return None
    return {
        "fund": fund_map[code],
        "analysis": analyses[code],
        "history": histories[code][-180:],
        "position": positions.get(code),
        "latest_report": load_latest_report(connection, code),
    }


def import_positions(connection, rows: list[dict], replace: bool = True) -> dict:
    refreshed = []
    refresh_errors = []
    imported_count = 0
    if replace:
        connection.execute("DELETE FROM positions")

    for row in rows:
        fund_name = row.get("fund_name", "")
        try:
            refreshed.append(sync_fund_data(connection, row["fund_code"], name_hint=fund_name))
        except RealDataError as exc:
            refresh_errors.append({"code": row["fund_code"], "error": str(exc)})
            continue

        connection.execute(
            """
            INSERT INTO positions (fund_code, shares, cost_basis, buy_date)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(fund_code) DO UPDATE SET
                shares = excluded.shares,
                cost_basis = excluded.cost_basis,
                buy_date = excluded.buy_date
            """,
            (row["fund_code"], row["shares"], row["cost_basis"], row["buy_date"]),
        )
        imported_count += 1

    connection.commit()
    return {
        "imported_count": imported_count,
        "requested_count": len(rows),
        "refreshed_count": len(refreshed),
        "refresh_errors": refresh_errors,
    }


def import_quick_position(connection, payload: dict, replace: bool = True) -> dict:
    row = normalize_quick_position(payload)
    # 移除这里的全局删除，根据业务逻辑：
    # 如果是针对单只基金的“更新”或“加入”，不应该清空其他持仓
    # connection.execute("DELETE FROM positions")

    try:
        refresh_result = sync_fund_data(connection, row["fund_code"], name_hint=row["fund_name"] or None)
    except RealDataError as exc:
        connection.rollback()
        raise ValueError(str(exc)) from exc

    latest_nav = latest_nav_for_code(connection, row["fund_code"])
    current_value = row["holding_amount"]
    current_return_rate = row["holding_return_rate"]
    cost_value = current_value / (1 + current_return_rate)
    shares = current_value / latest_nav
    cost_basis = cost_value / shares

    connection.execute(
        """
        INSERT INTO positions (fund_code, shares, cost_basis, buy_date)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(fund_code) DO UPDATE SET
            shares = excluded.shares,
            cost_basis = excluded.cost_basis,
            buy_date = excluded.buy_date
        """,
        (row["fund_code"], shares, cost_basis, row["buy_date"]),
    )
    connection.commit()
    return {
        "fund_code": row["fund_code"],
        "fund_name": refresh_result["name"],
        "holding_amount": round(current_value, 2),
        "holding_return_rate": round(current_return_rate * 100, 2),
        "estimated_shares": round(shares, 2),
        "estimated_cost_value": round(cost_value, 2),
        "latest_nav": round(latest_nav, 4),
    }


def generate_reports(connection, fund_code: str | None = None, refresh_first: bool = True) -> dict:
    if fund_code:
        codes = [fund_code]
    else:
        codes = [row["fund_code"] for row in connection.execute("SELECT fund_code FROM positions ORDER BY fund_code")]
    if not codes:
        return {"reports": [], "errors": []}

    refresh_errors = []
    if refresh_first:
        refresh_result = refresh_codes(connection, codes)
        refresh_errors = refresh_result["errors"]

    reports = []
    for code in codes:
        detail = build_fund_detail_payload(connection, code)
        if not detail:
            continue
        report = generate_ai_report(build_report_context(detail), connection)
        reports.append(save_report(connection, code, report))
    connection.commit()
    return {"reports": reports, "errors": refresh_errors}


class FundPlatformHandler(BaseHTTPRequestHandler):
    server_version = "FundPlatform/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.serve_static("index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            filename = path.removeprefix("/static/")
            self.serve_static(filename)
            return
        if path == "/api/dashboard":
            with get_connection() as connection:
                self.send_json(build_dashboard_payload(connection))
            return
        if path == "/api/funds":
            with get_connection() as connection:
                payload = build_dashboard_payload(connection)["funds"]
                self.send_json(payload)
            return
        if path == "/api/reports":
            code = parse_query(self.path).get("fund_code")
            if not code:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "需要提供 fund_code。")
                return
            with get_connection() as connection:
                report = load_latest_report(connection, code)
            if not report:
                self.send_error_json(HTTPStatus.NOT_FOUND, "该基金还没有解读报告。")
                return
            self.send_json(report)
            return
        if path == "/api/data-source":
            self.send_json(
                {
                    "provider": "eastmoney-public",
                    "provider_name": "天天基金公开页面接口",
                    "notes": "系统只保留真实基金数据；如果目标基金拉取失败，不会写入任何模拟或占位净值。",
                    "fund_code_url": "https://fund.eastmoney.com/js/fundcode_search.js",
                    "history_url_pattern": "https://fund.eastmoney.com/f10/F10DataApi.aspx?type=lsjz&code=<code>&page=1&per=49",
                }
            )
            return
        if path == "/api/ai/config":
            with get_connection() as connection:
                self.send_json({"ok": True, **get_current_ai_config(connection)})
            return
        if path == "/api/learning/topics":
            code = parse_query(self.path).get("fund_code")
            with get_connection() as connection:
                detail = build_fund_detail_payload(connection, code) if code else None
            self.send_json({"ok": True, "topics": recommend_learning_topics(detail)})
            return
        if path.startswith("/api/funds/"):
            code = unquote(path.split("/")[-1])
            with get_connection() as connection:
                payload = build_fund_detail_payload(connection, code)
                if not payload:
                    self.send_error_json(HTTPStatus.NOT_FOUND, "未找到对应基金。")
                    return
                self.send_json(payload)
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "请求的资源不存在。")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        body = self.read_json()

        if path == "/api/positions/import":
            content = body.get("content", "")
            replace = bool(body.get("replace", True))
            try:
                rows = normalize_position_rows(content)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            with get_connection() as connection:
                result = import_positions(connection, rows, replace=replace)
            self.send_json({"ok": True, **result})
            return
        if path == "/api/positions/quick-import":
            replace = bool(body.get("replace", True))
            try:
                with get_connection() as connection:
                    result = import_quick_position(connection, body, replace=replace)
            except ValueError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json({"ok": True, **result})
            return

        if path == "/api/funds/refresh":
            codes = body.get("codes")
            held_only = bool(body.get("held_only"))
            with get_connection() as connection:
                if held_only:
                    codes = [row["fund_code"] for row in connection.execute("SELECT fund_code FROM positions ORDER BY fund_code")]
                elif not codes:
                    codes = [row["code"] for row in connection.execute("SELECT code FROM funds ORDER BY code")]
                result = refresh_codes(connection, list(dict.fromkeys(codes)))
            self.send_json({"ok": True, **result})
            return
        if path == "/api/reports/generate":
            fund_code = body.get("fund_code")
            refresh_first = bool(body.get("refresh_first", True))
            with get_connection() as connection:
                result = generate_reports(connection, fund_code=fund_code, refresh_first=refresh_first)
            self.send_json({"ok": True, **result})
            return
        if path == "/api/ai/config":
            provider = body.get("provider")
            model = body.get("model")
            if not provider:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "需要提供 provider。")
                return
            with get_connection() as connection:
                result = save_ai_config(connection, provider, model)
                connection.commit()
            self.send_json({"ok": True, **result})
            return
        if path == "/api/ai/test":
            try:
                with get_connection() as connection:
                    result = test_ai_provider(connection)
            except AIProviderError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json({"ok": True, **result})
            return
        if path == "/api/assistant/ask":
            question = str(body.get("question", "")).strip()
            fund_code = body.get("fund_code")
            mode = str(body.get("mode", "qa")).strip() or "qa"
            try:
                with get_connection() as connection:
                    detail = build_fund_detail_payload(connection, fund_code) if fund_code else None
                    result = ask_ai_assistant(question, detail, mode=mode, connection=connection)
            except ValueError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json({"ok": True, **result})
            return

        if path == "/api/positions/delete":
            fund_code = body.get("fund_code", "").strip()
            if not fund_code:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "需要提供 fund_code。")
                return
            with get_connection() as connection:
                connection.execute("DELETE FROM positions WHERE fund_code = ?", (fund_code,))
                connection.commit()
            self.send_json({"ok": True, "fund_code": fund_code})
            return
        if path == "/api/positions/reset":
            with get_connection() as connection:
                connection.execute("DELETE FROM positions")
                connection.commit()
            self.send_json({"ok": True})
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "请求的接口不存在。")

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def serve_static(self, filename: str, content_type: str | None = None) -> None:
        file_path = (STATIC_DIR / filename).resolve()
        if STATIC_DIR not in file_path.parents and file_path != STATIC_DIR:
            self.send_error_json(HTTPStatus.FORBIDDEN, "禁止访问该文件。")
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "静态文件不存在。")
            return

        mapping = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }
        mime = content_type or mapping.get(file_path.suffix, "application/octet-stream")
        content = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"ok": False, "error": message}, status=status)

    def log_message(self, format: str, *args) -> None:
        return


def run() -> None:
    init_db()
    host = "127.0.0.1"
    port = 8000
    server = ThreadingHTTPServer((host, port), FundPlatformHandler)
    print(f"Fund platform running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
