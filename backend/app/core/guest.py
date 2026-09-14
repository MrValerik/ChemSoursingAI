"""Guest RBAC and an isolated, read-only synthetic workspace.

No production rows are copied. The anchor keeps the shared in-memory SQLite
database alive; each HTTP request gets its own connection and transaction.
"""
import re
from functools import lru_cache
from threading import Lock
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.models import Base, RFQ, User
from app.models.enums import UserRole, RFQStatus

GUEST_USERNAME = "__public_guest__"
_init_lock = Lock()

# Explicitly reviewed local reads. New endpoints are denied until reviewed;
# even GET can run a network lookup or disclose installation configuration.
_READ_PATHS = re.compile(
    r"(?:/auth/me|/rfq(?:/\d+(?:/(?:quotations|summary|recipients|communications|purchase-decision|purchase-history|analogs))?)?"
    r"|/search-runs(?:/\d+)?|/suppliers(?:/\d+/purchase-history)?"
    r"|/substances(?:/price-history|/\d+(?:/(?:requests|price-history|history|purchase-history))?)?)"
)


def guest_read_allowed(method: str, path: str) -> bool:
    return method == "GET" and _READ_PATHS.fullmatch(path.rstrip("/")) is not None


@lru_cache(maxsize=1)
def _workspace():
    from app.core.seed import seed_demo_workspace

    engine = create_engine(
        f"sqlite:///file:guest_{uuid4().hex}?mode=memory&cache=shared&uri=true",
        connect_args={"check_same_thread": False}, poolclass=NullPool,
    )
    anchor = engine.connect()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        owner = User(id=1, username=GUEST_USERNAME, full_name="Гость",
                     role=UserRole.BUYER, password_hash="!", is_active=True,
                     auto_dispatch_after_search=False)
        db.add(owner)
        db.commit()
        seed_demo_workspace(db)
        for cas, name, volume, application in [
            ("77-92-9", "Лимонная кислота", "2000 kg", "food production"),
            ("56-81-5", "Глицерин", "1000 kg", "cosmetic production"),
        ]:
            db.add(RFQ(cas=cas, name=name, volume=volume, application=application,
                       owner_id=owner.id, status=RFQStatus.DRAFT,
                       incoterms=["FCA"], channels=["email"],
                       search_countries=["Китай", "Индия"], verification={"demo": True}))
        owner.role = UserRole.GUEST
        db.commit()
    return factory, anchor


def guest_session():
    with _init_lock:
        factory, _anchor = _workspace()
    db = factory()
    # Defence in depth against accidental writes in a whitelisted read route.
    db.execute(text("PRAGMA query_only=ON"))
    return db
