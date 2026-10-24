"""Proteções para um serviço pessoal em loopback; não substituem autenticação."""
from __future__ import annotations

from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


class LocalSecurityMiddleware:
    def __init__(self, app, max_body_bytes: int = 32768):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    origin_matches = (parsed.scheme == scope["scheme"]
                                      and parsed.netloc == headers.get("host", "")
                                      and not parsed.path and not parsed.query and not parsed.fragment)
                except ValueError:
                    origin_matches = False
                if not origin_matches:
                    return await JSONResponse({"detail": "Origem da solicitação não permitida."}, 403)(scope, receive, send)
            if headers.get("sec-fetch-site") == "cross-site":
                return await JSONResponse({"detail": "Solicitação externa bloqueada."}, 403)(scope, receive, send)
            if headers.get("content-type", "").split(";")[0].lower() != "application/json":
                return await JSONResponse({"detail": "Envie os dados como application/json."}, 415)(scope, receive, send)
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > self.max_body_bytes:
                    return await JSONResponse({"detail": "Solicitação maior que 32 KiB."}, 413)(scope, receive, send)
                if not message.get("more_body", False):
                    break
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()
        else:
            bounded_receive = receive

        async def secure_send(message):
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"x-frame-options", b"DENY"),
                ])
                # A documentação interativa usa seus próprios scripts.
                if scope["path"] not in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
                    response_headers.append((b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                        b"connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"))
                if scope["path"].startswith("/api/"):
                    response_headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": response_headers}
            await send(message)
        await self.app(scope, bounded_receive, secure_send)
