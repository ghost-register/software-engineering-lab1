"""Reporting, exceptional closure and moderation without losing trade facts."""

from . import validation
from .database import new_id, now
from .domain import Change, ProductState as ProductStatus, ReservationState as Status, require
from .service import Service


class GovernanceService(Service):
    """The governance module coordinates transaction state in the same database."""

    def report(self, actor_id, product_id, reason):
        """One reporter can have only one pending report on a given product."""
        def operation(repo, actor):
            product = repo.get("products", product_id)
            require(product["seller_id"] != actor.id, "不能举报本人商品", 403, "FORBIDDEN")
            explanation = validation.text(reason, "举报原因", 1000)
            identifier = new_id()
            repo.insert("reports", {
                "id": identifier, "product_id": product_id, "reporter_id": actor.id,
                "reason": explanation, "status": "PENDING", "created_at": now(),
            })
            return Change(dict(repo.get("reports", identifier)), after="PENDING",
                          reason=explanation, object_id=identifier)
        return self.perform(actor_id, "REPORT_PRODUCT", product_id, operation)

    def resolve_report(self, actor_id, report_id, result):
        """Record an administrative conclusion while retaining the original reason."""
        def operation(repo, actor):
            repo.admin(actor.id)
            explanation = validation.text(result, "处理结论", 1000)
            report = repo.get("reports", report_id)
            require(report["status"] == "PENDING", "举报已处理")
            repo.update("reports", report_id, {
                "status": "RESOLVED", "result": explanation,
                "handled_by": actor.id, "handled_at": now(),
            })
            return Change(dict(repo.get("reports", report_id)), "PENDING", "RESOLVED", explanation)
        return self.perform(actor_id, "RESOLVE_REPORT", report_id, operation)

    def moderate(self, actor_id, product_id, reason):
        """Close active reservations atomically, but preserve completed trades."""
        def operation(repo, actor):
            repo.admin(actor.id)
            explanation = validation.text(reason, "违规处置原因", 1000)
            product = repo.get("products", product_id)
            if product["status"] == ProductStatus.COMPLETED:
                return Change(dict(product), ProductStatus.COMPLETED, ProductStatus.COMPLETED,
                              explanation)
            require(product["status"] != ProductStatus.DRAFT, "私人草稿不需要违规下架")
            repo.update("products", product_id, {
                "status": ProductStatus.OFFLINE, "version": product["version"] + 1,
                "updated_at": now(),
            })
            self.checkpoint("MODERATE", "after_product")
            active = repo.active_reservation(product_id)
            if active:
                repo.update("reservations", active["id"], {"status": Status.CLOSED, "reason": explanation})
            # A pending review for an offline product must no longer be actionable.
            repo.connection.execute(
                "UPDATE reviews SET decision='REJECTED',reason=?,reviewer_id=?,handled_at=? "
                "WHERE product_id=? AND decision='PENDING'", (explanation, actor.id, now(), product_id)
            )
            return Change(dict(repo.get("products", product_id)), product["status"],
                          ProductStatus.OFFLINE, explanation)
        return self.perform(actor_id, "MODERATE", product_id, operation)

    def decide_close(self, actor_id, request_id, approved, result):
        """Only a still-confirmed reservation can be closed after investigation."""
        def operation(repo, actor):
            repo.admin(actor.id)
            require(isinstance(approved, bool), "批准字段必须为布尔值", 422, "VALIDATION")
            explanation = validation.text(result, "处理原因", 1000)
            application = repo.get("close_requests", request_id)
            require(application["status"] == "PENDING", "异常申请已处理")
            reservation = repo.get("reservations", application["reservation_id"])
            product = repo.get("products", reservation["product_id"])
            if approved:
                require(reservation["status"] == Status.CONFIRMED
                        and product["status"] == ProductStatus.CONFIRMED,
                        "交易已完成或状态已变化，不能批准关闭")
                repo.update("products", product["id"], {
                    "status": ProductStatus.OFFLINE, "version": product["version"] + 1,
                    "updated_at": now(),
                })
                self.checkpoint("DECIDE_CLOSE", "after_product")
                repo.update("reservations", reservation["id"], {
                    "status": Status.CLOSED, "reason": explanation,
                })
            decision = "APPROVED" if approved else "REJECTED"
            repo.update("close_requests", request_id, {
                "status": decision, "result": explanation,
                "handled_by": actor.id, "handled_at": now(),
            })
            return Change(dict(repo.get("close_requests", request_id)), "PENDING", decision, explanation)
        return self.perform(actor_id, "DECIDE_CLOSE", request_id, operation)
