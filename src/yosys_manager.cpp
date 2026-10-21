#include "yosys_manager.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <sys/stat.h>

#include "capacitance_parser.hpp"
#include "capacitance_predictor.hpp"
#include "plog/Log.h"
#include "utils.hpp"
#include "yosys_tcl_generator.hpp"

namespace {

// The four selectable physical delay stages must fit between opposite clock
// edges.  The longest TT routed chain is about 0.93 ns; a 2.5 ns cycle leaves
// 0.32 ns beyond that half-cycle delay for explicit uncertainty, interface
// budget, and route variation.  This replaces the impossible former 200 ps
// cycle and is a conservative implementation target, not a characterized
// macro frequency claim.
constexpr double kClockPeriodNs = 2.500;
constexpr double kClockUncertaintyNs = 0.050;
constexpr double kIoDelayNs = 0.100;
constexpr double kInputMinDelayNs = 0.020;
constexpr double kOutputMinDelayNs = -0.020;
constexpr double kInputSlewNs = 0.010;
constexpr double kMaxTransitionNs = 0.150;
constexpr double kMaxNetCapPf = 0.020;
constexpr double kArrayLoadMargin = 1.50;
constexpr double kDefaultAbcLoadFf = 3.898;
constexpr double kAbcDelayTargetPs = 140.0;

std::size_t count_occurrences(const std::string& text,
                              const std::string& needle) {
    std::size_t count = 0;
    std::size_t pos = 0;
    while ((pos = text.find(needle, pos)) != std::string::npos) {
        ++count;
        pos += needle.size();
    }
    return count;
}

}  // namespace

YosysManager::YosysManager(const MainCliOptions& cli_options)
    : cli_options_(cli_options) {
    cur_path_ = get_executable_directory();
    const std::string verilog_subdir = cli_options_.single_port ? "tech/verilog_sp" : "tech/verilog_dp";
    std::string cwd_tech = join_path(get_current_dir_name(), verilog_subdir);
    if (directory_exists(cwd_tech) || file_exists(join_path(cwd_tech, "sram_control.v"))) {
        rtl_path_ = cwd_tech;
        cur_path_ = get_current_dir_name();
    } else {
        rtl_path_ = join_path(cur_path_, verilog_subdir);
    }
    // Syn path must match SpiceIntegrator's CWD-based tmp (repo/tmp) so downstream can find netlist
    syn_path_ = join_path(get_current_dir_name(), "tmp/syn_" + get_run_timestamp());

    if (!directory_exists(join_path(get_current_dir_name(), "tmp"))) {
        create_directory(join_path(get_current_dir_name(), "tmp"), nullptr);
    }
    if (!directory_exists(join_path(get_executable_directory(), "tmp"))) {
        create_directory(join_path(get_executable_directory(), "tmp"), nullptr);
    }
    if (!directory_exists(syn_path_)) {
        create_directory(syn_path_, nullptr);
    }
}

std::string YosysManager::generate_parameter_string() const {
    int addr_width = get_addr_width(cli_options_);
    std::ostringstream oss;
    oss << "ADDR_WIDTH=" << addr_width
        << ",NUM_WL=" << cli_options_.num_wls
        << ",NUM_BANK=" << cli_options_.num_banks
        << ",COLUMN_MUX=" << cli_options_.num_rows_per_mux;
    if (!cli_options_.single_port) {
        oss << ",WL_BUF=" << cli_options_.num_wl_buf
            << ",SAE_BUF=" << cli_options_.num_sae_buf;
    }
    return oss.str();
}

std::string YosysManager::generate_yosys_script() const {
    OpenFinRAM::YosysTclGenerator gen;
    std::string platform = cli_options_.platform_path;
    if (!platform.empty() && platform.front() != '/') {
        platform = join_path(get_current_dir_name(), platform);
    }
    std::string tech_lib = join_path(cur_path_, "tech/lib");
    return gen.generate_script(
        rtl_path_, syn_path_, generate_parameter_string(),
        get_addr_width(cli_options_),
        cli_options_.num_wls, cli_options_.num_banks,
        cli_options_.num_rows_per_mux,
        cli_options_.num_wl_buf, cli_options_.num_sae_buf,
        abc_output_load_ff(), kAbcDelayTargetPs,
        platform, tech_lib,
        cli_options_.single_port, cli_options_.share_port_b, cli_options_.bitcell_6t,
        cli_options_.row_predecode_bits);
}

double YosysManager::abc_output_load_ff() const {
    double worst_load_ff = 0.0;
    for (const auto& entry : pin_capacitances_) {
        worst_load_ff = std::max(
            worst_load_ff, entry.second * 1000.0 * kArrayLoadMargin);
    }
    // Past a net's cap limit the port buffer repair_design sizes carries the
    // load (the 6T macro's array-wide nets); ABC maps the logic before it.
    return std::clamp(worst_load_ff, kDefaultAbcLoadFf, kMaxNetCapPf * 1000.0);
}

bool YosysManager::generate_timing_constraints() const {
    const std::string abc_path = syn_path_ + "/abc.constr";
    std::ofstream abc(abc_path);
    if (!abc.is_open()) {
        LOGE << "Cannot write ABC constraints: " << abc_path;
        return false;
    }
    abc << "set_driving_cell BUFx2_ASAP7_75t_R\n";
    abc << "set_load " << std::fixed << std::setprecision(3)
        << abc_output_load_ff() << "\n";
    abc.close();

    const std::string sdc_path = syn_path_ + "/timing.sdc";
    std::ofstream sdc(sdc_path);
    if (!sdc.is_open()) {
        LOGE << "Cannot write timing constraints: " << sdc_path;
        return false;
    }
    sdc << "# OpenFinRAM ctrl_decode constraints; time=ns capacitance=pF\n";
    sdc << "# mode: " << (cli_options_.single_port ? "single-port" : "dual-port") << "\n";
    sdc << "create_clock -name clk -period " << std::fixed
        << std::setprecision(3) << kClockPeriodNs << " [get_ports {clk}]\n";
    sdc << "set_clock_uncertainty " << kClockUncertaintyNs
        << " [get_clocks {clk}]\n";
    sdc << "set_input_transition " << kInputSlewNs << " [all_inputs]\n";
    if (cli_options_.single_port) {
        sdc << "set_input_delay -clock clk -max " << kIoDelayNs
            << " [get_ports -quiet {ce_n we_n oe_n A* sdel*}]\n";
        sdc << "set_input_delay -clock clk -min " << kInputMinDelayNs
            << " [get_ports -quiet {ce_n we_n oe_n A* sdel*}]\n";
    } else {
        sdc << "set_input_delay -clock clk -max " << kIoDelayNs
            << " [get_ports -quiet {ce_n_A ce_n_B we_n_A we_n_B oe_n_A oe_n_B A_A* A_B* rst_n}]\n";
        sdc << "set_input_delay -clock clk -min " << kInputMinDelayNs
            << " [get_ports -quiet {ce_n_A ce_n_B we_n_A we_n_B oe_n_A oe_n_B A_A* A_B* rst_n}]\n";
    }
    sdc << "set_output_delay -clock clk -max " << kIoDelayNs
        << " [all_outputs]\n";
    sdc << "set_output_delay -clock clk -min " << kOutputMinDelayNs
        << " [all_outputs]\n";
    sdc << "set_max_transition " << kMaxTransitionNs
        << " [current_design]\n";
    // A counted array-wide net (the 6T macro's sel_lo) is more than a
    // routed net's limit by design; the cells' own Liberty limits still hold.
    double max_cap_pf = kMaxNetCapPf;
    for (const auto& pin : counted_pins_) max_cap_pf = std::max(max_cap_pf, 1.1 * pin_capacitances_.at(pin));
    sdc << "set_max_capacitance " << max_cap_pf
        << " [current_design]\n";
    sdc << "set_max_fanout 10 [current_design]\n";
    sdc << "set_voltage 0.700\n";
    sdc << "\n# Predicted array loads with " << kArrayLoadMargin
        << "x routing/model margin.\n";

    const std::string load_report_path = syn_path_ + "/periphery_loads.rpt";
    std::ofstream loads(load_report_path);
    if (!loads.is_open()) {
        LOGE << "Cannot write periphery load report: " << load_report_path;
        return false;
    }
    loads << "# port_pattern predicted_pf constrained_pf\n";
    for (const auto& entry : pin_capacitances_) {
        // Counted loads (fins and a wire allowance) take no guess margin.
        const double margin = counted_pins_.count(entry.first) ? 1.0 : kArrayLoadMargin;
        const double constrained_pf = entry.second > 0.0
            ? entry.second * margin
            : kDefaultAbcLoadFf / 1000.0;
        sdc << "set_load " << std::fixed << std::setprecision(6)
            << constrained_pf << " [get_ports -quiet {"
            << entry.first << "*}]\n";
        loads << std::left << std::setw(16) << (entry.first + "*")
              << " " << std::right << std::fixed << std::setprecision(6)
              << entry.second << " " << constrained_pf << "\n";
    }
    loads << "abc_worst_load_ff " << std::fixed << std::setprecision(3)
          << abc_output_load_ff() << "\n";
    loads.close();
    sdc.close();

    LOGI << "Generated load-aware constraints: " << sdc_path
         << " (ABC load " << abc_output_load_ff() << " fF)";
    return true;
}

bool YosysManager::generate_synthesis_script() {
    LOGD << std::string(70, '=');
    LOGD << "Generating Yosys Synthesis Script (open-source ASAP7 "
         << (cli_options_.single_port ? "single-port" : "dual-port") << ")";
    LOGD << std::string(70, '=');

    if (!directory_exists(syn_path_)) {
        if (!create_directory(syn_path_, nullptr)) {
            LOGE << "Failed to create synthesis directory: " << syn_path_;
            return false;
        }
    }
    std::string script_path = syn_path_ + "/synth.ys";
    std::string content = generate_yosys_script();
    std::ofstream out(script_path);
    if (!out.is_open()) {
        LOGE << "Cannot open file for writing: " << script_path;
        return false;
    }
    out << content;
    out.close();
    if (!generate_timing_constraints()) return false;
    LOGI << "Yosys script written to: " << script_path;
    return true;
}

bool YosysManager::run_yosys() {
    LOGD << std::string(70, '=');
    LOGD << "Running Yosys";
    LOGD << std::string(70, '=');
    std::string script_path = syn_path_ + "/synth.ys";
    if (!file_exists(script_path)) {
        LOGE << "synth.ys not found at " << script_path;
        return false;
    }
    // Use bash/sh - tcsh may not be installed; yosys is a plain shell command
    std::string cmd = "bash -c 'cd \"" + syn_path_ + "\" && yosys -s synth.ys > synth.log 2>&1' > /dev/null 2>&1";
    LOGD << "  ▶ Running: " << cmd;
    int result = std::system(cmd.c_str());
    if (result != 0) {
        LOGE << "Yosys failed with exit code: " << result << " (check "
             << syn_path_ << "/synth.log); refusing to generate a correctness stub";
        return false;
    }
    LOGD << "  ✓ Yosys completed successfully";
    return true;
}

bool YosysManager::verify_synthesis_output() {
    LOGD << std::string(70, '=');
    LOGD << "Verifying Yosys Output";
    LOGD << std::string(70, '=');
    std::string netlist_path = syn_path_ + "/netlist.v";
    if (!file_exists(netlist_path)) {
        LOGE << "  ✗ Error: netlist.v not generated at " << netlist_path;
        return false;
    }
    struct stat st;
    if (stat(netlist_path.c_str(), &st) == 0) {
        LOGD << "  ✓ Netlist generated: " << netlist_path << " (" << st.st_size << " bytes)";
        return true;
    }
    LOGE << "  ✗ Error checking netlist file";
    return false;
}

bool YosysManager::verify_periphery_structure() {
    const std::string netlist_path = syn_path_ + "/netlist.v";
    std::ifstream netlist(netlist_path);
    if (!netlist.is_open()) {
        LOGE << "Cannot inspect mapped periphery netlist: " << netlist_path;
        return false;
    }
    std::ostringstream contents;
    contents << netlist.rdbuf();
    const std::string text = contents.str();

    const std::size_t dff_count =
        count_occurrences(text, "DFFHQNx1_ASAP7_75t_R") +
        count_occurrences(text, "DFFHQNx2_ASAP7_75t_R") +
        count_occurrences(text, "DFFHQNx3_ASAP7_75t_R");

    if (cli_options_.single_port) {
        const std::size_t invx1_count =
            count_occurrences(text, "INVx1_ASAP7_75t_R");
        const std::size_t bufx4_count =
            count_occurrences(text, "BUFx4_ASAP7_75t_R");
        std::size_t sdel_d_count = 0;
        for (unsigned bit = 0; bit < 4; ++bit) {
            sdel_d_count += count_occurrences(
                text, ".D(sdel[" + std::to_string(bit) + "])");
        }

        const std::size_t expected_dffs =
            static_cast<std::size_t>(get_addr_width(cli_options_) + 7);
        constexpr std::size_t kExpectedDelayInverters = 108;
        const std::size_t expected_wl_drivers =
            static_cast<std::size_t>(2 * cli_options_.num_wls * cli_options_.num_banks);
        const bool pass = dff_count == expected_dffs &&
                          invx1_count >= kExpectedDelayInverters &&
                          bufx4_count >= expected_wl_drivers &&
                          sdel_d_count == 4;

        const std::string report_path = syn_path_ + "/periphery_structure.rpt";
        std::ofstream report(report_path);
        if (report.is_open()) {
            report << "dff_count " << dff_count << " expected " << expected_dffs << "\n";
            report << "invx1_count " << invx1_count << " expected_min "
                   << kExpectedDelayInverters << "\n";
            report << "bufx4_wordline_drivers " << bufx4_count << " expected_min "
                   << expected_wl_drivers << "\n";
            report << "sdel_register_inputs " << sdel_d_count << " expected 4\n";
            report << "status " << (pass ? "PASS" : "FAIL") << "\n";
        }

        if (!pass) {
            LOGE << "Periphery structural signoff failed: DFF=" << dff_count
                 << "/" << expected_dffs << ", INVx1=" << invx1_count
                 << "/>=108, BUFx4=" << bufx4_count << "/>="
                 << expected_wl_drivers << ", sdel D pins=" << sdel_d_count << "/4 (see "
                 << report_path << ")";
            return false;
        }
        LOGI << "Periphery structural signoff PASS: " << dff_count
             << " state flops, " << invx1_count
             << " INVx1 cells, " << bufx4_count
             << " BUFx4 drivers, all sdel bits retained";
        return true;
    } else {
        // Dual-port DP: BUFx2 delay chain, async-reset flops (DFFASR)
        // Count any DFF variant (DFFHQN for plain, DFFASR for reset).
        std::size_t dff_asr_count = count_occurrences(text, "DFFASRHQN");
        std::size_t dff_total = dff_count + dff_asr_count;
        const std::size_t bufx2_count =
            count_occurrences(text, "BUFx2_ASAP7_75t_R");
        // One controller per port: the generated single-port macro has one.
        const std::size_t ports = cli_options_.bitcell_6t ? 1 : 2;
        const std::size_t expected_bufx2 =
            ports * (cli_options_.num_wl_buf + cli_options_.num_sae_buf);
        const std::size_t expected_dffs =
            ports * (get_addr_width(cli_options_) + 2);
        const std::size_t named_delay_count =
            count_occurrences(text, "physical_dp_delay_");

        const bool pass = dff_total == expected_dffs &&
                          named_delay_count == expected_bufx2;

        const std::string report_path = syn_path_ + "/periphery_structure.rpt";
        std::ofstream report(report_path);
        if (report.is_open()) {
            report << "mode dual-port\n";
            report << "dff_count " << dff_total << " expected " << expected_dffs << "\n";
            report << "  dff_hqn=" << dff_count << " dff_asr=" << dff_asr_count << "\n";
            report << "bufx2_total " << bufx2_count << "\n";
            report << "named_delay_bufx2 " << named_delay_count << " expected "
                   << expected_bufx2 << "\n";
            report << "status " << (pass ? "PASS" : "FAIL") << "\n";
        }

        if (!pass) {
            LOGE << "Periphery structural signoff failed (DP): DFF=" << dff_total
                 << " (HQN=" << dff_count << " ASR=" << dff_asr_count << ") /" << expected_dffs
                 << ", named delay BUFx2=" << named_delay_count << "/" << expected_bufx2
                 << " (total BUFx2=" << bufx2_count << "; see " << report_path << ")";
            return false;
        }
        LOGI << "Periphery structural signoff PASS (DP): " << dff_total
             << " state/address flops, " << named_delay_count
             << " protected BUFx2 delay cells";
        return true;
    }
}

bool YosysManager::fix_assign_statements() {
    // OpenROAD understands the direct wire aliases emitted by Yosys.  A prior
    // implementation replaced every assign with a scalar buffer, which
    // corrupted concatenated/bus aliases.  Keep legal Yosys assigns intact;
    // physical outputs already have explicit standard-cell drivers.
    LOGD << std::string(70, '=');
    LOGD << "Checking and Fixing Assign Statements (Yosys)";
    LOGD << std::string(70, '=');
    std::string netlist_path = syn_path_ + "/netlist.v";
    if (!file_exists(netlist_path)) return false;
    std::ifstream infile(netlist_path);
    if (!infile.is_open()) return false;
    std::string line;
    int assign_count = 0;
    while (std::getline(infile, line)) {
        size_t ap = line.find("assign");
        if (ap != std::string::npos) {
            size_t cp = line.find("//");
            if (cp == std::string::npos || ap < cp) assign_count++;
        }
    }
    infile.close();
    if (assign_count == 0) {
        LOGD << "  ✓ No assign statements found";
        return true;
    }
    LOGD << "  ✓ Retaining " << assign_count
         << " legal Yosys wire alias assign(s)";
    return true;
}

bool YosysManager::predict_capacitance() {
    LOGD << std::string(70, '=');
    LOGD << "Predicting Capacitance (Yosys path)";
    LOGD << std::string(70, '=');
    LOGD << "  Configuration: num_wls*2=" << cli_options_.num_wls * 2 << ", num_data_bits=" << cli_options_.num_data_bits
         << ", single_port=" << cli_options_.single_port;
    OpenFinRAM::CapacitancePredictor predictor;
    auto predictions = predictor.predict_all(cli_options_.num_wls * 2, cli_options_.num_data_bits);
    std::map<std::string, std::vector<std::string>> signal_map;
    if (cli_options_.single_port) {
        signal_map["WLT"] = {"wlt", "wlb"};
        signal_map["YSELT"] = {"yselt", "yseltn", "yselb", "yselbn"};
        signal_map["BLPRECHTN"] = {"blprechtn", "blprechbn"};
        signal_map["WRENA"] = {"wrena", "wrenan"};
        signal_map["SAE"] = {"sae", "saprechn", "oeb_out", "oe_out"};
    } else {
        // The wordlines are driven by the slice strips at the array; the
        // controller's predecode lines fan out to the slices like a column
        // select does to its columns.
        signal_map["YSELT"] = {"ysel_A", "yseln_A", "ysel_B", "yseln_B",
                               "sel_hi_A", "sel_hi_B", "sel_lo_A", "sel_lo_B"};
        signal_map["BLPRECHTN"] = {"blprechn_A", "blprechn_B"};
        signal_map["WRENA"] = {"wrena_A", "wrenan_A"};
        signal_map["SAE"] = {"sae_A", "sae_B", "oeb_out_A", "oe_out_A", "oeb_out_B", "oe_out_B"};
    }
    for (auto &pred : predictions) {
        auto it = signal_map.find(pred.first);
        if (it != signal_map.end()) {
            for (auto &pin : it->second) pin_capacitances_[pin] = pred.second;
        } else {
            pin_capacitances_[pred.first] = pred.second;
        }
    }
    if (cli_options_.bitcell_6t) predict_6t_loads();
    return true;
}

void YosysManager::predict_6t_loads() {
    // The srambank predictor above knows nothing of the generated macro's
    // array-wide nets (0.1 fF for a sel_lo that is 170 fF at 256 rows), so
    // the output buffers stayed BUFx2 and a large macro's sel_lo settled
    // after the wordline enable: the previous row's wordline fired first and
    // SAE fired before the new one (x256x2x1, x64x64x1).
    for (const auto& [pin, pf] : six_t_port_loads_pf(cli_options_)) {
        pin_capacitances_[pin] = pf;
        counted_pins_.insert(pin);
    }
}

bool YosysManager::run_synthesis() {
    LOGD << std::string(70, '#');
    LOGD << "# Yosys Synthesis Flow (" << (cli_options_.single_port ? "single-port" : "dual-port") << " ASAP7)";
    LOGD << std::string(70, '#');
    if (!predict_capacitance()) return false;
    if (!generate_synthesis_script()) return false;
    if (!run_yosys()) return false;
    if (!verify_synthesis_output()) return false;
    if (!fix_assign_statements()) return false;
    if (!verify_periphery_structure()) return false;
    LOGD << "✓ Yosys Synthesis Complete";
    return true;
}
