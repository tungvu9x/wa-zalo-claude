import hmac
import time
import uuid

from cryptography.fernet import Fernet
from fastapi import HTTPException, Request

from . import config

_fernet = Fernet(config.MASTER_KEY.encode())


def encrypt_secret(secret: str) -> bytes:
    return _fernet.encrypt(secret.encode())


def decrypt_secret(blob) -> str:
    return _fernet.decrypt(bytes(blob)).decode()


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="invalid credentials")


async def device_auth(request: Request) -> dict:
    """Xac thuc agent bang HMAC-SHA256.

    message = "{timestamp}\\n{METHOD}\\n{path}\\n{sha256 noi dung}"
    Header: x-device-id, x-timestamp, x-signature, x-content-sha256 (hex thuong).
    """
    h = request.headers
    did, ts, sig, sha = (
        h.get("x-device-id"), h.get("x-timestamp"), h.get("x-signature"), h.get("x-content-sha256"),
    )
    if not (did and ts and sig and sha):
        raise _unauthorized()
    try:
        ts_int = int(ts)
        uuid.UUID(did)
    except ValueError:
        raise _unauthorized()
    if abs(time.time() - ts_int) > config.CLOCK_SKEW_SECONDS:
        raise HTTPException(status_code=401, detail="clock skew too large")

    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            """SELECT d.id, d.tenant_id, d.secret_enc
                 FROM devices d JOIN tenants t ON t.id = d.tenant_id
                WHERE d.id = %s AND d.revoked_at IS NULL AND t.active""",
            (did,),
        )
        row = await cur.fetchone()
    if row is None:
        raise _unauthorized()

    secret = decrypt_secret(row["secret_enc"])
    msg = f"{ts_int}\n{request.method}\n{request.url.path}\n{sha.lower()}".encode()
    expected = hmac.new(secret.encode(), msg, "sha256").hexdigest()
    if not hmac.compare_digest(expected, sig.lower()):
        raise _unauthorized()
    return {"device_id": row["id"], "tenant_id": row["tenant_id"], "content_sha256": sha.lower()}


async def admin_auth(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(token, config.ADMIN_TOKEN):
        raise _unauthorized()
