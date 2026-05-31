"""
core/exporter.py — Export SQLite tables to CSV files.
"""

import sqlite3
from datetime import date
from pathlib import Path

from loguru import logger

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

from config import DB_PATH, EXPORT_DIR

ALLOWED_TABLES = frozenset(
    {
        "matches",
        "map_results",
        "player_match_stats",
        "players",
        "player_career_stats",
        "player_event_stats",
        "player_map_stats",
        "teams",
        "team_map_stats",
        "events",
        "team_stats",
        "event_teams",
        "world_rankings",
        "player_rankings",
        "roster_history",
        "news",
    }
)


class Exporter:
    """
    Exports SQLite tables to timestamped CSV files in exports/.
    Uses pandas read_sql for efficient bulk reading.
    """

    def __init__(self, db_path: str = DB_PATH, export_dir: str = EXPORT_DIR) -> None:
        self.db_path = db_path
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def _get_table_names(self) -> list[str]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            return [row[0] for row in cursor.fetchall() if row[0] in ALLOWED_TABLES]

    def export_table(self, table_name: str) -> dict:
        """
        Export a single table to CSV. Returns summary dict.
        """
        if table_name not in ALLOWED_TABLES:
            raise ValueError(
                f"Table '{table_name}' is not an allowed export target. "
                f"Allowed tables: {sorted(ALLOWED_TABLES)}"
            )

        if not PANDAS_AVAILABLE:
            logger.error("[Exporter] pandas not installed. Cannot export.")
            return {}

        today = date.today().strftime("%Y-%m-%d")
        filename = f"{table_name}_{today}.csv"
        output_path = self.export_dir / filename

        try:
            conn = sqlite3.connect(self.db_path)
            try:
                df = pd.read_sql(f"SELECT * FROM {table_name}", conn)
            finally:
                conn.close()

            df.to_csv(output_path, index=False, encoding="utf-8")
            file_size = output_path.stat().st_size
            size_str = self._human_size(file_size)

            summary = {
                "table": table_name,
                "rows": len(df),
                "file": str(output_path),
                "size": size_str,
            }
            logger.info(
                f"[Exporter] {table_name:30s} → {len(df):>10,} rows  {size_str:>10}  {output_path.name}"
            )
            return summary

        except Exception as e:
            logger.error(f"[Exporter] Failed to export {table_name}: {e}")
            return {}

    def export_all(self) -> list[dict]:
        """
        Export every table in the DB to separate CSVs.
        Prints a summary after completion.
        """
        if not PANDAS_AVAILABLE:
            logger.error("[Exporter] pandas not installed. Cannot export.")
            return []

        tables = self._get_table_names()
        if not tables:
            logger.warning("[Exporter] No tables found in DB.")
            return []

        logger.info(
            f"[Exporter] Exporting {len(tables)} tables to {self.export_dir}/..."
        )
        summaries = []
        for table in tables:
            summary = self.export_table(table)
            if summary:
                summaries.append(summary)

        # Print summary table
        print("\n" + "=" * 70)
        print(f"{'TABLE':<30} {'ROWS':>12} {'SIZE':>10}")
        print("=" * 70)
        total_rows = 0
        for s in summaries:
            print(f"{s['table']:<30} {s['rows']:>12,} {s['size']:>10}")
            total_rows += s.get("rows", 0)
        print("=" * 70)
        print(f"{'TOTAL':.<30} {total_rows:>12,}")
        print(f"\nFiles written to: {self.export_dir.resolve()}\n")

        return summaries

    @staticmethod
    def _human_size(num_bytes: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if num_bytes < 1024:
                return f"{num_bytes:.1f} {unit}"
            num_bytes /= 1024
        return f"{num_bytes:.1f} TB"
