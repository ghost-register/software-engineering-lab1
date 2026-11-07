"""Persistence primitives; SQL identifiers never come from HTTP parameters."""

import sqlite3

from .domain import DomainError, User, require

TABLES = frozenset(
    {
        "users",
        "sessions",
        "products",
        "images",
        "reviews",
        "reservations",
        "request_results",
        "reports",
        "close_requests",
        "audit_logs",
    }
)


class Repository:
    """A unit-of-work repository bound to an explicitly supplied connection."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    @staticmethod
    def _table(table):
        if table not in TABLES:
            raise ValueError("Unknown internal table")
        return table

    def one(self, sql: str, parameters=()):
        """Fetch one optional row using SQLite's parameter binding."""
        return self.connection.execute(sql, parameters).fetchone()

    def all(self, sql: str, parameters=()) -> list[dict]:
        """Convert rows at the repository boundary for JSON-safe results."""
        return [dict(row) for row in self.connection.execute(sql, parameters)]

    def get(self, table: str, identifier: str):
        """Resolve a domain object without accepting a dynamic column name."""
        row = self.one(f"SELECT * FROM {self._table(table)} WHERE id=?", (identifier,))
        if row is None:
            raise DomainError("记录不存在", 404, "NOT_FOUND")
        return row

    def insert(self, table: str, values: dict):
        """Insert trusted service-owned field dictionaries with bound values."""
        table = self._table(table)
        columns = tuple(values)
        if not columns or any(not name.isidentifier() for name in columns):
            raise ValueError("Invalid internal field list")
        placeholders = ",".join("?" for _ in columns)
        names = ",".join(f'"{column}"' for column in columns)
        self.connection.execute(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})", tuple(values.values())
        )

    def update(self, table: str, identifier: str, values: dict):
        """Change explicit fields; services own all transition decisions."""
        table = self._table(table)
        if not values or any(not key.isidentifier() for key in values):
            raise ValueError("Invalid internal field list")
        assignments = ",".join(f'"{key}"=?' for key in values)
        self.connection.execute(
            f"UPDATE {table} SET {assignments} WHERE id=?", (*values.values(), identifier)
        )

    def actor(self, identifier: str) -> User:
        """Recheck account status inside each business write transaction."""
        require(bool(identifier), "请先登录", 401, "UNAUTHENTICATED")
        row = self.get("users", identifier)
        require(row["status"] == "ACTIVE", "账号已停用", 403, "DISABLED")
        return User.from_row(row)

    def admin(self, identifier: str) -> User:
        """The administrative role is checked server-side on every operation."""
        actor = self.actor(identifier)
        require(actor.role == "ADMIN", "需要管理员权限", 403, "FORBIDDEN")
        return actor

    def active_reservation(self, product_id: str):
        """Find the one reservation covered by the partial unique index."""
        return self.one(
            "SELECT * FROM reservations WHERE product_id=? "
            "AND status IN ('PENDING_SELLER','SELLER_CONFIRMED')",
            (product_id,),
        )
