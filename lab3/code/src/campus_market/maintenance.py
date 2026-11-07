"""Read-only consistency diagnostics and conservative local retention tasks."""

from datetime import datetime, timedelta, timezone

from .database import now
from .domain import require
from .repository import Repository
from .service import audit


class MaintenanceService:
    """Operational tasks are exposed locally through the CLI, never to buyers."""

    def __init__(self, database):
        self.database = database

    def consistency(self):
        """Find coupled-state mismatches in addition to SQLite page integrity."""
        with self.database.read() as connection:
            repo = Repository(connection)
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
            product_mismatches = repo.all(
                "SELECT p.id,p.status,count(r.id) AS active_count,"
                "max(r.status) AS reservation_status "
                "FROM products p LEFT JOIN reservations r ON r.product_id=p.id "
                "AND r.status IN ('PENDING_SELLER','SELLER_CONFIRMED') GROUP BY p.id HAVING "
                "(p.status='RESERVED' AND (count(r.id)!=1 OR max(r.status)!='PENDING_SELLER')) OR "
                "(p.status='CONFIRMED' AND "
                "(count(r.id)!=1 OR max(r.status)!='SELLER_CONFIRMED')) OR "
                "(p.status NOT IN ('RESERVED','CONFIRMED') AND count(r.id)!=0)"
            )
            completed_mismatches = repo.all(
                "SELECT r.id,r.product_id,p.status FROM reservations r "
                "JOIN products p ON p.id=r.product_id WHERE r.status='COMPLETED' "
                "AND (p.status!='COMPLETED' OR r.completed_at IS NULL)"
            )
            result_mismatches = repo.all(
                "SELECT q.request_key FROM request_results q JOIN reservations r "
                "ON r.id=q.reservation_id WHERE q.user_id!=r.buyer_id"
            )
        return {
            "ok": integrity == ["ok"]
            and not any(
                [foreign_keys, product_mismatches, completed_mismatches, result_mismatches]
            ),
            "integrity": integrity,
            "foreign_keys": foreign_keys,
            "product_mismatches": product_mismatches,
            "completed_mismatches": completed_mismatches,
            "request_owner_mismatches": result_mismatches,
        }

    def retention(self, keep_days=180, *, apply=False):
        """Preview by default; an applied cleanup still keeps at least 180 days."""
        require(
            isinstance(keep_days, int) and not isinstance(keep_days, bool) and keep_days >= 180,
            "关键日志至少保留180天",
            422,
            "VALIDATION",
        )
        timestamp = now()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        with self.database.transaction() as connection:
            repo = Repository(connection)
            counts = {
                "expired_sessions": repo.one(
                    "SELECT count(*) FROM sessions WHERE expires_at<=?", (timestamp,)
                )[0],
                "expired_request_results": repo.one(
                    "SELECT count(*) FROM request_results WHERE expires_at<=?", (timestamp,)
                )[0],
                "old_audit_logs": repo.one(
                    "SELECT count(*) FROM audit_logs WHERE created_at<?", (cutoff,)
                )[0],
            }
            if apply:
                connection.execute("DELETE FROM sessions WHERE expires_at<=?", (timestamp,))
                connection.execute("DELETE FROM request_results WHERE expires_at<=?", (timestamp,))
                connection.execute("DELETE FROM audit_logs WHERE created_at<?", (cutoff,))
                audit(
                    repo,
                    None,
                    "LOCAL_RETENTION",
                    None,
                    "SUCCESS",
                    reason=f"retention_days={keep_days}",
                )
        return {"applied": apply, "keep_days": keep_days, "cutoff_utc": cutoff, "counts": counts}
