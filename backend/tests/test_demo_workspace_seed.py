from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.seed import seed_demo_workspace
from app.models import (
    Base,
    Communication,
    EvidenceClaim,
    Quotation,
    RFQ,
    RfqRecipient,
    SearchRun,
    SourceDocument,
    Supplier,
    User,
)
from app.models.enums import RFQStatus, UserRole
from app.services.communication_history import list_communication_overview
from app.services.quotation_service import build_summary


def test_demo_workspace_seed_is_ready_and_idempotent() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        db.add(
            User(
                username="demo-buyer",
                full_name="Demo Buyer",
                role=UserRole.BUYER,
                password_hash="not-used-in-this-test",
            )
        )
        db.commit()

        seed_demo_workspace(db)
        seed_demo_workspace(db)

        rfq = db.scalar(select(RFQ))
        assert rfq is not None
        assert rfq.name == "Ацетилсалициловая кислота, 500 кг"
        assert rfq.status == RFQStatus.SUMMARIZED
        assert rfq.owner is not None
        assert rfq.verification and rfq.verification["demo"] is True

        assert db.scalar(select(func.count(RFQ.id))) == 1
        assert db.scalar(select(func.count(Supplier.id))) == 3
        assert db.scalar(select(func.count(RfqRecipient.id))) == 3
        assert db.scalar(select(func.count(Communication.id))) == 12
        assert db.scalar(select(func.count(Quotation.id))) == 3
        assert db.scalar(select(func.count(SearchRun.id))) == 1
        assert db.scalar(select(func.count(SourceDocument.id))) == 3
        assert db.scalar(select(func.count(EvidenceClaim.id))) == 18

        search_run = db.scalar(select(SearchRun))
        assert search_run is not None
        assert search_run.status == "completed"
        assert search_run.result_payload is not None
        assert len(search_run.result_payload["results"]) == 3
        assert [
            result["confidence"]
            for result in search_run.agent_runs[-1].output_payload[
                "qualified_results"
            ]
        ] == [92, 86, 48]
        assert len(search_run.agent_runs) == 7
        assert sum(
            result["supplier_type"] == "manufacturer"
            for result in search_run.agent_runs[-1].output_payload[
                "qualified_results"
            ]
        ) == 2

        overview = list_communication_overview(db, rfq.id)
        assert len(overview.conversations) == 3
        assert all(len(dialog.messages) == 4 for dialog in overview.conversations)
        assert sorted(dialog.data_collection_status for dialog in overview.conversations) == [
            "collecting",
            "complete",
            "complete",
        ]

        summary = build_summary(db, rfq.id)
        assert len(summary) == 3
        assert [row.price for row in summary] == [11.8, 12.4, 10.9]
        assert [row.is_complete for row in summary] == [True, True, False]
        assert [row.supplier for row in summary] == [
            "Gujarat FineChem",
            "Qingdao Nova Chemicals",
            "Eastern Trade Solutions",
        ]
        assert [row.origin_country for row in summary] == ["Индия", "Китай", "Китай"]
        assert [row.packaging for row in summary] == [
            "50 kg bags",
            "25 kg fiber drums",
            "25 kg bags",
        ]
        assert [row.price_unit for row in summary] == ["kg", "kg", "kg"]
        assert all(row.quoted_quantity == "500 kg" for row in summary)
        assert all(row.landed_cost is not None for row in summary)
