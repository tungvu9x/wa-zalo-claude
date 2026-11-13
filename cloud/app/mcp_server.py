"""MCP server chi doc: tra cuu tin nhan WhatsApp/Zalo va file PDF/Excel da doc noi dung.

Gan vao FastAPI tai /mcp (Streamable HTTP, stateless, Bearer MCP_TOKEN).
Claude Desktop ket noi qua `npx mcp-remote` (xem README).
"""
import hmac
import json
import re
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from psycopg_pool import AsyncConnectionPool
from starlette.responses import JSONResponse

from . import config, report

INSTRUCTIONS = """\
Du lieu tin nhan WhatsApp va Zalo ca nhan cua chu tai khoan (KNXStore), kem file dinh kem.
- direction "out" / from "Toi" = chu tai khoan gui; "in" = khach/doi tac gui.
- Moi thoi gian theo mui gio cua tenant (mac dinh Asia/Ho_Chi_Minh). Tham so since/until nhan
  YYYY-MM-DD (until tinh tron ngay), ISO datetime, "today", "yesterday" hoac "7d" (7 ngay truoc).
- Tong hop ngay: goi daily_digest. Tim file bao gia: list_files(doc_type="quote"), sau do read_file
  de doc noi dung. doc_type la phan loai heuristic theo ten file/tu khoa, can kiem lai noi dung.
- read_status "empty" = PDF dang anh scan, chua OCR, chi co ten file.
- Bao cao tuan/thang: period_report lay so lieu (thoi gian phan hoi tinh theo gio lam viec), doc them
  hoi thoai/bao gia neu can, viet nhan xet ngan (4-8 gach dau dong, tieng Viet) roi save_report_note de
  hien tren trang quan tri. Chi luu khi nguoi dung yeu cau viet bao cao.
- Du lieu chi gom tin nhan nhan duoc khi agent dang chay; khoang may tat co the thieu tin.
"""

server = MCPServer("wa-zalo-daily", instructions=INSTRUCTIONS)
RO = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
Channel = Literal["all", "whatsapp", "zalo"]
TimeArg = Annotated[str | None, Field(description='YYYY-MM-DD, ISO datetime, "today", "yesterday" hoac "Nd"')]

_pool: AsyncConnectionPool | None = None
_tenant: tuple | None = None


def bind(pool: AsyncConnectionPool) -> None:
    global _pool
    _pool = pool


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":"))


async def _q(sql: str, params: dict) -> list[dict]:
    async with _pool.connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def _ctx() -> dict:
    """Tenant + mui gio. Cache sau lan dau."""
    global _tenant
    if _tenant is None:
        if config.MCP_TENANT_ID:
            rows = await _q("SELECT id, timezone FROM tenants WHERE id = %(id)s", {"id": config.MCP_TENANT_ID})
        else:
            rows = await _q("SELECT id, timezone FROM tenants WHERE active ORDER BY created_at", {})
            if len(rows) > 1:
                raise ToolError("Co nhieu tenant, dat MCP_TENANT_ID trong .env")
        if not rows:
            raise ToolError("Chua co tenant nao")
        _tenant = (rows[0]["id"], rows[0]["timezone"])
    return {"tenant": _tenant[0], "tz": _tenant[1]}


def _parse_time(s: str | None, tz: str, end: bool = False) -> datetime | None:
    """end=True: ngay YYYY-MM-DD duoc tinh tron ngay (tra ve 00:00 ngay hom sau)."""
    if not s:
        return None
    s = s.strip().lower()
    zone = ZoneInfo(tz)
    today = datetime.now(zone).date()
    d: date | None = None
    if s == "today":
        d = today
    elif s == "yesterday":
        d = today - timedelta(days=1)
    elif m := re.fullmatch(r"(\d{1,4})d", s):
        d = today - timedelta(days=int(m.group(1)))
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        d = date.fromisoformat(s)
    if d is not None:
        if end:
            d += timedelta(days=1)
        return datetime.combine(d, time.min, tzinfo=zone)
    try:
        dt = datetime.fromisoformat(s.upper() if "t" in s else s)
    except ValueError:
        raise ToolError(f"Khong hieu thoi gian '{s}'")
    return dt if dt.tzinfo else dt.replace(tzinfo=zone)


def _range(ctx: dict, since: str | None, until: str | None, default_days: int | None) -> tuple:
    tz = ctx["tz"]
    lo = _parse_time(since, tz)
    hi = _parse_time(until, tz, end=True)
    if lo is None and default_days is not None:
        lo = datetime.combine(datetime.now(ZoneInfo(tz)).date() - timedelta(days=default_days), time.min, tzinfo=ZoneInfo(tz))
    return lo, hi


def _time_where(lo, hi, params: dict, col: str = "m.sent_at") -> list[str]:
    w = []
    if lo is not None:
        params["lo"] = lo
        w.append(f"{col} >= %(lo)s")
    if hi is not None:
        params["hi"] = hi
        w.append(f"{col} < %(hi)s")
    return w


def _fmt(col: str = "m.sent_at", f: str = "YYYY-MM-DD HH24:MI") -> str:
    return f"to_char({col} AT TIME ZONE %(tz)s, '{f}')"


_FILES_JSON = """coalesce((
  SELECT json_agg(json_build_object('id', a.id, 'name', a.file_name, 'upload', a.status,
                                    'kind', d.kind, 'read', d.status, 'doc_type', d.doc_type) ORDER BY a.id)
    FROM attachments a
    LEFT JOIN documents d ON d.tenant_id = a.tenant_id AND d.sha256 = a.sha256
   WHERE a.message_id = m.id), '[]'::json)"""


def _sender(r: dict) -> str:
    if r["direction"] == "out":
        return "Toi"
    return r.get("sender_name") or r.get("sender_id") or "?"


def _clip(s: str | None, n: int) -> str | None:
    if s is None or len(s) <= n:
        return s
    return s[:n] + f"…(+{len(s) - n} ky tu)"


def _is_noise(body: str | None, files) -> bool:
    return not files and bool(body) and body.startswith(("[chat.sticker]", "[chat.gif]"))


# ---------------------------------------------------------------- tools

@server.tool(annotations=RO)
async def daily_digest(
    date: Annotated[str | None, Field(description='Ngay can tong hop: YYYY-MM-DD, "today" (mac dinh) hoac "yesterday"')] = None,
    channel: Channel = "all",
    max_messages_per_chat: Annotated[int, Field(ge=5, le=200)] = 40,
) -> str:
    """Toan bo hoi thoai trong mot ngay, gom theo chat: so tin den/di, tin nhan (cat ngan) va file dinh kem.
    Dung de tong hop cong viec trong ngay, ai hoi gi, ai gui bao gia. Chat co nhieu tin hon gioi han chi
    giu cac tin cuoi; dung get_chat_messages de xem day du."""
    ctx = await _ctx()
    day = date or "today"
    lo = _parse_time(day, ctx["tz"])
    hi = _parse_time(day, ctx["tz"], end=True)
    params = {**ctx, "lo": lo, "hi": hi}
    w = ["m.tenant_id = %(tenant)s", "m.sent_at >= %(lo)s", "m.sent_at < %(hi)s"]
    if channel != "all":
        params["channel"] = channel
        w.append("m.channel = %(channel)s")
    rows = await _q(
        f"""SELECT m.id, m.channel, m.chat_id, m.chat_name, m.is_group, m.direction, m.sender_name,
                   m.sender_id, m.body, {_fmt(f="HH24:MI")} AS t, {_FILES_JSON} AS files
              FROM messages m WHERE {' AND '.join(w)} ORDER BY m.sent_at LIMIT 20000""",
        params,
    )
    chats: dict[tuple, dict] = {}
    for r in rows:
        key = (r["channel"], r["chat_id"])
        c = chats.setdefault(key, {
            "channel": r["channel"], "chat_id": r["chat_id"], "chat_name": None, "is_group": r["is_group"],
            "in": 0, "out": 0, "first": r["t"], "last": r["t"], "messages": [], "files": [],
        })
        c["chat_name"] = r["chat_name"] or c["chat_name"]
        c[r["direction"]] += 1
        c["last"] = r["t"]
        for f in r["files"]:
            c["files"].append({**f, "t": r["t"], "from": _sender(r)})
        if _is_noise(r["body"], r["files"]):
            continue
        msg = {"t": r["t"], "from": _sender(r)}
        if r["body"]:
            msg["text"] = _clip(r["body"], 500)
        if r["files"]:
            msg["files"] = [f["name"] or f"file#{f['id']}" for f in r["files"]]
        c["messages"].append(msg)
    out = []
    for c in sorted(chats.values(), key=lambda c: c["last"], reverse=True):
        if len(c["messages"]) > max_messages_per_chat:
            c["omitted_earlier"] = len(c["messages"]) - max_messages_per_chat
            c["messages"] = c["messages"][-max_messages_per_chat:]
        out.append(c)
    return _j({
        "date": lo.date().isoformat(), "timezone": ctx["tz"], "chats": len(out),
        "messages_in": sum(c["in"] for c in out), "messages_out": sum(c["out"] for c in out),
        "files": sum(len(c["files"]) for c in out), "truncated": len(rows) >= 20000,
        "conversations": out,
    })


@server.tool(annotations=RO)
async def list_chats(
    since: TimeArg = None,
    until: TimeArg = None,
    channel: Channel = "all",
    name_query: Annotated[str | None, Field(description="Loc theo ten chat/nhom, khong phan biet dau")] = None,
    limit: Annotated[int, Field(ge=1, le=300)] = 50,
) -> str:
    """Danh sach cuoc tro chuyen co hoat dong trong khoang thoi gian (mac dinh 7 ngay), moi nhat truoc,
    kem so tin den/di va so file. Dung de tim chat_id cho get_chat_messages."""
    ctx = await _ctx()
    lo, hi = _range(ctx, since, until, 7)
    params = {**ctx, "limit": limit}
    w = ["m.tenant_id = %(tenant)s", *_time_where(lo, hi, params)]
    if channel != "all":
        params["channel"] = channel
        w.append("m.channel = %(channel)s")
    if name_query:
        params["nq"] = name_query
        w.append("strpos(f_unaccent(lower(coalesce(m.chat_name, m.sender_name, ''))), f_unaccent(lower(%(nq)s))) > 0")
    rows = await _q(
        f"""SELECT m.channel, m.chat_id,
                   (array_agg(m.chat_name ORDER BY m.sent_at DESC) FILTER (WHERE m.chat_name IS NOT NULL))[1] AS chat_name,
                   bool_or(m.is_group) AS is_group,
                   count(*) FILTER (WHERE m.direction = 'in') AS msgs_in,
                   count(*) FILTER (WHERE m.direction = 'out') AS msgs_out,
                   coalesce(sum(ac.n), 0)::int AS files,
                   {_fmt('max(m.sent_at)')} AS last_at
              FROM messages m
              LEFT JOIN LATERAL (SELECT count(*) AS n FROM attachments a WHERE a.message_id = m.id) ac ON true
             WHERE {' AND '.join(w)}
             GROUP BY m.channel, m.chat_id
             ORDER BY max(m.sent_at) DESC LIMIT %(limit)s""",
        params,
    )
    return _j({"since": lo, "until": hi, "count": len(rows), "chats": rows})


@server.tool(annotations=RO)
async def get_chat_messages(
    chat_id: str,
    channel: Literal["whatsapp", "zalo"],
    since: TimeArg = None,
    until: TimeArg = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 200,
    before_id: Annotated[int | None, Field(description="Phan trang: lay cac tin cu hon id nay (gia tri next_before_id)")] = None,
) -> str:
    """Noi dung day du mot cuoc tro chuyen theo thu tu thoi gian (lay `limit` tin moi nhat trong khoang)."""
    ctx = await _ctx()
    lo, hi = _range(ctx, since, until, None)
    params = {**ctx, "chat_id": chat_id, "channel": channel, "limit": limit + 1}
    w = ["m.tenant_id = %(tenant)s", "m.channel = %(channel)s", "m.chat_id = %(chat_id)s", *_time_where(lo, hi, params)]
    if before_id is not None:
        params["before_id"] = before_id
        w.append("(m.sent_at, m.id) < (SELECT sent_at, id FROM messages WHERE id = %(before_id)s)")
    rows = await _q(
        f"""SELECT m.id, m.chat_name, m.direction, m.sender_name, m.sender_id, m.body, {_fmt()} AS t,
                   {_FILES_JSON} AS files
              FROM messages m WHERE {' AND '.join(w)}
             ORDER BY m.sent_at DESC, m.id DESC LIMIT %(limit)s""",
        params,
    )
    more = len(rows) > limit
    rows = rows[:limit][::-1]
    msgs = []
    for r in rows:
        msg = {"id": r["id"], "t": r["t"], "from": _sender(r)}
        if r["body"]:
            msg["text"] = r["body"]
        if r["files"]:
            msg["files"] = r["files"]
        msgs.append(msg)
    chat_name = next((r["chat_name"] for r in reversed(rows) if r["chat_name"]), None)
    return _j({
        "channel": channel, "chat_id": chat_id, "chat_name": chat_name, "count": len(msgs),
        "next_before_id": rows[0]["id"] if more and rows else None, "messages": msgs,
    })


@server.tool(annotations=RO)
async def search_messages(
    query: Annotated[str, Field(min_length=1, description="Tu khoa, khong can dau (vd: 'bao gia den')")],
    since: TimeArg = None,
    until: TimeArg = None,
    channel: Channel = "all",
    chat_id: str | None = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> str:
    """Tim tin nhan theo tu khoa trong noi dung, ten chat va ten nguoi gui (bo dau tieng Viet, khop tat ca tu)."""
    ctx = await _ctx()
    lo, hi = _range(ctx, since, until, None)
    params = {**ctx, "q": query, "limit": limit}
    w = ["m.tenant_id = %(tenant)s", "m.tsv @@ plainto_tsquery('simple', f_unaccent(%(q)s))", *_time_where(lo, hi, params)]
    if channel != "all":
        params["channel"] = channel
        w.append("m.channel = %(channel)s")
    if chat_id:
        params["chat_id"] = chat_id
        w.append("m.chat_id = %(chat_id)s")
    rows = await _q(
        f"""SELECT m.id, m.channel, m.chat_id, m.chat_name, m.direction, m.sender_name, m.sender_id,
                   m.body, {_fmt()} AS t, {_FILES_JSON} AS files
              FROM messages m WHERE {' AND '.join(w)}
             ORDER BY m.sent_at DESC LIMIT %(limit)s""",
        params,
    )
    hits = [{
        "id": r["id"], "t": r["t"], "channel": r["channel"], "chat_id": r["chat_id"], "chat_name": r["chat_name"],
        "from": _sender(r), "text": _clip(r["body"], 1000), **({"files": r["files"]} if r["files"] else {}),
    } for r in rows]
    return _j({"query": query, "count": len(hits), "messages": hits})


@server.tool(annotations=RO)
async def list_files(
    since: TimeArg = None,
    until: TimeArg = None,
    kind: Annotated[Literal["documents", "pdf", "excel", "all"], Field(description="documents = PDF + Excel/CSV (mac dinh); all = ca anh, voice")] = "documents",
    doc_type: Annotated[Literal["any", "quote", "po", "invoice", "other"], Field(description="quote gom ca bang gia (price_table)")] = "any",
    query: Annotated[str | None, Field(description="Tim trong ten file va noi dung file (vd ma san pham, ten du an)")] = None,
    channel: Channel = "all",
    chat_id: str | None = None,
    limit: Annotated[int, Field(ge=1, le=300)] = 50,
) -> str:
    """Danh sach file dinh kem (mac dinh 30 ngay gan nhat, moi nhat truoc) kem nguoi gui, chat, trang thai doc
    noi dung va doan dau noi dung. Dung read_file(id) de doc toan bo."""
    ctx = await _ctx()
    lo, hi = _range(ctx, since, until, 30)
    params = {**ctx, "limit": limit}
    w = ["a.tenant_id = %(tenant)s", *_time_where(lo, hi, params)]
    if kind == "pdf":
        w.append(r"(d.kind = 'pdf' OR lower(coalesce(a.file_name, '')) ~ '\.pdf$')")
    elif kind == "excel":
        w.append(r"(d.kind IN ('xlsx','xls','csv') OR lower(coalesce(a.file_name, '')) ~ '\.(xlsx|xlsm|xls|csv)$')")
    elif kind == "documents":
        w.append(r"(d.kind IS NOT NULL OR lower(coalesce(a.file_name, '')) ~ '\.(pdf|xlsx|xlsm|xls|csv)$')")
    if doc_type == "quote":
        w.append("d.doc_type IN ('quote', 'price_table')")
    elif doc_type != "any":
        params["doc_type"] = doc_type
        w.append("d.doc_type = %(doc_type)s")
    if query:
        params["q"] = query
        w.append("""(d.tsv @@ plainto_tsquery('simple', f_unaccent(%(q)s))
                      OR strpos(f_unaccent(lower(coalesce(a.file_name, ''))), f_unaccent(lower(%(q)s))) > 0)""")
    if channel != "all":
        params["channel"] = channel
        w.append("m.channel = %(channel)s")
    if chat_id:
        params["chat_id"] = chat_id
        w.append("m.chat_id = %(chat_id)s")
    rows = await _q(
        f"""SELECT a.id, a.file_name, a.size, a.status AS upload, a.error AS upload_error,
                   d.kind, d.status AS read, d.doc_type, d.pages, d.sheets,
                   m.channel, m.chat_id, m.chat_name, m.direction, m.sender_name, m.sender_id,
                   {_fmt()} AS t, left(m.body, 200) AS message,
                   left(regexp_replace(coalesce(d.text, ''), '\\s+', ' ', 'g'), 300) AS preview
              FROM attachments a
              JOIN messages m ON m.id = a.message_id
              LEFT JOIN documents d ON d.tenant_id = a.tenant_id AND d.sha256 = a.sha256
             WHERE {' AND '.join(w)}
             ORDER BY m.sent_at DESC, a.id DESC LIMIT %(limit)s""",
        params,
    )
    files = []
    for r in rows:
        f = {k: v for k, v in r.items() if v not in (None, "") and k not in ("direction", "sender_name", "sender_id")}
        f["from"] = _sender(r)
        if r["upload"] == "stored" and r["read"] is None and (r["kind"] or r["file_name"]):
            f["read"] = "pending"
        files.append(f)
    return _j({"since": lo, "until": hi, "count": len(files), "files": files})


@server.tool(annotations=RO)
async def read_file(
    attachment_id: Annotated[int, Field(description="id tu list_files / daily_digest")],
    offset: Annotated[int, Field(ge=0)] = 0,
    max_chars: Annotated[int, Field(ge=1000, le=100_000)] = 40_000,
) -> str:
    """Noi dung text da doc tu file PDF/Excel/CSV. Excel: moi sheet mot khoi, cot cach nhau bang tab.
    File dai: goi lai voi offset = next_offset."""
    ctx = await _ctx()
    rows = await _q(
        f"""SELECT a.id, a.file_name, a.size, a.status AS upload, a.error AS upload_error,
                   d.kind, d.status AS read, d.error AS read_error, d.doc_type, d.hints, d.pages, d.sheets,
                   d.truncated, d.text, m.channel, m.chat_id, m.chat_name, m.direction, m.sender_name,
                   m.sender_id, m.body AS message, {_fmt()} AS t
              FROM attachments a
              JOIN messages m ON m.id = a.message_id
              LEFT JOIN documents d ON d.tenant_id = a.tenant_id AND d.sha256 = a.sha256
             WHERE a.tenant_id = %(tenant)s AND a.id = %(id)s""",
        {**ctx, "id": attachment_id},
    )
    if not rows:
        raise ToolError(f"Khong co file id {attachment_id}")
    r = rows[0]
    text = r.pop("text") or ""
    chunk = text[offset: offset + max_chars]
    end = offset + len(chunk)
    meta = {k: v for k, v in r.items() if v not in (None, "") and k not in ("direction", "sender_name", "sender_id", "truncated")}
    meta["from"] = _sender(r)
    if r["read"] is None:
        meta["note"] = "File chua duoc doc noi dung (dang cho upload hoac worker)." if r["upload"] != "failed" else "Agent khong tai duoc file."
    elif r["read"] == "empty":
        meta["note"] = "Khong co lop text (PDF scan), can OCR."
    meta.update(total_chars=len(text), offset=offset, next_offset=end if end < len(text) else None,
                extractor_truncated=bool(r["truncated"]))
    return _j({**meta, "content": chunk})


@server.tool(annotations=RO)
async def system_status() -> str:
    """Trang thai agent (lan cuoi thay, ket noi WhatsApp/Zalo, hang doi) va tien do doc file.
    Dung khi nghi thieu tin nhan hoac file chua co noi dung."""
    ctx = await _ctx()
    devices = await _q(
        f"""SELECT name, {_fmt('last_seen_at')} AS last_seen,
                   extract(epoch FROM now() - last_seen_at)::int AS seconds_since_seen,
                   agent_version, state, revoked_at IS NOT NULL AS revoked
              FROM devices WHERE tenant_id = %(tenant)s ORDER BY created_at""",
        ctx,
    )
    stats = await _q(
        f"""SELECT
              (SELECT count(*) FROM messages WHERE tenant_id = %(tenant)s) AS messages,
              (SELECT {_fmt('min(sent_at)', 'YYYY-MM-DD')} FROM messages WHERE tenant_id = %(tenant)s) AS oldest,
              (SELECT {_fmt('max(sent_at)')} FROM messages WHERE tenant_id = %(tenant)s) AS newest,
              (SELECT count(*) FROM attachments WHERE tenant_id = %(tenant)s AND status = 'pending') AS files_waiting_upload,
              (SELECT count(*) FROM attachments WHERE tenant_id = %(tenant)s AND status = 'failed') AS files_failed_download,
              (SELECT json_object_agg(status, n) FROM (SELECT status, count(*) n FROM documents
                  WHERE tenant_id = %(tenant)s GROUP BY status) s) AS documents_by_status""",
        ctx,
    )
    return _j({"timezone": ctx["tz"], "server_time": datetime.now(ZoneInfo(ctx["tz"])).isoformat(timespec="seconds"),
               "devices": devices, **stats[0]})


@server.tool(annotations=RO)
async def period_report(
    period: Literal["week", "month"] = "week",
    date: Annotated[str | None, Field(description='Ngay bat ky trong ky (YYYY-MM-DD, "today", "7d"...). Mac dinh: ky hien tai')] = None,
) -> str:
    """So lieu bao cao tuan/thang so voi ky truoc: so hoi thoai, khach moi, tin den/di, ti le tra loi,
    thoi gian phan hoi trung vi (chi tinh gio lam viec), tin theo ngay, hoi thoai nhieu nhat, bao gia
    gui/nhan va phan hoi sau do, chat chua tra loi qua 24 gio, nhan xet da luu (neu co)."""
    ctx = await _ctx()
    anchor = (_parse_time(date or "today", ctx["tz"])).date()
    return _j(await report.build_report(_pool, ctx["tenant"], ctx["tz"], period, anchor))


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
async def save_report_note(
    text: Annotated[str, Field(min_length=1, max_length=20_000, description="Nhan xet, Markdown don gian (gach dau dong)")],
    period: Literal["week", "month"] = "week",
    date: Annotated[str | None, Field(description="Ngay bat ky trong ky, giong period_report")] = None,
) -> str:
    """Luu (ghi de) nhan xet cho bao cao tuan/thang de hien tren trang quan tri va ban PDF."""
    ctx = await _ctx()
    anchor = (_parse_time(date or "today", ctx["tz"])).date()
    start = await report.save_note(_pool, ctx["tenant"], period, anchor, text.strip(), "Claude")
    return _j({"saved": True, "period": period, "period_start": start.isoformat()})


# ---------------------------------------------------------------- HTTP

_http_app = server.streamable_http_app(
    stateless_http=True,
    json_response=True,
    # Da co Bearer token; Host header thay doi theo LAN IP / Cloudflare nen tat kiem tra Host
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


class MCPEndpoint:
    """ASGI app cho route /mcp: kiem tra Bearer token roi chuyen cho session manager."""

    async def __call__(self, scope, receive, send):
        if not config.MCP_TOKEN:
            await JSONResponse({"error": "MCP tat (chua dat MCP_TOKEN)"}, status_code=503)(scope, receive, send)
            return
        auth = dict(scope.get("headers") or []).get(b"authorization", b"")
        token = auth[7:] if auth[:7].lower() == b"bearer " else b""
        if not hmac.compare_digest(token, config.MCP_TOKEN.encode()):
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        await server.session_manager.handle_request(scope, receive, send)
