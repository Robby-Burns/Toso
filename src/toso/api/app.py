"""TOSO FastAPI Application Factory.

Builds the FastAPI application with router mounting, CORS middleware,
optional token authentication, and static file serving for web UI assets.
"""

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from toso.api.routes import router
from toso.api.state import APIServerState


def create_app(
    state: APIServerState | None = None,
    auth_password: str | None = None,
    static_dir: Path | str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        state: Optional APIServerState instance; instantiated if None.
        auth_password: Optional password/token for access control.
                       Defaults to TOSO_WEB_PASSWORD environment variable.
        static_dir: Optional path to pre-built static UI directory (e.g. Next.js export).

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="TOSO Trading System API",
        description="Operational and Governance API for TOSO Trading Platform",
        version="1.0.0",
    )

    app.state.server_state = state or APIServerState()

    # CORS configuration for web clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Authentication Gate Middleware
    expected_password = auth_password or os.environ.get("TOSO_WEB_PASSWORD")

    if expected_password:

        @app.middleware("http")
        async def auth_middleware(request: Request, call_next: Any) -> Response:
            # Exempt health check and docs
            if request.url.path in ("/healthz", "/docs", "/openapi.json"):
                return await call_next(request)  # type: ignore[no-any-return]

            # Check Authorization header or cookie
            auth_header = request.headers.get("Authorization")
            cookie_token = request.cookies.get("toso_session")

            valid = False
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ", 1)[1].strip()
                if token == expected_password:
                    valid = True
            elif cookie_token == expected_password:
                valid = True

            if not valid:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized: Valid TOSO authorization required."},
                )

            return await call_next(request)  # type: ignore[no-any-return]

    # Mount API and WebSocket routes
    app.include_router(router)

    # Optional static file serving (for unified single-port Docker container)
    ui_dist_path = Path(static_dir) if static_dir else Path("web/out")
    if ui_dist_path.exists() and ui_dist_path.is_dir():
        app.mount("/", StaticFiles(directory=str(ui_dist_path), html=True), name="ui-static")

    return app
