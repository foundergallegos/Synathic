import asyncio
import asyncpg

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/synathic"

async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        exec_count = await conn.fetchval("SELECT count(*) FROM executions")
        events_count = await conn.fetchval("SELECT count(*) FROM events")
        verif_count = await conn.fetchval("SELECT count(*) FROM verifications")
        print(f"executions={exec_count}, events={events_count}, verifications={verif_count}")

        latest_verif = await conn.fetchrow("SELECT * FROM verifications ORDER BY checked_at DESC LIMIT 1")
        print("latest_verification:", dict(latest_verif) if latest_verif else None)
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
