"""
consolidate.py
──────────────
Standalone script to build consolidated DQ reporting files.

Scans today's (or all historical) output runs, filters out ERROR rows,
deduplicates by (dremio_col, virt_full_path, date), and appends only
new non-error results to the consolidated CSV and YAML files.

Usage
─────
  # Consolidate today's runs only
  python consolidate.py

  # Consolidate ALL historical runs (first-time migration)
  python consolidate.py --all

Output
──────
  output/
  └── consolidated/
      ├── all_columns_history.csv
      └── all_columns_history.yaml
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path("output")
CONSOLIDATED_DIR = OUTPUT_DIR / "consolidated"

CONSOLIDATED_CSV = CONSOLIDATED_DIR / "all_columns_history.csv"
CONSOLIDATED_YAML = CONSOLIDATED_DIR / "all_columns_history.yaml"

# Columns for the consolidated CSV (no "error" column)
FIELDNAMES = [
    "dremio_col",
    "virt_full_path",
    "dataset",
    "domain",
    "rule",
    "total_lignes",
    "valides",
    "score_pct",
    "flag",
    "timestamp",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _date_from_folder_name(folder_name: str) -> str | None:
    """
    Extract the date portion (YYYY-MM-DD) from a folder name like '2026-06-03_14-33-05'.
    Returns None if the folder name doesn't match the expected pattern.
    """
    try:
        return folder_name[:10]  # 'YYYY-MM-DD'
    except (IndexError, ValueError):
        return None


def _dedup_key(row: dict) -> tuple[str, str, str]:
    """
    Build a deduplication key: (dremio_col, virt_full_path, date).
    The date is extracted from the timestamp field (YYYY-MM-DDTHH:MM:SS → YYYY-MM-DD).
    """
    ts = row.get("timestamp", "")
    date_part = ts[:10] if len(ts) >= 10 else ts
    return (row.get("dremio_col", ""), row.get("virt_full_path", ""), date_part)


def _load_existing_consolidated() -> tuple[list[dict], set[tuple]]:
    """
    Load the existing consolidated CSV file and extract all deduplication keys.

    Returns:
        (rows, keys) — list of existing rows and set of (dremio_col, virt_full_path, date) tuples.
    """
    rows: list[dict] = []
    keys: set[tuple] = set()

    if not CONSOLIDATED_CSV.exists():
        return rows, keys

    with open(CONSOLIDATED_CSV, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            keys.add(_dedup_key(row))

    logger.info("Loaded %d existing consolidated rows (%d unique keys)", len(rows), len(keys))
    return rows, keys


def _find_run_folders(target_dates: list[str] | None = None) -> list[Path]:
    """
    Find all run output folders, optionally filtered by date(s).

    Args:
        target_dates: List of date strings (YYYY-MM-DD) to filter by.
                      If None, returns all folders.

    Returns:
        Sorted list of folder paths (chronological order).
    """
    if not OUTPUT_DIR.exists():
        return []

    folders = []
    for item in sorted(os.listdir(OUTPUT_DIR)):
        folder_path = OUTPUT_DIR / item
        if not folder_path.is_dir():
            continue
        if item == "consolidated":
            continue

        if target_dates is not None:
            folder_date = _date_from_folder_name(item)
            if folder_date not in target_dates:
                continue

        folders.append(folder_path)

    return folders


def _read_run_columns(run_folder: Path) -> list[dict]:
    """
    Read all per-table CSV files from a run folder and return column-level rows.

    Args:
        run_folder: Path to a timestamped run folder (e.g., output/2026-06-08_10-53-49/).

    Returns:
        List of dicts with the consolidated fieldnames (no 'error' field).
    """
    csv_dir_name = f"{run_folder.name}_csv"
    csv_dir = run_folder / csv_dir_name

    if not csv_dir.exists():
        logger.warning("  No CSV subfolder found: %s", csv_dir)
        return []

    rows: list[dict] = []

    for csv_file in sorted(csv_dir.iterdir()):
        if not csv_file.name.endswith(".csv"):
            continue
        # Skip _all_tables.csv — we only want per-table detail files
        if csv_file.name == "_all_tables.csv":
            continue

        try:
            with open(csv_file, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Filter out ERROR rows
                    if row.get("flag", "").upper() == "ERROR":
                        continue

                    # Build a clean row with only consolidated fieldnames
                    clean_row = {field: row.get(field, "") for field in FIELDNAMES}
                    rows.append(clean_row)
        except Exception as exc:
            logger.error("  Error reading %s: %s", csv_file, exc)

    return rows


def _write_consolidated_csv(rows: list[dict]) -> None:
    """Write all consolidated rows to the CSV file (full rewrite)."""
    CONSOLIDATED_DIR.mkdir(parents=True, exist_ok=True)

    with open(CONSOLIDATED_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    logger.info("Consolidated CSV written: %s (%d rows)", CONSOLIDATED_CSV, len(rows))


def _write_consolidated_yaml(rows: list[dict]) -> None:
    """Write all consolidated rows to the YAML file (full rewrite)."""
    CONSOLIDATED_DIR.mkdir(parents=True, exist_ok=True)

    # Convert numeric strings back to proper types for cleaner YAML
    typed_rows = []
    for row in rows:
        typed = dict(row)
        # Convert numeric fields
        for int_field in ("total_lignes", "valides"):
            if typed[int_field] and typed[int_field] != "N/A":
                try:
                    typed[int_field] = int(typed[int_field])
                except (ValueError, TypeError):
                    pass
        if typed["score_pct"] and typed["score_pct"] != "N/A":
            try:
                typed["score_pct"] = float(typed["score_pct"])
            except (ValueError, TypeError):
                pass
        typed_rows.append(typed)

    data = {"columns": typed_rows}

    with open(CONSOLIDATED_YAML, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)

    logger.info("Consolidated YAML written: %s (%d rows)", CONSOLIDATED_YAML, len(rows))


# ── Main logic ────────────────────────────────────────────────────────────────

def consolidate(all_dates: bool = False) -> None:
    """
    Main consolidation logic.

    Args:
        all_dates: If True, process all historical run folders.
                   If False, process only today's run folders.
    """
    # 1. Determine which dates to process
    if all_dates:
        target_dates = None  # all folders
        logger.info("Mode: --all (processing all historical runs)")
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        target_dates = [today]
        logger.info("Mode: today only (%s)", today)

    # 2. Find matching run folders
    run_folders = _find_run_folders(target_dates)
    if not run_folders:
        logger.warning("No run folders found for the target date(s).")
        return

    logger.info("Found %d run folder(s) to process:", len(run_folders))
    for f in run_folders:
        logger.info("  • %s", f.name)

    # 3. Load existing consolidated data
    existing_rows, existing_keys = _load_existing_consolidated()

    # 4. Process each run folder (chronological order)
    new_count = 0
    skipped_error = 0
    skipped_duplicate = 0

    for run_folder in run_folders:
        logger.info("Processing: %s", run_folder.name)
        columns = _read_run_columns(run_folder)

        for row in columns:
            key = _dedup_key(row)
            if key in existing_keys:
                skipped_duplicate += 1
                continue

            # New non-error row → add it
            existing_rows.append(row)
            existing_keys.add(key)
            new_count += 1

    # 5. Write consolidated files
    _write_consolidated_csv(existing_rows)
    _write_consolidated_yaml(existing_rows)

    # 6. Summary
    logger.info("═" * 60)
    logger.info("Consolidation complete:")
    logger.info("  New rows added:     %d", new_count)
    logger.info("  Duplicates skipped: %d", skipped_duplicate)
    logger.info("  Total consolidated: %d", len(existing_rows))
    logger.info("═" * 60)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consolidate DQ run outputs into a single reporting file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_dates",
        help="Process ALL historical runs (not just today).",
    )
    args = parser.parse_args()
    consolidate(all_dates=args.all_dates)


if __name__ == "__main__":
    main()
