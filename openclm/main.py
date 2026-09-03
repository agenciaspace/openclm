from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm.exc import StaleDataError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api import router
from .config import Settings
from .db import make_engine, session_factory
from .oauth import router as oauth_router

STATIC = Path(__file__).parent / "static"


class BodyLimit:
    """Enforce the limit while reading, including chunked requests without Content-Length."""

    def __init__(self, app, limit=2 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.limit:
                return await JSONResponse({"detail": "Requisição acima do limite de 2 MB."}, 413)(
                    scope, receive, send
                )
            chunks.append(body)
            if not message.get("more_body", False):
                break
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None):
    cfg = settings or Settings()
    engine = make_engine(cfg.database_url)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(
        title="OpenCLM API",
        version="0.1.0",
        description="Self-hosted contracts, typed questions, documents, approval workflows and local AI. All contract data endpoints require authentication. One organization per installation.",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.engine = engine
    app.state.sessions = session_factory(engine)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.url.path != "/api/v1/webhooks/docusign"
        ):
            origin = request.headers.get("origin")
            if origin and origin != cfg.app_url.rstrip("/"):
                return JSONResponse({"detail": "Origem não autorizada."}, status_code=403)
            if request.headers.get("x-openclm") != "1" and not request.headers.get(
                "authorization", ""
            ).startswith("Bearer "):
                return JSONResponse(
                    {"detail": "Inclua X-OpenCLM: 1 nas requisições com sessão."}, status_code=403
                )
        response = await call_next(request)
        response.headers.update(
            {
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Cache-Control": "no-store",
                "X-Frame-Options": "DENY",
            }
        )
        if cfg.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(StaleDataError)
    async def stale(request, exc):
        return JSONResponse(
            {"detail": "O contrato mudou durante a operação. Atualize e tente novamente."}, 409
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Never echo credentials, document contents or malformed Unicode in validation errors.
        errors = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse({"detail": errors}, 422)

    @app.exception_handler(IntegrityError)
    async def conflict(request, exc):
        return JSONResponse(
            {"detail": "Registro duplicado ou operação concorrente. Atualize antes de continuar."},
            409,
        )

    @app.exception_handler(OperationalError)
    async def database_unavailable(request, exc):
        return JSONResponse(
            {"detail": "Banco indisponível. Verifique a conexão e as migrações."}, 503
        )

    @app.get("/health", include_in_schema=False)
    def health():
        with engine.connect() as connection:
            connection.execute(text("SELECT version_num FROM alembic_version"))
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC / "index.html")

    @app.get("/docs", include_in_schema=False)
    def docs():
        return FileResponse(STATIC / "docs.html")

    app.include_router(router)
    app.include_router(oauth_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.add_middleware(BodyLimit)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(
            {urlparse(cfg.app_url).hostname, "localhost", "127.0.0.1", "testserver"}
        ),
    )
    return app


app = create_app()
