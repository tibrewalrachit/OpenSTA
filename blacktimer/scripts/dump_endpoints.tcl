# Dump per-endpoint min/max slack to CSV for the BlackTimer golden diff
# (blacktimer/harness/golden_diff.py). Run under OpenSTA:
#
#   sta -no_init -exit blacktimer/scripts/dump_endpoints.tcl
#
# Inputs come from environment variables so the same script serves every
# design in the designs volume:
#
#   BT_LIBERTY   space-separated .lib/.lib.gz files (all corners to load)
#   BT_VERILOG   structural verilog netlist
#   BT_TOP       top module to link
#   BT_SPEF      SPEF parasitics (optional; leave unset for wireload/none)
#   BT_SDC       SDC constraints
#   BT_OUT       output CSV path
#
# Output rows: endpoint,slack_min,slack_max with slacks in SECONDS
# (non-finite slacks are written as inf/-inf), matching the tolerance
# convention in golden_diff.py (1 ps == 1e-12).

proc bt_env {name {default ""}} {
  if { [info exists ::env($name)] } {
    return $::env($name)
  }
  if { $default eq "" } {
    puts stderr "dump_endpoints.tcl: required env var $name is not set"
    exit 2
  }
  return $default
}

# Report/property values in ns; converted to seconds on output below.
set_cmd_units -time ns -capacitance ff -resistance kohm

foreach lib [bt_env BT_LIBERTY] {
  read_liberty $lib
}
read_verilog [bt_env BT_VERILOG]
link_design [bt_env BT_TOP]
if { [info exists ::env(BT_SPEF)] && $::env(BT_SPEF) ne "" } {
  read_spef $::env(BT_SPEF)
}
read_sdc [bt_env BT_SDC]

# Force a full timing update before querying slacks (property queries would
# trigger search lazily anyway; this keeps the run's cost in one place).
sta::find_timing -full_update

proc bt_slack_seconds {pin prop} {
  set val [get_property $pin $prop]
  # Unconstrained endpoints come back as "INF"/"-INF" (Unit::asString), and
  # Tcl would happily parse those as doubles -- catch them before the math.
  if { [string match -nocase "*inf*" $val] || ![string is double -strict $val] } {
    if { [string match "-*" $val] } {
      return "-inf"
    }
    return "inf"
  }
  return [format %.6e [expr { $val * 1e-9 }]]
}

set out [open [bt_env BT_OUT] w]
puts $out "endpoint,slack_min,slack_max"
foreach pin [sta::endpoints] {
  set name [get_full_name $pin]
  set smin [bt_slack_seconds $pin slack_min]
  set smax [bt_slack_seconds $pin slack_max]
  puts $out "$name,$smin,$smax"
}
close $out
puts "dump_endpoints: wrote [bt_env BT_OUT]"
