"""BlackTimer Modal application (DESIGN.md section 4).

Phase 0 scaffolding: the app topology -- image, volumes, and the function
families of section 4.1 -- is real and runnable today, with the CPU engine
(vendored OpenSTA, compiled by build() into the build-cache volume) standing
in for the GPU engine everywhere a kernel will eventually run. `golden_diff`
therefore executes as the null test from section 9 Phase 0: OpenSTA on both
sides, proving the harness end to end before any CUDA lands.

Usage (requires a Modal account and `pip install modal`):

    modal run blacktimer/modal_app.py::seed_designs   # once: upload smoke designs
    modal run blacktimer/modal_app.py::ci             # the per-merge gate
    modal run blacktimer/modal_app.py::diff --design gcd_sky130hd
"""

# NOTE: no `from __future__ import annotations` here -- Modal's class
# parameter encoder resolves annotations at runtime and PEP 563 string
# annotations break modal.parameter().

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import modal

for _p in (Path(__file__).resolve().parent,
           Path("/opt/blacktimer/OpenSTA/blacktimer")):
    if (_p / "session_rpc.py").exists():
        sys.path.insert(0, str(_p))
        break
from session_rpc import SessionRpcMixin  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

app = modal.App("blacktimer")

# ---------------------------------------------------------------------------
# Image: CUDA toolkit + OpenSTA deps + vendored OpenSTA built from this repo.
# Mirrors Dockerfile.ubuntu22.04 at the repo root, on a CUDA devel base so
# Phase 1 kernels compile in the same image.
# ---------------------------------------------------------------------------
image = (
    modal.Image.from_registry(
        # 12.8+: first CUDA with Blackwell (sm_100) codegen for the B200s
        "nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.11"
    )
    .apt_install(
        "git", "wget", "cmake", "ninja-build", "gcc", "g++", "gdb",
        "tcl-dev", "tcl-tclreadline", "swig", "bison", "flex",
        "automake", "autotools-dev", "libgtest-dev", "libeigen3-dev",
        "libfmt-dev", "ccache", "zlib1g-dev",
    )
    .run_commands(
        # CUDD (BDD package OpenSTA links against)
        "wget -q https://raw.githubusercontent.com/davidkebo/cudd/main/"
        "cudd_versions/cudd-3.0.0.tar.gz",
        "tar -xf cudd-3.0.0.tar.gz && rm cudd-3.0.0.tar.gz",
        "cd cudd-3.0.0 && ./configure && make -j$(nproc)",
    )
    .pip_install("pytest", "py7zr")
    # Vendored OpenSTA source == this repository (DESIGN.md section 2.3:
    # reuse OpenSTA's readers rather than rewriting parsers). Deliberately
    # NOT compiled here: a compile layer makes the image build a ~30-minute
    # streamed operation that flaky client connections abort. build() below
    # compiles into the build-cache volume with ccache instead.
    .add_local_dir(
        str(REPO_ROOT),
        "/opt/blacktimer/OpenSTA",
        copy=True,
        ignore=[".git", "build", "**/__pycache__"],
    )
    # session_rpc is imported by this definition file at load time, so it
    # must ship alongside it in the container (the copy inside the vendored
    # tree above is for the pytest run, not for this module's import).
    .add_local_python_source("session_rpc")
)

SRC = "/opt/blacktimer/OpenSTA"
BT = f"{SRC}/blacktimer"

designs_vol = modal.Volume.from_name("blacktimer-designs", create_if_missing=True)
cache_vol = modal.Volume.from_name("blacktimer-build-cache", create_if_missing=True)

DESIGNS = "/designs"
CACHE = "/cache"
# The sta binary lives on the build-cache volume (compiled by build(),
# reused by every other function; survives image rebuilds via ccache).
STA = f"{CACHE}/build/sta"

# ---------------------------------------------------------------------------
# Engine dispatch. Phase 0 has exactly one engine (OpenSTA on CPU); the GPU
# engine registers itself here in Phase 1. golden_diff always uses "cpu" as
# the reference and DUT_ENGINE as the device under test, so flipping this one
# constant turns the null test into the real diff.
# ---------------------------------------------------------------------------
DUT_ENGINE = "cpu"


def _load_design(name: str) -> dict:
    """Read a design descriptor from the designs volume.

    Layout: /designs/<name>/design.json with keys liberty (list), verilog,
    top, sdc, and optionally spef -- all paths relative to the design dir.
    """
    design_dir = Path(DESIGNS) / name
    meta = json.loads((design_dir / "design.json").read_text())
    meta["dir"] = str(design_dir)
    return meta


def _run_engine(engine: str, design: dict, out_csv: str) -> None:
    """Run one full-timing pass and dump endpoint slacks to out_csv."""
    if engine != "cpu":
        raise NotImplementedError(
            f"engine {engine!r}: GPU engine lands in Phase 1 (DESIGN.md s9)"
        )
    d = Path(design["dir"])
    env = dict(
        os.environ,
        BT_LIBERTY=" ".join(str(d / lib) for lib in design["liberty"]),
        BT_VERILOG=str(d / design["verilog"]),
        BT_TOP=design["top"],
        BT_SDC=str(d / design["sdc"]),
        BT_OUT=out_csv,
    )
    if design.get("spef"):
        env["BT_SPEF"] = str(d / design["spef"])
    subprocess.run(
        [STA, "-no_init", "-exit", f"{BT}/scripts/dump_endpoints.tcl"],
        env=env, check=True,
    )


@app.function(image=image, volumes={CACHE: cache_vol}, cpu=16, timeout=3600)
def build() -> str:
    """[CPU] Compile the engine into the build-cache volume (DESIGN.md
    s4.1). ccache lives on the same volume, so rebuilds after source-only
    image refreshes are incremental; the resulting sta binary at
    {CACHE}/build/sta is shared by every other function."""
    env = dict(
        os.environ,
        CCACHE_DIR=f"{CACHE}/ccache",
        CMAKE_CXX_COMPILER_LAUNCHER="ccache",
        CMAKE_C_COMPILER_LAUNCHER="ccache",
    )
    build_dir = f"{CACHE}/build"
    subprocess.run(
        ["cmake", "-G", "Ninja", "-B", build_dir, "-S", SRC,
         "-DCUDD_DIR=/cudd-3.0.0",
         "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache",
         "-DCMAKE_C_COMPILER_LAUNCHER=ccache"],
        env=env, check=True,
    )
    subprocess.run(["ninja", "-C", build_dir, "sta"], env=env, check=True)
    cache_vol.commit()
    return "build ok: " + subprocess.run(
        [STA, "-version"], capture_output=True, text=True).stdout.strip()


@app.function(image=image, gpu="B200", volumes={CACHE: cache_vol},
              timeout=1800)
def unit_tests() -> str:
    """[B200] Kernel-level tests. Phase 0: harness tests + GPU smoke check."""
    subprocess.run(["nvidia-smi"], check=True)
    env = dict(os.environ, STA_BIN=STA, STA_DESIGN=f"{SRC}/work/design.tcl")
    subprocess.run(
        ["python", "-m", "pytest", f"{BT}/tests", "-q"], env=env, check=True
    )
    return "unit tests ok"


@app.function(image=image, gpu="B200",
              volumes={DESIGNS: designs_vol, CACHE: cache_vol}, timeout=3600)
def full_sta(design_name: str, corner: str = "default") -> str:
    """[B200] One end-to-end timing run; returns the endpoint-slack CSV."""
    design = _load_design(design_name)
    out = f"/tmp/{design_name}.{corner}.{DUT_ENGINE}.csv"
    _run_engine(DUT_ENGINE, design, out)
    return Path(out).read_text()


@app.function(image=image, volumes={DESIGNS: designs_vol}, timeout=3600)
def mmmc_sweep(design_name: str, corners: list[str]) -> dict[str, str]:
    """[fan-out coordinator] Map corner batches over B200 containers.

    Phase 3 replaces per-corner full runs with a shared graph image on the
    volume plus corner-batched (BC<=8) workers; the fan-out shape via
    .starmap is already the final one.
    """
    results = full_sta.starmap((design_name, c) for c in corners)
    return dict(zip(corners, results))


@app.function(image=image, volumes={DESIGNS: designs_vol, CACHE: cache_vol},
              memory=32768, timeout=7200)
def golden_diff(design_name: str) -> str:
    """[CPU, high-mem] Reference OpenSTA vs DUT engine, 1 ps endpoint gate.

    Phase 0: DUT is also the CPU engine (null test). Raises on divergence so
    CI fails loudly. Waivers live at /designs/<name>/waivers.json.
    """
    import sys

    sys.path.insert(0, BT)
    from harness import golden_diff as gd

    design = _load_design(design_name)
    golden_csv = f"/tmp/{design_name}.golden.csv"
    dut_csv = f"/tmp/{design_name}.dut.csv"
    _run_engine("cpu", design, golden_csv)
    _run_engine(DUT_ENGINE, design, dut_csv)

    waivers = Path(design["dir"]) / "waivers.json"
    result = gd.diff_files(
        golden_csv, dut_csv, waivers if waivers.exists() else None
    )
    print(result.summary())
    if not result.clean:
        raise RuntimeError(f"golden_diff diverged on {design_name}")
    return result.summary()


@app.function(image=image, gpu="B200",
              volumes={DESIGNS: designs_vol, CACHE: cache_vol}, timeout=3600)
def bench(design_name: str, corner: str = "default") -> dict:
    """[B200] Perf harness following the GPUTimer TCAD'23 protocol
    (EVALUATION.md E1): one full-timing iteration measured end-to-end,
    memory preparation included. Phase 0: wall-clock only; Phase 2 adds the
    per-stage breakdown and NCU capture to the designs volume."""
    import multiprocessing
    import sys

    sys.path.insert(0, BT)
    from harness.evaluation import FullTimingRow, Platform

    design = _load_design(design_name)
    t0 = time.monotonic()
    _run_engine(DUT_ENGINE, design, f"/tmp/{design_name}.bench.csv")
    row = FullTimingRow(design_name,
                        ours_ms=(time.monotonic() - t0) * 1000.0)
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
        capture_output=True, text=True,
    ).stdout.strip()
    platform = Platform(gpu=gpu or "unknown",
                        cpus=multiprocessing.cpu_count())
    return {
        "protocol": "E1 full timing (EVALUATION.md)",
        "benchmark": row.benchmark,
        "corner": corner,
        "engine": DUT_ENGINE,
        "ours_ms": row.ours_ms,
        # published-baseline ratios exist only for the TAU15-14nm suite
        "vs_published": row.vs_published(),
        "platform": platform.caption(),
    }


def _write_design_tcl(design: dict) -> str:
    """Emit a load script for a design.json descriptor (TimerSession's
    equivalent of the env-var plumbing in _run_engine)."""
    d = Path(design["dir"])
    lines = [f"read_liberty {{{d / lib}}}" for lib in design["liberty"]]
    lines.append(f"read_verilog {{{d / design['verilog']}}}")
    lines.append(f"link_design {{{design['top']}}}")
    if design.get("spef"):
        lines.append(f"read_spef {{{d / design['spef']}}}")
    lines.append(f"read_sdc {{{d / design['sdc']}}}")
    path = f"/tmp/{design['top']}.load.tcl"
    Path(path).write_text("\n".join(lines) + "\n")
    return path


@app.cls(image=image, gpu="B200",
         volumes={DESIGNS: designs_vol, CACHE: cache_vol},
         scaledown_window=600)
class TimerSession(SessionRpcMixin):
    """[B200] Long-lived incremental server (DESIGN.md s4.1, s7).

    Phase 0: loads the design into a persistent OpenSTA process on enter;
    the sta-claude RPC surface (session_rpc.SessionRpcMixin) serves queries
    and ECOs against it. Phase 4 swaps the GPU engine + command channel in
    behind the same methods.
    """

    design_name: str = modal.parameter()

    @modal.enter()
    def load(self) -> None:
        self.design = _load_design(self.design_name)
        self.rpc_init(STA, _write_design_tcl(self.design))

    @modal.exit()
    def unload(self) -> None:
        self.rpc_close()

    # sta-claude RPC surface (server/backends/blacktimer.py client).
    # Modal only exposes decorated attributes of the class itself, so the
    # mixin methods get one-line remote forwarders here.

    @modal.method()
    def summary(self) -> dict:
        return SessionRpcMixin.summary(self)

    @modal.method()
    def worst_paths(self, n: int = 5, path_delay: str = "max") -> list:
        return SessionRpcMixin.worst_paths(self, n, path_delay)

    @modal.method()
    def get_path(self, endpoint: str, path_delay: str = "max") -> dict:
        return SessionRpcMixin.get_path(self, endpoint, path_delay)

    @modal.method()
    def pin_timing(self, pin: str) -> dict:
        return SessionRpcMixin.pin_timing(self, pin)

    @modal.method()
    def endpoint_histogram(self, bins: int = 10,
                           path_delay: str = "max") -> dict:
        return SessionRpcMixin.endpoint_histogram(self, bins, path_delay)

    @modal.method()
    def compare_corners(self) -> dict:
        return SessionRpcMixin.compare_corners(self)

    @modal.method()
    def check_exceptions(self) -> dict:
        return SessionRpcMixin.check_exceptions(self)

    @modal.method()
    def clock_info(self) -> dict:
        return SessionRpcMixin.clock_info(self)

    @modal.method()
    def run_tcl(self, script: str) -> str:
        return SessionRpcMixin.run_tcl(self, script)

    @modal.method()
    def apply_eco(self, edits: list) -> dict:
        return SessionRpcMixin.apply_eco(self, edits)

    @modal.method()
    def report_wns(self) -> float:
        """Back-compat convenience: worst setup slack in ns."""
        return self.summary()["worst_slack_max"]


# ---------------------------------------------------------------------------
# GCS-Timer (cuhk-eda, DAC'24, BSD-3): GPU-accelerated CCS-model timing.
# Runs as its own function family -- its frontend is fixed to the four EPFL
# benchmarks it ships (mul/log2/div/hyp) plus the ASAP7 RVT_TT CCS libraries,
# so it does not plug into the design.json flow. PrimeTime and HSPICE
# reference results ship with the benchmarks, so its accuracy claims are
# reproducible here without any commercial tool.
# ---------------------------------------------------------------------------

GCS = f"{DESIGNS}/gcs"
GCS_DESIGNS = ("mul", "log2", "div", "hyp")


@app.function(image=image, volumes={DESIGNS: designs_vol}, cpu=8,
              timeout=7200)
def seed_gcs() -> str:
    """[CPU] Fetch GCS-Timer + ASAP7 CCS libs into the designs volume and
    compile both binary variants (GBA and EVALUATE=1 stage-accuracy mode).
    nvcc compiles fine without a GPU; sm_100 targets the B200, with a
    compute_90 PTX fallback for older cards."""
    import shutil
    import zipfile

    import py7zr

    gcs = Path(GCS)
    if not (gcs / "src").exists():
        subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/cuhk-eda/GCS-Timer.git", str(gcs)],
            check=True)
    asap = Path("/tmp/asap7")
    if not asap.exists():
        subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none",
             "--sparse",
             "https://github.com/The-OpenROAD-Project/asap7sc7p5t_28.git",
             str(asap)], check=True)
        subprocess.run(["git", "-C", str(asap), "sparse-checkout", "set",
                        "LIB/CCS"], check=True)
    (gcs / "lib").mkdir(exist_ok=True)
    for lib in ("INVBUF_RVT_TT_ccs_220122", "SIMPLE_RVT_TT_ccs_211120",
                "AO_RVT_TT_ccs_211120", "OA_RVT_TT_ccs_211120"):
        name = f"asap7sc7p5t_{lib}.lib"
        if not (gcs / "lib" / name).exists():
            with py7zr.SevenZipFile(asap / "LIB/CCS" / f"{name}.7z") as z:
                z.extractall(gcs / "lib")
    for d in ("div", "hyp"):
        spef = gcs / "bm" / d / "test.spef"
        if not spef.exists():
            zipfile.ZipFile(f"{spef}.zip").extractall(spef.parent)

    nvcc_flags = ["-std=c++14", "-O3", "-x", "cu",
                  "-gencode", "arch=compute_100,code=sm_100",
                  "-gencode", "arch=compute_90,code=compute_90"]
    subprocess.run(["nvcc", *nvcc_flags, str(gcs / "src/main.cpp"),
                    "-o", str(gcs / "GCS_Timer")], check=True, cwd=gcs)
    eval_src = Path("/tmp/gcs_eval_src")
    if eval_src.exists():
        shutil.rmtree(eval_src)
    shutil.copytree(gcs / "src", eval_src)
    hpp = eval_src / "gpu_timer.hpp"
    hpp.write_text(hpp.read_text().replace(
        "#define EVALUATE 0", "#define EVALUATE 1", 1))
    subprocess.run(["nvcc", *nvcc_flags, str(eval_src / "main.cpp"),
                    "-o", str(gcs / "GCS_Timer_evaluate")], check=True,
                   cwd=gcs)
    designs_vol.commit()
    return "gcs seeded: binaries + 4 CCS libs + 4 benchmarks"


@app.function(image=image, gpu="B200", volumes={DESIGNS: designs_vol},
              timeout=3600)
def gcs_timer(design: str, cpu: bool = False, evaluate: bool = False) -> dict:
    """[B200] One GCS-Timer run. evaluate=True reproduces the paper's
    stage-delay accuracy comparison against the shipped PrimeTime/HSPICE
    references; otherwise GBA arrivals (self-compared against test.pt)."""
    if design not in GCS_DESIGNS:
        raise ValueError(f"design must be one of {GCS_DESIGNS}")
    binary = "./GCS_Timer_evaluate" if evaluate else "./GCS_Timer"
    args = [binary, design] + (["-CPU"] if cpu else [])
    t0 = time.monotonic()
    proc = subprocess.run(args, cwd=GCS, capture_output=True, text=True,
                          check=True)
    return {
        "design": design,
        "mode": ("CPU" if cpu else "GPU") + ("/EVALUATE" if evaluate else ""),
        "wall_seconds": time.monotonic() - t0,
        "log": proc.stdout[-8000:],
    }


# ---------------------------------------------------------------------------
# Local entrypoints
# ---------------------------------------------------------------------------

# Smoke designs seeded from this repo's examples/ so the Phase 0 gate needs
# no external benchmark download. TAU15/19 and leon/netcard/vga are added to
# the volume separately (licensing requires fetching them from the contest
# sites; see README.md).
SMOKE_DESIGNS = {
    "gcd_sky130hd": {
        "liberty": ["sky130hd_tt.lib.gz"],
        "verilog": "gcd_sky130hd.v",
        "top": "gcd",
        "spef": "gcd_sky130hd.spef",
        "sdc": "gcd_sky130hd.sdc",
    },
}


@app.local_entrypoint()
def seed_designs() -> None:
    """Upload the in-repo smoke designs to the designs volume (run once)."""
    examples = REPO_ROOT / "examples"
    with designs_vol.batch_upload(force=True) as batch:
        for name, meta in SMOKE_DESIGNS.items():
            files = [meta["verilog"], meta["sdc"], *meta["liberty"]]
            if meta.get("spef"):
                files.append(meta["spef"])
            missing = [f for f in files if not (examples / f).exists()]
            if missing:
                print(f"skipping {name}: missing {missing}")
                continue
            for f in files:
                batch.put_file(str(examples / f), f"/{name}/{f}")
            desc = {k: v for k, v in meta.items()}
            batch.put_file(
                _tmp_json(desc), f"/{name}/design.json"
            )
            print(f"seeded {name}")


def _tmp_json(obj: dict) -> str:
    import tempfile

    fh = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False)
    json.dump(obj, fh, indent=2)
    fh.close()
    return fh.name


@app.local_entrypoint()
def ci() -> None:
    """The per-merge gate (DESIGN.md s9 cost model): build + unit tests +
    golden diff on the smoke designs."""
    print(build.remote())
    print(unit_tests.remote())
    for summary in golden_diff.map(SMOKE_DESIGNS.keys()):
        print(summary)


@app.local_entrypoint()
def diff(design: str) -> None:
    print(golden_diff.remote(design))


@app.local_entrypoint()
def gcs(design: str = "mul", cpu: bool = False, evaluate: bool = False) -> None:
    """Run GCS-Timer on a benchmark (seed once with ::seed_gcs)."""
    result = gcs_timer.remote(design, cpu, evaluate)
    print(f"{result['design']} [{result['mode']}] "
          f"wall={result['wall_seconds']:.2f}s")
    print(result["log"])
