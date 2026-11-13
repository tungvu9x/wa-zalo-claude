"""Bao cao tuan/thang: so lieu tinh tu messages/attachments/documents, khong dung AI.

Thoi gian phan hoi: tu tin "den" dau tien chua duoc tra loi trong mot chat den tin "di" ke tiep
cua chat do, chi tinh phan nam trong gio lam viec (tenant_settings).
"""
from datetime import date, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from psycopg_pool import AsyncConnectionPool

DEFAULT_SETTINGS = {"work_days": [1, 2, 3, 4, 5, 6], "work_start": time(8, 0), "work_end": time(17, 30)}
QUOTE_TYPES = ("quote", "price_table")


async def get_settings(pool: AsyncConnectionPool, tenant) -> dict:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT work_days, work_start, work_end FROM tenant_settings WHERE tenant_id = %s", (tenant,)
        )
        row = await cur.fetchone()
    return dict(row) if row else dict(DEFAULT_SETTINGS)


def period_bounds(kind: str, anchor: date) -> tuple[date, date]:
    if kind == "week":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=7)
    start = anchor.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt


def previous_anchor(kind: str, start: date) -> date:
    return start - timedelta(days=7) if kind == "week" else (start - timedelta(days=1)).replace(day=1)


def business_seconds(a: datetime, b: datetime, s: dict, tz: ZoneInfo) -> float:
    if b <= a:
        return 0.0
    a, b = a.astimezone(tz), b.astimezone(tz)
    total = 0.0
    day = a.date()
    end_day = min(b.date(), a.date() + timedelta(days=120))
    while day <= end_day:
        if day.isoweekday() in s["work_days"]:
            ws = datetime.combine(day, s["work_start"], tzinfo=tz)
            we = datetime.combine(day, s["work_end"], tzinfo=tz)
            lo, hi = max(a, ws), min(b, we)
            if hi > lo:
                total += (hi - lo).total_seconds()
        day += timedelta(days=1)
    return total


def fmt_duration(sec: float | None) -> str | None:
    if sec is None:
        return None
    m = int(round(sec / 60))
    if m < 60:
        return f"{m} phút"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} giờ {m} phút" if m else f"{h} giờ"
    d, h = divmod(h, 24)
    return f"{d} ngày {h} giờ" if h else f"{d} ngày"


def fmt_ago(sec: float) -> str:
    h = sec / 3600
    if h < 48:
        return f"{int(h)} giờ"
    return f"{int(h // 24)} ngày"


async def _period_stats(pool, tenant, tz: ZoneInfo, s: dict, start: date, end: date, now: datetime) -> dict:
    lo = datetime.combine(start, time.min, tzinfo=tz)
    hi = datetime.combine(end, time.min, tzinfo=tz)
    look = hi + timedelta(days=14)  # tim tin tra loi sau ky
    async with pool.connection() as conn:
        cur = await conn.execute(
            """SELECT m.id, m.channel, m.chat_id, m.chat_name, m.sender_name, m.direction, m.sent_at, m.body
                 FROM messages m
                WHERE m.tenant_id = %s AND m.sent_at >= %s AND m.sent_at < %s
                ORDER BY m.channel, m.chat_id, m.sent_at, m.id""",
            (tenant, lo, look),
        )
        msgs = await cur.fetchall()
        cur = await conn.execute(
            """SELECT channel, chat_id FROM messages WHERE tenant_id = %s
                GROUP BY channel, chat_id HAVING min(sent_at) >= %s AND min(sent_at) < %s""",
            (tenant, lo, hi),
        )
        new_chats = {(r["channel"], r["chat_id"]) for r in await cur.fetchall()}
        cur = await conn.execute(
            f"""SELECT a.id, a.file_name, m.id AS message_id, m.channel, m.chat_id, m.chat_name, m.direction, m.sent_at
                  FROM attachments a
                  JOIN messages m ON m.id = a.message_id
                  JOIN documents d ON d.tenant_id = a.tenant_id AND d.sha256 = a.sha256
                 WHERE a.tenant_id = %s AND m.sent_at >= %s AND m.sent_at < %s
                   AND d.doc_type IN {QUOTE_TYPES}
                 ORDER BY m.sent_at""",
            (tenant, lo, hi),
        )
        quotes = await cur.fetchall()

    in_period = lambda t: lo <= t < hi  # noqa: E731
    days = {}
    d = start
    while d < end:
        days[d] = {"in": 0, "out": 0}
        d += timedelta(days=1)

    chats: dict[tuple, dict] = {}
    by_chat: dict[tuple, list] = {}
    for m in msgs:
        key = (m["channel"], m["chat_id"])
        by_chat.setdefault(key, []).append(m)
        if not in_period(m["sent_at"]):
            continue
        days[m["sent_at"].astimezone(tz).date()][m["direction"]] += 1
        c = chats.setdefault(key, {"channel": m["channel"], "chat_id": m["chat_id"], "chat_name": None,
                                   "in": 0, "out": 0, "rts": [], "quotes": 0})
        c[m["direction"]] += 1
        if m["chat_name"]:
            c["chat_name"] = m["chat_name"]
        elif not c["chat_name"] and m["direction"] == "in" and m["sender_name"]:
            c["chat_name"] = m["sender_name"]

    # Thoi gian phan hoi va so luot hoi chua duoc tra loi
    blocks = answered = 0
    all_rts = []
    for key, lst in by_chat.items():
        waiting_since = None
        for m in lst:
            if m["direction"] == "in":
                if waiting_since is None and in_period(m["sent_at"]):
                    waiting_since = m["sent_at"]
                    blocks += 1
            elif waiting_since is not None:
                rt = business_seconds(waiting_since, m["sent_at"], s, tz)
                answered += 1
                all_rts.append(rt)
                if key in chats:
                    chats[key]["rts"].append(rt)
                waiting_since = None

    # Bao gia + phan hoi sau do
    quote_rows = []
    for q in quotes:
        key = (q["channel"], q["chat_id"])
        if key in chats:
            chats[key]["quotes"] += 1
        follow = None
        for m in by_chat.get(key, []):
            if m["sent_at"] > q["sent_at"] and m["direction"] != q["direction"]:
                follow = m["sent_at"]
                break
        if follow:
            follow_text = ("Khách trả lời sau " if q["direction"] == "out" else "Mình trả lời sau ") + \
                fmt_duration((follow - q["sent_at"]).total_seconds())
            late = False
        else:
            age = (now - q["sent_at"]).total_seconds()
            follow_text = ("Khách chưa phản hồi " if q["direction"] == "out" else "Mình chưa phản hồi ") + fmt_ago(age)
            late = age > 3 * 86400
        quote_rows.append({
            "attachment_id": q["id"], "file_name": q["file_name"], "direction": q["direction"],
            "channel": q["channel"], "chat_id": q["chat_id"],
            "chat_name": q["chat_name"] or chats.get(key, {}).get("chat_name"),
            "date": q["sent_at"].astimezone(tz).strftime("%d/%m %H:%M"), "follow": follow_text, "late": late,
        })

    top = sorted(chats.values(), key=lambda c: c["in"] + c["out"], reverse=True)[:15]
    return {
        "range": {"start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat()},
        "kpi": {
            "chats": len(chats),
            "new_chats": len([k for k in chats if k in new_chats]),
            "messages_in": sum(c["in"] for c in chats.values()),
            "messages_out": sum(c["out"] for c in chats.values()),
            "reply_rate": round(answered / blocks, 3) if blocks else None,
            "median_response_seconds": median(all_rts) if all_rts else None,
            "quotes_out": sum(1 for q in quote_rows if q["direction"] == "out"),
            "quotes_in": sum(1 for q in quote_rows if q["direction"] == "in"),
        },
        "daily": [{"date": d.isoformat(), "in": v["in"], "out": v["out"]} for d, v in days.items()],
        "top_chats": [{
            "channel": c["channel"], "chat_id": c["chat_id"], "chat_name": c["chat_name"],
            "messages_in": c["in"], "messages_out": c["out"], "quotes": c["quotes"],
            "median_response": fmt_duration(median(c["rts"])) if c["rts"] else None,
            "median_response_seconds": median(c["rts"]) if c["rts"] else None,
        } for c in top],
        "quotes": quote_rows,
    }


async def unanswered(pool, tenant, tz: ZoneInfo, now: datetime, hours: int = 24, days: int = 30) -> list[dict]:
    """Chat co tin cuoi la cua khach, qua `hours` gio chua tra loi (trong `days` ngay gan nhat)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """SELECT DISTINCT ON (channel, chat_id) channel, chat_id, chat_name, sender_name, direction, body, sent_at
                 FROM messages WHERE tenant_id = %s AND sent_at > now() - make_interval(days => %s)
                ORDER BY channel, chat_id, sent_at DESC, id DESC""",
            (tenant, days),
        )
        rows = await cur.fetchall()
    out = []
    for r in rows:
        age = (now - r["sent_at"]).total_seconds()
        if r["direction"] == "in" and age > hours * 3600:
            out.append({"channel": r["channel"], "chat_id": r["chat_id"],
                        "chat_name": r["chat_name"] or r["sender_name"],
                        "last_message": (r["body"] or "[file]")[:200], "waiting": fmt_ago(age), "waiting_seconds": age})
    return sorted(out, key=lambda x: -x["waiting_seconds"])


async def build_report(pool, tenant, tz_name: str, kind: str, anchor: date) -> dict:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    s = await get_settings(pool, tenant)
    start, end = period_bounds(kind, anchor)
    pstart, pend = period_bounds(kind, previous_anchor(kind, start))
    cur = await _period_stats(pool, tenant, tz, s, start, end, now)
    prev = await _period_stats(pool, tenant, tz, s, pstart, pend, now)
    async with pool.connection() as conn:
        c = await conn.execute(
            "SELECT text, author, updated_at FROM report_notes WHERE tenant_id = %s AND kind = %s AND period_start = %s",
            (tenant, kind, start),
        )
        note = await c.fetchone()
    iso = start.isocalendar()
    title = f"Tuần {iso.week}/{iso.year}" if kind == "week" else f"Tháng {start.month}/{start.year}"
    k = cur["kpi"]
    return {
        "kind": kind, "title": title, "timezone": tz_name,
        "start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat(),
        "previous": {"start": pstart.isoformat(), "end": (pend - timedelta(days=1)).isoformat(), "kpi": prev["kpi"]},
        "kpi": {**k, "median_response": fmt_duration(k["median_response_seconds"])},
        "work_hours": {"days": s["work_days"], "start": s["work_start"].strftime("%H:%M"),
                       "end": s["work_end"].strftime("%H:%M")},
        "daily": cur["daily"], "top_chats": cur["top_chats"], "quotes": cur["quotes"],
        "unanswered": await unanswered(pool, tenant, tz, now),
        "note": {"text": note["text"], "author": note["author"],
                 "updated_at": note["updated_at"].astimezone(tz).strftime("%d/%m/%Y %H:%M")} if note else None,
    }


async def save_note(pool, tenant, kind: str, anchor: date, text: str, author: str) -> date:
    start, _ = period_bounds(kind, anchor)
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO report_notes (tenant_id, kind, period_start, text, author, updated_at)
               VALUES (%s, %s, %s, %s, %s, now())
               ON CONFLICT (tenant_id, kind, period_start) DO UPDATE SET
                 text = EXCLUDED.text, author = EXCLUDED.author, updated_at = now()""",
            (tenant, kind, start, text, author),
        )
    return start
