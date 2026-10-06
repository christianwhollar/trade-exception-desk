import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from .auth import configured_keys, identity
from .domain import Investigation, bond_price, yield_to_maturity
from .observe import instrument
from .store import Conflict, Store
from .workflow import execute
from .feeds import ingest
from .pricing import FixedBond, price as dated_price
from typing import Literal


class Decision(BaseModel):
    approved: bool
    note: str = Field(min_length=5, max_length=1000)
    expected_version: int | None = Field(default=None, ge=1)


class Annotation(BaseModel):
    expected_version: int = Field(ge=1)
    note: str = Field(min_length=5, max_length=1000)
    assignee: str | None = Field(default=None, max_length=80)
    priority: Literal["normal", "urgent"] | None = None


class FeedImport(BaseModel):
    internal_csv: str = Field(min_length=1, max_length=1_000_000)
    counterparty_csv: str = Field(min_length=1, max_length=1_000_000)


class Bond(BaseModel):
    coupon_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    yield_rate: float = Field(gt=-0.9, le=10, allow_inf_nan=False)
    years: int = Field(ge=1, le=100)


def public_case(row):
    return {
        "id": row["id"],
        "status": row["status"],
        "attempts": row["attempts"],
        "report": json.loads(row["report"]) if row["report"] else None,
        "payload": json.loads(row["payload"]),
        **{
            key: row[key]
            for key in ("maker", "version", "created_at", "updated_at", "assignee", "priority")
        },
    }


def create_app(path=None, keys=None):
    @asynccontextmanager
    async def lifespan(app):
        if not app.state.keys:
            raise RuntimeError("Set API_KEYS_JSON, or APP_DEMO=1 for local synthetic demos")
        app.state.store = Store(path or Path(os.getenv("DATA_DIR", "runtime")) / "trades.db")
        import asyncio
        import threading
        from .worker import continuous

        stop = threading.Event()
        worker = (
            asyncio.create_task(asyncio.to_thread(continuous, app.state.store, stop))
            if os.getenv("DESK_WORKER") == "1"
            else None
        )
        try:
            yield
        finally:
            stop.set()
            if worker:
                await worker
            app.state.tracing.shutdown()

    app = FastAPI(title="Trade exception desk", lifespan=lifespan)
    app.state.keys = configured_keys() if keys is None else keys
    instrument(app, "trade-exception-desk")

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        with app.state.store.connect() as db:
            db.execute("SELECT 1")
        return {"status": "ok"}

    @app.post("/cases")
    def create(
        case: Investigation,
        idempotency_key: str = Header(min_length=1, max_length=120),
        actor=Depends(identity),
    ):
        row, created = app.state.store.create(actor, idempotency_key, case.model_dump(mode="json"))
        return {**public_case(row), "created": created}

    @app.get("/cases")
    def queue(
        status: str | None = None,
        q: str = Query(default="", max_length=100),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        actor=Depends(identity),
    ):
        result = app.state.store.list_cases(actor.tenant, status, q, limit, offset)
        return {**result, "items": [public_case(r) for r in result["items"]]}

    @app.get("/dashboard")
    def dashboard(actor=Depends(identity)):
        with app.state.store.connect() as db:
            states = dict(
                db.execute(
                    "SELECT status,COUNT(*) FROM cases WHERE tenant=? GROUP BY status",
                    (actor.tenant,),
                ).fetchall()
            )
            imports = db.execute(
                "SELECT COUNT(*) FROM imports WHERE tenant=?", (actor.tenant,)
            ).fetchone()[0]
            last_event = db.execute(
                "SELECT MAX(sequence) FROM audit WHERE tenant=?", (actor.tenant,)
            ).fetchone()[0]
        return {
            "states": states,
            "imports": imports,
            "last_event": last_event or 0,
            "identity": {"user": actor.user, "role": actor.role, "tenant": actor.tenant},
        }

    @app.get("/cases/{case_id}/events")
    def events(case_id: str, actor=Depends(identity)):
        if not app.state.store.get(actor.tenant, case_id):
            raise HTTPException(404, "Case not found")
        return {"events": app.state.store.case_events(actor.tenant, case_id)}

    @app.patch("/cases/{case_id}")
    def annotate(case_id: str, update: Annotation, actor=Depends(identity)):
        try:
            return public_case(
                app.state.store.annotate(
                    actor,
                    case_id,
                    update.expected_version,
                    update.note,
                    update.assignee,
                    update.priority,
                )
            )
        except KeyError:
            raise HTTPException(404, "Case not found") from None

    @app.post("/cases/{case_id}/retry")
    def retry(case_id: str, update: Annotation, actor=Depends(identity)):
        try:
            return public_case(
                app.state.store.retry(actor, case_id, update.expected_version, update.note)
            )
        except KeyError:
            raise HTTPException(404, "Case not found") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None

    @app.post("/imports")
    def import_feeds(
        batch: FeedImport,
        idempotency_key: str = Header(min_length=1, max_length=120),
        actor=Depends(identity),
    ):
        try:
            return ingest(
                app.state.store, actor, idempotency_key, batch.internal_csv, batch.counterparty_csv
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/imports")
    def imports(actor=Depends(identity)):
        return {"items": app.state.store.imports(actor.tenant)}

    @app.get("/cases/{case_id}")
    def get(case_id: str, actor=Depends(identity)):
        row = app.state.store.get(actor.tenant, case_id)
        if not row:
            raise HTTPException(404, "Case not found")
        return public_case(row)

    @app.post("/cases/{case_id}/investigate")
    def run(case_id: str, actor=Depends(identity)):
        try:
            return public_case(execute(app.state.store, actor.tenant, case_id))
        except KeyError:
            raise HTTPException(404, "Case not found") from None

    @app.post("/cases/{case_id}/decision")
    def decide(case_id: str, decision: Decision, actor=Depends(identity)):
        try:
            return public_case(
                app.state.store.decide(
                    actor, case_id, decision.approved, decision.note, decision.expected_version
                )
            )
        except KeyError:
            raise HTTPException(404, "Case not found") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None

    @app.get("/audit")
    def audit(actor=Depends(identity)):
        if actor.role != "reviewer":
            raise HTTPException(403, "Reviewer required")
        return app.state.store.audit(actor.tenant)

    @app.post("/pricing/bond")
    def price(bond: Bond, actor=Depends(identity)):
        price = bond_price(bond.coupon_rate, bond.yield_rate, bond.years)
        return {
            "price": price,
            "recovered_yield": yield_to_maturity(price, bond.coupon_rate, bond.years),
            "convention": "synthetic annual coupon, per 100 face, no accrued interest",
        }

    @app.post("/pricing/fixed-bond")
    def price_dated(bond: FixedBond, actor=Depends(identity)):
        return dated_price(bond)

    from .webapp import mount

    mount(app)

    return app


app = create_app()
