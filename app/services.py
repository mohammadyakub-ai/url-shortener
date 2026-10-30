"""Business logic for creating and resolving short links.

Keeping this layer free of FastAPI imports makes it directly unit-testable and
keeps the route functions thin.
"""

import secrets
import string

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Url

CODE_ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 7
MAX_CREATE_ATTEMPTS = 5

RESERVED_WORDS = frozenset({"health", "metrics", "stats", "docs", "openapi.json", "redoc"})


class CodeGenerationError(RuntimeError):
    """Raised when a unique, non-reserved code cannot be allocated."""


def _random_code(length: int = CODE_LENGTH) -> str:
    """Draw a code using a CSPRNG; never use ``random`` for shareable identifiers."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def generate_code() -> str:
    """Return a random 7-character base62 code that is not a reserved route word."""
    for _ in range(100):
        candidate = _random_code()
        if candidate not in RESERVED_WORDS:
            return candidate
    raise CodeGenerationError("unable to generate a non-reserved code")


def create_link(db: Session, target_url: str) -> Url:
    """Insert a link, retrying on unique-constraint collisions.

    The database constraint is the single source of truth for uniqueness. We
    rely on it rather than a pre-flight SELECT, which would race under
    concurrency.
    """
    for _ in range(MAX_CREATE_ATTEMPTS):
        url = Url(code=generate_code(), target_url=target_url)
        db.add(url)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(url)
        return url
    raise CodeGenerationError(
        f"failed to allocate a unique code after {MAX_CREATE_ATTEMPTS} attempts"
    )


def get_link(db: Session, code: str) -> Url | None:
    """Look up a link by its short code."""
    return db.scalar(select(Url).where(Url.code == code))


def increment_clicks(db: Session, url_id: int) -> None:
    """Atomically add one click without reading the row into Python.

    ``clicks = clicks + 1`` is evaluated by the database, so parallel redirects
    cannot overwrite each other.
    """
    db.execute(update(Url).where(Url.id == url_id).values(clicks=Url.clicks + 1))
    db.commit()


def resolve_link(db: Session, code: str) -> str | None:
    """Return the target URL for ``code`` and record a click, or None if unknown."""
    url = get_link(db, code)
    if url is None:
        return None
    increment_clicks(db, url.id)
    return url.target_url
