"""Doc text tu mot file PDF/Excel/CSV. Chay trong tien trinh con de co the kill khi treo
hoac ton qua nhieu RAM, khong anh huong API.

    python -m app.extract_cli <path> <kind> <max_chars>
In ra stdout mot dong JSON: {"text", "pages", "sheets", "truncated", "error"}
"""
import csv
import io
import json
import sys

MAX_PDF_PAGES = 80
MAX_ROWS_PER_SHEET = 3000


class _Buf:
    def __init__(self, limit: int):
        self.parts: list[str] = []
        self.n = 0
        self.limit = limit
        self.truncated = False

    def add(self, s: str) -> bool:
        if self.n >= self.limit:
            self.truncated = True
            return False
        s = s[: self.limit - self.n]
        self.parts.append(s)
        self.n += len(s)
        return True

    def text(self) -> str:
        return "".join(self.parts)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _rows_out(buf: _Buf, title: str, rows) -> int:
    buf.add(f"=== Sheet: {title} ===\n")
    count = 0
    for row in rows:
        cells = [_cell(v) for v in row]
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        count += 1
        if count > MAX_ROWS_PER_SHEET:
            buf.add(f"... (cat bot, sheet co hon {MAX_ROWS_PER_SHEET} dong)\n")
            buf.truncated = True
            break
        if not buf.add("\t".join(cells) + "\n"):
            break
    buf.add("\n")
    return count


def extract_xlsx(path: str, buf: _Buf) -> dict:
    import openpyxl

    # Truyen file object: storage luu theo sha256 khong co duoi, openpyxl tu choi theo ten file
    fh = open(path, "rb")
    wb = openpyxl.load_workbook(fh, read_only=True, data_only=True)
    sheets = []
    try:
        for ws in wb.worksheets:
            if getattr(ws, "sheet_state", "visible") != "visible":
                continue
            n = _rows_out(buf, ws.title, ws.iter_rows(values_only=True))
            sheets.append({"name": ws.title, "rows": n})
    finally:
        wb.close()
        fh.close()
    return {"sheets": sheets}


def extract_xls(path: str, buf: _Buf) -> dict:
    import xlrd

    wb = xlrd.open_workbook(path, on_demand=True)
    sheets = []
    try:
        for i in range(wb.nsheets):
            sh = wb.sheet_by_index(i)
            if getattr(sh, "visibility", 0) != 0:
                continue
            n = _rows_out(buf, sh.name, (sh.row_values(r) for r in range(sh.nrows)))
            sheets.append({"name": sh.name, "rows": n})
    finally:
        wb.release_resources()
    return {"sheets": sheets}


def extract_csv(path: str, buf: _Buf) -> dict:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1258", "latin-1"):
        try:
            data = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(data[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    n = _rows_out(buf, "csv", csv.reader(io.StringIO(data), dialect))
    return {"sheets": [{"name": "csv", "rows": n}]}


def extract_pdf(path: str, buf: _Buf) -> dict:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages[:MAX_PDF_PAGES], start=1):
            txt = page.extract_text() or ""
            if not buf.add(f"--- Trang {i}/{total} ---\n{txt}\n"):
                break
            page.flush_cache()
        if total > MAX_PDF_PAGES:
            buf.add(f"... (chi doc {MAX_PDF_PAGES}/{total} trang)\n")
            buf.truncated = True
    return {"pages": total}


EXTRACTORS = {"pdf": extract_pdf, "xlsx": extract_xlsx, "xls": extract_xls, "csv": extract_csv}


def main() -> None:
    path, kind, max_chars = sys.argv[1], sys.argv[2], int(sys.argv[3])
    buf = _Buf(max_chars)
    out = {"text": "", "pages": None, "sheets": None, "truncated": False, "error": None}
    try:
        out.update(EXTRACTORS[kind](path, buf))
    except Exception as e:  # file hong, co mat khau...
        out["error"] = f"{type(e).__name__}: {e}"[:500]
    out["text"] = buf.text()
    out["truncated"] = buf.truncated
    # ghi bytes UTF-8: stdout Windows mac dinh la cp1252, se loi voi tieng Viet
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
