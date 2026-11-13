"""Trang quan tri web tai /admin: dang nhap bang ADMIN_UI_PASSWORD, cookie ky HMAC.

Trang co the hien ma QR lien ket WhatsApp/Zalo, nen:
  - chi mo tren 127.0.0.1 (API_HOST mac dinh), khong dua ra Internet;
  - moi API can cookie; lenh ghi can them header x-wa-admin (chong CSRF, cookie SameSite=Strict).
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import segno
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import config, mcp_server, report

STATIC = Path(__file__).parent / "static"
COOKIE = "wa_admin"
SESSION_SECONDS = 7 * 86400
router = APIRouter()

_LOGIN_HTML = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Đăng nhập · WA-Zalo Daily</title>
<link rel="stylesheet" href="/admin/static/admin.css"></head>
<body class="login"><form method="post" action="/admin/login" class="login-box">
<h1>WA-Zalo Daily</h1><p class="muted">Trang quản trị</p>
<label>Mật khẩu<input type="password" name="password" autofocus required autocomplete="current-password"></label>
{error}<button class="btn primary" type="submit">Đăng nhập</button>
<p class="muted small">Mật khẩu là ADMIN_UI_PASSWORD trong cloud\\.env</p></form></body></html>"""


# ---------------------------------------------------------------- phien dang nhap

def _key() -> bytes:
    # doi mat khau thi moi phien cu het hieu luc
    return hashlib.sha256(b"wa-admin|" + config.MASTER_KEY.encode() + b"|" + config.ADMIN_UI_PASSWORD.encode()).digest()


def _make_cookie() -> str:
    exp = str(int(time.time()) + SESSION_SECONDS)
    return f"{exp}.{hmac.new(_key(), exp.encode(), 'sha256').hexdigest()}"


def _logged_in(request: Request) -> bool:
    if not config.ADMIN_UI_PASSWORD:
        return False
    val = request.cookies.get(COOKIE, "")
    exp, _, sig = val.partition(".")
    if not exp.isdigit() or int(exp) < time.time():
        return False
    return hmac.compare_digest(sig, hmac.new(_key(), exp.encode(), "sha256").hexdigest())


def need_admin(request: Request) -> None:
    if not _logged_in(request):
        raise HTTPException(401, "chua dang nhap")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-wa-admin") != "1":
        raise HTTPException(403, "thieu header x-wa-admin")


@router.get("/admin", include_in_schema=False)
async def admin_page(request: Request):
    if not config.ADMIN_UI_PASSWORD:
        return HTMLResponse("<p>Trang quản trị đang tắt: đặt ADMIN_UI_PASSWORD trong cloud/.env rồi khởi động lại.</p>", 503)
    if not _logged_in(request):
        return RedirectResponse("/admin/login", 303)
    return FileResponse(STATIC / "admin.html", headers={"cache-control": "no-store"})


@router.get("/admin/login", include_in_schema=False)
async def login_page():
    return HTMLResponse(_LOGIN_HTML.format(error=""))


@router.post("/admin/login", include_in_schema=False)
async def login(request: Request):
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    pw = (form.get("password") or [""])[0]
    if not config.ADMIN_UI_PASSWORD or not hmac.compare_digest(pw.encode(), config.ADMIN_UI_PASSWORD.encode()):
        await asyncio.sleep(1)  # cham doan mat khau
        return HTMLResponse(_LOGIN_HTML.format(error='<p class="error">Sai mật khẩu</p>'), 401)
    resp = RedirectResponse("/admin", 303)
    resp.set_cookie(COOKIE, _make_cookie(), max_age=SESSION_SECONDS, httponly=True, samesite="strict", path="/admin")
    return resp


@router.post("/admin/logout", include_in_schema=False)
async def logout():
    resp = RedirectResponse("/admin/login", 303)
    resp.delete_cookie(COOKIE, path="/admin")
    return resp


# ---------------------------------------------------------------- tien ich

def _pool(request: Request):
    return request.app.state.pool


async def _rows(request: Request, sql: str, params=None) -> list[dict]:
    async with _pool(request).connection() as conn:
        cur = await conn.execute(sql, params or {})
        return await cur.fetchall()


async def _active_device(request: Request, tenant) -> dict | None:
    rows = await _rows(
        request,
        """SELECT id, name, last_seen_at, agent_version, state FROM devices
            WHERE tenant_id = %(t)s AND revoked_at IS NULL
            ORDER BY last_seen_at DESC NULLS LAST, created_at DESC LIMIT 1""",
        {"t": tenant},
    )
    return rows[0] if rows else None


def _claude_desktop() -> dict | None:
    """Chi co y nghia khi Cloud chay cung may Windows voi Claude Desktop."""
    if os.name != "nt":
        return None
    p = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return {"path": str(p), "configured": "wa-zalo-daily" in (data.get("mcpServers") or {})}
    except FileNotFoundError:
        return {"path": str(p), "configured": False}
    except Exception as e:
        return {"path": str(p), "configured": False, "error": str(e)[:200]}


def _qr_uri(kind: str | None, data: str | None) -> str | None:
    if not data:
        return None
    if kind == "png":
        return "data:image/png;base64," + data
    return segno.make(data, error="l").svg_data_uri(scale=6, border=2, dark="#16181D")


# ---------------------------------------------------------------- tong quan

@router.get("/admin/api/overview")
async def overview(request: Request):
    need_admin(request)
    ctx = await mcp_server._ctx()
    tz = ZoneInfo(ctx["tz"])
    dev = await _active_device(request, ctx["tenant"])
    devices = await _rows(
        request,
        """SELECT id, name, revoked_at IS NOT NULL AS revoked, agent_version, state,
                  to_char(last_seen_at AT TIME ZONE %(tz)s, 'DD/MM HH24:MI:SS') AS last_seen,
                  extract(epoch FROM now() - last_seen_at)::int AS seconds_since_seen
             FROM devices WHERE tenant_id = %(tenant)s ORDER BY created_at""",
        ctx,
    )
    issues = await _rows(
        request,
        r"""SELECT a.id, a.file_name, a.status AS upload, a.error AS upload_error, d.status AS read,
                   d.error AS read_error, m.channel, m.chat_name,
                   to_char(m.sent_at AT TIME ZONE %(tz)s, 'DD/MM HH24:MI') AS t
              FROM attachments a
              JOIN messages m ON m.id = a.message_id
              LEFT JOIN documents d ON d.tenant_id = a.tenant_id AND d.sha256 = a.sha256
             WHERE a.tenant_id = %(tenant)s AND m.sent_at > now() - interval '7 days'
               AND (a.status = 'failed'
                    OR d.status IN ('empty', 'failed')
                    OR (d.status = 'unsupported' AND lower(coalesce(a.file_name, '')) ~ '\.(pdf|xlsx|xlsm|xls|csv)$'))
             ORDER BY m.sent_at DESC LIMIT 30""",
        ctx,
    )
    stats = (await _rows(
        request,
        """SELECT (SELECT count(*) FROM messages WHERE tenant_id = %(tenant)s) AS messages,
                  (SELECT count(*) FROM files WHERE tenant_id = %(tenant)s) AS files,
                  (SELECT coalesce(sum(size), 0) FROM files WHERE tenant_id = %(tenant)s) AS bytes,
                  (SELECT to_char(min(sent_at) AT TIME ZONE %(tz)s, 'DD/MM/YYYY') FROM messages
                    WHERE tenant_id = %(tenant)s) AS oldest,
                  (SELECT retention_days FROM tenants WHERE id = %(tenant)s) AS retention_days,
                  (SELECT count(*) FROM attachments a LEFT JOIN documents d
                       ON d.tenant_id = a.tenant_id AND d.sha256 = a.sha256
                    WHERE a.tenant_id = %(tenant)s AND a.status = 'stored' AND d.sha256 IS NULL
                      AND lower(coalesce(a.file_name, '')) ~ '\\.(pdf|xlsx|xlsm|xls|csv)$') AS docs_pending,
                  (SELECT json_object_agg(status, n) FROM (SELECT status, count(*) n FROM documents
                    WHERE tenant_id = %(tenant)s GROUP BY status) s) AS docs_by_status""",
        ctx,
    ))[0]
    digest = json.loads(await mcp_server.daily_digest(date="today", channel="all", max_messages_per_chat=5))
    host = request.headers.get("host") or f"127.0.0.1:{request.url.port or 8080}"
    return {
        "now": datetime.now(tz).strftime("%A %d/%m/%Y %H:%M"), "timezone": ctx["tz"],
        "device": {"id": str(dev["id"]), "name": dev["name"]} if dev else None,
        "devices": [{**d, "id": str(d["id"])} for d in devices],
        "auth": await _auth_payload(request, ctx["tenant"]),
        "digest": digest, "issues": issues, "stats": stats,
        "mcp": {
            "enabled": bool(config.MCP_TOKEN), "endpoint": f"http://{host}/mcp",
            "token_hint": ("…" + config.MCP_TOKEN[-4:]) if config.MCP_TOKEN else None,
            "claude_desktop": _claude_desktop(),
        },
    }


@router.get("/admin/api/mcp-token")
async def mcp_token(request: Request):
    """Chi tra token khi nguoi dung bam Sao chep."""
    need_admin(request)
    return {"token": config.MCP_TOKEN}


# ---------------------------------------------------------------- dang nhap WhatsApp / Zalo

async def _auth_payload(request: Request, tenant) -> dict:
    dev = await _active_device(request, tenant)
    out = {}
    hb = (dev or {}).get("state", {}).get("connectors", {}) if dev else {}
    rows = await _rows(
        request,
        """SELECT connector, state, detail, account, qr_kind, qr_data, pairing_code,
                  extract(epoch FROM now() - updated_at)::int AS age
             FROM auth_state WHERE device_id = %(d)s""",
        {"d": dev["id"]},
    ) if dev else []
    by = {r["connector"]: r for r in rows}
    cmds = await _rows(
        request,
        """SELECT DISTINCT ON (connector) connector, action, status, result,
                  extract(epoch FROM now() - created_at)::int AS age
             FROM agent_commands WHERE device_id = %(d)s ORDER BY connector, id DESC""",
        {"d": dev["id"]},
    ) if dev else []
    cmd_by = {c["connector"]: c for c in cmds}
    agent_age = None
    if dev and dev["last_seen_at"]:
        agent_age = (datetime.now(dev["last_seen_at"].tzinfo) - dev["last_seen_at"]).total_seconds()
    for name in ("whatsapp", "zalo"):
        a = by.get(name)
        h = hb.get(name) or {}
        state = (a or {}).get("state") or h.get("state") or "unknown"
        out[name] = {
            "state": state,
            "detail": (a or {}).get("detail") if a else h.get("detail"),
            "account": (a or {}).get("account") or h.get("account"),
            "last_message_at": h.get("last_message_at"),
            "qr": _qr_uri(a["qr_kind"], a["qr_data"]) if a and state == "qr_needed" else None,
            "pairing_code": a["pairing_code"] if a and state == "pair_code" else None,
            "updated_seconds_ago": a["age"] if a else None,
            "last_command": cmd_by.get(name),
        }
    return {"agent_online": agent_age is not None and agent_age < 150, "agent_seen_seconds_ago": agent_age,
            "connectors": out}


@router.get("/admin/api/auth")
async def auth(request: Request):
    need_admin(request)
    ctx = await mcp_server._ctx()
    return await _auth_payload(request, ctx["tenant"])


class AuthCmdIn(BaseModel):
    phone: str | None = Field(None, max_length=20)


@router.post("/admin/api/auth/{connector}/{action}")
async def auth_command(connector: str, action: str, body: AuthCmdIn, request: Request):
    need_admin(request)
    allowed = {"whatsapp": {"relogin", "pair", "logout"}, "zalo": {"relogin", "logout"}}
    if action not in allowed.get(connector, set()):
        raise HTTPException(400, "lenh khong hop le")
    args = {}
    if action == "pair":
        phone = re.sub(r"\D", "", body.phone or "")
        if phone.startswith("0"):
            phone = "84" + phone[1:]
        if not 9 <= len(phone) <= 15:
            raise HTTPException(400, "so dien thoai khong hop le")
        args["phone"] = phone
    ctx = await mcp_server._ctx()
    dev = await _active_device(request, ctx["tenant"])
    if not dev:
        raise HTTPException(409, "chua co agent nao ket noi")
    async with _pool(request).connection() as conn:
        await conn.execute(
            """UPDATE agent_commands SET status = 'expired', updated_at = now()
                WHERE device_id = %s AND connector = %s AND status = 'pending'""",
            (dev["id"], connector),
        )
        cur = await conn.execute(
            "INSERT INTO agent_commands (device_id, connector, action, args) VALUES (%s,%s,%s,%s) RETURNING id",
            (dev["id"], connector, action, Jsonb(args)),
        )
        row = await cur.fetchone()
        await mcp_server_audit(conn, ctx["tenant"], f"auth.{connector}.{action}", {"command_id": row["id"]})
    return {"queued": True, "id": row["id"]}


async def mcp_server_audit(conn, tenant, action: str, detail: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (tenant_id, actor, action, detail) VALUES (%s, 'admin-ui', %s, %s)",
        (tenant, action, Jsonb(detail)),
    )


# ---------------------------------------------------------------- hoi thoai / file

@router.get("/admin/api/chat")
async def chat(request: Request, channel: str, chat_id: str, before_id: int | None = None):
    need_admin(request)
    if channel not in ("whatsapp", "zalo"):
        raise HTTPException(400, "channel")
    return json.loads(await mcp_server.get_chat_messages(chat_id=chat_id, channel=channel, limit=100, before_id=before_id))


@router.get("/admin/api/files")
async def files(request: Request, q: str | None = None, since: str = "30d", channel: str = "all",
                kind: str = "documents", doc_type: str = "any", limit: int = 100):
    need_admin(request)
    if channel not in ("all", "whatsapp", "zalo") or kind not in ("documents", "pdf", "excel", "all") \
            or doc_type not in ("any", "quote", "po", "invoice", "other"):
        raise HTTPException(400, "bo loc khong hop le")
    try:
        return json.loads(await mcp_server.list_files(since=since, kind=kind, doc_type=doc_type, query=q or None,
                                                      channel=channel, limit=max(1, min(limit, 300))))
    except mcp_server.ToolError as e:
        raise HTTPException(400, str(e))


@router.get("/admin/api/files/{attachment_id}")
async def file_detail(attachment_id: int, request: Request, offset: int = 0):
    need_admin(request)
    try:
        return json.loads(await mcp_server.read_file(attachment_id=attachment_id, offset=offset, max_chars=60_000))
    except mcp_server.ToolError as e:
        raise HTTPException(404, str(e))


async def _attachment(request: Request, attachment_id: int) -> dict:
    ctx = await mcp_server._ctx()
    rows = await _rows(
        request,
        """SELECT a.id, a.sha256, a.file_name, a.mime, f.path, f.mime AS fmime
             FROM attachments a LEFT JOIN files f ON f.tenant_id = a.tenant_id AND f.sha256 = a.sha256
            WHERE a.tenant_id = %(tenant)s AND a.id = %(id)s""",
        {**ctx, "id": attachment_id},
    )
    if not rows:
        raise HTTPException(404, "khong co file")
    return {**rows[0], "tenant": ctx["tenant"]}


@router.get("/admin/files/{attachment_id}/download")
async def download(attachment_id: int, request: Request):
    if not _logged_in(request):
        raise HTTPException(401, "chua dang nhap")
    a = await _attachment(request, attachment_id)
    if not a["path"]:
        raise HTTPException(404, "file goc khong co tren Cloud")
    p = (Path(config.STORAGE_DIR) / a["path"]).resolve()
    if not p.is_relative_to(Path(config.STORAGE_DIR).resolve()) or not p.exists():
        raise HTTPException(404, "file goc khong con")
    name = a["file_name"] or f"file-{attachment_id}"
    return FileResponse(p, filename=name, media_type=a["mime"] or a["fmime"] or "application/octet-stream")


@router.post("/admin/api/files/{attachment_id}/reextract")
async def reextract(attachment_id: int, request: Request):
    need_admin(request)
    a = await _attachment(request, attachment_id)
    async with _pool(request).connection() as conn:
        cur = await conn.execute(
            """UPDATE documents SET status = 'failed', attempts = 0, extracted_at = now() - interval '1 day'
                WHERE tenant_id = %s AND sha256 = %s""",
            (a["tenant"], a["sha256"]),
        )
    if cur.rowcount == 0:
        raise HTTPException(404, "file nay khong phai PDF/Excel hoac chua upload xong")
    return {"queued": True}


class DocTypeIn(BaseModel):
    doc_type: str = Field(pattern=r"^(quote|price_table|po|invoice|other)$")


@router.post("/admin/api/files/{attachment_id}/doc-type")
async def set_doc_type(attachment_id: int, body: DocTypeIn, request: Request):
    need_admin(request)
    a = await _attachment(request, attachment_id)
    async with _pool(request).connection() as conn:
        cur = await conn.execute(
            """UPDATE documents SET doc_type = %s, doc_type_locked = true, hints = %s
                WHERE tenant_id = %s AND sha256 = %s""",
            (body.doc_type, Jsonb(["sua tay"]), a["tenant"], a["sha256"]),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "file nay chua co noi dung da doc")
        await mcp_server_audit(conn, a["tenant"], "document.doc_type", {"sha256": a["sha256"], "doc_type": body.doc_type})
    return {"ok": True}


# ---------------------------------------------------------------- bao cao

@router.get("/admin/api/report")
async def get_report(request: Request, kind: str = "week", date_: str | None = Query(None, alias="date")):
    need_admin(request)
    if kind not in ("week", "month"):
        raise HTTPException(400, "kind")
    ctx = await mcp_server._ctx()
    try:
        anchor = date.fromisoformat(date_) if date_ else datetime.now(ZoneInfo(ctx["tz"])).date()
    except ValueError:
        raise HTTPException(400, "ngay khong hop le")
    return await report.build_report(_pool(request), ctx["tenant"], ctx["tz"], kind, anchor)


class NoteIn(BaseModel):
    kind: str = Field(pattern=r"^(week|month)$")
    date: date
    text: str = Field(max_length=20_000)


@router.put("/admin/api/report/note")
async def put_note(body: NoteIn, request: Request):
    need_admin(request)
    ctx = await mcp_server._ctx()
    if not body.text.strip():
        async with _pool(request).connection() as conn:
            start, _ = report.period_bounds(body.kind, body.date)
            await conn.execute("DELETE FROM report_notes WHERE tenant_id = %s AND kind = %s AND period_start = %s",
                               (ctx["tenant"], body.kind, start))
        return {"deleted": True}
    await report.save_note(_pool(request), ctx["tenant"], body.kind, body.date, body.text.strip(), "Quản trị")
    return {"ok": True}


# ---------------------------------------------------------------- cai dat

class SettingsIn(BaseModel):
    work_days: list[int] = Field(min_length=1, max_length=7)
    work_start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    work_end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    retention_days: int = Field(ge=7, le=3650)


@router.get("/admin/api/settings")
async def get_settings(request: Request):
    need_admin(request)
    ctx = await mcp_server._ctx()
    s = await report.get_settings(_pool(request), ctx["tenant"])
    t = (await _rows(request, "SELECT name, timezone, retention_days FROM tenants WHERE id = %(tenant)s", ctx))[0]
    return {"work_days": s["work_days"], "work_start": s["work_start"].strftime("%H:%M"),
            "work_end": s["work_end"].strftime("%H:%M"), **t}


@router.put("/admin/api/settings")
async def put_settings(body: SettingsIn, request: Request):
    need_admin(request)
    days = sorted(set(body.work_days))
    if any(d < 1 or d > 7 for d in days):
        raise HTTPException(400, "ngay lam viec 1..7")
    if body.work_start >= body.work_end:
        raise HTTPException(400, "gio bat dau phai truoc gio ket thuc")
    ctx = await mcp_server._ctx()
    async with _pool(request).connection() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO tenant_settings (tenant_id, work_days, work_start, work_end, updated_at)
                   VALUES (%s, %s, %s, %s, now())
                   ON CONFLICT (tenant_id) DO UPDATE SET work_days = EXCLUDED.work_days,
                     work_start = EXCLUDED.work_start, work_end = EXCLUDED.work_end, updated_at = now()""",
                (ctx["tenant"], days, body.work_start, body.work_end),
            )
            await conn.execute("UPDATE tenants SET retention_days = %s WHERE id = %s", (body.retention_days, ctx["tenant"]))
            await mcp_server_audit(conn, ctx["tenant"], "settings.update", body.model_dump())
    return {"ok": True}


@router.post("/admin/api/devices/{device_id}/revoke")
async def revoke(device_id: uuid.UUID, request: Request):
    need_admin(request)
    ctx = await mcp_server._ctx()
    async with _pool(request).connection() as conn:
        cur = await conn.execute(
            "UPDATE devices SET revoked_at = now() WHERE id = %s AND tenant_id = %s AND revoked_at IS NULL",
            (device_id, ctx["tenant"]),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "khong co thiet bi")
        await mcp_server_audit(conn, ctx["tenant"], "device.revoke", {"device_id": str(device_id)})
    return {"revoked": True}
