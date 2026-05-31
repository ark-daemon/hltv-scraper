"""
db/database.py - Async SQLite database manager using aiosqlite.
"""

import aiosqlite
from loguru import logger
from pathlib import Path
import re


class Database:
    """Async SQLite database manager for the data extraction pipeline."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open a persistent database connection."""
        try:
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            await self._conn.execute("PRAGMA foreign_keys=ON")
            await self._conn.commit()
            logger.info(f"[DB] Connected to {self.db_path}")
        except Exception as e:
            logger.error(f"[DB] Failed to connect: {e}")
            raise

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            try:
                await self._conn.close()
                logger.info("[DB] Connection closed.")
            except Exception as e:
                logger.error(f"[DB] Error closing connection: {e}")

    async def init_db(self) -> None:
        """Read and execute schema.sql to create all tables."""
        schema_path = Path(__file__).parent / "schema.sql"
        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema_sql = f.read()
            await self._conn.executescript(schema_sql)
            await self._conn.commit()
            logger.info("[DB] Schema initialized successfully.")
        except FileNotFoundError:
            logger.error(f"[DB] schema.sql not found at {schema_path}")
            raise
        except Exception as e:
            logger.error(f"[DB] Error initializing schema: {e}")
            raise

    async def insert_or_ignore(self, table: str, row_dict: dict) -> bool:
        """INSERT OR IGNORE a single row. Returns True if inserted."""
        if not row_dict:
            return False
        columns = ", ".join(row_dict.keys())
        placeholders = ", ".join(["?" for _ in row_dict])
        sql = f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({placeholders})"
        try:
            async with self._conn.execute(sql, list(row_dict.values())) as cursor:
                await self._conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"[DB] insert_or_ignore error on {table}: {e} | data={row_dict}")
            return False

    async def insert_or_replace(self, table: str, row_dict: dict) -> bool:
        """INSERT OR REPLACE a single row. Returns True on success."""
        if not row_dict:
            return False
        columns = ", ".join(row_dict.keys())
        placeholders = ", ".join(["?" for _ in row_dict])
        sql = f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})"
        try:
            async with self._conn.execute(sql, list(row_dict.values())) as cursor:
                await self._conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"[DB] insert_or_replace error on {table}: {e} | data={row_dict}")
            return False

    async def upsert(self, table: str, row_dict: dict, pk_column: str) -> bool:
        """
        Upsert a single row using ON CONFLICT(pk_column) DO UPDATE.

        Unlike INSERT OR REPLACE, this preserves columns not present in row_dict.
        """
        if not row_dict:
            return False
        if pk_column not in row_dict:
            raise ValueError(
                f"upsert requires pk_column '{pk_column}' in row_dict keys for table '{table}'"
            )

        self._validate_identifier(table, "table name")
        self._validate_identifier(pk_column, "pk column")

        columns = list(row_dict.keys())
        for col in columns:
            self._validate_identifier(col, "column name")

        insert_cols = ", ".join(columns)
        placeholders = ", ".join(["?" for _ in columns])
        update_cols = [c for c in columns if c != pk_column]

        if update_cols:
            update_set = ", ".join([f"{c}=excluded.{c}" for c in update_cols])
            conflict_action = f"DO UPDATE SET {update_set}"
        else:
            # Degenerate case: only PK provided.
            conflict_action = "DO NOTHING"

        sql = (
            f"INSERT INTO {table} ({insert_cols}) VALUES ({placeholders}) "
            f"ON CONFLICT({pk_column}) {conflict_action}"
        )
        try:
            async with self._conn.execute(sql, list(row_dict.values())) as cursor:
                await self._conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"[DB] upsert error on {table}: {e} | data={row_dict}")
            return False

    async def bulk_insert(self, table: str, rows: list[dict], replace: bool = False) -> int:
        """Bulk insert many rows. Uses OR REPLACE when replace=True, else OR IGNORE."""
        if not rows:
            return 0
        columns = ", ".join(rows[0].keys())
        placeholders = ", ".join(["?" for _ in rows[0]])
        conflict = "REPLACE" if replace else "IGNORE"
        sql = f"INSERT OR {conflict} INTO {table} ({columns}) VALUES ({placeholders})"
        values = [list(row.values()) for row in rows]

        # Chunk to stay within SQLite default SQLITE_MAX_VARIABLE_NUMBER (999)
        col_count = len(rows[0])
        chunk_size = max(1, 999 // col_count)
        total_inserted = 0

        try:
            for i in range(0, len(values), chunk_size):
                chunk = values[i : i + chunk_size]
                async with self._conn.executemany(sql, chunk) as cursor:
                    await self._conn.commit()
                    rc = cursor.rowcount
                    total_inserted += rc if rc is not None and rc >= 0 else len(chunk)
            return total_inserted
        except Exception as e:
            logger.error(f"[DB] bulk_insert error on {table}: {e}")
            return -1

    async def get_all_ids(self, table: str, id_column: str) -> set:
        """Return a set of all existing IDs for a given table + column."""
        sql = f"SELECT {id_column} FROM {table}"
        try:
            async with self._conn.execute(sql) as cursor:
                rows = await cursor.fetchall()
                return {row[0] for row in rows}
        except Exception as e:
            logger.error(f"[DB] get_all_ids error on {table}.{id_column}: {e}")
            return set()

    async def row_count(self, table: str) -> int:
        """Return integer count of rows in a table."""
        sql = f"SELECT COUNT(*) FROM {table}"
        try:
            async with self._conn.execute(sql) as cursor:
                result = await cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"[DB] row_count error on {table}: {e}")
            return 0

    async def execute(self, sql: str, params: list | None = None) -> list[tuple]:
        """Execute arbitrary SQL and return all rows."""
        try:
            async with self._conn.execute(sql, params or []) as cursor:
                return await cursor.fetchall()
        except Exception as e:
            logger.error(f"[DB] execute error: {e} | sql={sql}")
            raise

    async def get_table_names(self) -> list[str]:
        """Return list of all user table names in the database (excludes sqlite internals)."""
        sql = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        try:
            async with self._conn.execute(sql) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
        except Exception as e:
            logger.error(f"[DB] get_table_names error: {e}")
            return []
    _IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def _validate_identifier(self, value: str, label: str) -> None:
        if not self._IDENT_RE.fullmatch(value):
            raise ValueError(f"Invalid {label}: {value!r}")
