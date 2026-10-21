#include "openroad_tcl_generator.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>
#include <cstdlib>

#include "plog/Log.h"
#include "utils.hpp"

namespace {

std::string shell_quote(const std::string& value) {
    std::string quoted = "'";
    for (char ch : value) {
        if (ch == '\'') {
            quoted += "'\\''";
        } else {
            quoted += ch;
        }
    }
    quoted += "'";
    return quoted;
}

}  // namespace

namespace OpenFinRAM {

OpenRoadTclGenerator::OpenRoadTclGenerator() {}

bool read_band_plan(const std::string& dir, BandPlan& plan) {
    std::ifstream in(join_path(dir, "plan.txt"));
    if (!in.is_open()) return false;
    std::map<std::string, double> values;
    std::string key;
    double value = 0.0;
    while (in >> key >> value) values[key] = value;
    for (const char* required : {"width", "reserved", "inset", "halo_x", "halo_y"}) {
        if (!values.count(required)) return false;
    }
    plan.enabled = true;
    plan.dir = dir;
    plan.width = values["width"];
    plan.reserved = values["reserved"];
    plan.inset = values["inset"];
    plan.halo_x = values["halo_x"];
    plan.halo_y = values["halo_y"];
    return plan.width > 0.0;
}

double OpenRoadTclGenerator::band_die_height(double width) const {
    const double core = align_to_site_height(
        (qor_.cell_area / max_utilization_ + band_.reserved) / width);
    return core + 2 * band_.inset;
}

void OpenRoadTclGenerator::set_site_height(double height) {
    site_height_ = height;
}

void OpenRoadTclGenerator::set_max_utilization(double utilization) {
    max_utilization_ = utilization;
}

void OpenRoadTclGenerator::set_bitcell_width(double width) {
    bitcell_width_ = width;
}

void OpenRoadTclGenerator::set_cpu_count(int local_cpu, int remote_cpu) {
    local_cpu_ = local_cpu;
}

bool OpenRoadTclGenerator::parse_qor_report(const std::string& qor_file) {
    std::ifstream f(qor_file);
    if (!f.is_open()) {
        LOGE << "Cannot open QoR report: " << qor_file;
        return false;
    }
    qor_ = QoRReport2();
    std::string line;
    while (std::getline(f, line)) {
        if (line.find("Cell Area:") != std::string::npos) {
            size_t pos = line.find(":");
            if (pos != std::string::npos) {
                std::istringstream iss(line.substr(pos+1));
                iss >> qor_.cell_area;
            }
        } else if (line.find("Chip area") != std::string::npos) {
            // Yosys stat fallback: "Chip area for module '\\ctrl_decode': 123.45"
            size_t pos = line.rfind(":");
            if (pos != std::string::npos) {
                std::istringstream iss(line.substr(pos+1));
                double v; iss >> v;
                if (v > 0) qor_.cell_area = v;
            }
        }
    }
    f.close();
    if (qor_.cell_area > 0) {
        qor_.valid = true;
        LOGI << "Parsed QoR (OpenROAD): Cell Area = " << qor_.cell_area;
        return true;
    }
    // fallback: estimate
    qor_.cell_area = 45.0;
    qor_.valid = true;
    LOGW << "QoR fallback area = " << qor_.cell_area;
    return true;
}

double OpenRoadTclGenerator::align_to_site_height(double h) const {
    int n = (int)std::ceil(h / site_height_);
    if (n % 2) n++;
    return n * site_height_;
}

double OpenRoadTclGenerator::calculate_floorplan_height(double width) const {
    if (!qor_.valid || width <= 0) return align_to_site_height(0.54);
    // Leave room for load/slew repair, CTS, taps, and fillers.  The previous
    // 90% sizing target made the load-aware controller impossible to repair.
    const double max_util = max_utilization_;
    double min_h = qor_.cell_area / width;
    double aligned = align_to_site_height(min_h);
    double util = qor_.cell_area / (width * aligned);
    if (util > max_util) {
        double target = qor_.cell_area / (width * max_util);
        aligned = align_to_site_height(target);
    }
    if (aligned < min_height_) aligned = align_to_site_height(min_height_);
    return aligned;
}

std::string OpenRoadTclGenerator::generate_floorplan_command(double& w, double& h) const {
    std::ostringstream oss;
    oss << std::fixed << std::setprecision(3);
    if (band_.enabled) {
        oss << std::setprecision(4);
        h = band_die_height(w);
        oss << "initialize_floorplan -die_area \"0 0 " << w << " " << h << "\""
            << " -core_area \"0 " << band_.inset << " " << w << " " << h - band_.inset << "\""
            << " -site " << site_name_;
        return oss.str();
    }
    if (h == 0.0) h = calculate_floorplan_height(w);
    else h = align_to_site_height(h);
    // OpenROAD initialize_floorplan expects microns (um), not DBU
    oss << "initialize_floorplan -die_area \"0 0 " << w << " " << h << "\""
        << " -core_area \"0 0 " << w << " " << h << "\""
        << " -site " << site_name_;
    return oss.str();
}

bool OpenRoadTclGenerator::generate_run_tcl(double width, double height,
                          const std::string& output_file,
                          int num_wlt, int num_wlb, int num_ysel,
                          int addr_width, int num_mux,
                          bool spice_only, double col_width,
                          const std::string& platform_path,
                          const std::string& tech_root,
                          bool single_port) const {
    if (!qor_.valid) {
        LOGE << "QoR not valid, cannot generate OpenROAD TCL";
        return false;
    }
    if (band_.enabled) height = band_die_height(width);
    else if (height == 0.0) height = calculate_floorplan_height(width);
    else height = align_to_site_height(height);

    std::string out_dir = output_file.substr(0, output_file.find_last_of("/\\"));
    if (!out_dir.empty()) {
        std::string cur; size_t pos=0;
        if (out_dir[0]=='/') { cur="/"; pos=1; }
        while (pos <= out_dir.size()) {
            size_t nxt = out_dir.find('/', pos);
            std::string part = out_dir.substr(pos, nxt-pos);
            if (!part.empty()) {
                if (!cur.empty() && cur.back()!='/') cur+="/";
                cur+=part;
                struct stat st; if (stat(cur.c_str(),&st)!=0) mkdir(cur.c_str(),0755);
            }
            if (nxt==std::string::npos) break;
            pos=nxt+1;
        }
    }
    std::ofstream file(output_file);
    if (!file.is_open()) { LOGE << "Cannot open " << output_file; return false; }

    file << std::fixed << std::setprecision(3);
    // ------------------------------------------------------------------
    // Resolve platform files: prefer platform/asap7, fallback to tech/
    auto resolve = [&](const std::string& plat_rel, const std::string& tech_rel) -> std::string {
        std::string p = join_path(platform_path, plat_rel);
        if (file_exists(p)) return p;
        std::string q = join_path(tech_root, tech_rel);
        if (file_exists(q)) return q;
        // still return platform path for debuggability; OpenROAD will error clearly
        return p;
    };
    // Common ASAP7 platform layout (openroad platform/asap7):
    //   lef/asap7_tech.lef, lef/asap7sc7p5t_28_R.lef
    //   lib/*.lib
    //   gds/asap7sc7p5t_28_R_*.gds  (optional)
    std::string tech_lef = resolve("lef/asap7_tech.lef", "lef/asap7_tech.lef");
    std::string cells_lef = resolve("lef/asap7sc7p5t_28_R.lef", "lef/asap7sc7p5t_28_R.lef");
    std::string lib_ao = resolve("lib/asap7sc7p5t_AO_RVT_TT.lib", "lib/asap7sc7p5t_AO_RVT_TT.lib");
    std::string lib_inv = resolve("lib/asap7sc7p5t_INVBUF_RVT_TT.lib", "lib/asap7sc7p5t_INVBUF_RVT_TT.lib");
    std::string lib_oa = resolve("lib/asap7sc7p5t_OA_RVT_TT.lib", "lib/asap7sc7p5t_OA_RVT_TT.lib");
    std::string lib_seq = resolve("lib/asap7sc7p5t_SEQ_RVT_TT.lib", "lib/asap7sc7p5t_SEQ_RVT_TT.lib");
    std::string lib_simple = resolve("lib/asap7sc7p5t_SIMPLE_RVT_TT.lib", "lib/asap7sc7p5t_SIMPLE_RVT_TT.lib");
    std::string set_rc = resolve("setRC.tcl", "setRC.tcl");
    std::string gds_merge = resolve("gds/asap7sc7p5t_28_R_220121a.gds", "gds/asap7sc7p5t_28_R_220121a.gds");
    std::string layermap = join_path(tech_root, "TechLib/asap7_fromAPR.layermap");

    // SDC and netlist from synthesis - must match YosysManager's CWD-based tmp
    std::string syn_root_cwd = join_path(get_current_dir_name(), "tmp/syn_" + get_run_timestamp());
    std::string syn_root_exe = join_path(get_executable_directory(), "tmp/syn_" + get_run_timestamp());
    std::string syn_root = directory_exists(syn_root_cwd) ? syn_root_cwd : syn_root_exe;
    if (file_exists(join_path(syn_root_cwd, "netlist.v"))) syn_root = syn_root_cwd;
    std::string netlist = join_path(syn_root, "netlist.v");
    std::string sdc = join_path(syn_root, "timing.sdc");

    if (single_port) {
        file << "# OpenROAD flow for ctrl_decode (single-port ASAP7) generated by OpenRoadTclGenerator\n";
    } else {
        file << "# OpenROAD flow for ctrl_decode (dual-port ASAP7) generated by OpenRoadTclGenerator\n";
    }
    file << "# width=" << width << " height=" << height << " platform=" << platform_path << "\n\n";
    file << "if {[catch {set openroad_version [openroad -version]}]} { puts \"OpenROAD version check skipped\" }\n\n";

    // Read liberty / LEF / verilog
    file << "read_liberty " << lib_ao << "\n";
    file << "read_liberty " << lib_inv << "\n";
    file << "read_liberty " << lib_oa << "\n";
    file << "read_liberty " << lib_seq << "\n";
    file << "read_liberty " << lib_simple << "\n";
    file << "read_lef " << tech_lef << "\n";
    file << "read_lef " << cells_lef << "\n";
    // link
    file << "read_verilog " << netlist << "\n";
    file << "link_design " << design_name_ << "\n\n";

    file << "read_sdc " << sdc << "\n";
    file << "source " << set_rc << "\n";
    file << "puts \"Loaded ASAP7 layer, signal, clock, and via RC\"\n\n";

    // Fail before placement if the SDC does not define real paths.
    file << "set pre_paths [find_timing_paths -path_delay max -group_path_count 20]\n";
    file << "if {[llength $pre_paths] == 0} { error \"PERIPHERY_STA: no constrained timing paths after read_sdc\" }\n";
    if (single_port) {
        file << "set sdel_paths [find_timing_paths -from [get_ports -quiet {sdel*}] -path_delay max -group_path_count 4]\n";
        file << "if {[llength $sdel_paths] < 4} { error \"PERIPHERY_STA: not all sdel inputs reach sequential timing endpoints\" }\n";
    } else {
        file << "set a_paths [find_timing_paths -from [get_ports -quiet {A_A*}] -path_delay max -group_path_count 4]\n";
        file << "if {[llength $a_paths] == 0} { error \"PERIPHERY_STA: no A_A timing paths (DP)\" }\n";
        if (!one_port_) {
            file << "set b_paths [find_timing_paths -from [get_ports -quiet {A_B*}] -path_delay max -group_path_count 4]\n";
            file << "if {[llength $b_paths] == 0} { error \"PERIPHERY_STA: no A_B timing paths (DP)\" }\n";
        }
    }
    file << "check_setup -verbose > pre_place_setup.rpt\n\n";

    // Floorplan
    file << generate_floorplan_command(width, height) << "\n";
    file << "catch {source \"" << join_path(platform_path, "make_tracks.tcl") << "\"} ; puts \"tracks sourced or skipped\"\n";
    file << "catch {source \"" << join_path(tech_root, "make_tracks.tcl") << "\"} ; puts \"tech tracks sourced or skipped\"\n";
    // Create tracks explicitly with positive offsets (ASAP7 M2 has -0.27 which fails validation)
    file << "make_tracks M1 -x_offset 0 -y_offset 0 -x_pitch 0.036 -y_pitch 0.036\n";
    file << "make_tracks M2 -x_offset 0 -y_offset 0 -x_pitch 0.036 -y_pitch 0.036\n";
    file << "make_tracks M3 -x_offset 0 -y_offset 0 -x_pitch 0.036 -y_pitch 0.036\n";
    // M4/M5 on the DRM's tracks (M4.AUX.2, M5.AUX.2): centred 12 nm past a
    // multiple of 48, so a minimum-width wire's edges are on the 24 nm grid.
    file << "make_tracks M4 -x_offset 0.012 -y_offset 0.012 -x_pitch 0.048 -y_pitch 0.048\n";
    file << "make_tracks M5 -x_offset 0.012 -y_offset 0.012 -x_pitch 0.048 -y_pitch 0.048\n";
    if (!single_port) {
        // Port A's inputs on the left edge and port B's on the right, each
        // port's address bus then its enables together and in index order,
        // as the macro's own pins are: its router then joins them straight.
        file << "proc dp_port_inputs {port} {\n"
                "    set names {}\n"
                "    foreach pin [get_ports -quiet A_${port}*] { lappend names [get_full_name $pin] }\n"
                "    set names [lsort -dictionary $names]\n"
                "    foreach enable {ce_n we_n oe_n} {\n"
                "        if {[llength [get_ports -quiet ${enable}_${port}]]} { lappend names ${enable}_${port} }\n"
                "    }\n"
                "    return $names\n"
                "}\n";
        file << "set_io_pin_constraint -pin_names [dp_port_inputs A] -region left:* -group -order\n";
        if (!one_port_) {
            file << "set_io_pin_constraint -pin_names [dp_port_inputs B] -region right:* -group -order\n";
        }
    }
    // The inputs stand 192 nm apart, the macro's side-pin pitch
    // (compile_asap7_2rw.py SIDE_PIN_PITCH, a whole number of M4 and M6
    // tracks), so each macro pin is level with its input and the router
    // drops straight onto it: at the default 96 the two pitches drifted
    // apart until the address nets' M5 jogs overlapped.
    // The band has only its two side edges for every pin, too few slots at 192.
    const std::string min_distance =
        single_port || band_.enabled ? "" : " -min_distance 0.192";
    if (band_.enabled) {
        // The strips go in as fixed physical instances with a keep-out; the
        // band's bottom and top edges touch the column tiles, so its pins
        // stand on the side edges only.
        file << "source {" << join_path(band_.dir, "band.tcl") << "}\n";
        file << "band_place_strips " << height << "\n";
        file << "cut_rows -halo_width_x " << band_.halo_x << " -halo_width_y " << band_.halo_y << "\n";
        file << "place_pins -hor_layers M4 -ver_layers M5 -exclude bottom:* -exclude top:*" << min_distance << "\n\n";
    } else {
        file << "place_pins -hor_layers M4 -ver_layers M5" << min_distance << "\n\n";
    }

    // Global connections
    file << "add_global_connection -net VDD -pin_pattern {^VDD$} -power\n";
    file << "add_global_connection -net VSS -pin_pattern {^VSS$} -ground\n";
    file << "global_connect\n\n";
    if (!single_port) {
        // DP DFFASR SETN pins are tied to 1'b1 (inactive async set) which Yosys
        // leaves as a POWER net "one_" that TritonRoute cannot route. Tie all
        // SETN pins to VDD via global connect so the net becomes special, then
        // remove the dangling constant net.
        file << "add_global_connection -net VDD -pin_pattern {SETN} -power\n";
        file << "add_global_connection -net VDD -pin_pattern {SET} -power\n";
        file << "add_global_connection -net VSS -pin_pattern {RESETN} -ground\n";
        file << "add_global_connection -net VSS -pin_pattern {RSTN} -ground\n";
        file << "global_connect\n";
        file << "catch {remove_nets -net one_}\n";
        file << "catch {remove_nets -net zero_}\n";
    }
    file << "set_voltage 0.7\n";
    // tapcell starts even rows `distance` in and odd rows twice that, dropping any tap
    // past the row's end, so 14 um places none in a die a few microns wide. Half the
    // width puts one mid-row in every even row (each shares its N-well with the
    // flipped row above) and the top row, well within ACTIVE.LUP.1's 30 um.
    file << "tapcell -distance " << std::min(14.0, width / 2.0)
         << " -tapcell_master TAPCELL_ASAP7_75t_R";
    if (band_.enabled) {
        // tapcell cuts the rows around blocks again, with a 2 um halo unless told.
        file << " -halo_width_x " << band_.halo_x << " -halo_width_y " << band_.halo_y;
    }
    file << "\n";
    // tapcell puts taps on even sites only (-disallow_one_site_gaps does not
    // move them): in a row of an odd number of sites the boundary rows' end
    // tap leaves one site, and the half-site filler there has the tap's
    // opposite implants beside it and the edge on its other side
    // (NSELECT.W.1/PSELECT.W.1).  Such a tap slides flush with its row's end.
    file << "proc flush_taps_to_row_ends {master} {\n"
         << "    set block [ord::get_db_block]\n"
         << "    set rows [$block getRows]\n"
         << "    foreach inst [$block getInsts] {\n"
         << "        if {[[$inst getMaster] getName] ne $master} { continue }\n"
         << "        lassign [$inst getLocation] x y\n"
         << "        set w [[$inst getMaster] getWidth]\n"
         << "        foreach row $rows {\n"
         << "            set box [$row getBBox]\n"
         << "            if {$y != [$box yMin] || $x < [$box xMin] || $x + $w > [$box xMax]} { continue }\n"
         << "            set site [[$row getSite] getWidth]\n"
         << "            set dx 0\n"
         << "            if {[$box xMax] - ($x + $w) == $site} { set dx $site }\n"
         << "            if {$x - [$box xMin] == $site} { set dx [expr {-$site}] }\n"
         << "            if {$dx != 0} {\n"
         << "                set status [$inst getPlacementStatus]\n"
         << "                $inst setPlacementStatus PLACED\n"
         << "                $inst setLocation [expr {$x + $dx}] $y\n"
         << "                $inst setPlacementStatus $status\n"
         << "            }\n"
         << "        }\n"
         << "    }\n"
         << "}\n"
         << "flush_taps_to_row_ends TAPCELL_ASAP7_75t_R\n\n";

    if (single_port) {
        // Preserve the explicitly instantiated physical delay line while allowing
        // the load-aware output and decode logic to be resized and buffered.
        file << "set physical_delay_cells [get_cells -hierarchical -quiet {physical_delay_*}]\n";
        file << "if {[llength $physical_delay_cells] != 108} { error \"PERIPHERY_STRUCTURE: expected 108 named physical delay inverters, found [llength $physical_delay_cells]\" }\n";
        file << "set physical_delay_inputs [get_cells -hierarchical -quiet {physical_delay_input_*}]\n";
        file << "if {[llength $physical_delay_inputs] != 8} { error \"PERIPHERY_STRUCTURE: expected 8 named delay-chain inputs, found [llength $physical_delay_inputs]\" }\n";
        file << "set wordline_driver_cells [get_cells -hierarchical -quiet {g_bank_logic*.u_wl*_driver}]\n";
        file << "if {[llength $wordline_driver_cells] != " << (2 * num_wlt * num_mux) << "} { error \"PERIPHERY_STRUCTURE: expected " << (2 * num_wlt * num_mux) << " dedicated wordline drivers, found [llength $wordline_driver_cells]\" }\n";
        file << "set_dont_touch $physical_delay_cells\n\n";
    } else {
        // Yosys gives the four DP delay chains stable names after checking the
        // configured stage count.  Preserve those exact cells through repair.
        file << "set dp_delay_cells [get_cells -hierarchical -quiet {physical_dp_delay_*}]\n";
        file << "set dp_delay_count [llength $dp_delay_cells]\n";
        file << "if {$dp_delay_count == 0} { error \"PERIPHERY_STRUCTURE: no named DP delay cells\" }\n";
        file << "foreach cell $dp_delay_cells { if {[get_property $cell ref_name] ne \"BUFx2_ASAP7_75t_R\"} { error \"PERIPHERY_STRUCTURE: DP delay cell is not BUFx2\" } }\n";
        file << "set_dont_touch $dp_delay_cells\n\n";
        // The wordlines are driven at the array by the driver-slice strips;
        // the controller's sel_hi/sel_lo outputs are ordinary buffered ports.
        // Several DP controls are clock-derived top-level outputs.  Give every
        // output a real driver stage before placement so hold repair has a
        // resizable data-path cell instead of an unbuffered clock-to-port arc.
        file << "buffer_ports -outputs -buffer_cell BUFx2_ASAP7_75t_R -max_utilization 90\n\n";
        for (const auto& [prefix, cell] : port_drivers_) {
            file << "foreach port [get_ports -quiet {" << prefix << "*}] {\n"
                 << "    set net [get_nets [get_full_name $port]]\n"
                 << "    foreach pin [get_pins -of_objects $net] {\n"
                 << "        if {[get_property $pin direction] ne \"output\"} { continue }\n"
                 << "        set inst [get_cells -of_objects $pin]\n"
                 << "        replace_cell $inst " << cell << "\n"
                 << "        set_dont_touch $inst\n"
                 << "    }\n"
                 << "    set_dont_touch $net\n"
                 << "}\n";
        }
        if (!port_drivers_.empty()) file << "\n";
    }

    // Placement and electrical repair use the per-output array loads from the
    // generated SDC.  Dedicated BUFx4 stages already isolate every WL output.
    file << "global_placement -density 0.70\n";
    file << "estimate_parasitics -placement\n";
    file << "repair_design -max_utilization 90 -slew_margin 10 -cap_margin 10 -verbose\n";
    file << "detailed_placement\n";
    file << "optimize_mirroring\n";
    file << "check_placement -verbose -report_file_name placement_check.rpt\n\n";

    // Build a propagated clock instead of accepting ideal-clock STA.
    if (single_port) {
        file << "unset_dont_touch $physical_delay_inputs\n";
    }
    file << "clock_tree_synthesis -clk_nets {clk} -root_buf BUFx4_ASAP7_75t_R -buf_list {BUFx2_ASAP7_75t_R BUFx4_ASAP7_75t_R BUFx8_ASAP7_75t_R} -wire_unit 20\n";
    file << "set_propagated_clock [get_clocks {clk}]\n";
    file << "repair_clock_nets -max_wire_length 20\n";
    if (single_port) {
        file << "set_dont_touch $physical_delay_inputs\n";
    }
    file << "estimate_parasitics -placement\n";
    file << "repair_timing -setup -max_utilization 90 -max_buffer_percent 20\n";
    file << "repair_timing -hold -max_utilization 90 -max_buffer_percent 20\n";
    if (!single_port) {
        // Hold repair can introduce shared buffers exceeding the controller's
        // fanout constraint. Repair those new nets before final placement.
        file << "repair_design -max_utilization 90 -slew_margin 10 -cap_margin 10 -verbose\n";
        file << R"(# Bound reset fanout left on shared hold-repair buffers.
set dp_reset_nets [dict create]
foreach pin [get_pins -hierarchical -quiet */RESETN] {
    set net [get_nets -of_objects $pin]
    dict lappend dp_reset_nets [get_full_name $net] $pin
}
set dp_reset_branch 0
dict for {net pins} $dp_reset_nets {
    if {[llength $pins] <= 10} { continue }
    for {set i 0} {$i < [llength $pins]} {incr i 8} {
        insert_buffer -buffer_cell BUFx2_ASAP7_75t_R -load_pins [lrange $pins $i [expr {$i+7}]] -buffer_name dp_reset_branch_$dp_reset_branch
        incr dp_reset_branch
    }
}
)";
    }
    file << "detailed_placement -incremental\n";
    file << "check_placement -verbose -report_file_name placement_post_cts.rpt\n\n";

    // Fill before routing so filler geometry participates in detailed-route
    // legality.  Do not attempt a second decap-as-filler pass into zero gaps.
    file << "filler_placement \"FILLER_ASAP7_75t_R FILLERxp5_ASAP7_75t_R\"\n";
    if (band_.enabled) {
        // Timing is closed on the ports' SDC loads; now route the select
        // outputs onto the strips' pins themselves.
        file << "band_connect_strips\n";
        // The strips have no timing model: timing-driven global routing
        // asks STA for their nets' slack and crashes once it maze-routes.
        file << "global_route -critical_nets_percentage 0\n";
    } else {
        file << "global_route\n";
    }
    // ASAP7 cells' M1 pins sit 9 or 27 nm off the 36 nm M1 tracks.  Without
    // via-in-pin the router lands a VIA12 on the track, half off the pin, and
    // two such 9 nm stubs on neighbouring pins break M1.S.2/M1.S.6 (25/20 nm
    // between short M1 shapes); inside the pin, the via sits on its centre.
    // Verified on the dual-port controller only.
    file << "detailed_route -output_drc detailed_route_drc.rpt"
         << (single_port ? "" : " -via_in_pin_bottom_layer M1 -via_in_pin_top_layer M1") << "\n\n";
    if (!single_port) {
        file << "if {![file exists detailed_route_drc.rpt] || [file size detailed_route_drc.rpt] != 0} { error \"PERIPHERY_DRC: routing violations remain\" }\n";
    }

    // Post-route-equivalent STA uses routed global parasitics plus the explicit
    // ASAP7 RC model.  This flow has no extraction SPEF yet, so reports say so
    // explicitly rather than claiming transistor-level signoff.
    file << "estimate_parasitics -global_routing\n";
    file << "set final_paths [find_timing_paths -path_delay max -group_path_count 100]\n";
    file << "if {[llength $final_paths] == 0} { error \"PERIPHERY_STA: no constrained post-route timing paths\" }\n";
    if (single_port) {
        file << "set final_delay_cells [get_cells -hierarchical -quiet {physical_delay_*}]\n";
        file << "if {[llength $final_delay_cells] != 108} { error \"PERIPHERY_STRUCTURE: delay topology changed during implementation\" }\n";
        file << "foreach cell $final_delay_cells { if {[get_property $cell ref_name] ne \"INVx1_ASAP7_75t_R\"} { error \"PERIPHERY_STRUCTURE: physical delay cell was resized\" } }\n";
        file << "set final_wordline_drivers [get_cells -hierarchical -quiet {g_bank_logic*.u_wl*_driver}]\n";
        file << "if {[llength $final_wordline_drivers] != " << (2 * num_wlt * num_mux) << "} { error \"PERIPHERY_STRUCTURE: wordline driver stage missing after implementation\" }\n";
        file << "foreach cell $final_wordline_drivers { if {![string match \"BUF*\" [get_property $cell ref_name]]} { error \"PERIPHERY_STRUCTURE: non-buffer cell used as dedicated wordline driver\" } }\n";
        file << "set structure_fd [open periphery_implementation.rpt w]\n";
        file << "puts $structure_fd \"physical_delay_invx1 [llength $final_delay_cells]\"\n";
        file << "puts $structure_fd \"dedicated_wordline_buffers [llength $final_wordline_drivers]\"\n";
        file << "puts $structure_fd \"constrained_max_paths [llength $final_paths]\"\n";
        file << "close $structure_fd\n";
    } else {
        file << "set final_dp_delay_cells [get_cells -hierarchical -quiet {physical_dp_delay_*}]\n";
        file << "if {[llength $final_dp_delay_cells] != $dp_delay_count} { error \"PERIPHERY_STRUCTURE: DP delay topology changed during implementation\" }\n";
        file << "foreach cell $final_dp_delay_cells { if {[get_property $cell ref_name] ne \"BUFx2_ASAP7_75t_R\"} { error \"PERIPHERY_STRUCTURE: DP delay cell was resized\" } }\n";
        file << "set structure_fd [open periphery_implementation.rpt w]\n";
        file << "puts $structure_fd \"mode dual-port\"\n";
        file << "puts $structure_fd \"protected_delay_bufx2 [llength $final_dp_delay_cells]\"\n";
        file << "puts $structure_fd \"constrained_max_paths [llength $final_paths]\"\n";
        file << "close $structure_fd\n";
    }
    file << "report_checks -path_delay min_max -fields {slew capacitance fanout input_pin net} -digits 4 > timing.rpt\n";
    file << "report_checks -to [get_ports -quiet {wlt* wlb* sel_hi_A* sel_hi_B* sel_lo_A* sel_lo_B*}] -path_delay min_max -fields {slew capacitance fanout input_pin net} -digits 4 > wordline_timing.rpt\n";
    file << "report_check_types -max_slew -max_capacitance -max_fanout -violators -verbose > electrical.rpt\n";
    file << "check_setup -verbose > final_setup.rpt\n";
    file << "report_clock_properties > clock_properties.rpt\n";
    file << "report_clock_skew -setup > clock_skew.rpt\n";
    file << "report_design_area > design_area.rpt\n";
    file << "report_tns -max\n";
    file << "report_wns -max\n";
    file << "if {[file size electrical.rpt] == 0} { set electrical_empty_fd [open electrical.rpt w]; puts $electrical_empty_fd \"PASS: no max slew, capacitance, or fanout violations.\"; close $electrical_empty_fd }\n";
    file << "set electrical_fd [open electrical.rpt r]\n";
    file << "set electrical_text [read $electrical_fd]\n";
    file << "close $electrical_fd\n";
    file << "if {[string first \"(VIOLATED)\" $electrical_text] >= 0} { error \"PERIPHERY_STA: max slew/capacitance/fanout violations remain\" }\n";
    file << "set setup_slack [worst_slack -max]\n";
    file << "set hold_slack [worst_slack -min]\n";
    file << "if {$setup_slack < -0.001} { error \"PERIPHERY_STA: setup timing violation $setup_slack ns\" }\n";
    file << "if {$hold_slack < -0.001} { error \"PERIPHERY_STA: hold timing violation $hold_slack ns\" }\n";
    file << "puts \"PERIPHERY_STA_PASS paths=[llength $final_paths] setup_slack=$setup_slack hold_slack=$hold_slack\"\n\n";

    // Physical pin creation for stacked_colgrp alignment (M3)
    // Reuse Innovus pin logic but emit OpenROAD add_pin / place_pin patterns
    // For MVP, we keep random pin placement; exact M3 alignment is handled by LayoutGenerator's final top-level pin remapping.
    // Emit a comment documenting the expected pin map for downstream GDS integration.
    file << "# Expected M3 pins for stacked_colgrp (handled by LayoutGenerator):\n";
    file << "#   WLT[" << num_wlt*num_mux-1 << ":0] WLB[" << num_wlb*num_mux-1 << ":0] at M3 y=[-0.15," << height+0.15 << "]\n";
    file << "#   blprechtn/yseltn/yselt/wrena/saprechn/sae/wrenan etc. per col_width=" << col_width << "\n\n";

    // OpenROAD 2.0 has no GDS writer. Emit DEF/ODB here; OpenRoadManager
    // streams the DEF through KLayout and substitutes full standard-cell GDS.
    if (band_.enabled) {
        // The strips are the macro's own instances: the controller keeps
        // only the wires it landed on their pins.
        file << "band_remove_strips\n";
    }
    file << "write_def ctrl_decode.def\n";
    file << "write_verilog netlist_for_lvs.v\n";
    file << "write_sdc ctrl_decode.sdc\n";
    file << "catch {write_db ctrl_decode.odb}\n";
    file << "exit\n";
    file.close();
    LOGI << "Generated OpenROAD TCL: " << output_file << " (" << width << " x " << height << ")";
    return true;
}

bool OpenRoadTclGenerator::run_openroad(const std::string& tcl_file,
                      const std::string& work_dir,
                      const std::string& log_file,
                      const std::string& openroad_bin) const {
    struct stat st;
    if (stat(work_dir.c_str(), &st)!=0 || !S_ISDIR(st.st_mode)) {
        LOGE << "Work dir missing: " << work_dir; return false;
    }
    std::ifstream chk(tcl_file); if (!chk.good()) { LOGE << "TCL missing: " << tcl_file; return false; } chk.close();
    std::ostringstream cmd;
    std::string tcl_name = tcl_file.substr(tcl_file.find_last_of("/\\")+1);
    // Prefer openroad_bin as full path if it exists under platform checkout
    std::string bin = openroad_bin;
    struct stat bst;
    if (stat(bin.c_str(), &bst)==0 && S_ISDIR(bst.st_mode)) {
        std::string cand1 = join_path(bin, "build/src/openroad");
        std::string cand2 = join_path(bin, "build/openroad");
        // Check for libortools availability for build/src binary; prefer installed openroad if broken
        auto has_lib = [&](const std::string& p){ struct stat s; return stat(p.c_str(), &s)==0; };
        bool cand1_ok = file_exists(cand1);
        bool cand2_ok = file_exists(cand2);
        // If cand1 exists but libortools missing, fallback to PATH openroad
        if (cand1_ok) {
            std::string ldd_check = "ldd \"" + cand1 + "\" 2>&1 | grep -q \"not found\"";
            int missing = system(ldd_check.c_str());
            if (missing == 0) {
                LOGW << "OpenROAD build binary missing shared libs, falling back to PATH openroad";
                cand1_ok = false;
            }
        }
        if (cand1_ok) bin = cand1;
        else if (cand2_ok) bin = cand2;
        else bin = "openroad";
    }
    // Also check if bin is a directory fallback still, prefer PATH openroad
    {
        struct stat s;
        if (stat(bin.c_str(), &s)==0 && S_ISDIR(s.st_mode)) bin = "openroad";
        // If bin is build/src/openroad but not executable due to libs, prefer openroad in PATH
        if (bin.find("build/src/openroad") != std::string::npos) {
            std::string ldd_check = "ldd \"" + bin + "\" 2>&1 | grep -q \"not found\"";
            if (system(ldd_check.c_str()) == 0) bin = "openroad";
        }
    }
    cmd << "bash -c 'cd \"" << work_dir << "\" && \"" << bin << "\" -exit " << tcl_name << "' > \"" << log_file << "\" 2>&1";
    LOGI << "Running OpenROAD: " << cmd.str();
    int rc = system(cmd.str().c_str());
    if (rc != 0) {
        LOGE << "OpenROAD failed rc=" << rc << " see " << log_file;
        return false;
    }
    LOGI << "OpenROAD completed";
    return true;
}

bool OpenRoadTclGenerator::stream_def_to_gds(
    const std::string& def_file,
    const std::string& tech_lef,
    const std::string& cell_lef,
    const std::string& macro_gds,
    const std::string& output_gds,
    const std::string& script_path) const {
    const std::vector<std::string> required_files = {
        def_file, tech_lef, cell_lef, macro_gds
    };
    for (const std::string& path : required_files) {
        if (!file_exists(path)) {
            LOGE << "Cannot stream DEF to GDS; required file is missing: " << path;
            return false;
        }
    }

    std::vector<std::string> script_candidates;
    if (!script_path.empty()) {
        script_candidates.push_back(script_path);
    }
    script_candidates.push_back(join_path(get_current_dir_name(), "scripts/def_to_gds.py"));
    script_candidates.push_back(join_path(get_executable_directory(), "scripts/def_to_gds.py"));
    script_candidates.push_back(join_path(get_executable_directory(), "../scripts/def_to_gds.py"));
    script_candidates.push_back(join_path(
        get_executable_directory(), "../share/OpenFinRAM/scripts/def_to_gds.py"));

    std::string converter_script;
    for (const std::string& candidate : script_candidates) {
        if (file_exists(candidate)) {
            converter_script = candidate;
            break;
        }
    }
    if (converter_script.empty()) {
        LOGE << "Cannot stream DEF to GDS; scripts/def_to_gds.py was not found";
        return false;
    }

    const std::string log_file = output_gds + ".log";
    std::ostringstream command;
    command << "python3 " << shell_quote(converter_script)
            << " --def-file " << shell_quote(def_file)
            << " --tech-lef " << shell_quote(tech_lef)
            << " --cell-lef " << shell_quote(cell_lef)
            << " --macro-gds " << shell_quote(macro_gds)
            << " --output-gds " << shell_quote(output_gds)
            << " --top-cell " << shell_quote(design_name_)
            << " > " << shell_quote(log_file) << " 2>&1";

    LOGI << "Streaming OpenROAD DEF to GDS with KLayout";
    LOGD << "  Converter: " << converter_script;
    int rc = std::system(command.str().c_str());
    if (rc != 0) {
        LOGE << "DEF-to-GDS stream-out failed with status " << rc
             << " (see " << log_file << ")";
        return false;
    }

    struct stat output_stat;
    if (stat(output_gds.c_str(), &output_stat) != 0 || output_stat.st_size == 0) {
        LOGE << "DEF-to-GDS converter did not produce a non-empty file: " << output_gds;
        return false;
    }

    LOGI << "Created merged controller GDS: " << output_gds
         << " (" << output_stat.st_size << " bytes)";
    return true;
}

bool OpenRoadTclGenerator::run_v2lvs(const std::string& work_dir,
                   const std::string& verilog_file,
                   const std::string& spice_file,
                   const std::string& cdl_file) const {
    auto quote = [](const std::string& value) {
        std::string escaped = "'";
        for (char c : value) escaped += c == '\'' ? "'\\''" : std::string(1, c);
        return escaped + "'";
    };
    const std::string cdl = cdl_file.empty()
        ? join_path(get_current_dir_name(), "tech/cdl/asap7sc7p5t_28_R.cdl") : cdl_file;
    if (!file_exists(cdl)) {
        LOGE << "Mapped controller conversion requires CDL: " << cdl;
        return false;
    }
    const std::string converter = join_path(get_current_dir_name(), "scripts/mapped_verilog_to_spice.py");
    const std::string command = "python3 " + quote(converter)
        + " --verilog " + quote(join_path(work_dir, verilog_file))
        + " --cdl " + quote(cdl)
        + " --output " + quote(join_path(work_dir, spice_file))
        + " > " + quote(join_path(work_dir, "verilog_to_spice.log")) + " 2>&1";
    if (std::system(command.c_str()) != 0) {
        LOGE << "Mapped controller SPICE conversion failed; see " << work_dir << "/verilog_to_spice.log";
        return false;
    }
    struct stat output;
    if (stat(join_path(work_dir, spice_file).c_str(), &output) != 0 || output.st_size == 0) {
        LOGE << "Mapped controller SPICE conversion wrote no netlist: " << join_path(work_dir, spice_file);
        return false;
    }
    LOGI << "Converted routed controller to structural SPICE with CDL pin ordering";
    return true;
}

// --- netlist post-processing reused from InnovusTclGenerator (trimmed) ---
bool OpenRoadTclGenerator::file_exists(const std::string& p) const { struct stat b; return stat(p.c_str(),&b)==0; }
std::vector<std::string> OpenRoadTclGenerator::read_file(const std::string& p) const {
    std::vector<std::string> lines; std::ifstream f(p); std::string l; while(std::getline(f,l)) lines.push_back(l); return lines;
}
bool OpenRoadTclGenerator::write_file(const std::string& p, const std::vector<std::string>& lines) const {
    std::ofstream f(p); if(!f.is_open()) return false; for(auto &l:lines) f<<l<<"\n"; return true;
}
std::vector<std::string> OpenRoadTclGenerator::merge_continuation(const std::vector<std::string>& lines) const {
    std::vector<std::string> r; for(auto &l:lines){ if(!l.empty()&&l[0]=='+'){ if(!r.empty()) r.back()+=" "+l.substr(1);} else r.push_back(l);} return r;
}
std::vector<std::string> OpenRoadTclGenerator::add_power_to_subckt(const std::vector<std::string>& lines) const {
    std::vector<std::string> r; for(auto &l:lines){ r.push_back(l); if(l.rfind(".SUBCKT",0)==0){ // add VDD VSS if missing
        if(l.find("VDD")==std::string::npos) r.back() += " VDD VSS";
    }} return r;
}
bool OpenRoadTclGenerator::parse_cdl(const std::string& p){ if(!file_exists(p)) return false; auto ls=read_file(p); parse_subckt_from_lines(ls); return true; }
void OpenRoadTclGenerator::parse_subckt_from_lines(const std::vector<std::string>& ls){
    for(auto &l:ls) if(l.rfind(".SUBCKT",0)==0){ std::istringstream iss(l); std::string kw, cell; iss>>kw>>cell; std::vector<std::string> pins; std::string pin; while(iss>>pin) pins.push_back(pin); subckt_dict_[cell]=pins; }
}
std::vector<std::string> OpenRoadTclGenerator::expand_pins(const std::vector<std::string>& ls){ return ls; }
std::vector<std::string> OpenRoadTclGenerator::process_connect(const std::vector<std::string>& ls) const { return ls; }

bool OpenRoadTclGenerator::post_process_netlist(const std::string& work_dir, const std::string& spice_file, const std::string& cdl_file) {
    std::string path = join_path(work_dir, spice_file);
    if (!file_exists(path)) { LOGE << "SPICE missing: " << path; return false; }
    auto lines = read_file(path);
    lines = merge_continuation(lines);
    lines = add_power_to_subckt(lines);
    // reuse CDL parsing if available
    if (!cdl_file.empty()) parse_cdl(cdl_file);
    lines = expand_pins(lines);
    lines = process_connect(lines);
    return write_file(path, lines);
}

} // namespace OpenFinRAM
