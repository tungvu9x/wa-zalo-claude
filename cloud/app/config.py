import os


def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Thieu bien moi truong {name}")
    return v


DATABASE_URL = _req("DATABASE_URL")
MASTER_KEY = _req("MASTER_KEY")  # Fernet key, ma hoa secret cua device
ADMIN_TOKEN = _req("ADMIN_TOKEN")
if len(ADMIN_TOKEN) < 32:
    raise RuntimeError("ADMIN_TOKEN phai dai it nhat 32 ky tu")

STORAGE_DIR = os.environ.get("STORAGE_DIR", "/data/files")
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_MB", "100")) * 1024 * 1024
MAX_JSON_BYTES = 5 * 1024 * 1024
CLOCK_SKEW_SECONDS = 300
RETENTION_INTERVAL_SECONDS = int(os.environ.get("RETENTION_INTERVAL_SECONDS", "3600"))
ORPHAN_FILE_GRACE_HOURS = 48

# Giai doan 2: doc file va MCP
EXTRACT_INTERVAL_SECONDS = int(os.environ.get("EXTRACT_INTERVAL_SECONDS", "30"))
EXTRACT_TIMEOUT_SECONDS = int(os.environ.get("EXTRACT_TIMEOUT_SECONDS", "120"))
EXTRACT_MAX_CHARS = int(os.environ.get("EXTRACT_MAX_CHARS", "300000"))
EXTRACT_MAX_MEMORY_MB = int(os.environ.get("EXTRACT_MAX_MEMORY_MB", "1536"))

# Bearer token cho /mcp. De trong thi MCP tat.
MCP_TOKEN = os.environ.get("MCP_TOKEN", "")
if MCP_TOKEN and len(MCP_TOKEN) < 32:
    raise RuntimeError("MCP_TOKEN phai dai it nhat 32 ky tu")
# Tenant MCP doc. De trong: dung tenant active duy nhat.
MCP_TENANT_ID = os.environ.get("MCP_TENANT_ID", "")

# Mat khau trang quan tri /admin. De trong thi trang tat.
ADMIN_UI_PASSWORD = os.environ.get("ADMIN_UI_PASSWORD", "")
if ADMIN_UI_PASSWORD and len(ADMIN_UI_PASSWORD) < 8:
    raise RuntimeError("ADMIN_UI_PASSWORD phai dai it nhat 8 ky tu")
