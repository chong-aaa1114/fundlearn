from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "data" / "fund_platform.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS funds (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    manager TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    description TEXT NOT NULL,
    data_source TEXT NOT NULL DEFAULT 'real',
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS fund_nav_history (
    fund_code TEXT NOT NULL,
    nav_date TEXT NOT NULL,
    unit_nav REAL NOT NULL,
    daily_return REAL NOT NULL,
    PRIMARY KEY (fund_code, nav_date),
    FOREIGN KEY (fund_code) REFERENCES funds(code)
);

CREATE TABLE IF NOT EXISTS positions (
    fund_code TEXT PRIMARY KEY,
    shares REAL NOT NULL,
    cost_basis REAL NOT NULL,
    buy_date TEXT NOT NULL,
    FOREIGN KEY (fund_code) REFERENCES funds(code)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_code TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    score REAL NOT NULL,
    action TEXT NOT NULL,
    reasons TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_code TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    threshold REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS daily_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    model_name TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    reason_analysis TEXT NOT NULL,
    rise_drivers TEXT NOT NULL,
    fall_drivers TEXT NOT NULL,
    watch_points TEXT NOT NULL,
    action_plan TEXT NOT NULL,
    news_highlights TEXT NOT NULL DEFAULT '[]',
    raw_payload TEXT NOT NULL,
    UNIQUE(report_date, fund_code),
    FOREIGN KEY (fund_code) REFERENCES funds(code)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def purge_mock_data(connection: sqlite3.Connection) -> None:
    mock_codes = [
        row["code"]
        for row in connection.execute(
            """
            SELECT code
            FROM funds
            WHERE data_source IS NULL OR lower(data_source) <> 'real'
            """
        )
    ]
    if not mock_codes:
        return

    placeholders = ",".join("?" for _ in mock_codes)
    connection.execute(f"DELETE FROM positions WHERE fund_code IN ({placeholders})", mock_codes)
    connection.execute(f"DELETE FROM fund_nav_history WHERE fund_code IN ({placeholders})", mock_codes)
    connection.execute(f"DELETE FROM signals WHERE fund_code IN ({placeholders})", mock_codes)
    connection.execute(f"DELETE FROM alerts WHERE fund_code IN ({placeholders})", mock_codes)
    connection.execute(f"DELETE FROM funds WHERE code IN ({placeholders})", mock_codes)


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as connection:
        connection.executescript(SCHEMA)
        ensure_column(connection, "funds", "data_source", "TEXT NOT NULL DEFAULT 'real'")
        ensure_column(connection, "funds", "last_synced_at", "TEXT")
        ensure_column(connection, "daily_reports", "news_highlights", "TEXT NOT NULL DEFAULT '[]'")
        purge_mock_data(connection)
        connection.commit()


def get_setting(connection: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = connection.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    return str(row["value"])


def set_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
