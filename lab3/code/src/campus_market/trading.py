"""Reservation idempotency, ownership and coupled state transitions."""

from datetime import datetime, timedelta, timezone
import hashlib
import json

from . import validation
from .database import new_id, now
from .domain import (
    Change,
    ProductState as ProductStatus,
    Reservation,
    ReservationState as Status,
    require,
)
from .service import Service


class TradingService(Service):
    """Every product/reservation transition runs under one writer transaction."""

    @staticmethod
    def _current(repo, reservation_id):
        reservation = dict(repo.get("reservations", reservation_id))
        projection = Reservation.from_row(reservation)
        product = repo.get("products", projection.product_id)
        return {"current_reservation": reservation, "product_status": product["status"]}

    @staticmethod
    def _set_product(repo, row, state):
        repo.update(
            "products",
            row["id"],
            {
                "status": state,
                "version": row["version"] + 1,
                "updated_at": now(),
            },
        )

    def reserve(self, actor_id, product_id, key):
        """Preserve the first successful result for at least twenty-four hours."""

        def operation(repo, actor):
            normalized_key = validation.request_key(key)
            fingerprint = hashlib.sha256(product_id.encode("utf-8")).hexdigest()
            cached = repo.one(
                "SELECT * FROM request_results WHERE user_id=? AND action='RESERVE' "
                "AND request_key=?",
                (actor.id, normalized_key),
            )
            if cached and cached["expires_at"] > now():
                require(cached["parameter_hash"] == fingerprint, "同一请求标识不能用于不同商品")
                original = json.loads(cached["response_json"])
                original.update(self._current(repo, cached["reservation_id"]))
                original["replayed"] = True
                return Change(original, object_id=product_id)
            if cached:
                repo.connection.execute(
                    "DELETE FROM request_results WHERE user_id=? AND action='RESERVE' "
                    "AND request_key=?",
                    (actor.id, normalized_key),
                )
            product = repo.get("products", product_id)
            require(product["seller_id"] != actor.id, "不能预约自己的商品", 403, "FORBIDDEN")
            require(product["status"] == ProductStatus.PUBLISHED, "商品已不可预约，请刷新状态")
            require(repo.active_reservation(product_id) is None, "该商品已有有效预约")
            reservation_id = new_id()
            timestamp = now()
            self._set_product(repo, product, ProductStatus.RESERVED)
            self.checkpoint("RESERVE", "after_product")
            repo.insert(
                "reservations",
                {
                    "id": reservation_id,
                    "product_id": product_id,
                    "buyer_id": actor.id,
                    "price_snapshot": product["price_cents"],
                    "status": Status.PENDING,
                    "created_at": timestamp,
                },
            )
            response = {"reservation": dict(repo.get("reservations", reservation_id))}
            expiry = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
            repo.insert(
                "request_results",
                {
                    "user_id": actor.id,
                    "action": "RESERVE",
                    "request_key": normalized_key,
                    "parameter_hash": fingerprint,
                    "reservation_id": reservation_id,
                    "response_json": json.dumps(response),
                    "created_at": timestamp,
                    "expires_at": expiry,
                },
            )
            response.update(self._current(repo, reservation_id))
            response["replayed"] = False
            return Change(response, ProductStatus.PUBLISHED, ProductStatus.RESERVED)

        return self.perform(actor_id, "RESERVE", product_id, operation)

    def cancel_or_reject(self, actor_id, reservation_id, *, reject=False, reason=""):
        """Buyer cancellation and seller rejection share the same pending guard."""
        action = "REJECT" if reject else "CANCEL"

        def operation(repo, actor):
            reservation = repo.get("reservations", reservation_id)
            product = repo.get("products", reservation["product_id"])
            owner = product["seller_id"] if reject else reservation["buyer_id"]
            require(actor.id == owner, "只能操作本人参与且身份对应的预约", 403, "FORBIDDEN")
            explanation = validation.text(reason, "拒绝原因", 1000, required=reject)
            next_state = Status.REJECTED if reject else Status.CANCELLED
            if reservation["status"] == next_state:
                return Change(dict(reservation), next_state, next_state, explanation)
            require(
                reservation["status"] == Status.PENDING
                and product["status"] == ProductStatus.RESERVED,
                "卖家已确认或预约状态已变化",
            )
            self._set_product(repo, product, ProductStatus.PUBLISHED)
            self.checkpoint(action, "after_product")
            repo.update(
                "reservations", reservation_id, {"status": next_state, "reason": explanation}
            )
            return Change(
                dict(repo.get("reservations", reservation_id)),
                Status.PENDING,
                next_state,
                explanation,
            )

        return self.perform(actor_id, action, reservation_id, operation)

    def confirm(self, actor_id, reservation_id):
        """Only the product seller can accept the buyer's pending reservation."""

        def operation(repo, actor):
            reservation = repo.get("reservations", reservation_id)
            product = repo.get("products", reservation["product_id"])
            require(product["seller_id"] == actor.id, "只有对应卖家可确认", 403, "FORBIDDEN")
            if reservation["status"] == Status.CONFIRMED:
                return Change(dict(reservation), Status.CONFIRMED, Status.CONFIRMED)
            require(
                reservation["status"] == Status.PENDING
                and product["status"] == ProductStatus.RESERVED,
                "预约状态已变化",
            )
            self._set_product(repo, product, ProductStatus.CONFIRMED)
            self.checkpoint("CONFIRM", "after_product")
            repo.update(
                "reservations",
                reservation_id,
                {
                    "status": Status.CONFIRMED,
                    "confirmed_at": now(),
                },
            )
            return Change(
                dict(repo.get("reservations", reservation_id)), Status.PENDING, Status.CONFIRMED
            )

        return self.perform(actor_id, "CONFIRM", reservation_id, operation)

    def complete(self, actor_id, reservation_id):
        """An accepted trade is completed by its buyer, never by its seller."""

        def operation(repo, actor):
            reservation = repo.get("reservations", reservation_id)
            product = repo.get("products", reservation["product_id"])
            require(reservation["buyer_id"] == actor.id, "只有预约买家可确认完成", 403, "FORBIDDEN")
            if reservation["status"] == Status.COMPLETED:
                return Change(dict(reservation), Status.COMPLETED, Status.COMPLETED)
            require(
                reservation["status"] == Status.CONFIRMED
                and product["status"] == ProductStatus.CONFIRMED,
                "预约尚未确认或已经关闭",
            )
            self._set_product(repo, product, ProductStatus.COMPLETED)
            self.checkpoint("COMPLETE", "after_product")
            repo.update(
                "reservations",
                reservation_id,
                {
                    "status": Status.COMPLETED,
                    "completed_at": now(),
                },
            )
            return Change(
                dict(repo.get("reservations", reservation_id)), Status.CONFIRMED, Status.COMPLETED
            )

        return self.perform(actor_id, "COMPLETE", reservation_id, operation)

    def request_close(self, actor_id, reservation_id, reason):
        """Participants request review; their application does not close a trade."""

        def operation(repo, actor):
            reservation = repo.get("reservations", reservation_id)
            product = repo.get("products", reservation["product_id"])
            require(
                actor.id in {reservation["buyer_id"], product["seller_id"]},
                "只有交易参与人可申请",
                403,
                "FORBIDDEN",
            )
            require(reservation["status"] == Status.CONFIRMED, "只能申请关闭已确认交易")
            explanation = validation.text(reason, "异常原因", 1000)
            identifier = new_id()
            repo.insert(
                "close_requests",
                {
                    "id": identifier,
                    "reservation_id": reservation_id,
                    "applicant_id": actor.id,
                    "reason": explanation,
                    "status": "PENDING",
                    "created_at": now(),
                },
            )
            return Change(
                dict(repo.get("close_requests", identifier)),
                after="PENDING",
                reason=explanation,
                object_id=identifier,
            )

        return self.perform(actor_id, "REQUEST_CLOSE", reservation_id, operation)
