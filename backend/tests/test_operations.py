import httpx
import pytest
import respx

from mcui.lxd import operations
from mcui.lxd.client import LXDError
from tests.conftest import EP1, lxd_err, lxd_ok

OP = {"id": "op1", "status": "Running", "created_at": "2026-01-01T00:00:00Z"}


def async_resp(op=OP):
    return httpx.Response(
        202,
        json={
            "type": "async",
            "status": "Operation created",
            "operation": f"/1.0/operations/{op['id']}",
            "metadata": op,
        },
    )


@respx.mock
async def test_start_async_and_sync(lxd):
    respx.post(f"{EP1}/1.0/instances/c1/state").mock(return_value=async_resp())
    st = await operations.start(lxd, "POST", "/1.0/instances/c1/state", json={"action": "start"})
    assert st.operation_id == "op1" and st.as_dict() == {"operation": OP, "metadata": None}
    respx.put(f"{EP1}/1.0/warnings/w").mock(return_value=lxd_ok({"x": 1}))
    st = await operations.start(lxd, "PUT", "/1.0/warnings/w", json={})
    assert st.operation_id is None and st.metadata == {"x": 1}


@pytest.mark.parametrize(
    "resp,msg",
    [
        (lxd_err("Instance is running", 400), "Instance is running"),
        (httpx.Response(500, text="<html>"), "non-JSON"),
        (httpx.Response(200, json=[1]), "HTTP 200"),
        (httpx.Response(404, json={"type": "sync"}), "HTTP 404"),
    ],
)
@respx.mock
async def test_start_errors(lxd, resp, msg):
    respx.delete(f"{EP1}/1.0/x").mock(return_value=resp)
    with pytest.raises(LXDError, match=msg):
        await operations.start(lxd, "DELETE", "/1.0/x")


@respx.mock
async def test_wait_get_cancel_list(lxd):
    w = respx.get(f"{EP1}/1.0/operations/op1/wait").mock(
        return_value=lxd_ok({**OP, "status": "Success"})
    )
    assert (await operations.wait(lxd, "op1", 999))["status"] == "Success"
    assert w.calls.last.request.url.params["timeout"] == "30"
    assert w.calls.last.request.extensions["timeout"]["read"] == 45  # outlives LXD's wait
    await operations.wait(lxd, "op1", -5)
    assert w.calls.last.request.url.params["timeout"] == "0"
    respx.get(f"{EP1}/1.0/operations/op1").mock(return_value=lxd_ok(OP))
    assert (await operations.get(lxd, "op1"))["id"] == "op1"
    c = respx.delete(f"{EP1}/1.0/operations/op1").mock(return_value=lxd_ok(None))
    await operations.cancel(lxd, "op1")
    assert c.called
    respx.get(f"{EP1}/1.0/operations").mock(
        return_value=lxd_ok(
            {
                "running": [{"id": "a", "created_at": "2026-01-02"}],
                "success": [{"id": "b", "created_at": "2026-01-03"}],
                "failure": None,
            }
        )
    )
    assert [o["id"] for o in await operations.list_all(lxd)] == ["b", "a"]
    respx.get(f"{EP1}/1.0/operations").mock(return_value=lxd_ok(None))
    assert await operations.list_all(lxd) == []


@respx.mock
async def test_run(lxd):
    respx.post(f"{EP1}/1.0/x").mock(return_value=async_resp())
    wait = respx.get(f"{EP1}/1.0/operations/op1/wait")
    wait.mock(return_value=lxd_ok({**OP, "status": "Success"}))
    st = await operations.run(lxd, "POST", "/1.0/x", timeout=5)
    assert st.operation["status"] == "Success"
    assert wait.calls.last.request.url.params["timeout"] == "5"
    wait.mock(return_value=lxd_ok({**OP, "status": "Failure", "err": "boom"}))
    with pytest.raises(LXDError, match="boom"):
        await operations.run(lxd, "POST", "/1.0/x")
    wait.mock(return_value=lxd_ok({**OP, "status": "Failure"}))
    with pytest.raises(LXDError, match="operation failed"):
        await operations.run(lxd, "POST", "/1.0/x")
    respx.post(f"{EP1}/1.0/y").mock(return_value=lxd_ok({"done": True}))
    assert (await operations.run(lxd, "POST", "/1.0/y")).metadata == {"done": True}


@respx.mock
async def test_wait_expiry_returns_running_operation(lxd):
    w = respx.get(f"{EP1}/1.0/operations/op1/wait")
    w.mock(return_value=lxd_err("context deadline exceeded", 504))
    respx.get(f"{EP1}/1.0/operations/op1").mock(return_value=lxd_ok(OP))
    assert (await operations.wait(lxd, "op1"))["status"] == "Running"
    w.mock(return_value=lxd_err("context deadline exceeded", 500))  # older LXD: 500 + message
    assert (await operations.wait(lxd, "op1"))["status"] == "Running"
    w.mock(return_value=lxd_err("Operation not found", 404))
    respx.get(f"{EP1}/1.0/operations/op1").mock(return_value=lxd_err("Operation not found", 404))
    with pytest.raises(LXDError, match="not found"):
        await operations.wait(lxd, "op1")


@respx.mock
async def test_wait_on_a_failed_operation_returns_it(lxd):
    w = respx.get(f"{EP1}/1.0/operations/op1/wait")
    w.mock(return_value=lxd_err('Volume "v1" is missing the required "volatile.uuid" setting', 400))
    get = respx.get(f"{EP1}/1.0/operations/op1")
    get.mock(return_value=lxd_ok({**OP, "status": "Failure", "err": "missing volatile.uuid"}))
    op = await operations.wait(lxd, "op1")
    assert op["status"] == "Failure" and op["err"] == "missing volatile.uuid"
    get.mock(return_value=lxd_ok(OP))  # still running: the wait's error stands
    with pytest.raises(LXDError, match="volatile.uuid"):
        await operations.wait(lxd, "op1")


@respx.mock
async def test_list_nests_bulk_children(lxd):
    """LXD 6.10's bulk stop (recorded on the Phase 5 VM): children stay under their parent."""
    import json
    from pathlib import Path

    rec = Path(__file__).parent / "fixtures/recorded-6.10/1.0_operations_recursion_2_bulk_stop.json"
    route = respx.get(f"{EP1}/1.0/operations").mock(
        return_value=lxd_ok(json.loads(rec.read_text()))  # `lxc query` output: no envelope
    )
    ops = await operations.list_all(lxd)
    assert route.calls.last.request.url.params["recursion"] == "2"
    bulk = [o for o in ops if o.get("child_count")]
    assert len(bulk) == 1 and bulk[0]["child_count"] == 3
    kids = {c["id"] for c in bulk[0]["children"]}
    assert len(kids) == 3 and not kids & {o["id"] for o in ops}  # not listed twice
