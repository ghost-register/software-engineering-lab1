"""Shared transactional application-service behavior and audit records."""

import logging
import sqlite3

from .database import new_id, now
from .domain import DomainError
from .repository import Repository

LOGGER = logging.getLogger(__name__)


def audit(repo, actor_id, action, object_id, result, before=None, after=None, reason=None):
    """Store a bounded, structured event without credential or payload data."""
    if actor_id and repo.one("SELECT id FROM users WHERE id=?", (actor_id,)) is None:
        actor_id = None
    repo.insert(
        "audit_logs",
        {
            "id": new_id(),
            "actor_id": actor_id,
            "action": action,
            "object_id": object_id,
            "result": result,
            "before_state": before,
            "after_state": after,
            "reason": reason,
            "created_at": now(),
        },
    )


class Service:
    """Atomic operations and independent failure logging after rollback."""

    def __init__(self, database):
        self.database = database
        self.fault_hook = None

    def checkpoint(self, action: str, point: str):
        """A test-only injection point, inaccessible from request parameters."""
        if self.fault_hook is not None:
            self.fault_hook(action, point)

    def failure(self, actor_id, action, object_id, reason):
        """A failed transaction cannot roll its failure event back with it."""
        try:
            with self.database.transaction() as connection:
                audit(Repository(connection), actor_id, action, object_id, "FAILURE", reason=reason)
        except sqlite3.Error:
            LOGGER.exception("Failure audit could not be persisted for action %s", action)

    def perform(self, actor_id, action, object_id, operation):
        """Authenticate, execute, audit and commit as one unit of work."""
        try:
            with self.database.transaction() as connection:
                repo = Repository(connection)
                actor = repo.actor(actor_id)
                change = operation(repo, actor)
                self.checkpoint(action, "before_commit")
                audit(
                    repo,
                    actor_id,
                    action,
                    change.object_id or object_id,
                    "SUCCESS",
                    change.before,
                    change.after,
                    change.reason,
                )
                return change.value
        except DomainError as exc:
            self.failure(actor_id, action, object_id, exc.message)
            raise
        except sqlite3.IntegrityError as exc:
            self.failure(actor_id, action, object_id, "唯一性或关联约束冲突")
            raise DomainError("记录状态已变化，请刷新后重试", 409, "CONFLICT") from exc
        except Exception:
            self.failure(actor_id, action, object_id, "操作失败，事务已回滚")
            raise
