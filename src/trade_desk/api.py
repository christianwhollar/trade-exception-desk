import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from .auth import configured_keys, identity
from .domain import Investigation, bond_price, yield_to_maturity
from .observe import instrument
from .store import Conflict, Store
from .workflow import execute


class Decision(BaseModel):
    approved: bool
    note: str = Field(min_length=5, max_length=1000)


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
    }


def create_app(path=None, keys=None):
    @asynccontextmanager
    async def lifespan(app):
        if not app.state.keys:
            raise RuntimeError("Set API_KEYS_JSON, or APP_DEMO=1 for local synthetic demos")
        app.state.store = Store(path or Path(os.getenv("DATA_DIR", "runtime")) / "trades.db")
        yield
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
                app.state.store.decide(actor, case_id, decision.approved, decision.note)
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

    return app


app = create_app()
