"""Synathic (real client-side verification, real Postgres) -> SmartMemory lite failure journal."""
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from synathic import expect, monitor

from smartmemory.pipeline.config import PipelineConfig
from smartmemory.tools.factory import create_lite_memory
from synathic_smartmemory import (
    ERROR_CLASSIFICATION,
    check_before_retry,
    classification_for,
    journal_verification_errors,
    record_verification,
)

HERE = Path(__file__).parent
POSTCONDITION = dict(postcondition="row_exists", table="customers", match_field="email")
DDL = "CREATE TABLE customers (id serial PRIMARY KEY, email text NOT NULL, updated_at timestamp NOT NULL DEFAULT now())"


def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def pg_dsn():
    name = f"synathic_test_pg_{uuid.uuid4().hex[:8]}"
    try:
        _docker("run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=synathic", "-p", "0:5432", "postgres:16")
        port = _docker("port", name, "5432/tcp").splitlines()[0].rsplit(":", 1)[1]
        dsn = f"postgresql://postgres:synathic@127.0.0.1:{port}/postgres"
        deadline = time.time() + 90
        while True:
            try:
                with psycopg.connect(dsn, connect_timeout=2) as conn:
                    conn.execute(DDL)
                break
            except psycopg.OperationalError:
                if time.time() > deadline:
                    raise
                time.sleep(1)
        yield dsn
    finally:
        rm = subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
        if rm.returncode != 0 and "No such container" not in rm.stderr:
            pytest.fail(f"docker rm -f {name} failed (container may be left running): {rm.stderr.strip()}")


@pytest.fixture
def sm():
    data_dir = HERE / f"sm-data-{uuid.uuid4().hex[:8]}"
    data_dir.mkdir()
    try:
        # auto_download_models=True is the real provisioning knob. SMARTMEMORY_HF_ALLOW_DOWNLOAD alone does not
        # provision the model at construction (the gate passes allow_download=False), see README.
        memory = create_lite_memory(
            str(data_dir), pipeline_profile=PipelineConfig.lite(llm_enabled=False), auto_download_models=True
        )
        try:
            yield memory
        finally:
            memory.close()
    finally:
        try:
            shutil.rmtree(data_dir)
        except OSError as exc:
            pytest.fail(f"could not remove SmartMemory data dir {data_dir}: {exc}")


@expect(sync=True, **POSTCONDITION)
async def create_customer_lying(email: str):
    """The 200 OK that lied: claims success, writes nothing."""
    return "success"


def make_honest(dsn: str):
    @expect(sync=True, **POSTCONDITION)
    async def create_customer(email: str):
        with psycopg.connect(dsn) as conn:
            conn.execute("INSERT INTO customers (email) VALUES (%s)", (email,))
        return "success"

    return create_customer


@pytest.mark.asyncio
async def test_failed_verification_becomes_failure_memory_and_blocks_retry(pg_dsn, sm):
    monitor.start(db_dsn=pg_dsn, endpoint="http://127.0.0.1:9/api/events")  # backend unreachable on purpose
    email = f"lie-{uuid.uuid4().hex[:6]}@example.com"

    result = await create_customer_lying(email=email)
    assert result["result"] == "success"
    assert result["verification"] == {"mode": "client", "status": "fail"}

    item_id = record_verification(sm, result, action="create_customer", args={"email": email}, postcondition=POSTCONDITION)
    assert item_id

    item = sm.get(item_id)
    meta = item.metadata
    assert item.origin == "failure:agent"  # lite store keeps origin on the item, not in metadata
    assert item.memory_type == "episodic"
    assert meta["error_type"] == classification_for("fail")
    assert "customers" in item.content and "email" in item.content and email in item.content

    hits = check_before_retry(sm, classification=classification_for("fail"), action="create_customer")
    assert [h["item_id"] for h in hits] == [item_id]
    assert email in hits[0]["content"]
    # a different classification must not match
    assert check_before_retry(sm, classification=classification_for("unknown"), action="create_customer") == []


@pytest.mark.asyncio
async def test_positive_control_passes_and_records_nothing(pg_dsn, sm):
    monitor.start(db_dsn=pg_dsn, endpoint="http://127.0.0.1:9/api/events")
    email = f"ok-{uuid.uuid4().hex[:6]}@example.com"

    result = await make_honest(pg_dsn)(email=email)
    assert result["verification"] == {"mode": "client", "status": "pass"}
    with psycopg.connect(pg_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM customers WHERE email=%s", (email,)).fetchone()[0] == 1

    assert record_verification(sm, result, action="create_customer", args={"email": email}, postcondition=POSTCONDITION) is None
    assert check_before_retry(sm, classification=classification_for("fail"), action="create_customer") == []


@pytest.mark.parametrize(
    "bad_result, match",
    [
        ("success", "expected a dict"),
        ({"result": "success"}, "no 'verification' dict"),
        ({"result": "success", "verification": {"mode": "client"}}, "unknown verification status"),
        ({"result": "success", "verification": {"mode": "client", "status": "maybe"}}, "unknown verification status"),
    ],
)
def test_unverified_or_malformed_result_raises(sm, bad_result, match):
    with pytest.raises(ValueError, match=match):
        record_verification(sm, bad_result, action="create_customer", args={"email": "x"}, postcondition=POSTCONDITION)


def test_bad_postcondition_dict_raises_on_failure(sm):
    failing = {"result": "success", "verification": {"mode": "client", "status": "fail"}}
    with pytest.raises(ValueError, match="missing required keys"):
        record_verification(sm, failing, action="create_customer", args={"email": "x"}, postcondition={"table": "customers"})


@pytest.mark.parametrize(
    "postcondition, args, match",
    [
        (POSTCONDITION, None, "missing the match field 'email'"),
        (POSTCONDITION, {"other": "x"}, "missing the match field 'email'"),
    ],
)
def test_missing_match_value_raises(sm, postcondition, args, match):
    failing = {"result": "success", "verification": {"mode": "client", "status": "fail"}}
    with pytest.raises(ValueError, match=match):
        record_verification(sm, failing, action="create_customer", args=args, postcondition=postcondition)


def test_timestamp_column_is_journaled_when_present(sm):
    failing = {"result": "success", "verification": {"mode": "client", "status": "fail"}}
    item_id = record_verification(
        sm, failing, action="create_customer", args={"email": "t@b.c"}, postcondition={**POSTCONDITION, "timestamp_column": "modified_at"}
    )
    assert "timestamp_column='modified_at'" in sm.get(item_id).content


def test_non_json_body_is_still_journaled(sm):
    body = {("customer", 123): "success", "verification": {"mode": "client", "status": "fail"}}
    item_id = record_verification(sm, body, action="create_customer", args={"email": "r@b.c"}, postcondition=POSTCONDITION)
    assert item_id is not None
    content = sm.get(item_id).content
    assert "(repr, not JSON)" in content and "('customer', 123)" in content


def test_dict_body_is_preserved_in_content(sm):
    body = {"id": 7, "ok": True, "verification": {"mode": "client", "status": "fail"}}
    item_id = record_verification(sm, body, action="create_customer", args={"email": "a@b.c"}, postcondition=POSTCONDITION)
    content = sm.get(item_id).content
    assert '"id": 7' in content and '"ok": true' in content and "verification" not in content.split("Agent returned:")[1]
    assert "found nothing" not in content


@pytest.mark.asyncio
async def test_bad_dsn_raises_and_wrapper_journals_error(sm):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]  # bound then released: nothing listens here
    monitor.start(db_dsn=f"postgresql://postgres:x@127.0.0.1:{closed_port}/postgres", endpoint="http://127.0.0.1:9/api/events")
    email = f"bad-dsn-{uuid.uuid4().hex[:6]}@example.com"

    with pytest.raises(RuntimeError):
        await create_customer_lying(email=email)  # propagates through the decorator, no result returned

    with pytest.raises(RuntimeError):
        with journal_verification_errors(sm, action="create_customer", args={"email": email}):
            await create_customer_lying(email=email)

    hits = check_before_retry(sm, classification=ERROR_CLASSIFICATION, action="create_customer")
    assert len(hits) == 1
    assert "RuntimeError" in hits[0]["content"] and "create_customer" in hits[0]["content"]
    assert sm.get(hits[0]["item_id"]).metadata["error_type"] == "synathic.verification.error"
