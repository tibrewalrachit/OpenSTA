"""Endpoint-slack golden diff for BlackTimer (DESIGN.md section 8).

Compares two endpoint-slack dumps -- a golden run (OpenSTA CPU engine) and a
device-under-test run (BlackTimer GPU engine; in Phase 0 the CPU engine again,
as the null test proving this harness) -- endpoint by endpoint.

Gate: every endpoint must agree within 1 ps absolute OR 0.1% relative
(DESIGN.md section 2.2). Known modeling divergences are recorded as explicit
waivers, each with a mandatory reason, so a passing diff is always
expected-clean rather than quietly tolerant.

Dump format (produced by scripts/dump_endpoints.tcl): CSV with a header row,
one row per endpoint pin:

    endpoint,slack_min,slack_max

Slacks are in the library time unit reported by OpenSTA (seconds when
scripts/dump_endpoints.tcl is used, which sets time units explicitly).
"""

from __future__ import annotations

import csv
import fnmatch
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

# DESIGN.md section 2.2: 1 ps absolute or 0.1% relative.
ABS_TOL_SECONDS = 1e-12
REL_TOL = 1e-3

_CHECKS = ("slack_min", "slack_max")


@dataclass
class EndpointSlacks:
    slack_min: float
    slack_max: float


@dataclass
class Waiver:
    """A documented, pattern-matched exemption from the 1 ps gate."""

    endpoint_pattern: str
    reason: str

    def matches(self, endpoint: str) -> bool:
        return fnmatch.fnmatchcase(endpoint, self.endpoint_pattern)


@dataclass
class Divergence:
    endpoint: str
    check: str  # "slack_min" | "slack_max" | "missing" | "extra"
    golden: float | None
    dut: float | None
    waived_by: Waiver | None = None

    def __str__(self) -> str:
        tag = f" [waived: {self.waived_by.reason}]" if self.waived_by else ""
        return (
            f"{self.endpoint} {self.check}: "
            f"golden={self.golden} dut={self.dut}{tag}"
        )


@dataclass
class DiffResult:
    endpoints_compared: int = 0
    failures: list[Divergence] = field(default_factory=list)
    waived: list[Divergence] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        status = "CLEAN" if self.clean else "DIVERGED"
        lines = [
            f"golden_diff: {status} "
            f"({self.endpoints_compared} endpoints, "
            f"{len(self.failures)} failures, {len(self.waived)} waived)"
        ]
        lines += [f"  FAIL {d}" for d in self.failures]
        lines += [f"  WAIVED {d}" for d in self.waived]
        return "\n".join(lines)


def slacks_agree(golden: float, dut: float,
                 abs_tol: float = ABS_TOL_SECONDS,
                 rel_tol: float = REL_TOL) -> bool:
    """1 ps absolute OR 0.1% relative, per DESIGN.md section 2.2.

    Both-infinite slacks (unconstrained endpoints reported as INF) agree;
    NaN never agrees with anything, including itself -- a NaN slack is a bug.
    """
    if math.isnan(golden) or math.isnan(dut):
        return False
    if math.isinf(golden) or math.isinf(dut):
        return golden == dut
    diff = abs(golden - dut)
    return diff <= abs_tol or diff <= rel_tol * abs(golden)


def load_endpoint_csv(path: str | Path) -> dict[str, EndpointSlacks]:
    endpoints: dict[str, EndpointSlacks] = {}
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        missing = {"endpoint", *_CHECKS} - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for row in reader:
            endpoints[row["endpoint"]] = EndpointSlacks(
                slack_min=float(row["slack_min"]),
                slack_max=float(row["slack_max"]),
            )
    return endpoints


def load_waivers(path: str | Path) -> list[Waiver]:
    """Waivers file: JSON list of {"endpoint_pattern": ..., "reason": ...}."""
    entries = json.loads(Path(path).read_text())
    waivers = []
    for entry in entries:
        if not entry.get("reason"):
            raise ValueError(
                f"waiver {entry.get('endpoint_pattern')!r} has no reason; "
                "every waiver must document its modeling difference"
            )
        waivers.append(Waiver(entry["endpoint_pattern"], entry["reason"]))
    return waivers


def diff_endpoints(golden: dict[str, EndpointSlacks],
                   dut: dict[str, EndpointSlacks],
                   waivers: list[Waiver] | None = None,
                   abs_tol: float = ABS_TOL_SECONDS,
                   rel_tol: float = REL_TOL) -> DiffResult:
    waivers = waivers or []
    result = DiffResult()

    def record(div: Divergence) -> None:
        div.waived_by = next(
            (w for w in waivers if w.matches(div.endpoint)), None)
        (result.waived if div.waived_by else result.failures).append(div)

    for endpoint in sorted(golden.keys() | dut.keys()):
        g, d = golden.get(endpoint), dut.get(endpoint)
        if g is None:
            record(Divergence(endpoint, "extra", None, None))
            continue
        if d is None:
            record(Divergence(endpoint, "missing", None, None))
            continue
        result.endpoints_compared += 1
        for check in _CHECKS:
            gv, dv = getattr(g, check), getattr(d, check)
            if not slacks_agree(gv, dv, abs_tol, rel_tol):
                record(Divergence(endpoint, check, gv, dv))
    return result


def diff_files(golden_csv: str | Path, dut_csv: str | Path,
               waivers_json: str | Path | None = None) -> DiffResult:
    waivers = load_waivers(waivers_json) if waivers_json else []
    return diff_endpoints(load_endpoint_csv(golden_csv),
                          load_endpoint_csv(dut_csv), waivers)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("golden_csv")
    parser.add_argument("dut_csv")
    parser.add_argument("--waivers", help="JSON waiver file")
    args = parser.parse_args(argv)

    result = diff_files(args.golden_csv, args.dut_csv, args.waivers)
    print(result.summary())
    return 0 if result.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
