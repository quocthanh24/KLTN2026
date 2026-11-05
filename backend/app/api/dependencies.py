from typing import Annotated

from fastapi import Depends, Request
from time import perf_counter
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.session import get_db
from app.services.auth_service import AuthService, Principal

DbSession = Annotated[AsyncSession, Depends(get_db)]
_bearer = HTTPBearer(auto_error=False)


async def get_current_principal(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise AppError(401, "AUTHENTICATION_REQUIRED", "Yêu cầu đăng nhập")
    started=perf_counter()
    principal=await AuthService(db).authenticate(credentials.credentials)
    request.state.ai_auth_ms=round((perf_counter()-started)*1000,2)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]
