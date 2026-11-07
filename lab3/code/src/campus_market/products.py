"""Seller workflows, immutable review snapshots and product visibility."""

import json

from . import validation
from .database import new_id, now
from .domain import Change, Product, ProductState as State, require
from .service import Service


def owned(repo, actor_id, product_id):
    """A user cannot edit a product by guessing another seller's identifier."""
    row = repo.get("products", product_id)
    require(row["seller_id"] == actor_id, "只能维护本人商品", 403, "FORBIDDEN")
    return row


def assert_version(row, version):
    """Reject stale forms rather than overwriting a more recent edit."""
    expected = validation.integer(version, "版本", -1, 1, 2**31 - 1)
    require(expected == row["version"], "页面已过期，请刷新后再操作")


class ProductService(Service):
    """Map draft, submission and publication transitions to atomic writes."""

    def __init__(self, database, image_store):
        super().__init__(database)
        self.image_store = image_store

    def create(self, actor_id, data):
        """Save an incomplete but otherwise valid private draft."""

        def operation(repo, actor):
            fields = validation.product_fields(data)
            identifier = new_id()
            timestamp = now()
            repo.insert(
                "products",
                {
                    "id": identifier,
                    "seller_id": actor.id,
                    **fields,
                    "status": State.DRAFT,
                    "version": 1,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )
            return Change(
                dict(repo.get("products", identifier)), after=State.DRAFT, object_id=identifier
            )

        return self.perform(actor_id, "CREATE_DRAFT", None, operation)

    def edit(self, actor_id, product_id, data):
        """Published edits disappear from public queries until reviewed again."""

        def operation(repo, actor):
            row = owned(repo, actor.id, product_id)
            assert_version(row, data.get("version"))
            require(
                row["status"] in {State.DRAFT, State.OFFLINE, State.PUBLISHED},
                "当前状态不能修改商品",
            )
            require(repo.active_reservation(product_id) is None, "商品已有有效预约")
            fields = validation.product_fields(data)
            next_state = State.PENDING_REVIEW if row["status"] == State.PUBLISHED else row["status"]
            updated = {
                **fields,
                "status": next_state,
                "version": row["version"] + 1,
                "updated_at": now(),
            }
            if next_state == State.PENDING_REVIEW:
                self._validate_complete(repo, {**dict(row), **updated})
            repo.update("products", product_id, updated)
            if next_state == State.PENDING_REVIEW:
                self._review_snapshot(repo, product_id)
            return Change(dict(repo.get("products", product_id)), row["status"], next_state)

        return self.perform(actor_id, "EDIT_PRODUCT", product_id, operation)

    def delete(self, actor_id, product_id, version):
        """Delete an unsubmitted draft without deleting audit or review history."""
        keys = []

        def operation(repo, actor):
            row = owned(repo, actor.id, product_id)
            assert_version(row, version)
            require(row["status"] == State.DRAFT, "只能删除未提交草稿")
            require(
                repo.one("SELECT id FROM reviews WHERE product_id=?", (product_id,)) is None,
                "已有审核历史的草稿请保留",
            )
            keys.extend(
                image["storage_key"]
                for image in repo.all(
                    "SELECT storage_key FROM images WHERE product_id=?", (product_id,)
                )
            )
            repo.connection.execute("DELETE FROM products WHERE id=?", (product_id,))
            return Change({"deleted": True}, State.DRAFT, "DELETED")

        result = self.perform(actor_id, "DELETE_DRAFT", product_id, operation)
        for key in keys:
            self.image_store.remove(key)
        return result

    @staticmethod
    def _validate_complete(repo, row):
        """Apply submission rules to a persisted aggregate, including images."""
        data = dict(row)
        data["price"] = None if row["price_cents"] is None else f"{row['price_cents'] / 100:.2f}"
        validation.product_fields(data, complete=True)
        count = repo.one("SELECT count(*) FROM images WHERE product_id=?", (row["id"],))[0]
        require(1 <= count <= 5, "提交审核需要1至5张图片", 422, "VALIDATION")

    @staticmethod
    def _review_snapshot(repo, product_id):
        """An administrator reviews the submitted version, not mutable form data."""
        row = dict(repo.get("products", product_id))
        row["images"] = repo.all(
            "SELECT * FROM images WHERE product_id=? ORDER BY sort_order", (product_id,)
        )
        repo.insert(
            "reviews",
            {
                "id": new_id(),
                "product_id": product_id,
                "submission_version": row["version"],
                "snapshot_json": json.dumps(row, ensure_ascii=False),
                "decision": "PENDING",
                "created_at": now(),
            },
        )

    def submit(self, actor_id, product_id, version):
        """One submission version produces one review task."""

        def operation(repo, actor):
            row = owned(repo, actor.id, product_id)
            assert_version(row, version)
            require(row["status"] in {State.DRAFT, State.OFFLINE}, "当前状态不能提交审核")
            require(repo.active_reservation(product_id) is None, "商品已有有效预约")
            self._validate_complete(repo, row)
            repo.update(
                "products",
                product_id,
                {
                    "status": State.PENDING_REVIEW,
                    "version": row["version"] + 1,
                    "updated_at": now(),
                },
            )
            self._review_snapshot(repo, product_id)
            return Change(
                dict(repo.get("products", product_id)), row["status"], State.PENDING_REVIEW
            )

        return self.perform(actor_id, "SUBMIT_REVIEW", product_id, operation)

    def offline(self, actor_id, product_id, version):
        """A seller can withdraw only an unreserved published product."""

        def operation(repo, actor):
            row = owned(repo, actor.id, product_id)
            assert_version(row, version)
            require(row["status"] == State.PUBLISHED, "只有已发布且未预约商品可主动下架")
            require(repo.active_reservation(product_id) is None, "商品已有有效预约")
            repo.update(
                "products",
                product_id,
                {
                    "status": State.OFFLINE,
                    "version": row["version"] + 1,
                    "updated_at": now(),
                },
            )
            return Change(dict(repo.get("products", product_id)), row["status"], State.OFFLINE)

        return self.perform(actor_id, "OFFLINE_PRODUCT", product_id, operation)

    def upload(self, actor_id, product_id, data: bytes):
        """Validate and write an image while holding the aggregate write transaction."""
        saved = {}

        def operation(repo, actor):
            row = owned(repo, actor.id, product_id)
            require(row["status"] in {State.DRAFT, State.OFFLINE}, "先在草稿或下架状态维护图片")
            images = repo.all("SELECT sort_order FROM images WHERE product_id=?", (product_id,))
            require(len(images) < 5, "每件商品最多5张图片", 422, "VALIDATION")
            saved.update(self.image_store.save(data))
            used = {image["sort_order"] for image in images}
            position = next(index for index in range(1, 6) if index not in used)
            image_id = new_id()
            repo.insert(
                "images",
                {"id": image_id, "product_id": product_id, "sort_order": position, **saved},
            )
            repo.update(
                "products", product_id, {"version": row["version"] + 1, "updated_at": now()}
            )
            return Change(dict(repo.get("images", image_id)), row["status"], row["status"])

        try:
            return self.perform(actor_id, "ADD_IMAGE", product_id, operation)
        except Exception:
            if saved:
                self.image_store.remove(saved["storage_key"])
            raise

    def delete_image(self, actor_id, product_id, image_id):
        """Remove only an image belonging to an editable aggregate."""
        key = []

        def operation(repo, actor):
            row = owned(repo, actor.id, product_id)
            require(row["status"] in {State.DRAFT, State.OFFLINE}, "当前商品状态不能删除图片")
            image = repo.get("images", image_id)
            require(image["product_id"] == product_id, "图片归属不符", 403, "FORBIDDEN")
            key.append(image["storage_key"])
            repo.connection.execute("DELETE FROM images WHERE id=?", (image_id,))
            repo.update(
                "products", product_id, {"version": row["version"] + 1, "updated_at": now()}
            )
            return Change({"deleted": True}, row["status"], row["status"])

        result = self.perform(actor_id, "DELETE_IMAGE", product_id, operation)
        self.image_store.remove(key[0])
        return result

    def review(self, actor_id, review_id, decision, reason=""):
        """Apply the first valid review decision; old pages cannot replace it."""

        def operation(repo, actor):
            repo.admin(actor.id)
            require(decision in {"APPROVED", "REJECTED"}, "审核决定无效", 422, "VALIDATION")
            explanation = validation.text(reason, "驳回原因", 1000, required=decision == "REJECTED")
            review = repo.get("reviews", review_id)
            row = repo.get("products", review["product_id"])
            product = Product.from_row(row)
            require(
                review["decision"] == "PENDING"
                and product.status == State.PENDING_REVIEW
                and product.version == review["submission_version"],
                "审核任务已处理或版本已变化",
            )
            next_state = State.PUBLISHED if decision == "APPROVED" else State.DRAFT
            timestamp = now()
            repo.update(
                "reviews",
                review_id,
                {
                    "decision": decision,
                    "reason": explanation,
                    "reviewer_id": actor.id,
                    "handled_at": timestamp,
                },
            )
            repo.update(
                "products",
                product.id,
                {
                    "status": next_state,
                    "version": product.version + 1,
                    "updated_at": timestamp,
                    "first_published_at": row["first_published_at"]
                    or (timestamp if decision == "APPROVED" else None),
                },
            )
            return Change(
                dict(repo.get("products", product.id)),
                product.status,
                next_state,
                explanation,
                product.id,
            )

        return self.perform(actor_id, "REVIEW_PRODUCT", review_id, operation)
