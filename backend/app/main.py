from collections.abc import AsyncIterator
import asyncio
from contextlib import suppress
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from sqlalchemy import text

from app.api.v1.router import router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.exception_handlers import (
    app_error_handler, http_error_handler, unexpected_error_handler, validation_error_handler,
)
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.services.pomodoro_service import transition_worker
    worker = None
    document_worker = None
    attendance_worker = None
    media_worker = None
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        from app.db.session import session_factory
        from app.services.study_session_service import StudySessionService
        async with session_factory() as db:
            await StudySessionService(db).recover_after_restart()
        if get_settings().rag_model_dir:
            from app.services.local_llm import warm_encoder
            try:
                await asyncio.to_thread(warm_encoder)
            except Exception:
                # Keep accounts/documents available; AI/worker report index
                # configuration errors without downloading or exposing paths.
                pass
        worker = asyncio.create_task(transition_worker())
        from app.services.document_processing import processing_worker
        document_worker = asyncio.create_task(processing_worker())
        from app.services.study_session_service import study_session_worker
        attendance_worker = asyncio.create_task(study_session_worker())
        if get_settings().media_authority_enabled:
            from app.services.media_authority import service_key, media_outbox_worker
            from app.db.media_authority import warm_media_pool
            service_key()  # no enabled startup without service authentication
            if not get_settings().media_control_url:
                raise RuntimeError('Media control endpoint required')
            await warm_media_pool()
            media_worker = asyncio.create_task(media_outbox_worker())
        yield
    finally:
        if media_worker:
            media_worker.cancel()
            with suppress(asyncio.CancelledError):
                await media_worker
        if attendance_worker:
            attendance_worker.cancel()
            with suppress(asyncio.CancelledError):
                await attendance_worker
        from app.services.openai_provider import close_clients
        await close_clients()
        if document_worker:
            document_worker.cancel()
            with suppress(asyncio.CancelledError):
                await document_worker
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await engine.dispose()
        from app.db.media_authority import media_engine, join_engine
        await media_engine.dispose()
        await join_engine.dispose()


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(HTTPException, http_error_handler)
app.add_exception_handler(Exception, unexpected_error_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(settings.frontend_url).rstrip("/")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(router)
