# OpenSTA backend validation for sta-claude

Every Tcl command/property the sta-claude opensta backend
(`sta-claude/server/backends/opensta.py`) relies on, probed against this
OpenSTA checkout (master @ 4533a67, built with CUDD 3.0.0) using the smoke
design in `work/design.tcl` (example1 netlist, Nangate45 slow/fast min/max
libraries, example1.dspef parasitics, propagated 10 ns clock; it has real
setup slack AND a real hold violation of -0.0027 ns at r1/D).

> NOTE: the `sta-claude/` checkout is not present in this workspace, so the
> fixes below are recorded here (and already applied to
> `blacktimer/session_rpc.py`) rather than patched into
> `server/backends/opensta.py`. Apply the "fix" column there once the repo
> is available.

## Probe results

| sta-claude call | Status | Fix / notes |
|---|---|---|
| `worst_slack -max` / `-min` | OK | returns ns float, e.g. `1.5154988803955591`; unconstrained designs return INF-like strings — parse defensively |
| `total_negative_slack -max` | OK | ns float |
| `get_property [get_clocks x] period` | OK | fixed-point string `10.000000`, in current time units |
| `report_exceptions` | **MISSING** | invalid command in this version. Recover exceptions from `write_sdc -no_timestamp <file>` and grep `set_false_path` / `set_multicycle_path` / `set_max_delay` / `set_min_delay` / `group_path` lines (implemented in `session_rpc.check_exceptions`) |
| `sta::corners` | **MISSING** | corners were renamed to **scenes** upstream (deprecation dated 11/2025): use `sta::scenes` (returns Scene objects) or public `get_scenes *`; scene name via `get_property $scene name`; switch with `set_scene <name>`; `define_corners` still exists but is deprecated in favor of `define_scenes_cmd` |
| `replace_cell <inst> <lib_cell>` | OK | returns 1 |
| `insert_buffer` | **MISSING** | no such command in OpenSTA. Compose from `make_instance` + `make_net` + `disconnect_pin`/`connect_pin` (all verified OK); see `session_rpc._insert_buffer` for the load-rewire recipe |

## Additional probes used by the RPC layer

| Call | Status | Notes |
|---|---|---|
| `sta::endpoints` / `sta::endpoint_count` | OK | endpoint pins; count = 7 on smoke design |
| `find_timing_paths -path_delay max -group_path_count N` | OK | PathEnd objects; properties: `startpoint`, `startpoint_clock`, `endpoint`, `endpoint_clock`, `endpoint_clock_pin`, `slack`, `points` |
| pin properties | OK | `slack_max/min[_rise/_fall]`, `slew_max/min[_rise/_fall]`; arrivals are **per-edge only** (`arrival_max_rise`, `arrival_max_fall`, ...) — there is no `arrival_max`; take the worse edge |
| clock properties | OK | `name`, `period`, `sources`, `is_generated`, `is_virtual`, `is_propagated` |
| `report_checks -format full_clock_expanded -fields {slew cap input_pins fanout}` | OK | fixtures captured in `work/fixtures/` (setup, hold, min_max) |
| `sta::find_timing -full_update` | OK | full timing update; note the name — there is **no** `update_timing` command in this version |
| `sta::redirect_string_begin` / `sta::redirect_string_end` | OK | captures report output into a string; REQUIRED when driving `sta` over a pipe, because the C++ report stream is block-buffered and arrives out of order with Tcl `puts` otherwise |
| `remove_clock` | **MISSING** | this OpenSTA names it `delete_clock` (`delete_clock [-all] clocks`) |
| `write_sdc -no_timestamp` | OK | exception recovery path |

## Interactive-pipe gotchas (for any backend driving `sta -no_splash`)

1. Report-command output is block-buffered on a pipe. Wrap commands with
   `sta::redirect_string_begin` / `sta::redirect_string_end` and re-emit
   the captured string via Tcl `puts` (line-buffered), or all report text
   arrives after your end-of-command sentinel.
2. Float properties are formatted with the current units
   (`set_cmd_units -time ns`); INF/-INF come back as literal strings that
   Tcl happily treats as doubles — check before arithmetic.
3. `get_property` on missing objects raises a Tcl error, not an empty
   result; wrap in `catch`.

## Fixtures for report_db.py parser tests (`work/fixtures/`)

- `report_setup_full_clock_expanded.txt` — 2 setup paths, MET, propagated
  clock lines, slew/cap/fanout/input_pins fields, 4 digits
- `report_hold_full_clock_expanded.txt` — 2 hold paths, worst VIOLATED
  (-0.0027 ns), input external delay stage lines
- `report_minmax_full_clock_expanded.txt` — combined min/max report

The parser tests themselves need `server/backends/report_db.py` from the
sta-claude checkout; the smoke test for the RPC layer is
`blacktimer/tests/test_session_rpc.py` (14 tests, all passing against a
live `sta` pipe).
