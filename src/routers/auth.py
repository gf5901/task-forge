"""Authentication API routes."""

import hmac

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str
    password: str


def _get_auth_config():
    """Import lazily to avoid circular deps with web module globals."""
    from ..web import AUTH_EMAIL, AUTH_ENABLED, AUTH_PASSWORD

    return AUTH_ENABLED, AUTH_EMAIL, AUTH_PASSWORD


@router.post("/login")
async def login(request: Request, body: LoginBody):
    auth_enabled, auth_email, auth_password = _get_auth_config()
    if not auth_enabled:
        return {"ok": True, "auth_enabled": False}
    if hmac.compare_digest(body.email, auth_email) and hmac.compare_digest(
        body.password, auth_password
    ):
        request.session["authenticated"] = True
        request.session["email"] = body.email
        return {"ok": True}
    return JSONResponse({"error": "invalid credentials"}, status_code=401)


@router.get("/me")
async def me(request: Request):
    auth_enabled, _, _ = _get_auth_config()
    if not auth_enabled:
        return {"authenticated": True, "auth_enabled": False}
    authed = request.session.get("authenticated", False)
    return {
        "authenticated": authed,
        "auth_enabled": True,
        "email": request.session.get("email", ""),
    }


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}
