"""Bounded read pool isolates SFU renewals from business transaction capacity."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from app.core.config import get_settings

settings=get_settings()
media_engine=create_async_engine(str(settings.database_url),pool_size=settings.media_authority_pool_size,max_overflow=0,pool_timeout=.2,pool_pre_ping=True)
media_factory=async_sessionmaker(media_engine,expire_on_commit=False)
join_engine=create_async_engine(str(settings.database_url),pool_size=4,max_overflow=0,pool_timeout=.05,pool_pre_ping=True)
join_factory=async_sessionmaker(join_engine,expire_on_commit=False)

def get_media_pools():
    return {'lease':media_factory,'join':join_factory,'refresh':join_factory}

async def warm_media_pool():
    connections=[]
    try:
        for _ in range(get_settings().media_authority_pool_size):
            conn=await media_engine.connect();connections.append(conn)
            await conn.execute(text('SELECT 1'))
    finally:
        for conn in connections:await conn.close()
    connections=[]
    try:
        for _ in range(4):
            conn=await join_engine.connect();connections.append(conn)
            await conn.execute(text('SELECT 1'))
    finally:
        for conn in connections:await conn.close()
