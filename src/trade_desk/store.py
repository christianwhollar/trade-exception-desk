import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, maker TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    report TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    lease_until REAL, lease_token TEXT,
                    UNIQUE(tenant, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant TEXT NOT NULL, case_id TEXT NOT NULL, body TEXT NOT NULL,
                    previous TEXT NOT NULL, digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imports (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, request_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL,
                    UNIQUE(tenant,request_key)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(cases)")}
            for name, definition in {
                "version": "INTEGER NOT NULL DEFAULT 1",
                "created_at": "REAL NOT NULL DEFAULT 0",
                "updated_at": "REAL NOT NULL DEFAULT 0",
                "assignee": "TEXT",
                "priority": "TEXT NOT NULL DEFAULT 'normal'",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE cases ADD COLUMN {name} {definition}")
            db.execute(
                "CREATE INDEX IF NOT EXISTS cases_queue ON cases(tenant,status,created_at,id)"
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, tenant, case_id, kind, actor, detail):
        row = db.execute(
            "SELECT digest FROM audit WHERE tenant=? ORDER BY sequence DESC LIMIT 1", (tenant,)
        ).fetchone()
        previous = row[0] if row else "0" * 64
        body = canonical(
            {"case_id": case_id, "kind": kind, "actor": actor, "detail": detail, "at": time.time()}
        )
        digest = hashlib.sha256((previous + body).encode()).hexdigest()
        db.execute(
            "INSERT INTO audit(tenant,case_id,body,previous,digest) VALUES(?,?,?,?,?)",
            (tenant, case_id, body, previous, digest),
        )

    def create(self, identity, key, payload):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._create(db, identity, key, payload)

    def _create(self, db, identity, key, payload):
        encoded = canonical(payload)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        old = db.execute(
            "SELECT * FROM cases WHERE tenant=? AND idempotency_key=?", (identity.tenant, key)
        ).fetchone()
        if old:
            if old["fingerprint"] != fingerprint:
                raise Conflict("Idempotency key already used for different input")
            return dict(old), False
        case_id, now = str(uuid.uuid4()), time.time()
        db.execute(
            "INSERT INTO cases(id,tenant,maker,idempotency_key,fingerprint,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (case_id, identity.tenant, identity.user, key, fingerprint, encoded, now, now),
        )
        self.event(
            db, identity.tenant, case_id, "created", identity.user, {"fingerprint": fingerprint}
        )
        return dict(db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()), True

    def get(self, tenant, case_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM cases WHERE tenant=? AND id=?", (tenant, case_id)
            ).fetchone()
        return dict(row) if row else None

    def reap_exhausted(self):
        """A worker that crashes on its last attempt must not leave a permanent lease."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            expired = db.execute(
                "SELECT id,tenant FROM cases WHERE status='working' AND attempts>=3 AND lease_until<?",
                (time.time(),),
            ).fetchall()
            for row in expired:
                db.execute(
                    "UPDATE cases SET status='failed', lease_until=NULL, lease_token=NULL WHERE id=?",
                    (row["id"],),
                )
                self.event(
                    db,
                    row["tenant"],
                    row["id"],
                    "failed",
                    "lease_reaper",
                    {"reason": "attempts exhausted after worker exit"},
                )
        return len(expired)

    def claim(self, tenant, case_id, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM cases WHERE tenant=? AND id=?", (tenant, case_id)
            ).fetchone()
            if not row:
                raise KeyError(case_id)
            if row["status"] == "working" and row["lease_until"] > now:
                raise Conflict("Investigation is already leased")
            if row["status"] not in ("pending", "working", "retry") or row["attempts"] >= 3:
                raise Conflict("Investigation is not eligible for execution")
            token = str(uuid.uuid4())
            db.execute(
                "UPDATE cases SET status='working', attempts=attempts+1, lease_until=?, lease_token=?,version=version+1,updated_at=? WHERE id=?",
                (now + 60, token, now, case_id),
            )
            self.event(db, tenant, case_id, "claimed", "worker", {"attempt": row["attempts"] + 1})
            return json.loads(row["payload"]), token

    def finish(self, tenant, case_id, token, report=None, error=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM cases WHERE tenant=? AND id=?", (tenant, case_id)
            ).fetchone()
            if (
                not row
                or row["status"] != "working"
                or row["lease_token"] != token
                or row["lease_until"] < time.time()
            ):
                raise Conflict("Stale worker cannot complete this investigation")
            status = ("failed" if row["attempts"] >= 3 else "retry") if error else "awaiting_review"
            db.execute(
                "UPDATE cases SET status=?, report=?, lease_until=NULL, lease_token=NULL,version=version+1,updated_at=? WHERE id=?",
                (status, canonical(report) if report else None, time.time(), case_id),
            )
            self.event(
                db,
                tenant,
                case_id,
                status,
                "worker",
                {"report_digest": hashlib.sha256(canonical(report).encode()).hexdigest()}
                if not error
                else {"error": "investigation failed"},
            )

    def decide(self, identity, case_id, approved, note, expected_version=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM cases WHERE tenant=? AND id=?", (identity.tenant, case_id)
            ).fetchone()
            if not row:
                raise KeyError(case_id)
            if identity.role != "reviewer" or identity.user == row["maker"]:
                raise PermissionError("Independent reviewer required")
            if row["status"] != "awaiting_review":
                raise Conflict("Case is not awaiting review")
            if expected_version is not None and row["version"] != expected_version:
                raise Conflict("Case changed; refresh before reviewing")
            status = "approved" if approved else "rejected"
            db.execute(
                "UPDATE cases SET status=?,version=version+1,updated_at=? WHERE id=?",
                (status, time.time(), case_id),
            )
            self.event(db, identity.tenant, case_id, status, identity.user, {"note": note})
        return self.get(identity.tenant, case_id)

    def audit(self, tenant):
        with self.connect() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM audit WHERE tenant=? ORDER BY sequence", (tenant,)
                )
            ]
        previous = "0" * 64
        valid = True
        for row in rows:
            expected = hashlib.sha256((previous + row["body"]).encode()).hexdigest()
            valid &= row["previous"] == previous and row["digest"] == expected
            previous = row["digest"]
        return {"valid": bool(valid), "head": previous, "events": rows}

    def list_cases(self, tenant, status=None, query="", limit=50, offset=0):
        where, args = "tenant=?", [tenant]
        if status:
            where += " AND status=?"
            args.append(status)
        if query:
            where += " AND (json_extract(payload,'$.internal.trade_id') LIKE ? OR json_extract(payload,'$.internal.instrument') LIKE ?)"
            args.extend(["%" + query.replace("%", "").replace("_", "") + "%"] * 2)
        with self.connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM cases WHERE {where}", args).fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM cases WHERE {where} ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        return {"total": total, "items": [dict(r) for r in rows]}

    def annotate(self, identity, case_id, version, note, assignee=None, priority=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM cases WHERE tenant=? AND id=?", (identity.tenant, case_id)
            ).fetchone()
            if not row:
                raise KeyError(case_id)
            if row["version"] != version:
                raise Conflict("Case changed; refresh before editing")
            if row["status"] in {"approved", "rejected"}:
                raise Conflict("Closed cases are read-only")
            db.execute(
                "UPDATE cases SET assignee=?,priority=?,version=version+1,updated_at=? WHERE id=?",
                (
                    assignee if assignee is not None else row["assignee"],
                    priority or row["priority"],
                    time.time(),
                    case_id,
                ),
            )
            self.event(
                db,
                identity.tenant,
                case_id,
                "annotated",
                identity.user,
                {"note": note, "assignee": assignee, "priority": priority},
            )
        return self.get(identity.tenant, case_id)

    def retry(self, identity, case_id, version, note):
        if identity.role != "reviewer":
            raise PermissionError("Reviewer required")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM cases WHERE tenant=? AND id=?", (identity.tenant, case_id)
            ).fetchone()
            if not row:
                raise KeyError(case_id)
            if row["version"] != version or row["status"] != "failed":
                raise Conflict("Only the current failed case version can be retried")
            db.execute(
                "UPDATE cases SET status='pending',attempts=0,version=version+1,updated_at=? WHERE id=?",
                (time.time(), case_id),
            )
            self.event(db, identity.tenant, case_id, "manual_retry", identity.user, {"note": note})
        return self.get(identity.tenant, case_id)

    def case_events(self, tenant, case_id):
        with self.connect() as db:
            return [
                {**dict(row), "event": json.loads(row["body"])}
                for row in db.execute(
                    "SELECT * FROM audit WHERE tenant=? AND case_id=? ORDER BY sequence",
                    (tenant, case_id),
                )
            ]

    def import_batch(self, identity, key, fingerprint, pairs, quarantined, matched):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM imports WHERE tenant=? AND request_key=?", (identity.tenant, key)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise Conflict("Import key already used for different files")
                return {**json.loads(old["result"]), "replayed": True}
            import_id = str(uuid.uuid4())
            cases = []
            for payload in pairs:
                content_key = "feed-" + hashlib.sha256(canonical(payload).encode()).hexdigest()
                row, created = self._create(db, identity, content_key, payload)
                cases.append(
                    {
                        "id": row["id"],
                        "trade_id": payload["internal"]["trade_id"],
                        "created": created,
                    }
                )
            result = {
                "id": import_id,
                "cases": cases,
                "quarantined": quarantined,
                "matched": matched,
                "created_at": time.time(),
                "replayed": False,
            }
            db.execute(
                "INSERT INTO imports VALUES(?,?,?,?,?,?)",
                (import_id, identity.tenant, key, fingerprint, canonical(result), time.time()),
            )
            self.event(
                db,
                identity.tenant,
                import_id,
                "feed_imported",
                identity.user,
                {
                    "fingerprint": fingerprint,
                    "exceptions": len(cases),
                    "quarantined": len(quarantined),
                    "matched": matched,
                },
            )
        return result

    def imports(self, tenant):
        with self.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT result FROM imports WHERE tenant=? ORDER BY created_at DESC LIMIT 50",
                    (tenant,),
                )
            ]
