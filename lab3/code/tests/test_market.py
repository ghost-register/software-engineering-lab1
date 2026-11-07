"""Behavioral tests for permission, lifecycle, repeat requests and rollback."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from PIL import Image

from campus_market import validation
from campus_market.domain import DomainError
from campus_market.repository import Repository
from campus_market.web import create_app


class MarketCase(unittest.TestCase):
    """Each test gets real on-disk SQLite storage and independent connections."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = create_app(self.temporary.name, testing=True)
        self.services = self.app.extensions["market"]
        self.db = self.app.extensions["database"]
        self.auth = self.services["auth"]
        self.products = self.services["products"]
        self.trading = self.services["trading"]
        self.governance = self.services["governance"]
        self.queries = self.services["queries"]
        self.admin = self.auth.create_admin("admin_test", "Testing2026")
        self.seller = self.account("seller_test")
        self.buyer = self.account("buyer_test")
        self.other = self.account("other_test")

    def account(self, username):
        return self.auth.register({"username": username, "password": "Testing2026"})

    @staticmethod
    def image_bytes():
        output = BytesIO()
        Image.new("RGB", (20, 20), "green").save(output, format="PNG")
        return output.getvalue()

    def published(self, price="12.34", title="测试机械键盘"):
        product = self.products.create(self.seller["id"], {
            "title": title, "description": "正常使用，校内线下交付。", "category": "数码电子",
            "condition": "9成新", "price": price,
        })
        self.products.upload(self.seller["id"], product["id"], self.image_bytes())
        product = self.queries.product(self.seller["id"], product["id"])
        self.products.submit(self.seller["id"], product["id"], product["version"])
        review = next(row for row in self.queries.admin_list(self.admin["id"], "reviews")
                      if row["product_id"] == product["id"])
        return self.products.review(self.admin["id"], review["id"], "APPROVED")

    def reservation(self, product):
        return self.trading.reserve(self.buyer["id"], product["id"], "request-key-0001")["reservation"]

    def rows(self, table):
        with self.db.read() as connection:
            return Repository(connection).all(f"SELECT * FROM {table}")

    def test_registration_role_and_case_insensitive_uniqueness(self):
        with self.assertRaises(DomainError):
            self.auth.register({"username": "hack_user", "password": "Testing2026", "role": "ADMIN"})
        with self.assertRaises(DomainError):
            self.account("SELLER_TEST")
        self.assertTrue(all("password_hash" not in user for user in [self.seller, self.buyer]))
        stored = self.rows("users")[0]["password_hash"]
        self.assertTrue(stored.startswith("scrypt:"))
        self.assertNotIn("Testing2026", stored)

    def test_sessions_logout_and_disabled_account(self):
        logged = self.auth.login({"username": "buyer_test", "password": "Testing2026"})
        self.assertIsNotNone(self.auth.authenticate(logged["token"]))
        self.auth.logout(logged["token"])
        self.assertIsNone(self.auth.authenticate(logged["token"]))
        logged = self.auth.login({"username": "buyer_test", "password": "Testing2026"})
        self.auth.disable(self.admin["id"], self.buyer["id"], "测试停用")
        self.assertIsNone(self.auth.authenticate(logged["token"]))
        with self.assertRaises(DomainError):
            self.auth.login({"username": "buyer_test", "password": "Testing2026"})
        with self.assertRaises(DomainError):
            self.products.create(self.buyer["id"], {})

    def test_money_and_validation_edges(self):
        for bad in ["0", "-1", "0.001", "1.001", "NaN", "Infinity", "100000", True]:
            with self.subTest(bad=bad), self.assertRaises(DomainError):
                validation.money(bad)
        self.assertEqual(validation.money("0.01"), 1)
        self.assertEqual(validation.money("99999.99"), 9999999)
        self.assertEqual(validation.money("12.30"), 1230)
        with self.assertRaises(DomainError):
            validation.integer("1.5", "page", 1, 1, 10)

    def test_incomplete_draft_and_complete_submission(self):
        product = self.products.create(self.seller["id"], {})
        self.assertEqual(product["status"], "DRAFT")
        self.assertEqual(self.queries.products(None, {})["total"], 0)
        with self.assertRaises(DomainError):
            self.products.submit(self.seller["id"], product["id"], product["version"])
        with self.assertRaises(DomainError):
            self.products.delete(self.other["id"], product["id"], product["version"])
        self.products.delete(self.seller["id"], product["id"], product["version"])
        self.assertEqual(self.rows("products"), [])

    def test_actual_image_validation_and_five_image_limit(self):
        product = self.products.create(self.seller["id"], {})
        with self.assertRaises(DomainError):
            self.products.upload(self.seller["id"], product["id"], b"not really a png")
        with self.assertRaises(DomainError):
            self.products.upload(self.seller["id"], product["id"], b"x" * (5 * 1024 * 1024 + 1))
        for _ in range(5):
            self.products.upload(self.seller["id"], product["id"], self.image_bytes())
        with self.assertRaises(DomainError):
            self.products.upload(self.seller["id"], product["id"], self.image_bytes())
        self.assertEqual(len(self.rows("images")), 5)

    def test_rejected_review_retains_reason_and_resubmits(self):
        product = self.published()
        edited = self.products.edit(self.seller["id"], product["id"], {
            "title": "重新拍照的键盘", "description": "完整说明", "category": "数码电子",
            "condition": "8成新", "price": "12.34", "version": product["version"],
        })
        self.assertEqual(edited["status"], "PENDING_REVIEW")
        review = self.queries.admin_list(self.admin["id"], "reviews")[0]
        with self.assertRaises(DomainError):
            self.products.review(self.admin["id"], review["id"], "REJECTED", "")
        rejected = self.products.review(self.admin["id"], review["id"], "REJECTED", "补充背面照片")
        listing = self.queries.products(self.seller["id"], {"scope": "mine"})
        self.assertEqual(listing["items"][0]["review_summary"], "补充背面照片")
        self.products.submit(self.seller["id"], product["id"], rejected["version"])
        review = self.queries.admin_list(self.admin["id"], "reviews")[0]
        approved = self.products.review(self.admin["id"], review["id"], "APPROVED")
        self.assertEqual(approved["first_published_at"], product["first_published_at"])

    def test_expired_edit_and_review_cannot_overwrite(self):
        product = self.published()
        review = self.queries.admin_list(self.admin["id"], "reviews")[0]
        with self.assertRaises(DomainError):
            self.products.review(self.admin["id"], review["id"], "REJECTED", "旧页面")
        with self.assertRaises(DomainError):
            self.products.offline(self.seller["id"], product["id"], product["version"] - 1)

    def test_query_combination_visibility_and_recommendation_separation(self):
        product = self.published()
        self.products.create(self.seller["id"], {"title": "隐藏草稿"})
        result = self.queries.products(None, {"keyword": "键盘", "category": "数码电子",
                                             "minimum": "12.34", "maximum": "12.34"})
        self.assertEqual([row["id"] for row in result["items"]], [product["id"]])
        empty = self.queries.products(None, {"keyword": "绝不匹配", "maximum": "1"})
        self.assertEqual(empty["total"], 0)
        self.assertEqual(len(self.queries.recommendations()), 1)
        with self.assertRaises(DomainError):
            self.queries.products(self.buyer["id"], {"scope": "all"})
        with self.assertRaises(DomainError):
            self.queries.products(None, {"minimum": "20", "maximum": "1"})
        self.assertEqual(self.queries.products(None, {"keyword": "%' OR 1=1 --"})["total"], 0)

    def test_idempotency_conflict_cancelled_old_key_and_new_key(self):
        product = self.published()
        result = self.trading.reserve(self.buyer["id"], product["id"], "same-key-0001")
        replay = self.trading.reserve(self.buyer["id"], product["id"], "same-key-0001")
        self.assertEqual(result["reservation"], replay["reservation"])
        other = self.published(title="另一件商品")
        with self.assertRaises(DomainError):
            self.trading.reserve(self.buyer["id"], other["id"], "same-key-0001")
        self.trading.cancel_or_reject(self.buyer["id"], result["reservation"]["id"])
        replay = self.trading.reserve(self.buyer["id"], product["id"], "same-key-0001")
        self.assertEqual(replay["current_reservation"]["status"], "CANCELLED")
        self.assertEqual(replay["product_status"], "PUBLISHED")
        newer = self.trading.reserve(self.buyer["id"], product["id"], "new-key-00002")
        self.assertNotEqual(newer["reservation"]["id"], result["reservation"]["id"])
        self.assertEqual(len(self.rows("reservations")), 2)
        recovered = self.queries.request_result(self.buyer["id"], "same-key-0001")
        self.assertEqual(recovered["current"]["status"], "CANCELLED")
        with self.assertRaises(DomainError):
            self.queries.request_result(self.other["id"], "same-key-0001")

    def test_price_snapshot_completion_statistics_and_no_double_count(self):
        product = self.published(price="12.34")
        reservation = self.reservation(product)
        with self.assertRaises(DomainError):
            self.products.offline(self.seller["id"], product["id"], product["version"])
        self.trading.confirm(self.seller["id"], reservation["id"])
        with self.assertRaises(DomainError):
            self.trading.cancel_or_reject(self.buyer["id"], reservation["id"])
        with self.assertRaises(DomainError):
            self.trading.complete(self.seller["id"], reservation["id"])
        self.trading.complete(self.buyer["id"], reservation["id"])
        self.trading.complete(self.buyer["id"], reservation["id"])
        stats = self.queries.statistics(self.admin["id"], {"start": "2020-01-01", "end": "2100-01-01"})
        self.assertEqual((stats["first_published"], stats["completed_trades"], stats["amount_cents"]),
                         (1, 1, 1234))
        self.governance.moderate(self.admin["id"], product["id"], "成交后追加违规记录")
        self.assertEqual(self.rows("products")[0]["status"], "COMPLETED")
        self.assertEqual(self.rows("reservations")[0]["status"], "COMPLETED")

    def test_reporting_duplicate_resolution_and_ownership(self):
        product = self.published()
        with self.assertRaises(DomainError):
            self.governance.report(self.seller["id"], product["id"], "本人商品")
        report = self.governance.report(self.buyer["id"], product["id"], "描述不符")
        with self.assertRaises(DomainError):
            self.governance.report(self.buyer["id"], product["id"], "重复举报")
        self.assertEqual(self.queries.reports(self.other["id"]), [])
        self.governance.resolve_report(self.admin["id"], report["id"], "已核查并联系卖家")
        self.governance.report(self.buyer["id"], product["id"], "后续新问题")
        self.assertEqual(len(self.queries.reports(self.buyer["id"])), 2)

    def test_approved_exception_closes_both_objects(self):
        product = self.published()
        reservation = self.reservation(product)
        self.trading.confirm(self.seller["id"], reservation["id"])
        request = self.trading.request_close(self.buyer["id"], reservation["id"], "无法线下交付")
        self.assertEqual(self.rows("products")[0]["status"], "CONFIRMED")
        self.governance.decide_close(self.admin["id"], request["id"], True, "确认无法交付")
        self.assertEqual(self.rows("products")[0]["status"], "OFFLINE")
        self.assertEqual(self.rows("reservations")[0]["status"], "CLOSED")

    def test_completed_trade_cannot_be_closed_by_stale_application(self):
        product = self.published()
        reservation = self.reservation(product)
        self.trading.confirm(self.seller["id"], reservation["id"])
        request = self.trading.request_close(self.buyer["id"], reservation["id"], "交付曾有疑问")
        self.trading.complete(self.buyer["id"], reservation["id"])
        with self.assertRaises(DomainError):
            self.governance.decide_close(self.admin["id"], request["id"], True, "旧页面批准")
        self.assertEqual(self.rows("products")[0]["status"], "COMPLETED")

    def test_twenty_buyers_ten_rounds_unique_winner(self):
        buyers = [self.account(f"concurrent_{index:02}") for index in range(20)]
        for round_number in range(10):
            product = self.published(title=f"竞争商品{round_number}")
            def attempt(buyer):
                try:
                    self.trading.reserve(buyer["id"], product["id"], f"round-{round_number:02}-request")
                    return True
                except DomainError:
                    return False
            with ThreadPoolExecutor(max_workers=20) as pool:
                winners = list(pool.map(attempt, buyers))
            self.assertEqual(sum(winners), 1)
            with self.db.read() as connection:
                count = connection.execute("SELECT count(*) FROM reservations WHERE product_id=? "
                                           "AND status='PENDING_SELLER'", (product["id"],)).fetchone()[0]
            self.assertEqual(count, 1)

    def test_cancel_confirm_race_has_one_legal_result(self):
        product = self.published()
        reservation = self.reservation(product)
        def attempt(action):
            try:
                if action == "cancel":
                    return self.trading.cancel_or_reject(self.buyer["id"], reservation["id"])["status"]
                return self.trading.confirm(self.seller["id"], reservation["id"])["status"]
            except DomainError:
                return "CONFLICT"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ["cancel", "confirm"]))
        self.assertIn("CONFLICT", results)
        state = self.rows("reservations")[0]["status"]
        expected = "PUBLISHED" if state == "CANCELLED" else "CONFIRMED"
        self.assertEqual(self.rows("products")[0]["status"], expected)

    def test_ten_injected_failures_roll_back_both_objects(self):
        for action in ["RESERVE", "CONFIRM", "COMPLETE", "CANCEL", "MODERATE"]:
            for repetition in range(2):
                product = self.published(title=f"故障-{action}-{repetition}")
                reservation = None if action == "RESERVE" else self.reservation(product)
                if action == "COMPLETE":
                    self.trading.confirm(self.seller["id"], reservation["id"])
                before = self.queries.product(self.seller["id"], product["id"])["status"]
                service = self.governance if action == "MODERATE" else self.trading
                def fail(current_action, point):
                    if current_action == action and point == "after_product":
                        raise RuntimeError("injected failure")
                service.fault_hook = fail
                try:
                    with self.assertRaises(RuntimeError):
                        if action == "RESERVE":
                            service.reserve(self.buyer["id"], product["id"], "fault-test-request")
                        elif action == "CONFIRM":
                            service.confirm(self.seller["id"], reservation["id"])
                        elif action == "COMPLETE":
                            service.complete(self.buyer["id"], reservation["id"])
                        elif action == "CANCEL":
                            service.cancel_or_reject(self.buyer["id"], reservation["id"])
                        else:
                            service.moderate(self.admin["id"], product["id"], "故障测试")
                finally:
                    service.fault_hook = None
                self.assertEqual(self.queries.product(self.seller["id"], product["id"])["status"], before)
                if reservation:
                    current = self.queries.reservation(self.buyer["id"], reservation["id"])
                    self.assertEqual(current["status"], "SELLER_CONFIRMED" if action == "COMPLETE"
                                     else "PENDING_SELLER")
                else:
                    with self.db.read() as connection:
                        self.assertEqual(connection.execute("SELECT count(*) FROM reservations "
                                                            "WHERE product_id=?", (product["id"],)).fetchone()[0], 0)
        failures = [row for row in self.rows("audit_logs") if row["result"] == "FAILURE"]
        self.assertEqual(len(failures), 10)

    def test_foreign_keys_unique_index_and_backup(self):
        with self.assertRaises(sqlite3.IntegrityError), self.db.transaction() as connection:
            connection.execute("INSERT INTO sessions VALUES ('hash','missing','csrf','2099')")
        product = self.published()
        reservation = self.reservation(product)
        with self.assertRaises(sqlite3.IntegrityError), self.db.transaction() as connection:
            connection.execute("INSERT INTO reservations SELECT 'duplicate',product_id,buyer_id,"
                               "price_snapshot,status,reason,created_at,confirmed_at,completed_at "
                               "FROM reservations WHERE id=?", (reservation["id"],))
        target = Path(self.temporary.name) / "backup.sqlite3"
        self.db.backup(target)
        with sqlite3.connect(target) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_audits_never_contain_password_or_active_session_token(self):
        logged = self.auth.login({"username": "buyer_test", "password": "Testing2026"})
        with self.assertRaises(DomainError):
            self.auth.login({"username": "buyer_test", "password": "Wrong2026"})
        logs = json.dumps(self.rows("audit_logs"))
        self.assertNotIn("Testing2026", logs)
        self.assertNotIn("Wrong2026", logs)
        self.assertNotIn(logged["token"], logs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
