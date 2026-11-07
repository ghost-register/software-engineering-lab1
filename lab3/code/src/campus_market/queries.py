"""Read models for public search, personal worklists and administration."""

import json

from . import validation
from .database import now
from .domain import ProductState, require
from .repository import Repository


class QueryService:
    """Visibility policies are applied to SQL before counting or paginating."""

    def __init__(self, database):
        self.database = database

    @staticmethod
    def _actor(repo, actor_id):
        return repo.actor(actor_id) if actor_id else None

    def products(self, actor_id, filters):
        """Combine authorized scopes, exact prices, stable sorting and pagination."""
        page = validation.integer(filters.get("page"), "页码", 1, 1, 100000)
        size = validation.integer(filters.get("page_size"), "每页数量", 20, 1, 100)
        keyword = validation.text(filters.get("keyword"), "关键词", 100, required=False)
        category = validation.category(filters.get("category"))
        minimum = validation.money(filters.get("minimum"), optional=True)
        maximum = validation.money(filters.get("maximum"), optional=True)
        require(minimum is None or maximum is None or minimum <= maximum,
                "最低价不能大于最高价", 422, "VALIDATION")
        sort = filters.get("sort", "newest")
        orders = {
            "newest": "p.first_published_at DESC,p.id ASC",
            "price_asc": "p.price_cents ASC,p.id ASC",
            "price_desc": "p.price_cents DESC,p.id ASC",
        }
        require(sort in orders, "排序方式无效", 422, "VALIDATION")
        scope = filters.get("scope", "public")
        clauses = []
        parameters = []
        with self.database.read() as connection:
            repo = Repository(connection)
            actor = self._actor(repo, actor_id)
            if scope == "mine":
                require(actor is not None, "请先登录", 401, "UNAUTHENTICATED")
                clauses.append("p.seller_id=?")
                parameters.append(actor.id)
            elif scope == "all":
                require(actor is not None and actor.role == "ADMIN",
                        "需要管理员权限", 403, "FORBIDDEN")
            else:
                require(scope == "public", "查询范围无效", 422, "VALIDATION")
                clauses.append("p.status='PUBLISHED'")
            status = filters.get("status")
            if status:
                require(status in set(ProductState), "商品状态无效", 422, "VALIDATION")
                clauses.append("p.status=?")
                parameters.append(status)
            if keyword:
                escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                clauses.append("(p.title LIKE ? ESCAPE '\\' OR p.description LIKE ? ESCAPE '\\')")
                parameters.extend([f"%{escaped}%"] * 2)
            if category:
                clauses.append("p.category=?")
                parameters.append(category)
            if minimum is not None:
                clauses.append("p.price_cents>=?")
                parameters.append(minimum)
            if maximum is not None:
                clauses.append("p.price_cents<=?")
                parameters.append(maximum)
            where = " AND ".join(clauses) or "1=1"
            total = repo.one(f"SELECT count(*) FROM products p WHERE {where}", parameters)[0]
            rows = repo.all(
                "SELECT p.*,u.username AS seller_name FROM products p JOIN users u ON u.id=p.seller_id "
                f"WHERE {where} ORDER BY {orders[sort]} LIMIT ? OFFSET ?",
                (*parameters, size, (page - 1) * size),
            )
            for row in rows:
                review = repo.one(
                    "SELECT reason,decision FROM reviews WHERE product_id=? "
                    "ORDER BY submission_version DESC LIMIT 1", (row["id"],)
                ) if scope == "mine" else None
                row["review_summary"] = (review["reason"][:60]
                                         if review and review["decision"] == "REJECTED" else "")
            return {"items": rows, "total": total, "page": page, "page_size": size}

    def recommendations(self):
        """An independent public query never changes the failed search's total."""
        return self.products(None, {"page_size": "4", "sort": "newest"})["items"]

    def product(self, actor_id, product_id):
        """Private details are restricted to the owner, administrator or participant."""
        with self.database.read() as connection:
            repo = Repository(connection)
            actor = self._actor(repo, actor_id)
            row = dict(repo.get("products", product_id))
            related = actor and repo.one(
                "SELECT id FROM reservations WHERE product_id=? AND buyer_id=?", (product_id, actor.id))
            privileged = actor and (actor.id == row["seller_id"] or actor.role == "ADMIN")
            require(row["status"] == "PUBLISHED" or privileged or related,
                    "商品不可公开查看", 403, "FORBIDDEN")
            row["images"] = repo.all(
                "SELECT id,sort_order FROM images WHERE product_id=? ORDER BY sort_order", (product_id,))
            row["seller_name"] = repo.get("users", row["seller_id"])["username"]
            row["latest_review"] = None
            if privileged:
                review = repo.one("SELECT * FROM reviews WHERE product_id=? "
                                  "ORDER BY submission_version DESC LIMIT 1", (product_id,))
                row["latest_review"] = dict(review) if review else None
            return row

    def reservations(self, actor_id, side="buyer"):
        """List only the caller's buyer or seller history, including terminal rows."""
        require(side in {"buyer", "seller"}, "预约视角无效", 422, "VALIDATION")
        with self.database.read() as connection:
            repo = Repository(connection)
            actor = repo.actor(actor_id)
            column = "r.buyer_id" if side == "buyer" else "p.seller_id"
            return repo.all(
                "SELECT r.*,p.title,p.seller_id,p.status AS product_status "
                f"FROM reservations r JOIN products p ON p.id=r.product_id WHERE {column}=? "
                "ORDER BY r.created_at DESC,r.id", (actor.id,))

    def reservation(self, actor_id, reservation_id):
        """Recheck participation even if a user obtained a valid detail URL."""
        with self.database.read() as connection:
            repo = Repository(connection)
            actor = repo.actor(actor_id)
            row = dict(repo.get("reservations", reservation_id))
            product = repo.get("products", row["product_id"])
            require(actor.role == "ADMIN" or actor.id in {row["buyer_id"], product["seller_id"]},
                    "只能查看本人交易", 403, "FORBIDDEN")
            row.update({"title": product["title"], "seller_id": product["seller_id"],
                        "product_status": product["status"]})
            return row

    def request_result(self, actor_id, key):
        """Recover a committed result after a network timeout using its original key."""
        key = validation.request_key(key)
        with self.database.read() as connection:
            repo = Repository(connection)
            actor = repo.actor(actor_id)
            row = repo.one("SELECT * FROM request_results WHERE user_id=? AND request_key=? "
                           "AND action='RESERVE' AND expires_at>?", (actor.id, key, now()))
            require(row is not None, "未找到保留期内的请求结果，请查看预约记录", 404, "NOT_FOUND")
            return {"original": json.loads(row["response_json"]),
                    "current": dict(repo.get("reservations", row["reservation_id"]))}

    def reports(self, actor_id):
        """Ordinary reporters see only their own report outcomes."""
        with self.database.read() as connection:
            repo = Repository(connection)
            actor = repo.actor(actor_id)
            if actor.role == "ADMIN":
                return repo.all("SELECT * FROM reports ORDER BY created_at DESC")
            return repo.all("SELECT * FROM reports WHERE reporter_id=? ORDER BY created_at DESC",
                            (actor.id,))

    def admin_list(self, actor_id, kind):
        """A fixed map keeps the administrative table selector off the SQL surface."""
        queries = {
            "reviews": "SELECT * FROM reviews ORDER BY created_at DESC",
            "close_requests": "SELECT * FROM close_requests ORDER BY created_at DESC",
            "users": "SELECT id,username,role,status,created_at FROM users ORDER BY created_at DESC",
        }
        require(kind in queries, "列表类型无效", 422, "VALIDATION")
        with self.database.read() as connection:
            repo = Repository(connection)
            repo.admin(actor_id)
            return repo.all(queries[kind])

    def audit_logs(self, actor_id, filters):
        """Filter read-only logs by actor, UTC-converted dates, object and result."""
        clauses, parameters = [], []
        for field in ["actor_id", "object_id", "result"]:
            value = validation.text(filters.get(field), field, 100, required=False)
            if value:
                clauses.append(f"{field}=?")
                parameters.append(value)
        if filters.get("start") or filters.get("end"):
            start, end = validation.date_interval(filters.get("start"), filters.get("end"))
            clauses.append("created_at>=? AND created_at<?")
            parameters.extend((start, end))
        page = validation.integer(filters.get("page"), "页码", 1, 1, 100000)
        where = " AND ".join(clauses) or "1=1"
        with self.database.read() as connection:
            repo = Repository(connection)
            repo.admin(actor_id)
            total = repo.one(f"SELECT count(*) FROM audit_logs WHERE {where}", parameters)[0]
            rows = repo.all(f"SELECT * FROM audit_logs WHERE {where} "
                            "ORDER BY created_at DESC,id LIMIT 50 OFFSET ?",
                            (*parameters, (page - 1) * 50))
            return {"items": rows, "total": total, "page": page}

    def statistics(self, actor_id, filters):
        """Count first publication separately from completed price snapshots."""
        start, end = validation.date_interval(filters.get("start"), filters.get("end"))
        category = validation.category(filters.get("category"))
        category_sql = " AND p.category=?" if category else ""
        parameters = (start, end, category) if category else (start, end)
        with self.database.read() as connection:
            repo = Repository(connection)
            repo.admin(actor_id)
            published = repo.one("SELECT count(*) FROM products p WHERE first_published_at>=? "
                                 f"AND first_published_at<?{category_sql}", parameters)[0]
            completed = repo.one(
                "SELECT count(*),coalesce(sum(r.price_snapshot),0) FROM reservations r "
                "JOIN products p ON p.id=r.product_id WHERE r.status='COMPLETED' "
                f"AND r.completed_at>=? AND r.completed_at<?{category_sql}", parameters)
            return {"first_published": published, "completed_trades": completed[0],
                    "amount_cents": completed[1], "start_utc": start, "end_utc": end}
