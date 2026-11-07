"""Normalize untrusted input at the application boundary."""

from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import re

from .domain import CATEGORIES, DomainError


def text(value, field: str, limit: int, *, required: bool = True) -> str:
    """Accept strings only and keep length checks consistent across services."""
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise DomainError(f"{field}必须是文本")
    value = value.strip()
    if required and not value:
        raise DomainError(f"请填写{field}")
    if len(value) > limit:
        raise DomainError(f"{field}不能超过{limit}字")
    return value


def username(value) -> str:
    """Registration uses the exact REQ-001 character and length constraints."""
    value = text(value, "用户名", 20)
    if not re.fullmatch(r"[A-Za-z0-9_]{4,20}", value):
        raise DomainError("用户名须为4至20位字母、数字或下划线")
    return value


def password(value) -> str:
    """Validate without trimming or silently changing a user's password."""
    if not isinstance(value, str) or not 8 <= len(value) <= 64:
        raise DomainError("密码须为8至64位并包含字母和数字")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"[0-9]", value):
        raise DomainError("密码须为8至64位并包含字母和数字")
    return value


def money(value, *, optional: bool = False) -> int | None:
    """Reject excess precision rather than rounding the customer's amount."""
    if value is None or value == "":
        if optional:
            return None
        raise DomainError("请填写价格")
    if isinstance(value, bool):
        raise DomainError("价格格式错误")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise DomainError("价格格式错误") from exc
    if not number.is_finite() or number.as_tuple().exponent < -2:
        raise DomainError("价格最多保留两位小数")
    if not Decimal("0.01") <= number <= Decimal("99999.99"):
        raise DomainError("价格须为0.01至99999.99元")
    return int(number * 100)


def integer(value, field: str, default: int, minimum: int, maximum: int) -> int:
    """Parse pagination and versions without accepting floats or booleans."""
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise DomainError(f"{field}必须是整数")
    result = int(value)
    if not minimum <= result <= maximum:
        raise DomainError(f"{field}超出允许范围")
    return result


def category(value, *, required: bool = False) -> str:
    """Use one shared category vocabulary in the form, query and database."""
    value = text(value, "分类", 40, required=required)
    if value and value not in CATEGORIES:
        raise DomainError("请选择有效分类")
    return value


def product_fields(data: dict, *, complete: bool = False) -> dict:
    """Drafts may be incomplete; every supplied field still must be valid."""
    return {
        "title": text(data.get("title"), "标题", 50, required=complete),
        "description": text(data.get("description"), "描述", 1000, required=complete),
        "category": category(data.get("category"), required=complete),
        "condition": text(data.get("condition"), "成色", 40, required=complete),
        "price_cents": money(data.get("price"), optional=not complete),
    }


def date_interval(start, end) -> tuple[str, str]:
    """Interpret dates in Asia/Shanghai and return a UTC half-open interval."""
    zone = timezone(timedelta(hours=8))
    try:
        left = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=zone)
        right = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=zone)
    except (ValueError, TypeError) as exc:
        raise DomainError("日期格式应为YYYY-MM-DD") from exc
    if left >= right:
        raise DomainError("结束日期必须晚于开始日期，结束日期不计入区间")
    return left.astimezone(timezone.utc).isoformat(), right.astimezone(timezone.utc).isoformat()


def request_key(value) -> str:
    """Keep idempotency keys bounded and printable for storage and lookup."""
    value = text(value, "请求标识", 100)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,100}", value):
        raise DomainError("请求标识须为8至100位字母、数字或_.:-")
    return value
