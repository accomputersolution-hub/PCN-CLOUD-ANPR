from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger
from app.core.providers import current_providers
from app.core.runtime import is_firestore, require_firebase_stack
from app.db.seed import ensure_demo_users, seed_if_empty
from app.db.session import get_session_factory, init_models

configure_logging()
logger = get_logger("pcn")
settings = get_settings()
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])


@asynccontextmanager
async def lifespan(_: FastAPI):
    providers = current_providers(settings)
    if providers.auth == "firebase" or providers.datastore == "firestore" or providers.storage == "firebase":
        require_firebase_stack()

    if is_firestore():
        from app.firebase.admin import get_firebase_admin_app

        get_firebase_admin_app()
        logger.info(
            "startup_firestore",
            env=settings.environment,
            providers={"auth": providers.auth, "datastore": providers.datastore, "storage": providers.storage},
        )
    else:
        await init_models()
        if settings.seed_demo_data:
            async with get_session_factory()() as session:
                await seed_if_empty(session)
                await ensure_demo_users(session)
        logger.info(
            "startup",
            env=settings.environment,
            providers={"auth": providers.auth, "datastore": providers.datastore, "storage": providers.storage},
        )
    yield
    logger.info("shutdown")


def create_app() -> FastAPI:
    application = FastAPI(
        title="PCN Cloud ANPR API",
        version="0.1.0",
        description="Multi-tenant Automatic Number Plate Recognition platform.",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url=f"{settings.api_v1_prefix}/docs",
        redoc_url=f"{settings.api_v1_prefix}/redoc",
        lifespan=lifespan,
    )
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    application.add_middleware(SlowAPIMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    @application.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, HTTPException):
            raise exc
        logger.exception("unhandled_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "An unexpected error occurred", "details": {}},
        )

    application.include_router(api_router, prefix=settings.api_v1_prefix)
    return application


app = create_app()
