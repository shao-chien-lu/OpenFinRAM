#include "synthesis_manager.hpp"

#include <iostream>
#include <fstream>
#include <sstream>
#include <cstdlib>
#include <sys/stat.h>
#include <iomanip>
#include <cmath>

#include "plog/Log.h"

#include "capacitance_parser.hpp"
#include "capacitance_predictor.hpp"
#include "utils.hpp"

SynthesisManager::SynthesisManager(const MainCliOptions& cli_options)
    : cli_options_(cli_options) {
    cur_path_ = get_executable_directory();

    if (cli_options_.single_port) {
        rtl_path_ = join_path(cur_path_, "tech/verilog_sp");
    } else {
        rtl_path_ = join_path(cur_path_, "tech/verilog_dp");
    }

    db_path_ = join_path(get_executable_directory(), "tech/db");
    syn_path_ = join_path(get_executable_directory(), "tmp/syn_" + get_run_timestamp());
    output_path_ = join_path(get_executable_directory(), "tmp/syn_" + get_run_timestamp());

    if (!directory_exists(join_path(get_executable_directory(), "tmp"))) {
        LOGD << "Creating tmp directory: " << join_path(get_executable_directory(), "tmp");
        create_directory(join_path(get_executable_directory(), "tmp"), nullptr);
    }
    if (!directory_exists(syn_path_)) {
        LOGD << "Creating synthesis directory: " << syn_path_;
        create_directory(syn_path_, nullptr);
    }
}

std::string SynthesisManager::generate_parameter_string() const {
    int addr_width = std::ceil(std::log2(cli_options_.num_wls))
                   + std::ceil(std::log2(cli_options_.num_banks))
                   + 1 + std::ceil(std::log2(cli_options_.num_rows_per_mux));

    std::ostringstream oss;
    oss << "ADDR_WIDTH=" << addr_width
        << ",NUM_WL=" << cli_options_.num_wls
        << ",NUM_BANK=" << cli_options_.num_banks
        << ",COLUMN_MUX=" << cli_options_.num_rows_per_mux;
    return oss.str();
}

std::string SynthesisManager::generate_tcl_content() const {
    std::ostringstream oss;
    int addr_width = get_addr_width(cli_options_);

    oss << "# Design Compiler synthesis script (Version 2)\n"
        << "# Configuration:\n"
        << "#   ADDR_WIDTH=" << addr_width << "\n"
        << "#   NUM_WL=" << cli_options_.num_wls << "\n"
        << "#   NUM_BANK=" << cli_options_.num_banks << "\n"
        << "#   WL_BUF=" << cli_options_.num_wl_buf << "\n"
        << "#   SAE_BUF=" << cli_options_.num_sae_buf << "\n\n"

        << "set_app_var search_path \"$search_path " << db_path_ << " " << rtl_path_ << "\"\n"
        << "set_app_var target_library \"asap7sc7p5t_AO_RVT_TT.db asap7sc7p5t_INVBUF_RVT_TT.db asap7sc7p5t_OA_RVT_TT.db asap7sc7p5t_SEQ_RVT_TT.db asap7sc7p5t_SIMPLE_RVT_TT.db\"\n"
        << "set_app_var link_library \"* asap7sc7p5t_AO_RVT_TT.db asap7sc7p5t_INVBUF_RVT_TT.db asap7sc7p5t_OA_RVT_TT.db asap7sc7p5t_SEQ_RVT_TT.db asap7sc7p5t_SIMPLE_RVT_TT.db\"\n"
        << "\n"

        << "# Analyze and elaborate\n"
        << "analyze -f sverilog {sram_control.v delay_cell.v"
        << (cli_options_.single_port ? "" : " row_decoder.v") << "}\n"
        << "elaborate ctrl_decode -parameters \"" << generate_parameter_string() << "\"\n"
        << "rename_design [current_design] ctrl_decode\n"
        << "link\n\n"

        << "create_clock -name clk -period 0.2 [get_ports clk]\n"
        << "set_clock_uncertainty 0.02 [get_clocks clk]\n"
        << "set_input_transition 0.01 [all_inputs]\n\n"
        
        << "# Prevent delay cells from being optimized\n"
        << "set DELAY_CELLS [get_cells {*u_phase_*/*u_*}]\n"
        << "set_dont_touch $DELAY_CELLS true\n\n"

        << "set_dont_use asap7sc7p5t_AO_RVT_TT/AOI211xp5_ASAP7_75t_R\n"
        << "set_dont_use asap7sc7p5t_OA_RVT_TT/OAI31xp33_ASAP7_75t_R\n"
        << "set_dont_use asap7sc7p5t_OA_RVT_TT/OAI32xp33_ASAP7_75t_R\n"
        << "set_dont_use asap7sc7p5t_OA_RVT_TT/OAI221xp5_ASAP7_75t_R\n\n";

    if (!pin_capacitances_.empty()) {
        oss << "# Output load settings (predicted capacitance)\n";
        for (const auto& entry : pin_capacitances_) {
            const std::string& pin_name = entry.first;
            double cap_pf = entry.second;
            oss << "set_load " << std::fixed << std::setprecision(6) 
                << (cap_pf * 1000) * 1.5 << " [get_ports " << pin_name << "*]\n";
        }
        oss << "\n";
    }

    oss << "set_fix_multiple_port_nets -all -buffer_constants [get_designs *] \n\n"
        << "# Compile and generate netlist\n"
        << "compile\n"
        << "report_qor > qor_report.txt\n"
        << "ungroup -all -flatten\n"
        << "define_name_rules NO_CASE_CONFLICT -case_insensitive -allowed \"A-Za-z0-9_\"\n"
        << "change_names -rules NO_CASE_CONFLICT -hierarchy\n"
        << "change_names -rules verilog -hierarchy\n"
        << "write_file -hierarchy -format verilog -output " << syn_path_ << "/netlist.v\n"
        << "write_sdc ./timing.sdc\n"
        << "exit\n";

    return oss.str();
}

bool SynthesisManager::generate_synthesis_script() {
    LOGD << std::string(70, '=');
    LOGD << "Generating Synthesis Script";
    LOGD << std::string(70, '=');

    if (!directory_exists(syn_path_)) {
        LOGD << "Creating synthesis directory: " << syn_path_;
        if (!create_directory(syn_path_, nullptr)) {
            LOGE << "Failed to create synthesis directory: " << syn_path_;
            return false;
        }
    }

    std::string syn_tcl_path = syn_path_ + "/syn_auto.tcl";
    std::string tcl_content = generate_tcl_content();

    std::ofstream tcl_file(syn_tcl_path);
    if (!tcl_file.is_open()) {
        LOGE << "Cannot open file for writing: " << syn_tcl_path;
        return false;
    }

    tcl_file << tcl_content;
    tcl_file.close();

    return true;
}

bool SynthesisManager::run_design_compiler() {
    LOGD << "\n" << std::string(70, '=');
    LOGD << "Running Design Compiler";
    LOGD << std::string(70, '=');
    
    std::string syn_tcl_path = syn_path_ + "/syn_auto.tcl";
    
    // Check if script was generated
    if (!file_exists(syn_tcl_path)) {
        LOGE << "  ✗ Error: syn_auto.tcl not found at " << syn_tcl_path;
        LOGE << "    Please run generate_synthesis_script() first";
        return false;
    }
    
    // Construct the Design Compiler command
    // Note: Adjust dc_shell path if needed for your environment
    std::string cmd = "tcsh -c 'cd " + syn_path_ + " && dc_shell -f syn_auto.tcl -output_log_file syn_auto.log' > /dev/null 2>&1";
    
    LOGD << "  ▶ Running: " << cmd;
    
    int result = std::system(cmd.c_str());
    
    if (result != 0) {
        LOGE << "  ✗ Design Compiler failed with exit code: " << result;
        LOGE << "    Check log file: " << syn_path_ << "/syn_auto.log";
        return false;
    }
    
    LOGD << "  ✓ Design Compiler completed successfully";
    return true;
}

bool SynthesisManager::verify_synthesis_output() {
    LOGD << "\n" << std::string(70, '=');
    LOGD << "Verifying Synthesis Output";
    LOGD << std::string(70, '=');
    
    std::string netlist_path = syn_path_ + "/netlist.v";
    std::string log_path = syn_path_ + "/syn_auto.log";
    
    if (!file_exists(netlist_path)) {
        LOGE << "  ✗ Error: netlist.v not generated";
        
        if (file_exists(log_path)) {
            LOGE << "\n  Checking log file for errors...";
            std::ifstream log_file(log_path.c_str());
            std::string line;
            int error_count = 0;
            while (std::getline(log_file, line) && error_count < 10) {
                if (line.find("Error") != std::string::npos ||
                    line.find("ERROR") != std::string::npos ||
                    line.find("error") != std::string::npos) {
                    LOGE << "    " << line;
                    error_count++;
                }
            }
        }
        return false;
    }
    
    // Get file size using stat
    struct stat stat_buf;
    if (stat(netlist_path.c_str(), &stat_buf) == 0) {
        LOGD << "  ✓ Netlist generated successfully";
        LOGD << "  → " << netlist_path;
        LOGD << "  File size: " << stat_buf.st_size << " bytes";
        return true;
    } else {
        LOGE << "  ✗ Error checking netlist file";
        return false;
    }
}

bool SynthesisManager::run_synthesis() {
    LOGD << std::string(70, '#');
    LOGD << "# Parameterized Design Synthesis Flow";
    LOGD << std::string(70, '#');

    if (!predict_capacitance()) {
        LOGE << "Synthesis failed at capacitance prediction step";
        return false;
    }

    if (!generate_synthesis_script()) {
        LOGE << "Synthesis failed at script generation step";
        return false;
    }

    if (!run_design_compiler()) {
        LOGE << "Synthesis failed at Design Compiler execution step";
        return false;
    }

    if (!verify_synthesis_output()) {
        LOGE << "Synthesis failed at output verification step";
        return false;
    }

    if (!fix_assign_statements()) {
        LOGE << "Synthesis failed at assign statement fixing step";
        return false;
    }

    LOGD << std::string(70, '=');
    LOGD << "✓ Synthesis Complete";
    LOGD << std::string(70, '=');

    return true;
}

bool SynthesisManager::predict_capacitance() {
    LOGD << std::string(70, '=');
    LOGD << "Predicting Capacitance using Statistical Model";
    LOGD << std::string(70, '=');

    LOGD << "  Configuration: num_wls * 2=" << cli_options_.num_wls * 2 
         << ", num_data_bits=" << cli_options_.num_data_bits;

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

    LOGD << "\n  Predicted Capacitances:";
    for (const auto& pred : predictions) {
        const std::string& metric = pred.first;
        double cap = pred.second;

        auto it = signal_map.find(metric);
        if (it != signal_map.end()) {
            for (const auto& pin : it->second) {
                pin_capacitances_[pin] = cap;

                LOGD << "    " << std::left << std::setw(20) << pin << ": "
                     << std::fixed << std::setprecision(6) 
                     << cap << " pF";
            }
        } else {
            pin_capacitances_[metric] = cap;

            LOGD << "    " << std::left << std::setw(20) << metric << ": "
                 << std::fixed << std::setprecision(6) 
                 << cap << " pF";
        }
    }

    return true;
}

bool SynthesisManager::fix_assign_statements() {
    LOGD << "\n" << std::string(70, '=');
    LOGD << "Checking and Fixing Assign Statements";
    LOGD << std::string(70, '=');
    
    std::string netlist_path = syn_path_ + "/netlist.v";
    
    // Check if netlist exists
    if (!file_exists(netlist_path)) {
        LOGE << "  ✗ Error: Netlist not found at " << netlist_path;
        return false;
    }
    
    // Read the netlist file
    std::ifstream infile(netlist_path);
    if (!infile.is_open()) {
        LOGE << "  ✗ Error: Cannot open netlist file: " << netlist_path;
        return false;
    }
    
    std::vector<std::string> lines;
    std::string line;
    int assign_count = 0;
    int line_num = 0;
    std::vector<int> assign_line_numbers;
    
    // Read all lines and count assigns
    while (std::getline(infile, line)) {
        line_num++;
        lines.push_back(line);
        
        // Check if line contains "assign"
        size_t assign_pos = line.find("assign");
        if (assign_pos != std::string::npos) {
            // Make sure it's not in a comment
            size_t comment_pos = line.find("//");
            if (comment_pos == std::string::npos || assign_pos < comment_pos) {
                assign_count++;
                assign_line_numbers.push_back(line_num);
            }
        }
    }
    infile.close();
    
    if (assign_count == 0) {
        LOGD << "  ✓ No assign statements found - netlist is clean";
        return true;
    }
    
    LOGD << "  ⚠ Found " << assign_count << " assign statement(s)";
    LOGD << "  Converting to HB1xp67_ASAP7_75t_R buffers...";
    
    // Process each line and replace assigns with buffers
    int buffer_counter = 0;
    for (size_t i = 0; i < lines.size(); i++) {
        std::string& current_line = lines[i];
        size_t assign_pos = current_line.find("assign");
        
        if (assign_pos != std::string::npos) {
            // Make sure it's not in a comment
            size_t comment_pos = current_line.find("//");
            if (comment_pos != std::string::npos && assign_pos >= comment_pos) {
                continue;
            }
            
            // Parse the assign statement: "assign output = input;"
            // Pattern: assign <spaces> output <spaces> = <spaces> input <spaces> ;
            
            // Find the '=' position
            size_t equal_pos = current_line.find('=', assign_pos);
            if (equal_pos == std::string::npos) {
                LOGE << "  ✗ Warning: Invalid assign at line " << (i+1);
                continue;
            }
            
            // Find the ';' position
            size_t semicolon_pos = current_line.find(';', equal_pos);
            if (semicolon_pos == std::string::npos) {
                LOGE << "  ✗ Warning: No semicolon found at line " << (i+1);
                continue;
            }
            
            // Extract output signal (between "assign" and "=")
            std::string output = current_line.substr(assign_pos + 6, equal_pos - assign_pos - 6);
            // Trim whitespace
            size_t start = output.find_first_not_of(" \t");
            size_t end = output.find_last_not_of(" \t");
            if (start != std::string::npos && end != std::string::npos) {
                output = output.substr(start, end - start + 1);
            }
            
            // Extract input signal (between "=" and ";")
            std::string input = current_line.substr(equal_pos + 1, semicolon_pos - equal_pos - 1);
            // Trim whitespace
            start = input.find_first_not_of(" \t");
            end = input.find_last_not_of(" \t");
            if (start != std::string::npos && end != std::string::npos) {
                input = input.substr(start, end - start + 1);
            }
            
            // Get indentation from original line
            std::string indent = "";
            for (size_t j = 0; j < current_line.length(); j++) {
                if (current_line[j] == ' ' || current_line[j] == '\t') {
                    indent += current_line[j];
                } else {
                    break;
                }
            }
            
            // Generate unique buffer instance name
            std::ostringstream buf_name;
            buf_name << "u_buf_assign_" << buffer_counter++;
            
            // Create buffer instantiation
            std::ostringstream new_line;
            new_line << indent << "HB1xp67_ASAP7_75t_R " << buf_name.str() 
                     << " ( .A(" << input << "), .Y(" << output << ") );";
            
            // Replace the line
            current_line = new_line.str();
            
            LOGD << "  • Line " << (i+1) << ": " << output << " = " << input 
                 << " → " << buf_name.str();
        }
    }
    
    // Write the modified netlist back
    std::string backup_path = netlist_path + ".backup";
    
    // Create backup
    std::string cp_cmd = "cp " + netlist_path + " " + backup_path;
    int result = std::system(cp_cmd.c_str());
    if (result != 0) {
        LOGE << "  ✗ Warning: Could not create backup file" << " (command: " << cp_cmd << ")";
    } else {
        LOGD << "  ✓ Created backup: " << backup_path;
    }
    
    // Write modified content
    std::ofstream outfile(netlist_path);
    if (!outfile.is_open()) {
        LOGE << "  ✗ Error: Cannot write to netlist file: " << netlist_path;
        return false;
    }
    
    for (const auto& l : lines) {
        outfile << l << "\n";
    }
    outfile.close();
    
    LOGD << "  ✓ Fixed " << assign_count << " assign statement(s)";
    LOGD << "  → Updated netlist: " << netlist_path;
    
    return true;
}
