import asyncio
import asyncpg

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/synathic"

async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO customers (name, email) VALUES ($1, $2)",
            'Inserted User', 'test@example.com'
        )
        print('Inserted customer test@example.com')
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
