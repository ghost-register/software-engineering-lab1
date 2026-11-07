"""Registration, password verification and revocable server-side sessions."""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

from . import validation
from .database import new_id, now
from .domain import Change, DomainError, User, require
from .repository import Repository
from .service import Service, audit


def token_hash(token: str) -> str:
    """Only a digest of the browser credential is kept in persistent storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService(Service):
    """Public registration never accepts roles supplied by the client."""

    def register(self, data: dict) -> dict:
        """Create a normal account and never echo its password hash."""
        try:
            require(not (set(data) - {"username", "password"}),
                    "注册只接受用户名和密码", 422, "VALIDATION")
            name = validation.username(data.get("username"))
            secret = validation.password(data.get("password"))
            hashed = generate_password_hash(secret, method="scrypt")
            identifier = new_id()
            with self.database.transaction() as connection:
                repo = Repository(connection)
                repo.insert("users", {
                    "id": identifier, "username": name, "password_hash": hashed,
                    "role": "USER", "status": "ACTIVE", "created_at": now(),
                })
                audit(repo, identifier, "REGISTER", identifier, "SUCCESS", after="ACTIVE")
                return User.from_row(repo.get("users", identifier)).to_dict()
        except sqlite3.IntegrityError as exc:
            self.failure(None, "REGISTER", None, "用户名重复")
            raise DomainError("用户名已存在", 409, "DUPLICATE_USERNAME") from exc
        except DomainError as exc:
            self.failure(None, "REGISTER", None, exc.message)
            raise

    def create_admin(self, name: str, secret: str) -> dict:
        """Explicit local CLI provisioning; no HTTP route exposes this method."""
        name = validation.username(name)
        secret = validation.password(secret)
        with self.database.transaction() as connection:
            repo = Repository(connection)
            require(repo.one("SELECT id FROM users WHERE username=?", (name,)) is None,
                    "账号已存在", 409)
            identifier = new_id()
            repo.insert("users", {
                "id": identifier, "username": name,
                "password_hash": generate_password_hash(secret, method="scrypt"),
                "role": "ADMIN", "status": "ACTIVE", "created_at": now(),
            })
            audit(repo, identifier, "PROVISION_ADMIN", identifier, "SUCCESS", after="ACTIVE")
            return User.from_row(repo.get("users", identifier)).to_dict()

    def login(self, data: dict) -> dict:
        """Return one raw session token only to the transport's cookie setter."""
        name = data.get("username")
        secret = data.get("password")
        actor_id = None
        try:
            require(isinstance(name, str) and isinstance(secret, str)
                    and len(name) <= 20 and len(secret) <= 64,
                    "用户名或密码错误", 401, "BAD_CREDENTIALS")
            with self.database.read() as connection:
                row = Repository(connection).one("SELECT * FROM users WHERE username=?", (name,))
            # Perform a hash verification even for an unknown username.
            stored = row["password_hash"] if row else generate_password_hash("dummy12345")
            valid = check_password_hash(stored, secret)
            require(row is not None and valid and row["status"] == "ACTIVE",
                    "用户名或密码错误", 401, "BAD_CREDENTIALS")
            actor_id = row["id"]
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            expiry = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
            with self.database.transaction() as connection:
                repo = Repository(connection)
                user = repo.actor(actor_id)
                repo.insert("sessions", {
                    "token_hash": token_hash(token), "user_id": actor_id,
                    "csrf_token": csrf, "expires_at": expiry,
                })
                audit(repo, actor_id, "LOGIN", actor_id, "SUCCESS")
            return {"token": token, "csrf_token": csrf, "user": user.to_dict()}
        except DomainError as exc:
            self.failure(actor_id, "LOGIN", None, exc.message)
            raise

    def authenticate(self, token: str | None) -> dict | None:
        """Check expiry and account status for both reads and writes."""
        if not token or len(token) > 200:
            return None
        with self.database.read() as connection:
            repo = Repository(connection)
            row = repo.one(
                "SELECT s.csrf_token,u.id,u.username,u.role,u.status "
                "FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token_hash=? AND s.expires_at>? AND u.status='ACTIVE'",
                (token_hash(token), now()),
            )
            if row is None:
                return None
            return {"user": User.from_row(row).to_dict(), "csrf_token": row["csrf_token"]}

    def logout(self, token: str):
        """Delete only the current session; old cookies immediately stop working."""
        with self.database.transaction() as connection:
            repo = Repository(connection)
            row = repo.one("SELECT user_id FROM sessions WHERE token_hash=?", (token_hash(token),))
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
            if row:
                audit(repo, row["user_id"], "LOGOUT", row["user_id"], "SUCCESS")

    def disable(self, actor_id: str, user_id: str, reason: str) -> dict:
        """Disable normal accounts and revoke all of their current sessions."""
        def operation(repo, actor):
            repo.admin(actor.id)
            reason_text = validation.text(reason, "停用原因", 1000)
            target = repo.get("users", user_id)
            require(target["role"] == "USER", "此入口只能停用普通账号", 403, "FORBIDDEN")
            repo.update("users", user_id, {"status": "DISABLED"})
            repo.connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return Change({"id": user_id, "status": "DISABLED"},
                          target["status"], "DISABLED", reason_text)
        return self.perform(actor_id, "DISABLE_ACCOUNT", user_id, operation)
