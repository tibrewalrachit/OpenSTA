"""End-to-end tests for the sta-claude RPC surface (session_rpc.py).

These drive a real `sta` process against work/design.tcl (example1 on
Nangate45 min/max with SPEF), so they run wherever an OpenSTA build exists:
locally after `ninja sta`, and inside the Modal image. Skipped when no sta
binary is available (e.g. the plain-CPU GitHub CI job).
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from session_rpc import SessionRpcMixin, TclShellError  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
STA = Path(os.environ.get("STA_BIN", REPO / "build" / "sta"))
DESIGN = Path(os.environ.get("STA_DESIGN", REPO / "work" / "design.tcl"))

pytestmark = pytest.mark.skipif(
    not STA.exists(), reason="no sta binary (run ninja sta first)")


class Session(SessionRpcMixin):
    pass


@pytest.fixture(scope="module")
def rpc():
    session = Session()
    session.rpc_init(STA, DESIGN)
    yield session
    session.rpc_close()


def assert_ns_ps_pair(d, key):
    assert key in d and f"{key}_ps" in d
    if d[key] is None:
        assert d[f"{key}_ps"] is None
    else:
        assert d[f"{key}_ps"] == int(round(d[key] * 1000))


class TestQueries:
    def test_summary(self, rpc):
        s = rpc.summary()
        for key in ("worst_slack_max", "worst_slack_min",
                    "tns_max", "tns_min"):
            assert_ns_ps_pair(s, key)
        # design.tcl: setup met, a real hold violation exists
        assert s["worst_slack_max"] > 0
        assert s["worst_slack_min"] < 0
        assert s["endpoint_count"] == 7
        assert s["scenes"] == ["default"]

    def test_worst_paths(self, rpc):
        paths = rpc.worst_paths(n=3, path_delay="max")
        assert 1 <= len(paths) <= 3
        worst = paths[0]
        assert worst["endpoint"] == "r3/D"
        assert_ns_ps_pair(worst, "slack")
        assert worst["slack"] > 0

    def test_worst_paths_hold(self, rpc):
        paths = rpc.worst_paths(n=1, path_delay="min")
        assert paths[0]["slack"] < 0  # the known hold violation

    def test_get_path(self, rpc):
        p = rpc.get_path("r3/D", path_delay="max")
        assert_ns_ps_pair(p, "slack")
        assert "full_clock_expanded" not in p["report"]  # raw report text
        assert "data arrival time" in p["report"]
        assert "slack (MET)" in p["report"]

    def test_pin_timing(self, rpc):
        t = rpc.pin_timing("r3/D")
        for key in ("slack_max", "slack_min", "arrival_max",
                    "arrival_min", "slew_max", "slew_min"):
            assert_ns_ps_pair(t, key)
        assert t["slack_max"] is not None
        assert t["arrival_max"] >= t["arrival_min"]

    def test_endpoint_histogram(self, rpc):
        h = rpc.endpoint_histogram(bins=5)
        assert sum(h["counts"]) >= 1
        assert len(h["bins"]) == 6
        assert h["bins"] == sorted(h["bins"])

    def test_compare_corners(self, rpc):
        c = rpc.compare_corners()
        assert len(c["corners"]) == 1  # Phase 0: single default scene
        assert c["corners"][0]["corner"] == "default"
        assert_ns_ps_pair(c["corners"][0], "worst_slack_max")

    def test_check_exceptions_empty(self, rpc):
        # Constraint: the smoke design has no timing exceptions.
        e = rpc.check_exceptions()
        assert e["count"] == 0
        assert e["exceptions"] == []

    def test_clock_info(self, rpc):
        c = rpc.clock_info()
        assert len(c["clocks"]) == 1
        clk = c["clocks"][0]
        assert clk["name"] == "clk"
        assert clk["period"] == pytest.approx(10.0)
        assert clk["period_ps"] == 10000
        assert clk["is_generated"] is False
        assert set(clk["sources"]) == {"clk1", "clk2", "clk3"}

    def test_clock_info_virtual_clock(self, rpc):
        # A source-less clock must not desync the token stream (regression:
        # empty sources collapsed in the flat Tcl list).
        rpc.run_tcl("create_clock -name bt_virt -period 8")
        try:
            clocks = {c["name"]: c for c in rpc.clock_info()["clocks"]}
            assert clocks["bt_virt"]["sources"] == []
            assert clocks["bt_virt"]["period"] == pytest.approx(8.0)
            assert set(clocks["clk"]["sources"]) == {"clk1", "clk2", "clk3"}
        finally:
            # this OpenSTA names it delete_clock, not SDC's remove_clock
            rpc.run_tcl("delete_clock bt_virt")


class TestTclAndEco:
    def test_run_tcl(self, rpc):
        assert rpc.run_tcl("expr {6 * 7}") == "42"

    def test_run_tcl_error_raises(self, rpc):
        with pytest.raises(TclShellError):
            rpc.run_tcl("this_command_does_not_exist")

    def test_apply_eco_replace_cell(self, rpc):
        before = rpc.summary()["worst_slack_max"]
        result = rpc.apply_eco(
            [{"op": "replace_cell", "instance": "u1", "lib_cell": "BUF_X4"}])
        assert result["applied"][0]["ok"]
        after = result["summary"]["worst_slack_max"]
        assert after != before  # sizing u1 moved the r3/D path
        # restore
        rpc.apply_eco(
            [{"op": "replace_cell", "instance": "u1", "lib_cell": "BUF_X1"}])

    def test_apply_eco_insert_buffer(self, rpc):
        result = rpc.apply_eco([{
            "op": "insert_buffer", "name": "bt_eco_buf",
            "lib_cell": "BUF_X1", "net": "u1z",
        }])
        assert result["applied"][0]["ok"]
        # the buffer is now in the r3/D path: slack must have moved
        t = rpc.pin_timing("bt_eco_buf/Z")
        assert t["slack_max"] is not None

    def test_unknown_eco_op_rejected(self, rpc):
        with pytest.raises(ValueError, match="unknown ECO op"):
            rpc.apply_eco([{"op": "delete_everything"}])
