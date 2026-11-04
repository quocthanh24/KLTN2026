from datetime import date
from uuid import UUID
from fastapi import APIRouter, Query, Response
from app.api.dependencies import CurrentPrincipal, DbSession
from app.services.learning_analytics_service import DEFAULT_TIMEZONE, LearningAnalyticsService

router = APIRouter(tags=['learning-analytics'])


@router.get('/users/me/learning-analytics')
async def overview(principal: CurrentPrincipal, db: DbSession, response: Response,
                   start_date: date | None = None, end_date: date | None = None,
                   timezone: str = Query(DEFAULT_TIMEZONE, max_length=80),
                   workspace_id: UUID | None = None, channel_id: UUID | None = None):
    response.headers['Cache-Control'] = 'private, no-store'
    return {'data': await LearningAnalyticsService(db).overview(principal, start_date, end_date,
        timezone, workspace_id, channel_id)}
