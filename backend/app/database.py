import os
from time import perf_counter
from fastapi import Request
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/synathic")

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=5, max_overflow=10)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db(request: Request):
    t_before_pool = perf_counter()
    async with async_session() as session:
        await session.connection()
        t_after_pool = perf_counter()
        request.state.synathic_pool_wait_ms = (t_after_pool - t_before_pool) * 1000
        yield session