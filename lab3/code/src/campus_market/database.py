"""Short-lived SQLite connections and explicit write transactions."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid


def now() -> str:
    """Store timezone-aware UTC timestamps with a uniform sortable format."""
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    """Generate identifiers independently of table row counts."""
    return uuid.uuid4().hex


class Database:
    """SQLite serializes writers; BEGIN IMMEDIATE prevents upgrade races."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self):
        """Create a per-operation connection, never shared across threads."""
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def initialize(self):
        """Apply additive schema initialization and enable concurrent readers."""
        with self.read() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            script = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
            connection.executescript(script)

    @contextmanager
    def read(self):
        """Close a read connection even if query validation raises."""
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        """Commit all aggregate writes together, or roll every one back."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def backup(self, destination):
        """Use SQLite's online backup API instead of copying live WAL files."""
        target = Path(destination).resolve()
        if target == self.path:
            raise ValueError("备份路径不能与数据库路径相同")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.read() as source, sqlite3.connect(target) as destination_db:
            source.backup(destination_db)
        return str(target)

    def integrity(self) -> list[str]:
        """Expose a read-only integrity check for the command-line tool."""
        with self.read() as connection:
            return [row[0] for row in connection.execute("PRAGMA integrity_check")]
