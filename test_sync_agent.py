import asyncio
from synathic import monitor, expect

monitor.start()


@expect(postcondition="row_exists", table="customers", match_field="email", sync=True)
async def create_customer_sync(email: str, name: str):
    print("[func] Creating customer (sync):", name, email)
    return {"status": "success", "customer_id": "sync-123"}


async def run_multiple_calls(email_base: str, name: str, iterations: int = 5):
    import time
    results = []
    for i in range(iterations):
        email = f"{email_base.split('@')[0]}+{i}@{email_base.split('@')[1]}"
        print(f"\ncall #{i+1} email={email}")
        t0 = time.perf_counter()
        res = await create_customer_sync(email, name)
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000
        print(f"call #{i+1} total elapsed: {elapsed_ms:.1f} ms -> returned: {res}")
        # If the SDK returned detailed timings inside the verification, print them
        try:
            # result may be dict with 'verification' containing client/server timings
            ver = res.get('verification') if isinstance(res, dict) else None
            if ver:
                print(f"call #{i+1} verification raw: {ver}")
                if isinstance(ver, dict):
                    client_timings = ver.get('client_timings') or ver.get('client_timings')
                    server_resp = ver.get('server_response') or ver.get('server_timings')
                    print(f"call #{i+1} client_timings: {client_timings}")
                    print(f"call #{i+1} server_timings: {server_resp}")
        except Exception:
            pass
        results.append((i+1, elapsed_ms, res))
        await asyncio.sleep(0.1)
    return results


if __name__ == '__main__':
    asyncio.run(run_multiple_calls('test@example.com', 'Test User', iterations=10))
