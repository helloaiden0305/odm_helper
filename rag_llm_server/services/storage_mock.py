from typing import Literal

from config import settings


def log_mock_storage(
    *,
    target: Literal["postgresql", "redis"],
    operation: str,
    session_id: str,
    trace_id: str,
    detail: str,
    summary_id: str = "",
) -> None:
    """Marks future persistence/cache boundaries without adding external services."""

    if target == "postgresql":
        print(
            f"DEBUG: [{trace_id}] 已落入 Mock PostgreSQL 数据库, "
            f"operation={operation}, session_id={session_id}, {detail}"
        )
        return

    if operation == "read_summary":
        print(
            f"DEBUG: [{trace_id}] 已从 Mock Redis 读取 summary_id, "
            f"key=context_summary:{summary_id}, session_id={session_id}, {detail}"
        )
        return

    if operation == "read_summary_fallback":
        print(
            f"DEBUG: [{trace_id}] Mock Redis miss，已从 Mock PostgreSQL 摘要表读取 summary_id, "
            f"summary_id={summary_id}, session_id={session_id}, {detail}"
        )
        return

    print(
        f"DEBUG: [{trace_id}] 已落入 Mock Redis, "
        f"operation={operation}, key=context_summary:{summary_id}, session_id={session_id}, "
        f"ttl_seconds={settings.CONTEXT_SUMMARY_CACHE_TTL_SECONDS}, {detail}"
    )
