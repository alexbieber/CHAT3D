"""HTML pages and signed file downloads."""
import mimetypes
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response

from .. import config
from ..security import verify_signed
from ..storage import LocalStorage, storage

router = APIRouter(include_in_schema=False)

PAGES = {
    "/": "index.html", "/pricing": "pricing.html", "/login": "auth.html", "/signup": "auth.html",
    "/forgot": "auth.html", "/reset": "auth.html", "/app": "app.html", "/account": "account.html",
    "/admin": "admin.html", "/developers": "developers.html",
    "/terms": "legal/terms.html", "/privacy": "legal/privacy.html", "/refunds": "legal/refunds.html",
    "/acceptable-use": "legal/acceptable-use.html",
}


@lru_cache(maxsize=64)
def _render(name: str) -> str:
    html = (config.FRONTEND / name).read_text()
    for k, v in {
        "{{APP_NAME}}": config.APP_NAME, "{{COMPANY_NAME}}": config.COMPANY_NAME,
        "{{COMPANY_ADDRESS}}": config.COMPANY_ADDRESS, "{{SUPPORT_EMAIL}}": config.SUPPORT_EMAIL,
        "{{APP_URL}}": config.APP_URL, "{{GOVERNING_LAW}}": config.GOVERNING_LAW,
        "{{SIGNUP_CREDITS}}": str(config.SIGNUP_CREDITS),
    }.items():
        html = html.replace(k, v)
    return html


def _page(name: str):
    def handler():
        return HTMLResponse(_render(name))
    return handler


for path, name in PAGES.items():
    router.add_api_route(path, _page(name), methods=["GET"])


@router.get("/files/{key:path}")
def signed_file(key: str, exp: int = Query(...), sig: str = Query(...), dl: str | None = None):
    if not isinstance(storage, LocalStorage):
        raise HTTPException(404)
    if not verify_signed(key, exp, sig):
        raise HTTPException(403, "This link has expired. Refresh the page to get a new one.")
    try:
        path = storage.local_path(key)
    except ValueError:
        raise HTTPException(404)
    if not path.is_file():
        raise HTTPException(404)
    media = "model/gltf-binary" if key.endswith(".glb") else (mimetypes.guess_type(key)[0] or "application/octet-stream")
    # Signed links are bearer URLs, so allow any origin to load them (model viewers in customers' own apps).
    headers = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
               "Access-Control-Allow-Origin": "*"}
    if dl:
        safe = "".join(c for c in dl if c.isalnum() or c in "-_.")[:80] or "download"
        return FileResponse(path, media_type=media, filename=safe, headers=headers)
    return FileResponse(path, media_type=media, headers=headers)


@router.get("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /app\nDisallow: /account\nDisallow: /admin\nDisallow: /files/\n",
                    media_type="text/plain")
