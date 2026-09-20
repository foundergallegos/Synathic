import asyncio
from synathic import monitor, expect

monitor.start()


@expect(postcondition="row_exists", table="customers", match_field="email", sync=True)
async def create_customer_sync_default(email: str, name: str):
    print("[func] Creating customer (default timestamp):", name, email)
    return {"status": "success", "customer_id": "sync-default-123"}


async def run_multiple_calls(email_base: str, name: str, iterations: int = 5):
    import time
    results = []
    for i in range(iterations):
        email = f"{email_base.split('@')[0]}+{i}@{email_base.split('@')[1]}"
        print(f"\ncall #{i+1} email={email}")
        t0 = time.perf_counter()
        res = await create_customer_sync_default(email, name)
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000
        print(f"call #{i+1} total elapsed: {elapsed_ms:.1f} ms -> returned: {res}")
        results.append((i+1, elapsed_ms, res))
        await asyncio.sleep(0.1)
    return results


if __name__ == '__main__':
    asyncio.run(run_multiple_calls('test@example.com', 'Test User', iterations=10))
