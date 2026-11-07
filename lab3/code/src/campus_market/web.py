"""Flask transport: request decoding, CSRF protection and view composition."""

from pathlib import Path
import secrets

from flask import Flask, g, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from .auth import AuthService
from .database import Database
from .domain import CATEGORIES, DomainError, require
from .governance import GovernanceService
from .images import ImageStore, MAX_IMAGE_BYTES
from .products import ProductService
from .queries import QueryService
from .repository import Repository
from .trading import TradingService


def body() -> dict:
    """Do not let lists, malformed JSON or implicit types reach the services."""
    data = request.get_json(silent=True)
    require(isinstance(data, dict), "请求正文必须是JSON对象", 422, "VALIDATION")
    return data


def actor_id():
    """The actor comes only from the server-validated session cookie."""
    require(g.identity is not None, "请先登录", 401, "UNAUTHENTICATED")
    return g.identity["user"]["id"]


def current_id():
    """Public views may be rendered without an authenticated actor."""
    return g.identity["user"]["id"] if g.identity else None


def create_app(data_dir=None, *, testing=False):
    """Build an isolated application with injectable local data paths."""
    app = Flask(__name__)
    directory = Path(data_dir or "instance").resolve()
    app.config.update(TESTING=testing, MAX_CONTENT_LENGTH=MAX_IMAGE_BYTES + 256 * 1024)
    database = Database(directory / "market.sqlite3")
    database.initialize()
    store = ImageStore(directory / "uploads")
    services = {
        "auth": AuthService(database), "products": ProductService(database, store),
        "trading": TradingService(database), "governance": GovernanceService(database),
        "queries": QueryService(database),
    }
    app.extensions["market"] = services
    app.extensions["database"] = database
    register_security(app, services["auth"])
    register_api(app, services, store)
    register_views(app, services)
    return app


def register_security(app, auth):
    """Protect browser writes and keep failures structured and safe to display."""
    @app.before_request
    def authenticate_request():
        g.identity = auth.authenticate(request.cookies.get("market_session"))
        g.guest_csrf = request.cookies.get("guest_csrf") or secrets.token_urlsafe(32)
        g.csrf = g.identity["csrf_token"] if g.identity else g.guest_csrf
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            provided = request.headers.get("X-CSRF-Token", "")
            require(bool(provided) and secrets.compare_digest(provided, g.csrf),
                    "页面校验已失效，请刷新后重试", 403, "CSRF")

    @app.after_request
    def secure_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        response.headers["Cache-Control"] = "no-store"
        if not request.cookies.get("guest_csrf"):
            response.set_cookie("guest_csrf", g.guest_csrf, httponly=True, samesite="Lax")
        return response

    @app.errorhandler(DomainError)
    def business_error(error):
        if request.path.startswith("/api/"):
            return jsonify(error={"code": error.code, "message": error.message}), error.status
        return render_template("error.html", message=error.message), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        message = "上传内容过大" if error.code == 413 else "请求地址或方法无效"
        if request.path.startswith("/api/"):
            return jsonify(error={"code": "HTTP_ERROR", "message": message}), error.code
        return render_template("error.html", message=message), error.code

    @app.context_processor
    def shared_context():
        return {"identity": g.identity, "csrf_token": g.csrf, "categories": CATEGORIES}

    @app.template_filter("yuan")
    def yuan(value):
        return "—" if value is None else f"¥{value // 100}.{value % 100:02}"


def register_api(app, services, store):
    """Transport handlers delegate business decisions to the application layer."""
    auth = services["auth"]
    products = services["products"]
    trading = services["trading"]
    governance = services["governance"]
    queries = services["queries"]

    @app.get("/api/session")
    def session_info():
        return jsonify(user=g.identity["user"] if g.identity else None, csrf_token=g.csrf)

    @app.post("/api/auth/register")
    def register():
        return jsonify(auth.register(body())), 201

    @app.post("/api/auth/login")
    def login():
        result = auth.login(body())
        response = jsonify(user=result["user"], csrf_token=result["csrf_token"])
        response.set_cookie("market_session", result["token"], httponly=True,
                            samesite="Lax", max_age=8 * 3600)
        return response

    @app.delete("/api/auth/session")
    def logout():
        actor_id()
        auth.logout(request.cookies.get("market_session", ""))
        response = jsonify(logged_out=True)
        response.delete_cookie("market_session")
        return response

    @app.get("/api/products")
    def search():
        return jsonify(queries.products(current_id(), request.args))

    @app.get("/api/recommendations")
    def recommendations():
        return jsonify(items=queries.recommendations())

    @app.post("/api/products")
    def create_product():
        return jsonify(products.create(actor_id(), body())), 201

    @app.get("/api/products/<identifier>")
    def product_detail(identifier):
        return jsonify(queries.product(current_id(), identifier))

    @app.patch("/api/products/<identifier>")
    def edit_product(identifier):
        return jsonify(products.edit(actor_id(), identifier, body()))

    @app.delete("/api/products/<identifier>")
    def delete_product(identifier):
        return jsonify(products.delete(actor_id(), identifier, body().get("version")))

    @app.post("/api/products/<identifier>/submit")
    def submit_product(identifier):
        return jsonify(products.submit(actor_id(), identifier, body().get("version")))

    @app.post("/api/products/<identifier>/offline")
    def offline_product(identifier):
        return jsonify(products.offline(actor_id(), identifier, body().get("version")))

    @app.post("/api/products/<identifier>/images")
    def upload_image(identifier):
        user_id = actor_id()
        upload = request.files.get("image")
        require(upload is not None, "请选择图片", 422, "VALIDATION")
        return jsonify(products.upload(user_id, identifier, upload.read(MAX_IMAGE_BYTES + 1))), 201

    @app.delete("/api/products/<identifier>/images/<image_id>")
    def delete_image(identifier, image_id):
        return jsonify(products.delete_image(actor_id(), identifier, image_id))

    @app.get("/api/images/<image_id>")
    def image_file(image_id):
        with app.extensions["database"].read() as connection:
            image = Repository(connection).get("images", image_id)
        queries.product(current_id(), image["product_id"])
        return send_file(store.path(image["storage_key"]), mimetype=image["content_type"])

    @app.post("/api/products/<identifier>/reservations")
    def reserve(identifier):
        return jsonify(trading.reserve(actor_id(), identifier, body().get("request_key"))), 201

    @app.get("/api/reservations")
    def reservations():
        return jsonify(items=queries.reservations(actor_id(), request.args.get("side", "buyer")))

    @app.get("/api/reservations/<identifier>")
    def reservation(identifier):
        return jsonify(queries.reservation(actor_id(), identifier))

    @app.get("/api/requests/<key>")
    def request_result(key):
        return jsonify(queries.request_result(actor_id(), key))

    @app.post("/api/reservations/<identifier>/cancel")
    def cancel(identifier):
        return jsonify(trading.cancel_or_reject(actor_id(), identifier))

    @app.post("/api/reservations/<identifier>/reject")
    def reject(identifier):
        return jsonify(trading.cancel_or_reject(actor_id(), identifier, reject=True,
                                               reason=body().get("reason")))

    @app.post("/api/reservations/<identifier>/confirm")
    def confirm(identifier):
        return jsonify(trading.confirm(actor_id(), identifier))

    @app.post("/api/reservations/<identifier>/complete")
    def complete(identifier):
        return jsonify(trading.complete(actor_id(), identifier))

    @app.post("/api/reservations/<identifier>/close-requests")
    def close_request(identifier):
        return jsonify(trading.request_close(actor_id(), identifier, body().get("reason"))), 201

    @app.post("/api/products/<identifier>/reports")
    def report(identifier):
        return jsonify(governance.report(actor_id(), identifier, body().get("reason"))), 201

    @app.get("/api/reports")
    def reports():
        return jsonify(items=queries.reports(actor_id()))

    @app.get("/api/admin/lists/<kind>")
    def admin_lists(kind):
        return jsonify(items=queries.admin_list(actor_id(), kind))

    @app.post("/api/admin/reviews/<identifier>/decision")
    def review(identifier):
        data = body()
        return jsonify(products.review(actor_id(), identifier, data.get("decision"),
                                       data.get("reason", "")))

    @app.post("/api/admin/products/<identifier>/moderate")
    def moderate(identifier):
        return jsonify(governance.moderate(actor_id(), identifier, body().get("reason")))

    @app.post("/api/admin/reports/<identifier>/resolve")
    def resolve_report(identifier):
        return jsonify(governance.resolve_report(actor_id(), identifier, body().get("result")))

    @app.post("/api/admin/close-requests/<identifier>/decision")
    def decide_close(identifier):
        data = body()
        return jsonify(governance.decide_close(actor_id(), identifier, data.get("approved"),
                                               data.get("result")))

    @app.post("/api/admin/users/<identifier>/disable")
    def disable_account(identifier):
        return jsonify(auth.disable(actor_id(), identifier, body().get("reason")))

    @app.get("/api/admin/logs")
    def audit_logs():
        return jsonify(queries.audit_logs(actor_id(), request.args))

    @app.get("/api/admin/statistics")
    def statistics():
        return jsonify(queries.statistics(actor_id(), request.args))


def register_views(app, services):
    """Desktop browser views reuse the same query policies as the JSON API."""
    queries = services["queries"]

    @app.get("/")
    def market():
        results = queries.products(current_id(), request.args)
        other = queries.recommendations() if results["total"] == 0 else []
        return render_template("market.html", results=results, recommendations=other)

    @app.get("/auth/<mode>")
    def auth_page(mode):
        require(mode in {"login", "register"}, "页面不存在", 404, "NOT_FOUND")
        return render_template("auth.html", mode=mode)

    @app.get("/products/new")
    def new_product():
        actor_id()
        return render_template("editor.html", product=None)

    @app.get("/products/<identifier>")
    def product_page(identifier):
        return render_template("product.html", product=queries.product(current_id(), identifier))

    @app.get("/products/<identifier>/edit")
    def product_editor(identifier):
        user_id = actor_id()
        product = queries.product(user_id, identifier)
        require(product["seller_id"] == user_id, "只能编辑本人商品", 403, "FORBIDDEN")
        return render_template("editor.html", product=product)

    @app.get("/selling")
    def selling():
        actor_id()
        return render_template("selling.html", results=queries.products(current_id(), {
            **request.args, "scope": "mine", "page_size": "100"}))

    @app.get("/reservations")
    def reservation_list():
        return render_template("reservations.html", items=queries.reservations(
            actor_id(), request.args.get("side", "buyer")), detail=None)

    @app.get("/reservations/<identifier>")
    def reservation_page(identifier):
        return render_template("reservations.html", detail=queries.reservation(actor_id(), identifier),
                               items=[])

    @app.get("/reports")
    def own_reports():
        return render_template("records.html", title="我的举报记录", items=queries.reports(actor_id()))

    @app.get("/admin")
    def admin_page():
        user_id = actor_id()
        return render_template("admin.html", users=queries.admin_list(user_id, "users"),
                               reviews=queries.admin_list(user_id, "reviews"),
                               reports=queries.reports(user_id),
                               close_requests=queries.admin_list(user_id, "close_requests"))
