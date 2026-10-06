import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routes import router
from .models import Base
from .database import engine, async_session
from sqlalchemy import text

API_KEY = os.getenv("SYNATHIC_API_KEY")
if not API_KEY:
    print("ADVERTENCIA: SYNATHIC_API_KEY no configurada, el backend acepta cualquier request")

app = FastAPI(title="Synathic API")

allow_origins = os.getenv("SYNATHIC_CORS_ORIGINS", "").split(",")
allow_origins = [origin.strip() for origin in allow_origins if origin.strip()]
if not allow_origins:
    allow_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE IF EXISTS customers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()"))
        await conn.execute(text("ALTER TABLE IF EXISTS verifications ADD COLUMN IF NOT EXISTS error_message TEXT"))
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