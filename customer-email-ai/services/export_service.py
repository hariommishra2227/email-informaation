"""Memory-conscious Excel export service."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable
import csv

from openpyxl import Workbook

import config
from excel_exporter import COLUMN_MAPPING, EXCEL_FILE_NAME, WORKSHEET_NAME
from services.customer_service import BUSINESS_COLUMNS, iter_customer_export_rows


ProgressCallback = Callable[[int], None]


def create_large_excel_export(
    user_id: str,
    *,
    limit: int | None = None,
    progress_callback: ProgressCallback | None = None,
    source: str = "",
    status: str = "",
    search: str = "",
) -> Path:
    """Create a write-only workbook from database chunks and return a temp path."""
    export_limit = int(limit or config.EXPORT_LIMIT)
    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet(WORKSHEET_NAME)
    worksheet.append(list(COLUMN_MAPPING.values()))
    written = 0
    with NamedTemporaryFile(prefix="customer_export_", suffix=".xlsx", delete=False) as handle:
        path = Path(handle.name)
    for chunk in iter_customer_export_rows(
        user_id, chunk_size=config.EXPORT_CHUNK_SIZE, limit=export_limit,
        source=source, status=status, search=search,
    ):
        for row in chunk:
            worksheet.append([row.get(column, "") for column in COLUMN_MAPPING.keys()])
            written += 1
        if progress_callback is not None:
            progress_callback(written)
        if written >= export_limit:
            break
    workbook.save(path)
    return path


def create_large_csv_export(
    user_id: str, *, limit: int | None = None, source: str = "", status: str = "", search: str = ""
) -> Path:
    """Write matching business rows incrementally to a bounded temporary CSV."""
    export_limit = int(limit or config.EXPORT_LIMIT)
    with NamedTemporaryFile(prefix="customer_export_", suffix=".csv", delete=False, mode="w", newline="", encoding="utf-8-sig") as handle:
        path = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=list(BUSINESS_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for chunk in iter_customer_export_rows(
            user_id, chunk_size=config.EXPORT_CHUNK_SIZE, limit=export_limit,
            source=source, status=status, search=search,
        ):
            writer.writerows(chunk)
    return path


def cleanup_export(path: Path) -> None:
    """Delete a temporary export file if it still exists."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = ["EXCEL_FILE_NAME", "create_large_excel_export", "create_large_csv_export", "cleanup_export"]
