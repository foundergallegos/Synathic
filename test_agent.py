import asyncio
from synathic import monitor, expect

monitor.start()

@expect(postcondition="row_exists", table="customers", match_field="email")
async def create_customer(email: str, name: str):
    print(f"Creating customer: {name} ({email})")
    return {"status": "success", "customer_id": "123"}

async def main():
    result = await create_customer("test@example.com", "Test User")
    print(f"Result: {result}")
    await asyncio.sleep(2)
    print("Done! Check http://localhost:8000/docs to see the events")

if __name__ == "__main__":
    asyncio.run(main())