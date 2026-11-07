"""Explicit, idempotent demonstration data for local course presentations."""

from io import BytesIO

from PIL import Image, ImageDraw

from .database import new_id, now
from .repository import Repository

DEMO_PASSWORD = "Campus2026"


def seed_demo(services):
    """Provision demo identities and publish products through real services."""
    database = services["auth"].database
    with database.read() as connection:
        repo = Repository(connection)
        if repo.one("SELECT id FROM users WHERE username='admin_demo'"):
            return {"created": False, "message": "演示数据已存在，不重复创建"}
    auth = services["auth"]
    admin = auth.create_admin("admin_demo", DEMO_PASSWORD)
    seller = auth.register({"username": "seller_demo", "password": DEMO_PASSWORD})
    buyer = auth.register({"username": "buyer_demo", "password": DEMO_PASSWORD})
    auth.register({"username": "other_demo", "password": DEMO_PASSWORD})
    examples = [
        ("九成新机械键盘", "数码电子", "128.00", (27, 99, 80)),
        ("高等数学教材上下册", "书籍教材", "35.00", (158, 107, 64)),
        ("可调节护眼台灯", "生活用品", "58.00", (78, 104, 137)),
        ("羽毛球拍一对", "运动户外", "80.00", (129, 111, 147)),
    ]
    for index, (title, category, price, color) in enumerate(examples):
        product = services["products"].create(seller["id"], {
            "title": title, "description": "演示商品：正常使用，有轻微使用痕迹；校内线下交付。",
            "category": category, "condition": "9成新", "price": price,
        })
        image = Image.new("RGB", (600, 360), color)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((170, 90, 430, 270), radius=20, fill=(225, 235, 225))
        draw.text((265, 165), f"DEMO {index + 1}", fill=color)
        data = BytesIO()
        image.save(data, format="PNG")
        services["products"].upload(seller["id"], product["id"], data.getvalue())
        product = services["queries"].product(seller["id"], product["id"])
        services["products"].submit(seller["id"], product["id"], product["version"])
        review = next(item for item in services["queries"].admin_list(admin["id"], "reviews")
                      if item["product_id"] == product["id"])
        services["products"].review(admin["id"], review["id"], "APPROVED")
    return {"created": True, "admin": admin["username"], "seller": seller["username"],
            "buyer": buyer["username"], "password": DEMO_PASSWORD}


def seed_performance(database, count=10000):
    """Populate a separate benchmark database without touching presentation data."""
    with database.transaction() as connection:
        repo = Repository(connection)
        seller = repo.one("SELECT id FROM users WHERE role='USER' LIMIT 1")
        if seller is None:
            raise ValueError("先创建基准测试用户")
        existing = repo.one("SELECT count(*) FROM products")[0]
        for index in range(existing, count):
            timestamp = now()
            repo.insert("products", {
                "id": new_id(), "seller_id": seller["id"], "title": f"基准商品{index}",
                "description": "组合查询性能数据", "category": "数码电子" if index % 2 else "书籍教材",
                "condition": "9成新", "price_cents": 100 + index % 10000,
                "status": "PUBLISHED", "version": 1, "first_published_at": timestamp,
                "created_at": timestamp, "updated_at": timestamp,
            })
    return count
