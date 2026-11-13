"""Worker doc noi dung file PDF/Excel/CSV da luu, ghi vao bang documents de MCP tra cuu.

Moi file chay trong tien trinh con (app.extract_cli) co timeout, file hong hoac qua nang
khong lam treo API. File loi duoc thu lai toi da MAX_ATTEMPTS lan.
"""
import asyncio
import json
import logging
import re
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path

from psycopg.errors import ForeignKeyViolation
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from . import config

log = logging.getLogger("wadaily.docs")
STORAGE = Path(config.STORAGE_DIR)
APP_ROOT = Path(__file__).resolve().parent.parent
MAX_ATTEMPTS = 3

DOC_EXT = ("pdf", "xlsx", "xlsm", "xls", "csv")
DOC_MIMES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "text/csv",
)


def fold(s: str | None) -> str:
    """Bo dau tieng Viet, chu thuong."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def detect_kind(path: Path, file_name: str | None, mime: str | None) -> str | None:
    """Xac dinh loai theo magic bytes, ten file chi dung de phan biet truong hop mo ho."""
    ext = file_name.rsplit(".", 1)[-1].lower() if file_name and "." in file_name else ""
    mime = (mime or "").lower()
    with open(path, "rb") as f:
        head = f.read(1024)
    if b"%PDF-" in head:
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                return "xlsx" if "xl/workbook.xml" in z.namelist() else None
        except zipfile.BadZipFile:
            return None
    if head.startswith(b"\xd0\xcf\x11\xe0"):  # OLE2: xls, doc, msg...
        return "xls" if ext == "xls" or "ms-excel" in mime else None
    if ext == "csv" or mime == "text/csv":
        return "csv"
    return None


# ---------------------------------------------------------------- phan loai tai lieu

_KW = {
    "quote": ["bao gia", "baogia", "bg", "bang gia", "quotation", "quote", "proforma", "price list", "pricelist", "offer"],
    "po": ["purchase order", "don dat hang", "don mua hang", "dat hang"],
    "invoice": ["hoa don", "invoice", "commercial invoice", "phieu xuat"],
}
_PRICE_COLS = (["don gia", "unit price", "u/price", "u.price", "gia ban"], ["thanh tien", "amount", "total", "tong cong"])


def _norm_name(name: str | None) -> str:
    return re.sub(r"[_\-.()\[\]]+", " ", fold(name))


def classify(file_name: str | None, text: str) -> tuple[str, list[str]]:
    """Doan loai tai lieu (quote / po / invoice / price_table / other) bang tu khoa.

    Heuristic, khong chinh xac 100%: ten file uu tien hon noi dung, chi xet 4000 ky tu dau.
    """
    name = _norm_name(file_name)
    head = fold(text[:4000])
    hints: list[str] = []
    for typ in ("po", "invoice", "quote"):
        for kw in _KW[typ]:
            if re.search(rf"\b{re.escape(kw)}\b", name):
                hints.append(f"ten:{kw}")
                return typ, hints
    for typ in ("quote", "po", "invoice"):
        for kw in _KW[typ]:
            if kw not in ("offer", "dat hang", "bg") and re.search(rf"\b{re.escape(kw)}\b", head):
                hints.append(f"noi dung:{kw}")
                return typ, hints
    price = [kw for kw in _PRICE_COLS[0] if kw in head]
    total = [kw for kw in _PRICE_COLS[1] if kw in head]
    if price and total:
        return "price_table", [f"cot:{price[0]}", f"cot:{total[0]}"]
    return "other", hints


# ---------------------------------------------------------------- worker

_CANDIDATES = f"""
SELECT f.tenant_id, f.sha256, f.path, f.mime, a.file_name, a.mime AS att_mime
  FROM files f
  LEFT JOIN documents d ON d.tenant_id = f.tenant_id AND d.sha256 = f.sha256
  LEFT JOIN LATERAL (
        SELECT file_name, mime FROM attachments a
         WHERE a.tenant_id = f.tenant_id AND a.sha256 = f.sha256
         ORDER BY (a.file_name IS NULL), a.id LIMIT 1) a ON true
 WHERE (d.sha256 IS NULL
        OR (d.status = 'failed' AND d.attempts < {MAX_ATTEMPTS}
            AND d.extracted_at < now() - interval '15 minutes'))
   AND (lower(coalesce(a.file_name, '')) ~ '\\.({"|".join(DOC_EXT)})$'
        OR lower(coalesce(f.mime, '')) = ANY(%(mimes)s)
        OR lower(coalesce(a.mime, '')) = ANY(%(mimes)s))
 ORDER BY f.created_at DESC
 LIMIT 20
"""

_UPSERT = """
INSERT INTO documents (tenant_id, sha256, kind, status, error, pages, sheets, text, truncated,
                       doc_type, hints, attempts, extracted_at)
VALUES (%(tenant_id)s, %(sha256)s, %(kind)s, %(status)s, %(error)s, %(pages)s, %(sheets)s, %(text)s,
        %(truncated)s, %(doc_type)s, %(hints)s, 1, now())
ON CONFLICT (tenant_id, sha256) DO UPDATE SET
  kind = EXCLUDED.kind, status = EXCLUDED.status, error = EXCLUDED.error, pages = EXCLUDED.pages,
  sheets = EXCLUDED.sheets, text = EXCLUDED.text, truncated = EXCLUDED.truncated,
  doc_type = CASE WHEN documents.doc_type_locked THEN documents.doc_type ELSE EXCLUDED.doc_type END,
  hints = EXCLUDED.hints,
  attempts = documents.attempts + 1, extracted_at = now()
"""


def _limit_memory() -> None:
    try:
        import resource

        lim = config.EXTRACT_MAX_MEMORY_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    except Exception:
        pass  # macOS khong ho tro RLIMIT_AS, bo qua


def _run_extractor_sync(path: Path, kind: str) -> dict:
    # subprocess.run trong thread thay vi asyncio subprocess: tren Windows psycopg async can
    # SelectorEventLoop, ma loop nay khong ho tro asyncio subprocess.
    kw = {"preexec_fn": _limit_memory} if sys.platform != "win32" else {"creationflags": subprocess.CREATE_NO_WINDOW}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.extract_cli", str(path), kind, str(config.EXTRACT_MAX_CHARS)],
            cwd=str(APP_ROOT), capture_output=True, timeout=config.EXTRACT_TIMEOUT_SECONDS, **kw,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"timeout {config.EXTRACT_TIMEOUT_SECONDS}s"}
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        return {"error": f"extractor exit {proc.returncode}: {tail[0]}"[:500]}
    return json.loads(proc.stdout.decode("utf-8"))


async def run_extractor(path: Path, kind: str) -> dict:
    return await asyncio.to_thread(_run_extractor_sync, path, kind)


async def process_one(pool: AsyncConnectionPool, row: dict) -> None:
    path = STORAGE / row["path"]
    rec = {
        "tenant_id": row["tenant_id"], "sha256": row["sha256"], "kind": None, "status": "failed",
        "error": None, "pages": None, "sheets": None, "text": None, "truncated": False,
        "doc_type": None, "hints": None,
    }
    try:
        kind = await asyncio.to_thread(detect_kind, path, row["file_name"], row["mime"] or row["att_mime"])
    except FileNotFoundError:
        rec["error"] = "file khong con tren storage"
        kind = None
    else:
        rec["kind"] = kind
        if kind is None:
            rec["status"] = "unsupported"
    if kind:
        res = await run_extractor(path, kind)
        text = (res.get("text") or "").replace("\x00", "")
        rec.update(
            error=res.get("error"), pages=res.get("pages"),
            sheets=Jsonb(res["sheets"]) if res.get("sheets") is not None else None,
            text=text or None, truncated=bool(res.get("truncated")),
        )
        if res.get("error") and not text.strip():
            rec["status"] = "failed"
        elif not text.strip() or (kind == "pdf" and len(re.sub(r"--- Trang \d+/\d+ ---|\s", "", text)) < 20):
            rec["status"] = "empty"  # PDF scan (anh), can OCR
        else:
            rec["status"] = "ok"
        doc_type, hints = classify(row["file_name"], text)
        rec["doc_type"], rec["hints"] = doc_type, Jsonb(hints)
    try:
        async with pool.connection() as conn:
            await conn.execute(_UPSERT, rec)
    except ForeignKeyViolation:
        pass  # file vua bi retention xoa
    log.info("extract %s kind=%s status=%s type=%s", row["sha256"][:12], rec["kind"], rec["status"], rec["doc_type"])


async def run_once(pool: AsyncConnectionPool) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(_CANDIDATES, {"mimes": list(DOC_MIMES)})
        rows = await cur.fetchall()
    for row in rows:
        await process_one(pool, row)
    return len(rows)


async def extract_loop(pool: AsyncConnectionPool) -> None:
    await asyncio.sleep(5)
    while True:
        try:
            n = await run_once(pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("extract loop failed")
            n = 0
        if n == 0:
            await asyncio.sleep(config.EXTRACT_INTERVAL_SECONDS)
