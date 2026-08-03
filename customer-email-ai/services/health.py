"""Health-check helpers safe for AWS load balancers."""

from __future__ import annotations

from database.connection import check_database


def health_status() -> dict[str, str]:
    """Return process/database health without secrets or email data."""
    status = {"application": "ok", "database": "unknown"}
    try:
        check_database()
    except Exception:
        status["database"] = "unavailable"
    else:
        status["database"] = "ok"
    return status
