"""TimerSession RPC surface for the sta-claude LLM timing-debug layer.

SessionRpcMixin provides the methods sta-claude's blacktimer backend
(server/backends/blacktimer.py) calls on the Modal TimerSession class:

    summary, worst_paths, get_path, pin_timing, endpoint_histogram,
    compare_corners, check_exceptions, clock_info, run_tcl, apply_eco

Phase 0 engine reality: the "existing query API" is the vendored OpenSTA
process, so every method is a thin wrapper that drives `sta -no_splash`
over a persistent Tcl pipe. When the GPU engine lands (Phase 1+), these
wrappers re-target its query API without changing the RPC surface.

Field conventions (matching sta-claude's opensta backend): times/slacks are
ns floats, with a sibling integer picosecond field suffixed `_ps` for every
slack/arrival/required value. Unconstrained values are None (never fake
numbers).

Command-name drift already reconciled against this OpenSTA version (see
work/OPENSTA_BACKEND_VALIDATION.md):
  - corners are "scenes" here: `sta::scenes` / `get_scenes`, not
    `sta::corners` (renamed upstream 11/2025);
  - `report_exceptions` does not exist; exceptions are recovered from
    `write_sdc -no_timestamp` output;
  - `insert_buffer` does not exist; it is composed from make_instance /
    make_net / connect_pin / disconnect_pin (all verified).
"""

import re
import subprocess
import tempfile
from pathlib import Path

PS_PER_NS = 1000.0

_END_SENTINEL = "__BT_EOC__"


def _ps(value_ns):
    """Integer picoseconds for an ns float (None passes through)."""
    return None if value_ns is None else int(round(value_ns * PS_PER_NS))


def _maybe_float(text):
    """Parse OpenSTA float output; INF/-INF/non-numeric -> None."""
    token = text.strip()
    if not token or "inf" in token.lower():
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _slack_fields(prefix, value_ns):
    return {prefix: value_ns, f"{prefix}_ps": _ps(value_ns)}


class TclShellError(RuntimeError):
    pass


class TclShell:
    """A persistent `sta -no_splash` process driven over stdin/stdout."""

    def __init__(self, sta_binary, design_tcl=None):
        self._proc = subprocess.Popen(
            [str(sta_binary), "-no_splash"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        # Swallow any banner up to the first sentinel.
        self._proc.stdin.write(f"puts {_END_SENTINEL}OK\n")
        self._proc.stdin.flush()
        self._read_until_sentinel()
        if design_tcl:
            self.eval(f"source {{{design_tcl}}}")

    def _read_until_sentinel(self):
        lines = []
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise TclShellError(
                    "sta process exited; last output:\n" + "".join(lines))
            if line.startswith(_END_SENTINEL):
                return "".join(lines), line[len(_END_SENTINEL):].strip()
            lines.append(line)

    def eval(self, script):
        """Run a Tcl script, return its stdout. Raises on Tcl errors.

        Report-command output (report_checks, ...) goes through OpenSTA's
        C++ Report stream, which is block-buffered on a pipe and would
        arrive after our sentinel; sta::redirect_string_begin/_end captures
        it and re-emits it through the line-buffered Tcl channel instead.
        """
        wrapped = (
            "sta::redirect_string_begin; "
            "set __bt_code [catch {" + script + "} __bt_err]; "
            "puts -nonewline [sta::redirect_string_end]; "
            "flush stdout; "
            f"if {{$__bt_code}} {{ puts {_END_SENTINEL}ERR:$__bt_err }}"
            f" else {{ puts {_END_SENTINEL}OK:$__bt_err }}"
        )
        self._proc.stdin.write(wrapped + "\n")
        self._proc.stdin.flush()
        output, status = self._read_until_sentinel()
        if status.startswith("ERR:"):
            raise TclShellError(f"{status[4:]}\nscript: {script}")
        result = status[3:]  # OK:<catch result == last command result>
        return output + result if output else result

    def close(self):
        try:
            self._proc.stdin.write("exit\n")
            self._proc.stdin.flush()
        except OSError:
            pass
        self._proc.wait(timeout=10)


class SessionRpcMixin:
    """The sta-claude RPC methods. Host class must call `rpc_init` first
    (TimerSession does so from its @modal.enter hook)."""

    def rpc_init(self, sta_binary, design_tcl):
        self._shell = TclShell(sta_binary, design_tcl)

    # -- queries ----------------------------------------------------------

    def summary(self):
        """Design-level health: WNS/TNS both directions, endpoint count."""
        wns_max = _maybe_float(self._shell.eval("worst_slack -max"))
        wns_min = _maybe_float(self._shell.eval("worst_slack -min"))
        tns_max = _maybe_float(self._shell.eval("total_negative_slack -max"))
        tns_min = _maybe_float(self._shell.eval("total_negative_slack -min"))
        return {
            **_slack_fields("worst_slack_max", wns_max),
            **_slack_fields("worst_slack_min", wns_min),
            **_slack_fields("tns_max", tns_max),
            **_slack_fields("tns_min", tns_min),
            "endpoint_count": int(self._shell.eval("sta::endpoint_count")),
            "scenes": self._scene_names(),
        }

    def worst_paths(self, n=5, path_delay="max"):
        """The n worst path ends, one summary dict per path."""
        self._shell.eval(
            f"set __bt_paths [find_timing_paths -path_delay {path_delay}"
            f" -group_path_count {int(n)} -slack_max inf]"
        )
        count = int(self._shell.eval("llength $__bt_paths"))
        paths = []
        for i in range(min(count, int(n))):
            self._shell.eval(f"set __bt_p [lindex $__bt_paths {i}]")
            slack = _maybe_float(
                self._shell.eval("get_property $__bt_p slack"))
            paths.append({
                "startpoint": self._shell.eval(
                    "get_full_name [get_property $__bt_p startpoint]"),
                "endpoint": self._shell.eval(
                    "get_full_name [get_property $__bt_p endpoint]"),
                "path_delay": path_delay,
                **_slack_fields("slack", slack),
            })
        return paths

    def get_path(self, endpoint, path_delay="max"):
        """Full detail for the worst path to one endpoint: parsed summary
        plus the raw full_clock_expanded report for the report parser."""
        report = self._shell.eval(
            f"report_checks -to {{{endpoint}}} -path_delay {path_delay}"
            " -format full_clock_expanded"
            " -fields {slew cap input_pins fanout} -digits 4"
        )
        self._shell.eval(
            f"set __bt_p [lindex [find_timing_paths -to {{{endpoint}}}"
            f" -path_delay {path_delay} -slack_max inf] 0]"
        )
        found = self._shell.eval("expr {$__bt_p ne \"\"}") == "1"
        slack = (
            _maybe_float(self._shell.eval("get_property $__bt_p slack"))
            if found else None
        )
        return {
            "endpoint": endpoint,
            "path_delay": path_delay,
            **_slack_fields("slack", slack),
            "report": report,
        }

    def pin_timing(self, pin):
        """Slack/arrival/slew at one pin, both directions. Pin arrivals are
        per-edge properties in this OpenSTA (arrival_max_rise/_fall); the
        reported arrival_max/min is the worse of the two edges."""
        def prop(name):
            try:
                return _maybe_float(self._shell.eval(
                    f"get_property [get_pins {{{pin}}}] {name}"))
            except TclShellError:
                return None

        def worst(values, pick):
            present = [v for v in values if v is not None]
            return pick(present) if present else None

        out = {"pin": pin}
        for key, val in (
            ("slack_max", prop("slack_max")),
            ("slack_min", prop("slack_min")),
            ("arrival_max", worst(
                (prop("arrival_max_rise"), prop("arrival_max_fall")), max)),
            ("arrival_min", worst(
                (prop("arrival_min_rise"), prop("arrival_min_fall")), min)),
            ("slew_max", prop("slew_max")),
            ("slew_min", prop("slew_min")),
        ):
            out.update(_slack_fields(key, val))
        return out

    def endpoint_histogram(self, bins=10, path_delay="max"):
        """Endpoint slack histogram: bin edges (ns) and counts."""
        prop = "slack_max" if path_delay == "max" else "slack_min"
        raw = self._shell.eval(
            "set __bt_s {}; foreach __bt_e [sta::endpoints]"
            f" {{ lappend __bt_s [get_property $__bt_e {prop}] }};"
            " set __bt_s"
        )
        slacks = [v for v in (_maybe_float(t) for t in raw.split())
                  if v is not None]
        if not slacks:
            return {"path_delay": path_delay, "bins": [], "counts": [],
                    "unconstrained": len(raw.split())}
        lo, hi = min(slacks), max(slacks)
        span = (hi - lo) or 1.0
        counts = [0] * bins
        for s in slacks:
            counts[min(int((s - lo) / span * bins), bins - 1)] += 1
        edges = [lo + span * i / bins for i in range(bins + 1)]
        return {
            "path_delay": path_delay,
            "bins": edges,
            "bins_ps": [_ps(e) for e in edges],
            "counts": counts,
            "unconstrained": len(raw.split()) - len(slacks),
        }

    def compare_corners(self):
        """Per-scene (nee corner) WNS/TNS. This OpenSTA calls corners
        "scenes"; Phase 0 loads a single "default" scene, so this returns
        one entry until MMMC configs land (Phase 3)."""
        results = []
        for scene in self._scene_names():
            self._shell.eval(f"set_scene {scene}")
            wns = _maybe_float(self._shell.eval("worst_slack -max"))
            tns = _maybe_float(self._shell.eval("total_negative_slack -max"))
            results.append({
                "corner": scene,   # sta-claude field name; value is a scene
                **_slack_fields("worst_slack_max", wns),
                **_slack_fields("tns_max", tns),
            })
        return {"corners": results}

    def check_exceptions(self):
        """Timing exceptions in effect. `report_exceptions` does not exist
        in this OpenSTA; recover them from write_sdc output instead."""
        with tempfile.NamedTemporaryFile(
                mode="r", suffix=".sdc", delete=False) as fh:
            sdc_path = fh.name
        self._shell.eval(f"write_sdc -no_timestamp {{{sdc_path}}}")
        sdc = Path(sdc_path).read_text()
        Path(sdc_path).unlink()
        kinds = ("set_false_path", "set_multicycle_path", "set_max_delay",
                 "set_min_delay", "group_path")
        exceptions = [
            line.strip() for line in sdc.splitlines()
            if line.strip().startswith(kinds)
        ]
        return {"count": len(exceptions), "exceptions": exceptions}

    def clock_info(self):
        """All clocks: period, sources, generated-ness."""
        raw = self._shell.eval(
            "set __bt_o {}; foreach __bt_c [all_clocks] {"
            " lappend __bt_o [get_property $__bt_c name]"
            " [get_property $__bt_c period]"
            " [get_property $__bt_c is_generated]"
            " [join [lmap __bt_src [get_property $__bt_c sources]"
            " { get_full_name $__bt_src }] ,] }; set __bt_o"
        )
        tokens = raw.split()
        clocks = []
        for name, period, is_gen, *rest in zip(
                tokens[0::4], tokens[1::4], tokens[2::4], tokens[3::4]):
            period_ns = _maybe_float(period)
            clocks.append({
                "name": name,
                "period": period_ns,
                "period_ps": _ps(period_ns),
                "is_generated": is_gen == "1",
                "sources": rest[0].split(",") if rest and rest[0] else [],
            })
        return {"clocks": clocks}

    # -- escape hatch and edits -------------------------------------------

    def run_tcl(self, script):
        """Raw Tcl escape hatch; returns stdout text (errors raise)."""
        return self._shell.eval(script)

    def apply_eco(self, edits):
        """Apply netlist edits and re-time. Each edit is a dict:
          {op: replace_cell, instance, lib_cell}
          {op: insert_buffer, name, lib_cell, net}  (composed: this OpenSTA
              has no insert_buffer command)
        Returns the post-ECO summary plus per-edit status."""
        applied = []
        for edit in edits:
            op = edit["op"]
            if op == "replace_cell":
                self._shell.eval(
                    f"replace_cell {{{edit['instance']}}} {{{edit['lib_cell']}}}")
            elif op == "insert_buffer":
                self._insert_buffer(edit["name"], edit["lib_cell"],
                                    edit["net"])
            else:
                raise ValueError(f"unknown ECO op {op!r}")
            applied.append({"op": op, "ok": True, **{
                k: v for k, v in edit.items() if k != "op"}})
        self._shell.eval("sta::find_timing -full_update")
        return {"applied": applied, "summary": self.summary()}

    def _insert_buffer(self, name, lib_cell, net):
        """make_instance + make_net + rewire: move the net's loads onto a
        new net driven by the buffer, buffer input on the original net."""
        new_net = f"{name}_out"
        loads = self._shell.eval(
            "join [lmap __bt_pin [get_pins -of_objects"
            f" [get_nets {{{net}}}] -filter {{direction == input}}]"
            " { get_full_name $__bt_pin }] \\n"
        ).split()
        self._shell.eval(f"make_instance {{{name}}} {{{lib_cell}}}")
        self._shell.eval(f"make_net {{{new_net}}}")
        for load in loads:
            self._shell.eval(f"disconnect_pin {{{net}}} {{{load}}}")
            self._shell.eval(f"connect_pin {{{new_net}}} {{{load}}}")
        # Buffer port names differ per library; find one input, one output.
        in_port = self._shell.eval(
            f"get_name [lindex [get_lib_pins -of_objects"
            f" [get_lib_cells {{{lib_cell}}}]"
            " -filter {direction == input}] 0]")
        out_port = self._shell.eval(
            f"get_name [lindex [get_lib_pins -of_objects"
            f" [get_lib_cells {{{lib_cell}}}]"
            " -filter {direction == output}] 0]")
        self._shell.eval(f"connect_pin {{{net}}} {{{name}/{in_port}}}")
        self._shell.eval(f"connect_pin {{{new_net}}} {{{name}/{out_port}}}")

    # -- helpers -----------------------------------------------------------

    def _scene_names(self):
        raw = self._shell.eval(
            "join [lmap __bt_s [sta::scenes]"
            " { get_property $__bt_s name }] \\n")
        return raw.split()

    def rpc_close(self):
        if getattr(self, "_shell", None):
            self._shell.close()
            self._shell = None
