# OpenSTA side of the OpenTimer correlation run (TAU2015 benchmark layout).
# Env: BENCH_DIR, BENCH_TOP, BENCH_OUT
set bench $::env(BENCH_DIR)
set top   $::env(BENCH_TOP)
set out   $::env(BENCH_OUT)
set_cmd_units -time ns -capacitance ff -resistance kohm
set sta_crpr_enabled 0
if { [info exists ::env(BENCH_DCALC)] } { set_delay_calculator $::env(BENCH_DCALC) }
set t0 [clock milliseconds]
read_liberty -min $bench/${top}_Early.mirror.lib
read_liberty -max $bench/${top}_Late.mirror.lib
read_verilog $bench/$top.v
link_design $top
read_spef $bench/$top.spef
read_sdc $bench/$top.sdc
set_propagated_clock [all_clocks]
set t1 [clock milliseconds]
set us [lindex [time { sta::find_timing -full_update }] 0]
set t2 [clock milliseconds]
puts "PHASES parse_ms=[expr {$t1-$t0}] update_ms=[format %.1f [expr {$us/1000.0}]]"
puts "GLOBAL wns_max=[worst_slack -max] wns_min=[worst_slack -min] tns_max=[total_negative_slack -max] tns_min=[total_negative_slack -min] endpoints=[sta::endpoint_count]"
set fh [open $out w]
puts $fh "endpoint,slack_min,slack_max"
foreach pin [sta::endpoints] {
  puts $fh "[get_full_name $pin],[get_property $pin slack_min],[get_property $pin slack_max]"
}
close $fh
puts "DUMP_DONE $out"
