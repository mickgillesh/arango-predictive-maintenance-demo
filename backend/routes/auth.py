import os

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

router = APIRouter()

ALLOWED_DOMAIN = "arangodb.com"

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@router.get("/auth/login", name="auth_login", include_in_schema=False)
async def login(request: Request) -> RedirectResponse:
    # Prefer an explicit redirect URI (needed when behind an HTTPS-terminating proxy)
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI") or str(
        request.url_for("auth_callback")
    )
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback", name="auth_callback", include_in_schema=False)
async def auth_callback(request: Request) -> RedirectResponse:
    if "error" in request.query_params:
        return HTMLResponse(
            f"<p>Google sign-in error: {request.query_params['error']}</p>"
            '<p><a href="/auth/login">Try again</a></p>',
            status_code=400,
        )
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo") or {}
    email: str = userinfo.get("email", "")
    if not email.lower().endswith(f"@{ALLOWED_DOMAIN}"):
        return HTMLResponse(
            f"<p>Access denied: only @{ALLOWED_DOMAIN} accounts are permitted.</p>"
            '<p><a href="/auth/login">Try a different account</a></p>',
            status_code=403,
        )
    request.session["user"] = {
        "email": email,
        "name": userinfo.get("name", email),
        "picture": userinfo.get("picture", ""),
    }
    return RedirectResponse(url="/")


@router.get("/auth/logout", name="auth_logout", include_in_schema=False)
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/auth/login")


@router.get("/auth/me", name="auth_me", include_in_schema=False)
async def me(request: Request) -> JSONResponse:
    user = request.session.get("user")
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    return JSONResponse({"authenticated": True, **user})
