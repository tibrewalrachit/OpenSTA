"""Unit tests for the golden-diff harness (pure Python, no GPU, no Modal).

These run in the plain-CPU CI job and inside unit_tests() on Modal.
"""

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.golden_diff import (  # noqa: E402
    EndpointSlacks,
    Waiver,
    diff_endpoints,
    diff_files,
    load_endpoint_csv,
    load_waivers,
    slacks_agree,
)

PS = 1e-12


def ep(smin, smax):
    return EndpointSlacks(slack_min=smin, slack_max=smax)


class TestSlacksAgree:
    def test_identical(self):
        assert slacks_agree(1.234e-9, 1.234e-9)

    def test_within_one_ps(self):
        assert slacks_agree(1.0e-9, 1.0e-9 + 0.9 * PS)

    def test_beyond_one_ps_small_slack(self):
        assert not slacks_agree(0.0, 2 * PS)

    def test_relative_tolerance_rescues_large_slacks(self):
        # 5 ps apart but only 0.05% of a 10 ns slack.
        assert slacks_agree(10e-9, 10e-9 + 5 * PS)

    def test_relative_tolerance_bound(self):
        # 0.2% of 10 ns: outside both gates.
        assert not slacks_agree(10e-9, 10e-9 * 1.002)

    def test_sign_matters(self):
        assert not slacks_agree(-50 * PS, 50 * PS)

    def test_unconstrained_endpoints_agree(self):
        assert slacks_agree(math.inf, math.inf)
        assert not slacks_agree(math.inf, 1e-9)
        assert not slacks_agree(math.inf, -math.inf)

    def test_nan_never_agrees(self):
        assert not slacks_agree(math.nan, math.nan)
        assert not slacks_agree(1e-9, math.nan)


class TestDiffEndpoints:
    def test_null_diff_is_clean(self):
        eps = {"u1/D": ep(1e-9, 2e-9), "u2/D": ep(-1e-10, 3e-9)}
        result = diff_endpoints(eps, dict(eps))
        assert result.clean
        assert result.endpoints_compared == 2

    def test_divergence_fails(self):
        golden = {"u1/D": ep(0.0, 1e-9)}
        dut = {"u1/D": ep(5 * PS, 1e-9)}
        result = diff_endpoints(golden, dut)
        assert not result.clean
        assert result.failures[0].check == "slack_min"
        assert result.failures[0].endpoint == "u1/D"

    def test_missing_endpoint_fails(self):
        result = diff_endpoints({"u1/D": ep(0, 0)}, {})
        assert not result.clean
        assert result.failures[0].check == "missing"

    def test_extra_endpoint_fails(self):
        result = diff_endpoints({}, {"u9/D": ep(0, 0)})
        assert not result.clean
        assert result.failures[0].check == "extra"

    def test_waiver_moves_divergence_to_waived(self):
        golden = {"clkgate_7/GCLK": ep(0.0, 1e-9)}
        dut = {"clkgate_7/GCLK": ep(9 * PS, 1e-9)}
        waivers = [Waiver("clkgate_*/GCLK", "slew merge policy difference")]
        result = diff_endpoints(golden, dut, waivers)
        assert result.clean
        assert len(result.waived) == 1
        assert result.waived[0].waived_by.reason == \
            "slew merge policy difference"

    def test_waiver_does_not_hide_other_endpoints(self):
        golden = {"a/D": ep(0.0, 0.0), "b/D": ep(0.0, 0.0)}
        dut = {"a/D": ep(9 * PS, 0.0), "b/D": ep(9 * PS, 0.0)}
        result = diff_endpoints(golden, dut, [Waiver("a/D", "documented")])
        assert not result.clean
        assert [f.endpoint for f in result.failures] == ["b/D"]


class TestFileIo:
    def _write_csv(self, path, rows):
        path.write_text(
            "endpoint,slack_min,slack_max\n"
            + "".join(f"{n},{a},{b}\n" for n, a, b in rows)
        )

    def test_round_trip_and_null_diff(self, tmp_path):
        csv_path = tmp_path / "g.csv"
        self._write_csv(csv_path, [("u1/D", "1.5e-10", "2.0e-09"),
                                   ("u2/D", "inf", "inf")])
        eps = load_endpoint_csv(csv_path)
        assert eps["u1/D"].slack_min == pytest.approx(1.5e-10)
        assert math.isinf(eps["u2/D"].slack_max)
        assert diff_files(csv_path, csv_path).clean

    def test_diff_files_with_waivers(self, tmp_path):
        g, d = tmp_path / "g.csv", tmp_path / "d.csv"
        self._write_csv(g, [("u1/D", "0.0", "1e-9")])
        self._write_csv(d, [("u1/D", "5e-12", "1e-9")])
        assert not diff_files(g, d).clean

        wpath = tmp_path / "waivers.json"
        wpath.write_text(json.dumps(
            [{"endpoint_pattern": "u1/D", "reason": "documented diff"}]))
        assert diff_files(g, d, wpath).clean

    def test_missing_column_rejected(self, tmp_path):
        bad = tmp_path / "bad.csv"
        bad.write_text("endpoint,slack_max\nu1/D,1e-9\n")
        with pytest.raises(ValueError, match="slack_min"):
            load_endpoint_csv(bad)

    def test_waiver_without_reason_rejected(self, tmp_path):
        wpath = tmp_path / "w.json"
        wpath.write_text(json.dumps([{"endpoint_pattern": "*"}]))
        with pytest.raises(ValueError, match="reason"):
            load_waivers(wpath)
