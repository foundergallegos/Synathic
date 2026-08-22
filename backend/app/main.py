from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routes import router
from .models import Base
from .database import engine, async_session
from sqlalchemy import text

app = FastAPI(title="Synathic API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Pre-warm DB connection pool: abrir varias conexiones y ejecutar una consulta simple
    try:
        warm_connections = 3
        for i in range(warm_connections):
            async with async_session() as session:
                await session.execute(text("SELECT 1"))
                await session.commit()
        print(f"[startup] DB pool warmup complete ({warm_connections} connections)")
    except Exception as e:
        print(f"[startup] DB pool warmup failed: {e}")

@app.get("/health")
async def health():
    return {"status": "ok"}