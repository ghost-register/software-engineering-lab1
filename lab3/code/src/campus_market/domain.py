"""Domain vocabulary and errors shared by application services."""

from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from typing import Any


class ProductState(StrEnum):
    """The seven product states from the lab-two state diagram."""

    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    PUBLISHED = "PUBLISHED"
    RESERVED = "RESERVED"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    OFFLINE = "OFFLINE"


class ReservationState(StrEnum):
    """Reservation history is retained after terminal transitions."""

    PENDING = "PENDING_SELLER"
    CONFIRMED = "SELLER_CONFIRMED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"
    COMPLETED = "COMPLETED"


class DomainError(Exception):
    """An expected, safe-to-display business rejection."""

    def __init__(self, message: str, status: int = 422, code: str = "VALIDATION"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def require(condition: bool, message: str, status: int = 409, code: str = "CONFLICT"):
    """Reject a transition before it can change persisted state."""
    if not condition:
        raise DomainError(message, status, code)


@dataclass(frozen=True)
class User:
    """Public identity; password hashes and tokens never enter this object."""

    id: str
    username: str
    role: str
    status: str

    @classmethod
    def from_row(cls, row):
        """Read only the explicitly public identity columns."""
        return cls(**{field.name: row[field.name] for field in fields(cls)})

    def to_dict(self) -> dict:
        """Serialize the safe identity for views and API responses."""
        return asdict(self)


@dataclass(frozen=True)
class Product:
    """A product aggregate root represented using exact integer cents."""

    id: str
    seller_id: str
    status: str
    version: int
    title: str | None
    price_cents: int | None

    @classmethod
    def from_row(cls, row):
        """Construct the domain projection used by transition guards."""
        return cls(**{field.name: row[field.name] for field in fields(cls)})


@dataclass(frozen=True)
class Reservation:
    """The immutable price snapshot belongs to the reservation."""

    id: str
    product_id: str
    buyer_id: str
    status: str
    price_snapshot: int

    @classmethod
    def from_row(cls, row):
        """Convert a persisted reservation into a transition projection."""
        return cls(**{field.name: row[field.name] for field in fields(cls)})


@dataclass
class Change:
    """An operation result together with its success-audit state pair."""

    value: Any
    before: str | None = None
    after: str | None = None
    reason: str | None = None
    object_id: str | None = None


ACTIVE_RESERVATIONS = (ReservationState.PENDING, ReservationState.CONFIRMED)
CATEGORIES = ("数码电子", "书籍教材", "生活用品", "运动户外", "其他")
