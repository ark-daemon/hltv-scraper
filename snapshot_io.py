"""Shared snapshot IO helpers (copy into each repo or import path)."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence


INT_COLUMNS = frozenset({"score_a", "score_b"})


def write_snapshot_files(
    *,
    out_dir: Path,
    rows: list[dict[str, Any]],
    columns: Sequence[str],
    source: str,
    game: str,
    schema_version: str = "1.0",
    extra_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write data.json / data.csv / data.parquet / manifest.json with nullable ints."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Normalize int columns: keep Python int or None (never float).
    clean: list[dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        for col in INT_COLUMNS:
            if col not in r:
                continue
            v = r[col]
            if v is None or v == "":
                r[col] = None
            else:
                r[col] = int(v)
        clean.append(r)

    json_path = out / "data.json"
    csv_path = out / "data.csv"
    parquet_path = out / "data.parquet"
    manifest_path = out / "manifest.json"

    json_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(columns), extrasaction="ignore")
        w.writeheader()
        for row in clean:
            out_row = {}
            for k in columns:
                v = row.get(k)
                # CSV: write ints as int-like strings without .0; empty for null
                if k in INT_COLUMNS:
                    out_row[k] = "" if v is None else str(int(v))
                else:
                    out_row[k] = "" if v is None else v
            w.writerow(out_row)

    try:
        import pandas as pd

        df = pd.DataFrame(clean, columns=list(columns))
        for col in INT_COLUMNS:
            if col in df.columns:
                df[col] = df[col].astype("Int64")  # nullable integer, no float coercion
        df.to_parquet(parquet_path, index=False)
    except Exception as exc:
        parquet_path.write_text(f"parquet_error: {exc}\n", encoding="utf-8")

    manifest: dict[str, Any] = {
        "source": source,
        "game": game,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "record_count": len(clean),
        "schema_version": schema_version,
        "columns": list(columns),
        "files": {"json": "data.json", "csv": "data.csv", "parquet": "data.parquet"},
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
