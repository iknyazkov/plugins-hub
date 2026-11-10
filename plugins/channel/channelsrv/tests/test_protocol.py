"""Protocol tests: versions, responses, field parsing."""

import pytest

from channelsrv import protocol


def test_version_ok() -> None:
    protocol.check_version({"v": 1, "op": "healthz"})


@pytest.mark.parametrize("bad", [{"op": "x"}, {"v": 2, "op": "x"}, {"v": "1", "op": "x"}])
def test_version_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.check_version(bad)


def test_ok_and_fail_shapes() -> None:
    assert protocol.ok(approval_id="a1") == {"ok": True, "approval_id": "a1"}
    assert protocol.fail("no") == {"ok": False, "error": "no"}


def test_req_str() -> None:
    assert protocol.req_str({"k": "v"}, "k") == "v"
    with pytest.raises(protocol.ProtocolError):
        protocol.req_str({"k": ""}, "k")
    with pytest.raises(protocol.ProtocolError):
        protocol.req_str({"k": 5}, "k")
    with pytest.raises(protocol.ProtocolError):
        protocol.req_str({}, "k")


def test_req_int_rejects_bool_and_garbage() -> None:
    assert protocol.req_int({"k": 7}, "k") == 7
    with pytest.raises(protocol.ProtocolError):
        protocol.req_int({"k": True}, "k")
    with pytest.raises(protocol.ProtocolError):
        protocol.req_int({"k": "7"}, "k")
    with pytest.raises(protocol.ProtocolError):
        protocol.req_int({}, "k")


def test_opt_str() -> None:
    assert protocol.opt_str({"k": "v"}, "k") == "v"
    assert protocol.opt_str({}, "k") is None
    with pytest.raises(protocol.ProtocolError):
        protocol.opt_str({"k": 1}, "k")


def test_agent_statuses_exclude_offline() -> None:
    assert "offline" in protocol.STATUSES
    assert "offline" not in protocol.AGENT_STATUSES
