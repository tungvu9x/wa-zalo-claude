import asyncio
import hashlib
import logging
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from psycopg.errors import ForeignKeyViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from . import admin_ui, config, docs, mcp_server
from .security import admin_auth, device_auth, encrypt_secret

log = logging.getLogger("wadaily")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
STORAGE = Path(config.STORAGE_DIR)


# ---------------------------------------------------------------- models

class AttachmentIn(BaseModel):
    file_name: str | None = Field(None, max_length=500)
    mime: str | None = Field(None, max_length=200)
    size: int | None = Field(None, ge=0)
    sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    error: str | None = Field(None, max_length=500)


class MessageIn(BaseModel):
    channel: Literal["whatsapp", "zalo"]
    external_id: str = Field(min_length=1, max_length=300)
    chat_id: str = Field(min_length=1, max_length=300)
    chat_name: str | None = Field(None, max_length=500)
    is_group: bool = False
    sender_id: str | None = Field(None, max_length=300)
    sender_name: str | None = Field(None, max_length=500)
    direction: Literal["in", "out"] = "in"
    body: str | None = Field(None, max_length=200_000)
    sent_at: AwareDatetime
    raw: dict | None = None
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=50)


class BatchIn(BaseModel):
    messages: list[MessageIn] = Field(min_length=1, max_length=100)


class HeartbeatIn(BaseModel):
    agent_version: str | None = Field(None, max_length=50)
    connectors: dict = Field(default_factory=dict)
    queue: dict = Field(default_factory=dict)


class QrIn(BaseModel):
    kind: Literal["text", "png"]
    data: str = Field(max_length=200_000)


class AuthStateIn(BaseModel):
    connector: Literal["whatsapp", "zalo"]
    state: str = Field(max_length=50)
    detail: str | None = Field(None, max_length=500)
    account: str | None = Field(None, max_length=200)
    qr: QrIn | None = None
    pairing_code: str | None = Field(None, max_length=20)


class CommandResultIn(BaseModel):
    id: int
    ok: bool
    detail: str | None = Field(None, max_length=500)


class TenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = "Asia/Ho_Chi_Minh"
    retention_days: int = Field(150, ge=1, le=3650)


class DeviceIn(BaseModel):
    tenant_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------- app

@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(
        config.DATABASE_URL, min_size=1, max_size=10, kwargs={"row_factory": dict_row}, open=False
    )
    await pool.open(wait=True)
    async with pool.connection() as conn:
        await conn.execute(SCHEMA_SQL)
    app.state.pool = pool
    (STORAGE / "tmp").mkdir(parents=True, exist_ok=True)
    mcp_server.bind(pool)
    tasks = [asyncio.create_task(retention_loop(pool)), asyncio.create_task(docs.extract_loop(pool))]
    try:
        async with mcp_server.server.session_manager.run():
            yield
    finally:
        for t in tasks:
            t.cancel()
        await pool.close()


app = FastAPI(title="wa-zalo-daily cloud", lifespan=lifespan)
# MCP (Streamable HTTP, Bearer MCP_TOKEN) cho Claude Desktop / claude.ai
app.router.routes.append(Route("/mcp", endpoint=mcp_server.MCPEndpoint()))
# Trang quan tri web
app.include_router(admin_ui.router)
app.mount("/admin/static", StaticFiles(directory=Path(__file__).parent / "static"), name="admin-static")


@app.get("/healthz")
async def healthz(request: Request):
    async with request.app.state.pool.connection() as conn:
        await conn.execute("SELECT 1")
    return {"ok": True}


# ---------------------------------------------------------------- agent endpoints

@app.post("/v1/messages")
async def ingest_messages(request: Request, dev: dict = Depends(device_auth)):
    body = await request.body()
    if len(body) > config.MAX_JSON_BYTES:
        raise HTTPException(413, "payload too large")
    if hashlib.sha256(body).hexdigest() != dev["content_sha256"]:
        raise HTTPException(400, "content sha256 mismatch")
    try:
        batch = BatchIn.model_validate_json(body)
    except ValidationError as e:
        raise HTTPException(422, detail=e.errors(include_url=False, include_context=False))

    tenant_id, device_id = dev["tenant_id"], dev["device_id"]
    accepted = duplicates = 0
    async with request.app.state.pool.connection() as conn:
        async with conn.transaction():
            for m in batch.messages:
                cur = await conn.execute(
                    """INSERT INTO messages
                         (tenant_id, device_id, channel, external_id, chat_id, chat_name, is_group,
                          sender_id, sender_name, direction, body, sent_at, raw)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (tenant_id, channel, external_id) DO NOTHING
                       RETURNING id""",
                    (tenant_id, device_id, m.channel, m.external_id, m.chat_id, m.chat_name,
                     m.is_group, m.sender_id, m.sender_name, m.direction, m.body, m.sent_at,
                     Jsonb(m.raw) if m.raw is not None else None),
                )
                row = await cur.fetchone()
                if row is None:
                    duplicates += 1
                    continue
                accepted += 1
                for a in m.attachments:
                    if a.sha256 is None:
                        status = "failed"
                    else:
                        cur = await conn.execute(
                            "SELECT 1 FROM files WHERE tenant_id=%s AND sha256=%s", (tenant_id, a.sha256)
                        )
                        status = "stored" if await cur.fetchone() else "pending"
                    await conn.execute(
                        """INSERT INTO attachments
                             (tenant_id, message_id, file_name, mime, size, sha256, status, error)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (tenant_id, row["id"], a.file_name, a.mime, a.size, a.sha256, status, a.error),
                    )
    return {"accepted": accepted, "duplicates": duplicates}


@app.put("/v1/files/{sha256}")
async def upload_file(sha256: str, request: Request, dev: dict = Depends(device_auth)):
    sha256 = sha256.lower()
    if not SHA_RE.match(sha256) or dev["content_sha256"] != sha256:
        raise HTTPException(400, "sha256 in path must match x-content-sha256")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > config.MAX_FILE_BYTES:
        raise HTTPException(413, "file too large")

    tenant_id = dev["tenant_id"]
    tmp = STORAGE / "tmp" / f"{uuid.uuid4().hex}.part"
    hasher = hashlib.sha256()
    size = 0
    final: Path | None = None
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > config.MAX_FILE_BYTES:
                    raise HTTPException(413, "file too large")
                hasher.update(chunk)
                await asyncio.to_thread(f.write, chunk)
        if hasher.hexdigest() != sha256:
            raise HTTPException(400, "hash mismatch")

        async with request.app.state.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT path FROM files WHERE tenant_id=%s AND sha256=%s", (tenant_id, sha256)
            )
            existing = await cur.fetchone()
            dedup = existing is not None
            if not dedup:
                now = datetime.now(timezone.utc)
                rel = Path(str(tenant_id)) / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}" / sha256[:2] / sha256
                final = STORAGE / rel
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, final)
                cur = await conn.execute(
                    """INSERT INTO files (tenant_id, sha256, size, mime, path)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING path""",
                    (tenant_id, sha256, size, request.headers.get("x-mime") or None, str(rel)),
                )
                if await cur.fetchone() is None:  # upload dong thoi, ban khac da vao truoc
                    dedup = True
                    final.unlink(missing_ok=True)
            await conn.execute(
                """UPDATE attachments SET status='stored'
                    WHERE tenant_id=%s AND sha256=%s AND status <> 'stored'""",
                (tenant_id, sha256),
            )
        return {"stored": True, "size": size, "dedup": dedup}
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/v1/heartbeat")
async def heartbeat(request: Request, dev: dict = Depends(device_auth)):
    body = await request.body()
    if hashlib.sha256(body).hexdigest() != dev["content_sha256"]:
        raise HTTPException(400, "content sha256 mismatch")
    try:
        hb = HeartbeatIn.model_validate_json(body)
    except ValidationError as e:
        raise HTTPException(422, detail=e.errors(include_url=False, include_context=False))
    async with request.app.state.pool.connection() as conn:
        await conn.execute(
            """UPDATE devices SET last_seen_at = now(), agent_version = %s, state = %s WHERE id = %s""",
            (hb.agent_version, Jsonb({"connectors": hb.connectors, "queue": hb.queue}), dev["device_id"]),
        )
    return {"ok": True, "server_time": datetime.now(timezone.utc).isoformat()}


async def _signed_body(request: Request, dev: dict) -> bytes:
    body = await request.body()
    if len(body) > config.MAX_JSON_BYTES:
        raise HTTPException(413, "payload too large")
    if hashlib.sha256(body).hexdigest() != dev["content_sha256"]:
        raise HTTPException(400, "content sha256 mismatch")
    return body


@app.post("/v1/auth-state")
async def auth_state(request: Request, dev: dict = Depends(device_auth)):
    """Agent bao trang thai dang nhap. QR chi giu khi dang cho quet; trang thai khac xoa QR."""
    try:
        a = AuthStateIn.model_validate_json(await _signed_body(request, dev))
    except ValidationError as e:
        raise HTTPException(422, detail=e.errors(include_url=False, include_context=False))
    keep_qr = a.state in ("qr_needed",)
    async with request.app.state.pool.connection() as conn:
        await conn.execute(
            """INSERT INTO auth_state (device_id, connector, state, detail, account, qr_kind, qr_data, pairing_code, updated_at)
               VALUES (%(d)s, %(c)s, %(s)s, %(detail)s, %(acc)s, %(qk)s, %(qd)s, %(pc)s, now())
               ON CONFLICT (device_id, connector) DO UPDATE SET
                 state = EXCLUDED.state, detail = EXCLUDED.detail,
                 account = coalesce(EXCLUDED.account, CASE WHEN EXCLUDED.state IN ('logged_out','qr_needed','starting')
                                                         THEN NULL ELSE auth_state.account END),
                 qr_kind = CASE WHEN %(keep)s THEN coalesce(EXCLUDED.qr_kind, auth_state.qr_kind) END,
                 qr_data = CASE WHEN %(keep)s THEN coalesce(EXCLUDED.qr_data, auth_state.qr_data) END,
                 pairing_code = EXCLUDED.pairing_code,
                 updated_at = now()""",
            {"d": dev["device_id"], "c": a.connector, "s": a.state, "detail": a.detail, "acc": a.account,
             "qk": a.qr.kind if a.qr else None, "qd": a.qr.data if a.qr else None,
             "pc": a.pairing_code, "keep": keep_qr},
        )
    return {"ok": True}


@app.post("/v1/commands/poll")
async def commands_poll(request: Request, dev: dict = Depends(device_auth)):
    await _signed_body(request, dev)
    async with request.app.state.pool.connection() as conn:
        await conn.execute(
            """UPDATE agent_commands SET status = 'expired', updated_at = now()
                WHERE device_id = %s AND status IN ('pending','sent') AND created_at < now() - interval '10 minutes'""",
            (dev["device_id"],),
        )
        cur = await conn.execute(
            """UPDATE agent_commands SET status = 'sent', updated_at = now()
                WHERE id IN (SELECT id FROM agent_commands WHERE device_id = %s AND status = 'pending'
                              ORDER BY id LIMIT 5 FOR UPDATE SKIP LOCKED)
            RETURNING id, connector, action, args""",
            (dev["device_id"],),
        )
        rows = await cur.fetchall()
    return {"commands": [{"id": r["id"], "connector": r["connector"], "action": r["action"], "args": r["args"] or {}}
                         for r in sorted(rows, key=lambda r: r["id"])]}


@app.post("/v1/commands/result")
async def commands_result(request: Request, dev: dict = Depends(device_auth)):
    try:
        r = CommandResultIn.model_validate_json(await _signed_body(request, dev))
    except ValidationError as e:
        raise HTTPException(422, detail=e.errors(include_url=False, include_context=False))
    async with request.app.state.pool.connection() as conn:
        await conn.execute(
            """UPDATE agent_commands SET status = %s, result = %s, updated_at = now()
                WHERE id = %s AND device_id = %s""",
            ("done" if r.ok else "failed", r.detail, r.id, dev["device_id"]),
        )
    return {"ok": True}


# ---------------------------------------------------------------- admin endpoints

@app.post("/admin/tenants", dependencies=[Depends(admin_auth)])
async def create_tenant(t: TenantIn, request: Request):
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO tenants (name, timezone, retention_days) VALUES (%s,%s,%s) RETURNING id",
            (t.name, t.timezone, t.retention_days),
        )
        row = await cur.fetchone()
        await _audit(conn, row["id"], "admin", "tenant.create", {"name": t.name})
    return {"tenant_id": str(row["id"])}


@app.post("/admin/devices", dependencies=[Depends(admin_auth)])
async def create_device(d: DeviceIn, request: Request):
    secret = secrets.token_urlsafe(32)
    try:
        async with request.app.state.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO devices (tenant_id, name, secret_enc) VALUES (%s,%s,%s) RETURNING id",
                (d.tenant_id, d.name, encrypt_secret(secret)),
            )
            row = await cur.fetchone()
            await _audit(conn, d.tenant_id, "admin", "device.create", {"device_id": str(row["id"])})
    except ForeignKeyViolation:
        raise HTTPException(404, "tenant not found")
    # secret chi hien mot lan
    return {"device_id": str(row["id"]), "device_secret": secret}


@app.post("/admin/devices/{device_id}/revoke", dependencies=[Depends(admin_auth)])
async def revoke_device(device_id: uuid.UUID, request: Request):
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE devices SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL RETURNING tenant_id",
            (device_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(404, "device not found or already revoked")
        await _audit(conn, row["tenant_id"], "admin", "device.revoke", {"device_id": str(device_id)})
    return {"revoked": True}


@app.get("/admin/devices", dependencies=[Depends(admin_auth)])
async def list_devices(request: Request):
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            """SELECT id, tenant_id, name, revoked_at, last_seen_at, agent_version, state
                 FROM devices ORDER BY created_at"""
        )
        rows = await cur.fetchall()
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        seen = r["last_seen_at"]
        out.append({
            "device_id": str(r["id"]), "tenant_id": str(r["tenant_id"]), "name": r["name"],
            "revoked": r["revoked_at"] is not None,
            "last_seen_at": seen.isoformat() if seen else None,
            "seconds_since_seen": int((now - seen).total_seconds()) if seen else None,
            "agent_version": r["agent_version"], "state": r["state"],
        })
    return out


# ---------------------------------------------------------------- retention

async def _audit(conn, tenant_id, actor: str, action: str, detail: dict | None = None):
    await conn.execute(
        "INSERT INTO audit_log (tenant_id, actor, action, detail) VALUES (%s,%s,%s,%s)",
        (tenant_id, actor, action, Jsonb(detail) if detail else None),
    )


async def run_retention(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT id, retention_days FROM tenants WHERE active")
        tenants = await cur.fetchall()
        for t in tenants:
            cur = await conn.execute(
                "DELETE FROM messages WHERE tenant_id = %s AND sent_at < now() - make_interval(days => %s)",
                (t["id"], t["retention_days"]),
            )
            deleted_msgs = cur.rowcount
            # File mo coi: khong con attachment nao tro toi. Co thoi gian cho de tranh xoa file
            # den truoc message cua no.
            cur = await conn.execute(
                """DELETE FROM files f
                    WHERE f.tenant_id = %s
                      AND f.created_at < now() - make_interval(hours => %s)
                      AND NOT EXISTS (SELECT 1 FROM attachments a
                                       WHERE a.tenant_id = f.tenant_id AND a.sha256 = f.sha256)
                RETURNING f.path""",
                (t["id"], config.ORPHAN_FILE_GRACE_HOURS),
            )
            paths = [r["path"] for r in await cur.fetchall()]
            for p in paths:
                (STORAGE / p).unlink(missing_ok=True)
            if deleted_msgs or paths:
                await _audit(conn, t["id"], "system", "retention.run",
                             {"messages_deleted": deleted_msgs, "files_deleted": len(paths)})
                log.info("retention tenant=%s messages=%d files=%d", t["id"], deleted_msgs, len(paths))
    # don file tmp bi bo do
    cutoff = time.time() - 86400
    for p in (STORAGE / "tmp").glob("*.part"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except FileNotFoundError:
            pass


async def retention_loop(pool: AsyncConnectionPool) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await run_retention(pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("retention failed")
        await asyncio.sleep(config.RETENTION_INTERVAL_SECONDS)
