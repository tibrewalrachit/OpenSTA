# OpenSTA NLDM leg of the GCS-Timer comparison: same netlist/SPEF/input
# slew as GCS-Timer's GBA mode (20 ps at every primary input).
set S $::env(GCS_SCRATCH)
set D $::env(GCS_DESIGN)
set_cmd_units -time ps -capacitance ff
foreach l {INVBUF_RVT_TT_nldm_220122 SIMPLE_RVT_TT_nldm_211120 AO_RVT_TT_nldm_211120 OA_RVT_TT_nldm_211120} {
  read_liberty $S/nldm/asap7sc7p5t_$l.lib
}
set t0 [clock milliseconds]
read_verilog $S/GCS-Timer/bm/$D/test.v
link_design top
read_spef $S/GCS-Timer/bm/$D/test.spef
set_input_transition 20 [all_inputs]
set t1 [clock milliseconds]
set us [lindex [time { sta::find_timing -full_update }] 0]
puts "PHASES parse_ms=[expr {$t1-$t0}] update_ms=[format %.1f [expr {$us/1000.0}]]"
set fh [open $S/results/$D.sta_nldm.csv w]
puts $fh "port,arrival_rise,arrival_fall"
foreach port [get_ports -filter {direction == output} *] {
  
  set ar [get_property [sta::find_pin [get_full_name $port]] arrival_max_rise]
  set af [get_property [sta::find_pin [get_full_name $port]] arrival_max_fall]
  puts $fh "[get_full_name $port],$ar,$af"
}
close $fh
puts "DUMP_DONE"
