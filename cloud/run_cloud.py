"""Chay Cloud truc tiep, khong Docker (Windows/macOS/Linux).

    python run_cloud.py
Doc cloud/.env, chay API + worker doc file + MCP tai API_HOST:API_PORT (mac dinh 127.0.0.1:8080).
"""
import asyncio
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_env(path: Path) -> None:
    if not path.exists():
        sys.exit(f"Thieu {path}. Chay install-windows.ps1 hoac chep tu .env.example")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


load_env(HERE / ".env")
sys.path.insert(0, str(HERE))
# Console Windows mac dinh cp1252/cp437: in log tieng Viet khong bi loi
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
import uvicorn  # noqa: E402

from app.main import app  # noqa: E402

server = uvicorn.Server(uvicorn.Config(
    app,
    host=os.environ.get("API_HOST", "127.0.0.1"),
    port=int(os.environ.get("API_PORT", "8080")),
    log_level="info",
))
# psycopg async khong chay tren ProactorEventLoop (mac dinh cua Windows)
loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
asyncio.run(server.serve(), loop_factory=loop_factory)
