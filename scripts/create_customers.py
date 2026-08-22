import asyncio
import asyncpg

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/synathic"

CREATE_CUSTOMERS = """
CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    name TEXT,
    email TEXT,
    created_at TIMESTAMP DEFAULT now()
)
"""

async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(CREATE_CUSTOMERS)
        print("customers table ensured")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
