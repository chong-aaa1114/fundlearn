from __future__ import annotations

from app.db import get_connection, init_db
from app.reports import build_report_context, generate_ai_report, save_report, write_daily_markdown
from server import build_fund_detail_payload, refresh_codes


def run() -> None:
    init_db()
    with get_connection() as connection:
        held_codes = [row["fund_code"] for row in connection.execute("SELECT fund_code FROM positions ORDER BY fund_code")]
        if not held_codes:
            print("No held funds found.")
            return

        refresh_codes(connection, held_codes)
        reports = []
        details_by_code = {}
        for code in held_codes:
            detail = build_fund_detail_payload(connection, code)
            if not detail:
                continue
            details_by_code[code] = detail
            report = generate_ai_report(build_report_context(detail), connection)
            reports.append(save_report(connection, code, report))
        connection.commit()

    if reports:
        path = write_daily_markdown(reports, details_by_code)
        print(f"Generated {len(reports)} reports -> {path}")


if __name__ == "__main__":
    run()
