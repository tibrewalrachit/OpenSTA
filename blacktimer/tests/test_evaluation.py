"""Tests for the GPUTimer-methodology evaluation module.

The transcription checks recompute the paper's published speed-up columns
from the transcribed runtimes -- a typo in either table would show up as a
mismatch against the speed-ups printed in the paper.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.evaluation import (  # noqa: E402
    BC_SWEEP,
    BENCHMARKS,
    CORNER_COUNT,
    GPUTIMER_TABLE_I,
    GPUTIMER_TABLE_II,
    TAU15_14NM,
    CornerSweepRow,
    FullTimingRow,
    IncrementalSample,
    Platform,
    crossover,
    format_table_i,
    format_table_ii,
)

PLATFORM = Platform(gpu="B200", cpus=16, git_sha="abcdef0123456789")


class TestPublishedDataTranscription:
    def test_all_fifteen_benchmarks_present(self):
        assert len(TAU15_14NM) == 15
        assert set(GPUTIMER_TABLE_I) == set(BENCHMARKS)
        assert set(GPUTIMER_TABLE_II) == set(BENCHMARKS)

    def test_table_ii_covers_full_bc_sweep(self):
        for runtimes in GPUTIMER_TABLE_II.values():
            assert set(runtimes) == set(BC_SWEEP)

    @pytest.mark.parametrize("name,expected", [
        ("aes_core_14nm", 0.98),   # the one sub-1x design in the paper
        ("leon2_14nm", 4.07),      # largest published Table I speed-up
        ("netcard_14nm", 4.05),
        ("leon3mp_14nm", 3.51),
    ])
    def test_table_i_speedups_match_paper(self, name, expected):
        base, ours = GPUTIMER_TABLE_I[name]
        assert base / ours == pytest.approx(expected, abs=0.005)

    @pytest.mark.parametrize("name,bc,expected", [
        ("leon2_14nm", 16, 5.66),   # paper: 5.66x-6.34x on the big three
        ("leon3mp_14nm", 16, 6.31),
        ("netcard_14nm", 16, 6.34),
        ("leon2_14nm", 4, 3.85),    # paper: 3.85x-3.90x at BC=4
        ("netcard_14nm", 4, 3.90),
        ("tip_master_14nm", 16, 8.89),  # >8x on small designs, per paper
    ])
    def test_table_ii_speedups_match_paper(self, name, bc, expected):
        row = CornerSweepRow(name, dict(GPUTIMER_TABLE_II[name]))
        assert row.speedup(bc) == pytest.approx(expected, abs=0.005)

    def test_stats_sanity(self):
        leon2 = BENCHMARKS["leon2_14nm"]
        assert leon2.gates == 1616369  # "1.6M gates" in the paper text
        assert leon2.nodes == 22450936
        assert CORNER_COUNT == 128


class TestFullTimingRow:
    def test_speedup_and_published_comparison(self):
        row = FullTimingRow("leon2_14nm", ours_ms=1000.0, baseline_ms=10000.0)
        assert row.speedup == pytest.approx(10.0)
        vs_ot16, vs_gput = row.vs_published()
        assert vs_ot16 == pytest.approx(23.928)
        assert vs_gput == pytest.approx(5.879)

    def test_no_baseline(self):
        row = FullTimingRow("leon2_14nm", ours_ms=1000.0)
        assert row.speedup is None


class TestCrossover:
    def test_clean_crossover(self):
        samples = [
            IncrementalSample(1_000, gpu_ms=80, cpu_ms=10),
            IncrementalSample(10_000, gpu_ms=90, cpu_ms=60),
            IncrementalSample(67_000, gpu_ms=100, cpu_ms=110),
            IncrementalSample(500_000, gpu_ms=200, cpu_ms=900),
        ]
        assert crossover(samples) == 67_000

    def test_later_cpu_win_invalidates_earlier_threshold(self):
        samples = [
            IncrementalSample(10_000, gpu_ms=50, cpu_ms=60),   # GPU wins early
            IncrementalSample(50_000, gpu_ms=120, cpu_ms=100),  # then loses
            IncrementalSample(100_000, gpu_ms=150, cpu_ms=400),
        ]
        assert crossover(samples) == 100_000

    def test_gpu_never_wins(self):
        samples = [IncrementalSample(1_000, gpu_ms=90, cpu_ms=10)]
        assert crossover(samples) is None

    def test_unsorted_input(self):
        samples = [
            IncrementalSample(500_000, gpu_ms=200, cpu_ms=900),
            IncrementalSample(1_000, gpu_ms=80, cpu_ms=10),
            IncrementalSample(67_000, gpu_ms=100, cpu_ms=110),
        ]
        assert crossover(samples) == 67_000


class TestFormatting:
    def test_table_i_markdown(self):
        rows = [FullTimingRow("leon2_14nm", ours_ms=500.0, baseline_ms=25000.0)]
        text = format_table_i(rows, PLATFORM)
        assert "leon2_14nm" in text
        assert "50.00x" in text          # measured speedup
        assert "47.86x" in text          # vs published OpenTimer-16T
        assert "B200, 16 CPUs @ abcdef012345" in text

    def test_table_ii_markdown(self):
        rows = [CornerSweepRow("netcard_14nm",
                               dict(GPUTIMER_TABLE_II["netcard_14nm"]))]
        text = format_table_ii(rows, PLATFORM)
        assert "128-corner" in text
        assert "6.34x" in text
        # every BC column present
        for bc in BC_SWEEP:
            assert f"BC={bc}" in text
