#include "spice_integrator.hpp"
#include "spice_generator.hpp"
#include <iostream>
#include <fstream>
#include <sstream>
#include <cctype>
#include <sys/stat.h>
#include <algorithm>
#include <regex>

#include "plog/Log.h"

#include "utils.hpp"

// Constructor
SpiceIntegrator::SpiceIntegrator(const SramIntegrationConfig& config)
    : config_(config) {}

SpiceIntegrator::SpiceIntegrator(const MainCliOptions& cli_options_)
    : cli_options_(cli_options_) {}

std::string SpiceIntegrator::get_config_string() const {
    std::ostringstream oss;
    oss << "SRAM Integration Configuration:\n"
        << "  Control Netlist: " << config_.ctrl_netlist << "\n"
        << "  Datapath Netlist: " << config_.datapath_netlist << "\n"
        << "  Output Netlist: " << config_.output_netlist << "\n"
        << "  Address Width: " << config_.addr_width << " bits\n"
        << "  Data Width: " << config_.data_width << " bits\n"
        << "  Wordlines: " << config_.num_wordlines << " (top and bottom)";
    return oss.str();
}

std::vector<std::string> SpiceIntegrator::parse_ctrl_ports(
    const std::string& netlist_sp) {
    
    std::vector<std::string> ports;
    
    std::ifstream file(netlist_sp.c_str());
    if (!file.is_open()) {
        std::cerr << "  ✗ Error: Cannot open netlist " << netlist_sp << std::endl;
        return ports;
    }
    
    std::string line;
    bool in_subckt = false;
    std::string subckt_section;
    
    // Read file and find ctrl_decode subcircuit
    while (std::getline(file, line)) {
        // Check if line starts .SUBCKT ctrl_decode
        if (line.substr(0, 7) == ".SUBCKT" || line.substr(0, 7) == ".subckt") {
            if (line.find("ctrl_decode") != std::string::npos ||
                line.find("CTRL_DECODE") != std::string::npos) {
                in_subckt = true;
                
                // Extract ports from this line
                std::istringstream iss(line);
                std::string token;
                int token_count = 0;
                
                while (iss >> token) {
                    token_count++;
                    if (token_count > 2) { // Skip ".SUBCKT ctrl_decode"
                        // Remove any trailing characters
                        if (token.back() == '\n' || token.back() == '\r') {
                            token.pop_back();
                        }
                        if (!token.empty() && token[0] != '*') {
                            ports.push_back(token);
                        }
                    }
                }
            }
        } else if (in_subckt && line.find(".ENDS") != std::string::npos) {
            // End of subcircuit found
            break;
        } else if (in_subckt && !line.empty()) {
            // Continuation line with more ports (usually starting with +)
            if (line[0] == '+' && line.find("=") == std::string::npos) {
                // Remove the continuation character
                std::string port_line = line.substr(1);
                std::istringstream iss(port_line);
                std::string token;
                
                while (iss >> token) {
                    // Skip comments and empty tokens
                    if (!token.empty() && token[0] != '*' && token[0] != '$') {
                        ports.push_back(token);
                    }
                }
            }
        }
    }
    
    file.close();
    
    std::cout << "  ✓ Parsed " << ports.size() << " ports from ctrl_decode" << std::endl;
    if (ports.size() > 5) {
        std::cout << "    First few ports: " << ports[0];
        for (size_t i = 1; i < std::min(size_t(5), ports.size()); ++i) {
            std::cout << ", " << ports[i];
        }
        std::cout << ", ..." << std::endl;
    }
    
    return ports;
}

std::map<std::string, std::string> SpiceIntegrator::build_port_mapping() const {
    std::map<std::string, std::string> port_map;
    
    // Basic control signals (updated to ce_n and we_n)
    port_map["clk"] = "clk";
    // port_map["rst_n"] = "rst_n";
    port_map["ce_n"] = "ce_n";
    port_map["we_n"] = "we_n";
    port_map["saprechn"] = "saprechn";
    port_map["sae"] = "sae";
    port_map["blprechtn"] = "blprechtn";
    port_map["blprechbn"] = "blprechbn";
    port_map["wrena"] = "wrena";
    port_map["wrenan"] = "wrenan";
    
    // Address ports
    for (uint64_t i = 0; i < config_.addr_width; ++i) {
        std::ostringstream key, val;
        key << "A[" << i << "]";
        val << "A[" << i << "]";
        port_map[key.str()] = val.str();
    }
    
    // Wordline ports
    for (uint64_t i = 0; i < config_.num_wordlines; ++i) {
        std::ostringstream key;
        key << "wlt[" << i << "]";
        port_map[key.str()] = key.str();
        
        key.str("");
        key << "wlb[" << i << "]";
        port_map[key.str()] = key.str();
    }
    
    // Y-select ports
    for (int i = 0; i < 4; ++i) {
        std::ostringstream key;
        key << "yselt[" << i << "]";
        port_map[key.str()] = key.str();
        
        key.str("");
        key << "yseltn[" << i << "]";
        port_map[key.str()] = key.str();
        
        key.str("");
        key << "yselb[" << i << "]";
        port_map[key.str()] = key.str();
        
        key.str("");
        key << "yselbn[" << i << "]";
        port_map[key.str()] = key.str();
    }
    
    return port_map;
}

std::string SpiceIntegrator::generate_header(const std::string& ctrl_netlist_path, const std::string& datapath_netlist_path) const {
    std::ostringstream oss;
    oss << "* HSPICE Netlist for Complete SRAM\n"
        << "* Configuration: " << cli_options_.num_data_bits << "-bit data, "
        << get_addr_width(cli_options_) << "-bit address\n"
        << "* Wordlines: " << cli_options_.num_wls << " (top) + "
        << cli_options_.num_wls << " (bottom)\n"
        << "* Generated automatically by OpenFinRAM\n"
        << "*\n"
        << "* ===================================================================\n"
        << "* Includes\n"
        << "* ===================================================================\n"
        << ".INCLUDE \"" << ctrl_netlist_path << "\"\n"
        << ".INCLUDE \"" << datapath_netlist_path << "\"\n"
        << "\n";
    return oss.str();
}

std::string SpiceIntegrator::generate_subckt_header() const {
    std::ostringstream oss;
    
    oss << "* ===================================================================\n"
        << "* SRAM Top Module (" << cli_options_.num_data_bits << "-bit data)\n"
        << "* ===================================================================\n";
    
    if (cli_options_.single_port) {
        oss << "* Single-Port Configuration\n";

        // Build port list (updated to ce_n and we_n)
        oss << ".SUBCKT sram_x" << cli_options_.num_wls * 2
            << "x" << cli_options_.num_data_bits
            << "x" << cli_options_.num_banks
            << " vdd vss clk sdel[0] sdel[1] sdel[2] sdel[3] ce_n we_n oe_n";
        
        // Address ports
        for (uint64_t i = 0; i < get_addr_width(cli_options_); ++i) {
            oss << " A[" << i << "]";
        }
        
        // Data input ports
        for (uint64_t i = 0; i < cli_options_.num_data_bits; ++i) {
            oss << " D[" << i << "]";
        }
        
        // Data output ports
        for (uint64_t i = 0; i < cli_options_.num_data_bits; ++i) {
            oss << " Q[" << i << "]";
        }
    } else {
        oss << "* Dual-Port Configuration\n";

        oss << ".SUBCKT sram_x" << cli_options_.num_wls * 2
            << "x" << cli_options_.num_data_bits
            << "x" << cli_options_.num_banks
            << " vdd vss clk rst_n ce_n_A we_n_A oe_n_A"
            << "\n+";

        for (uint64_t i = 0; i < get_addr_width(cli_options_); ++i) {
            oss << " A_A[" << i << "]";
        }
        oss << "\n+";
        for (uint64_t i = 0; i < cli_options_.num_data_bits; ++i) {
            oss << " D_A[" << i << "]";
        }
        oss << "\n+";
        for (uint64_t i = 0; i < cli_options_.num_data_bits; ++i) {
            oss << " Q_A[" << i << "]";
        }
        oss << "\n+";

        // The generated single-port macro is port A alone.
        if (cli_options_.bitcell_6t) {
            oss << "\n\n";
            return oss.str();
        }
        oss << " ce_n_B we_n_B oe_n_B"
            << "\n+";
        for (uint64_t i = 0; i < get_addr_width(cli_options_); ++i) {
            oss << " A_B[" << i << "]";
        }
        oss << "\n+";
        for (uint64_t i = 0; i < cli_options_.num_data_bits; ++i) {
            oss << " D_B[" << i << "]";
        }
        oss << "\n+";
        for (uint64_t i = 0; i < cli_options_.num_data_bits; ++i) {
            oss << " Q_B[" << i << "]";
        }
    }
    
    
    oss << "\n\n";
    return oss.str();
}

std::string SpiceIntegrator::generate_ctrl_instance(
    const std::vector<std::string>& ctrl_ports) const {
    
    std::ostringstream oss;
    
    oss << "** Instantiate Control Decoder\n"
        << "** Port order matched from synthesized netlist.sp\n"
        << "Xctrl";
    
    auto port_map = build_port_mapping();
    
    int col_count = 0;
    for (const auto& port : ctrl_ports) {
        // Try to find mapping
        std::string connection = port;
        
        // Check lowercase version
        std::string port_lower = port;
        std::transform(port_lower.begin(), port_lower.end(), 
                      port_lower.begin(), ::tolower);
        
        if (port_map.find(port_lower) != port_map.end()) {
            connection = port_map[port_lower];
        } else if (port_map.find(port) != port_map.end()) {
            connection = port_map[port];
        }
        
        // Line wrapping every 8 ports
        if (col_count > 0 && col_count % 8 == 0) {
            oss << "\n+";
        }
        oss << " " << connection;
        col_count++;
    }
    
    oss << " ctrl_decode\n\n";
    return oss.str();
}

std::string SpiceIntegrator::generate_datapath_instance() const {
    std::ostringstream oss;
    // Divided wordlines (--segment-bits): each half is its segments, each
    // segment's tiles their own instance on their own wordline bus.
    const unsigned segments = cli_options_.single_port ? 1 : cli_options_.wordline_segments();
    const uint64_t seg_bits = cli_options_.single_port ? cli_options_.num_data_bits / 2
                                                       : cli_options_.wordline_segment_bits();

    for (int top_bottom = 0; top_bottom < 2; ++top_bottom) {
    for (unsigned seg = 0; seg < segments; ++seg) {
        const std::string suffix = segments > 1 ? "_s" + std::to_string(seg) : "";
        const uint64_t first_bit = top_bottom * (cli_options_.num_data_bits / 2) + seg * seg_bits;

        // The two halves of the data bits.  In the two-port macro they are
        // the stacks below (`lo`) and above (`hi`) the controller band, each
        // with its own wordlines from its own driver strips.
        const char* half = cli_options_.single_port
            ? (top_bottom == 0 ? "top" : "bottom")
            : (top_bottom == 0 ? "lo" : "hi");
        oss << "** Instantiate SRAM Datapath\n"
            << "Xdata_" << half << suffix << " ";
        
        for (int bank = 0; bank < cli_options_.num_banks; ++bank) {
            if (cli_options_.single_port) {
                for (uint64_t i = 0; i < cli_options_.num_wls; ++i) {
                    oss << " wlt[" << i + bank * cli_options_.num_wls << "]";
                }
                oss << "\n+";
                
                for (uint64_t i = 0; i < cli_options_.num_wls; ++i) {
                    oss << " wlb[" << i + bank * cli_options_.num_wls << "]";
                }
                oss << "\n+";
            } else {
                // One unsplit array per column: 2*NUM_WL wordlines a bank,
                // driven by this half's strips.
                const uint64_t rows = 2 * cli_options_.num_wls;
                const std::vector<const char*> ports = cli_options_.bitcell_6t
                    ? std::vector<const char*>{"a"} : std::vector<const char*>{"a", "b"};
                for (const char* port : ports) {
                    for (uint64_t i = 0; i < rows; ++i) {
                        oss << " wl_" << port << "_" << half << suffix << "[" << i + bank * rows << "]";
                    }
                    oss << "\n+";
                }
            }
        }

        // Data IO
        if (cli_options_.single_port) {
            for (uint64_t i = 0; i < cli_options_.num_data_bits / 2; ++i) {
                oss << " D[" << i + top_bottom * (cli_options_.num_data_bits / 2) << "]";
            }
            oss << "\n+";

            for (uint64_t i = 0; i < cli_options_.num_data_bits / 2; ++i) {
                oss << " Q[" << i + top_bottom * (cli_options_.num_data_bits / 2) << "]";
            }
            oss << "\n+";
        } else {
            for (uint64_t i = 0; i < seg_bits; ++i) {
                oss << " D_A[" << i + first_bit << "]";
            }
            oss << "\n+";

            for (uint64_t i = 0; i < seg_bits; ++i) {
                oss << " Q_A[" << i + first_bit << "]";
            }
            oss << "\n+";

            for (uint64_t i = 0; !cli_options_.bitcell_6t && i < cli_options_.num_data_bits / 2; ++i) {
                oss << " D_B[" << i + top_bottom * (cli_options_.num_data_bits / 2) << "]";
            }
            oss << "\n+";

            for (uint64_t i = 0; !cli_options_.bitcell_6t && i < cli_options_.num_data_bits / 2; ++i) {
                oss << " Q_B[" << i + top_bottom * (cli_options_.num_data_bits / 2) << "]";
            }
            oss << "\n+";
        }

        std::vector<std::string> ctrl_sigs;
        if (cli_options_.single_port) {
            ctrl_sigs = {
                "wrena", "wrenan", "saprechn", "sae", "oeb_out", "oe_out",
                "blprechtn", "blprechbn"
            };
        } else if (cli_options_.bitcell_6t) {
            ctrl_sigs = {"wrena_A", "wrenan_A", "oeb_out_A", "oe_out_A", "blprechn_A", "sae_A"};
        } else {
            ctrl_sigs = {
                "wrena_A", "wrenan_A", "wrena_B", "wrenan_B",
                "oeb_out_A", "oe_out_A", "oeb_out_B", "oe_out_B",
                "blprechn_A", "blprechn_B",
                "sae_A", "sae_B"
            };
        }
        for (const auto& sig : ctrl_sigs) {
            // Shared, port B's enables are the controller's one per pair of
            // banks (SHARED_B); its precharges stay one per bank.
            const bool per_pair = cli_options_.share_port_b && sig != "blprechn_B" &&
                                  sig.size() > 2 && sig.compare(sig.size() - 2, 2, "_B") == 0;
            const int count = per_pair ? cli_options_.num_banks / 2 : cli_options_.num_banks;
            for (int bank = 0; bank < count; ++bank) {
                oss << " " << sig << "[" << bank << "]";
            }

            oss << "\n+";
        }

        if (cli_options_.single_port) {
            for (int bank = 0; bank < cli_options_.num_banks; ++bank) {
                // yseltn
                for (int i = 0; i < 4; ++i) {
                    oss << " yseltn[" << i + 4 * bank << "]";
                }
                oss << "\n+";

                // yselt
                for (int i = 0; i < 4; ++i) {
                    oss << " yselt[" << i + 4 * bank << "]";
                }
                oss << "\n+";

                // yselbn
                for (int i = 0; i < 4; ++i) {
                    oss << " yselbn[" << i + 4 * bank << "]";
                }
                oss << "\n+";

                // yselb
                for (int i = 0; i < 4; ++i) {
                    oss << " yselb[" << i + 4 * bank << "]";
                }
                oss << "\n+";
            }
        } else {
            const std::vector<const char*> buses = cli_options_.bitcell_6t
                ? std::vector<const char*>{"yseln_A", "ysel_A"}
                : std::vector<const char*>{"yseln_A", "ysel_A", "yseln_B", "ysel_B"};
            // A bank's select bus: a line a column, or for the 6T block at
            // 16:1 the two predecoded groups (the controller's YSEL_W).
            const int mux = static_cast<int>(cli_options_.num_rows_per_mux);
            const int ysel_w = cli_options_.bitcell_6t && mux >= 16 ? 4 + mux / 4 : mux;
            for (const char* bus : buses) {
                for (int index = 0; index < static_cast<int>(cli_options_.num_banks) * ysel_w; ++index) {
                    oss << " " << bus << "[" << index << "]";
                }
                oss << "\n+";
            }
        }
        
        // Control signals and power
        oss << " VDD VSS\n"
            << "+ stacked_colgrp_x"<< cli_options_.num_wls * 2 
            << "x" << seg_bits << "x" << cli_options_.num_banks << "\n\n";
    }
    }

    return oss.str();
}

// Two-port only: a pair of wordline driver strips (port A's and port B's) on
// each side of the controller band, per bank.  The controller's
// sel_hi_<P>[bank][k] is slice k's SEL (one-hot of the wordline index's high
// bits, gated by the wordline phase) and sel_lo_<P>[j] the B<j> shared by
// every slice (one-hot of its low two bits); the pair's WL_<P>[i] is wordline
// i of that bank in that half.
std::string SpiceIntegrator::generate_wordline_strip_instances() const {
    std::ostringstream oss;
    if (cli_options_.single_port) return oss.str();
    const uint64_t rows = 2 * cli_options_.num_wls;
    const uint64_t slices = rows / 4;
    OpenFinRAM::SpiceGenerator generator(cli_options_);
    // With divided wordlines the band's strips drive the segment next to the
    // band (the lower stack's last, the upper's first), and a mid pair under
    // each other segment of a stack drives it (up) and the one below (down).
    const unsigned segments = cli_options_.wordline_segments();
    auto bus = [segments](char port, const char* half, unsigned seg) {
        return std::string("wl_") + port + "_" + half + (segments > 1 ? "_s" + std::to_string(seg) : "");
    };
    oss << "** Instantiate the wordline driver strips\n";
    for (const char* half : {"lo", "hi"}) {
        const unsigned band_seg = std::string(half) == "lo" ? segments - 1 : 0;
        for (int bank = 0; bank < cli_options_.num_banks; ++bank) {
            oss << "Xwl_" << half << "_" << bank;
            const std::vector<const char*> ports = cli_options_.bitcell_6t
                ? std::vector<const char*>{"A"} : std::vector<const char*>{"A", "B"};
            for (const char* port : ports) {
                for (uint64_t k = 0; k < slices; ++k) {
                    oss << " sel_hi_" << port << "[" << bank * slices + k << "]";
                }
                oss << "\n+";
                for (int j = 0; j < 4; ++j) {
                    oss << " sel_lo_" << port << "[" << j << "]";
                }
                oss << "\n+";
                for (uint64_t i = 0; i < rows; ++i) {
                    oss << " " << bus(static_cast<char>(std::tolower(port[0])), half, band_seg)
                        << "[" << i + bank * rows << "]";
                    if (i % 8 == 7 && i + 1 < rows) oss << "\n+";
                }
                oss << "\n+";
            }
            oss << " VDD VSS " << generator.wordline_strip_pair_name(half) << "\n";
            for (unsigned seg = 1; seg < segments; ++seg) {
                for (const char* side : {"d", "u"}) {
                    oss << "Xwl_" << half << "_m" << seg << side << "_" << bank;
                    for (uint64_t k = 0; k < slices; ++k) oss << " sel_hi_A[" << bank * slices + k << "]";
                    for (int j = 0; j < 4; ++j) oss << " sel_lo_A[" << j << "]";
                    oss << "\n+";
                    const unsigned target = side[0] == 'd' ? seg - 1 : seg;
                    for (uint64_t i = 0; i < rows; ++i) {
                        oss << " " << bus('a', half, target) << "[" << i + bank * rows << "]";
                        if (i % 8 == 7 && i + 1 < rows) oss << "\n+";
                    }
                    oss << "\n+ VDD VSS " << generator.wordline_strip_name() << "\n";
                }
            }
        }
    }
    oss << "\n";
    return oss.str();
}

std::string SpiceIntegrator::generate_footer() const {
    std::ostringstream oss;
    
    // oss << "Vvsswrite vsswrite vss DC 0\n";

    oss << "\n.ENDS sram_x" << cli_options_.num_wls * 2
        << "x" << cli_options_.num_data_bits
        << "x" << cli_options_.num_banks << "\n";
    
    return oss.str();
}

bool SpiceIntegrator::flatten_netlist(const std::string& input_path, const std::string& output_path) const {
    std::ifstream infile(input_path.c_str());
    if (!infile.is_open()) {
        LOGE << "  ✗ Error: Cannot open input netlist for flattening: " << input_path;
        return false;
    }

    std::ofstream outfile(output_path.c_str());
    if (!outfile.is_open()) {
        LOGE << "  ✗ Error: Cannot open output netlist for flattening: " << output_path;
        return false;
    }

    std::string cdl_path = join_path(get_current_dir_name(), "tech/cdl/asap7sc7p5t_28_R.cdl");
    std::ifstream cdl_file(cdl_path.c_str());
    if (!cdl_file.is_open()) {
        LOGE << "  ✗ Error: Cannot open CDL file for flattening: " << cdl_path;
        return false;
    }

    // First write CDL content
    std::string cdl_line;
    while (std::getline(cdl_file, cdl_line)) {
        outfile << cdl_line << "\n";
    }
    outfile << "\n";

    std::string line;
    while (std::getline(infile, line)) {
        // Check for .INCLUDE statements
        if (line.substr(0, 4) == ".inc" || line.substr(0, 4) == ".INC" ||
            line.substr(0, 8) == ".INCLUDE" || line.substr(0, 8) == ".include") {
                // Parse include path between quotes
                std::string include_path = line.substr(line.find_first_of("\"") + 1, line.find_last_of("\"") - line.find_first_of("\"") - 1);
                
                
                // Read all content
                std::ifstream inc_file(include_path.c_str());
                if (!inc_file.is_open()) {
                    LOGE << "  ✗ Error: Cannot open included file: " << include_path;
                    return false;
                }

                std::string inc_line;
                while (std::getline(inc_file, inc_line)) {
                    if (inc_line.substr(0, 8) == ".INCLUDE") {
                        continue; // Skip nested includes
                    }
                    if (inc_line == "*.BUSDELIMITER [ ") {
                        continue; // Skip bus delimiter lines
                    }
                    // if (inc_line.find(".SUBCKT") != std::string::npos && inc_line.find("ctrl_decode") != std::string::npos) {
                    //     LOGD << "Add power ports to ctrl_decode instance";
                    //     outfile << inc_line << " VDD VSS\n";
                    //     continue;
                    // }
                    outfile << inc_line << "\n";
                }
        } else {
            // Write line as is
            outfile << line << "\n";
        }
    }

    return true;
}

bool SpiceIntegrator::replace_chars_for_sis(const std::string& input_path, const std::string& output_path) const {
    std::ifstream infile(input_path.c_str());
    if (!infile.is_open()) {
        LOGE << "  ✗ Error: Cannot open input netlist for character replacement: " << input_path;
        return false;
    }

    std::ofstream outfile(output_path.c_str());
    if (!outfile.is_open()) {
        LOGE << "  ✗ Error: Cannot open output netlist for character replacement: " << output_path;
        return false;
    }

    std::string line;
    while (std::getline(infile, line)) {
        // Replace [ and ] with _ for SIS compatibility and to lowercase
        std::string modified_line = line;
        // std::replace(modified_line.begin(), modified_line.end(), '[', '_');
        // std::replace(modified_line.begin(), modified_line.end(), ']', ' ');
        // std::transform(modified_line.begin(), modified_line.end(), modified_line.begin(), ::tolower);

        // Replace state_A[0] and state_A[1] with state_A_0 and state_A_1
        if (line.find("state_A[0]") != std::string::npos) {
            modified_line = std::regex_replace(modified_line, std::regex("state_A\\[0\\]"), "state_A_0");
        }
        if (line.find("state_A[1]") != std::string::npos) {
            modified_line = std::regex_replace(modified_line, std::regex("state_A\\[1\\]"), "state_A_1");
        }

        outfile << modified_line << "\n";
    }

    return true;
}

std::string single_port_names(const std::string& text) {
    static const std::regex enable(R"((^|\s)(ce|we|oe)_n_A(?=\s|$))");
    static const std::regex bus(R"((^|\s)([ADQ])_A\[)");
    return std::regex_replace(std::regex_replace(text, enable, "$1$2_n"), bus, "$1$2[");
}

bool SpiceIntegrator::integrate_sram() {
    // Mkdir results directory if it doesn't exist
    std::string results_dir = join_path(get_current_dir_name(), "results");
    if (!create_directory(results_dir, nullptr) && !directory_exists(results_dir)) {
        LOGE << "  ✗ Error: Cannot create results directory: " << results_dir;
        return false;
    }

    // output to ./results/{cell_name}_timestamp/{cell_name}.sp
    std::string cell_name = "sram_x" + std::to_string(cli_options_.num_wls * 2) + "x" + std::to_string(cli_options_.num_data_bits) + "x" + std::to_string(cli_options_.num_banks);
    std::string cell_results_dir = join_path(results_dir, cell_name + "_" + get_run_timestamp());
    if (!create_directory(cell_results_dir, nullptr) && !directory_exists(cell_results_dir)) {
        LOGE << "  ✗ Error: Cannot create cell results directory: " << cell_results_dir;
        return false;
    }
    std::string output_file_path = join_path(cell_results_dir, cell_name + ".sp");

    LOGD << "\n" << std::string(70, '=');
    LOGD << "Integrating Control and Datapath";
    LOGD << std::string(70, '=');
    
    // Verify input files exist (support both Innovus and OpenROAD P&R)
    std::string ctrl_netlist_path = join_path(get_current_dir_name(), "tmp/innovus_" + get_run_timestamp() + "/netlist_for_lvs.sp");
    std::string ctrl_netlist_openroad = join_path(get_current_dir_name(), "tmp/openroad_" + get_run_timestamp() + "/netlist_for_lvs.sp");
    // Prefer whichever exists; if use_openroad flag, prefer openroad
    if (cli_options_.use_openroad || cli_options_.openroad_only) {
        if (file_exists(ctrl_netlist_openroad)) ctrl_netlist_path = ctrl_netlist_openroad;
    } else {
        // commercial path: keep innovus, but fallback to openroad if innovus missing (for bring-up)
        if (!file_exists(ctrl_netlist_path) && file_exists(ctrl_netlist_openroad)) {
            ctrl_netlist_path = ctrl_netlist_openroad;
        }
    }
    // final fallback: if still missing, try the alternate location
    if (!file_exists(ctrl_netlist_path)) {
        if (file_exists(ctrl_netlist_openroad)) ctrl_netlist_path = ctrl_netlist_openroad;
        else if (file_exists(join_path(get_current_dir_name(), "tmp/syn_" + get_run_timestamp() + "/netlist.sp"))) {
            // Yosys synthesis netlist can be used directly for LVS bring-up
            ctrl_netlist_path = join_path(get_current_dir_name(), "tmp/syn_" + get_run_timestamp() + "/netlist.sp");
        }
    }
    if (!file_exists(ctrl_netlist_path)) {
        LOGE << "  ✗ Error: Control netlist not found: " 
             << ctrl_netlist_path << " (also checked " << ctrl_netlist_openroad << ")";
        return false;
    }
    
    std::string datapath_netlist_path = join_path(get_current_dir_name(), "tmp/sram_colgrp_" + get_run_timestamp() + ".sp");
    if (!file_exists(datapath_netlist_path)) {
        LOGE << "  ✗ Error: Datapath netlist not found: " 
             << datapath_netlist_path;
        return false;
    }
    
    LOGD << "  ▶ Merging netlists...";
    
    // Parse control circuit ports
    LOGD << "  ▶ Parsing control circuit ports...";
    std::vector<std::string> ctrl_ports = parse_ctrl_ports(ctrl_netlist_path);
    // ctrl_ports.push_back("VDD");
    // ctrl_ports.push_back("VSS");
    
    if (ctrl_ports.empty()) {
        LOGE << "  ✗ Error: Could not parse control ports";
        return false;
    }

    // Generate SPICE netlist
    LOGD << "\n  ▶ Generating integrated SPICE netlist...";
    
    std::ofstream outfile(output_file_path.c_str());
    if (!outfile.is_open()) {
        LOGE << "  ✗ Error: Cannot open output file " << output_file_path;
        return false;
    }
    
    // Write sections
    outfile << generate_header(ctrl_netlist_path, datapath_netlist_path);
    std::string top = generate_subckt_header() + generate_ctrl_instance(ctrl_ports) +
                      generate_datapath_instance() + generate_wordline_strip_instances() +
                      generate_footer();
    // The generated single-port macro is port A of the two-port machinery
    // inside; at its boundary it has no port: ce_n, we_n, oe_n, A[], D[], Q[].
    if (cli_options_.bitcell_6t) top = single_port_names(top);
    outfile << top;
    
    outfile.close();
    
    // Verify output
    if (!file_exists(output_file_path)) {
        LOGE << "  ✗ Error: Output file not created";
        return false;
    }
    
    LOGD << "✓ Integration completed → " << output_file_path;
    
    // Flatten the netlist to resolve includes and subcircuit definitions
    LOGD << "\n  ▶ Flattening netlist for LVS compatibility...";
    std::string flattened_output_path = join_path(get_current_dir_name(), "tmp/sram_flat_" + get_run_timestamp() + ".sp");
    if (!flatten_netlist(output_file_path, flattened_output_path)) {
        LOGE << "  ✗ Error: Netlist flattening failed";
        return false;
    }

    // Replace [] with _ for SIS compatibility
    std::string sis_ready_output_path = join_path(get_current_dir_name(), "tmp/sram_flat_sis_" + get_run_timestamp() + ".sp");
    if (!replace_chars_for_sis(flattened_output_path, sis_ready_output_path)) {
        LOGE << "  ✗ Error: Character replacement for SIS failed";
        return false;
    }

    // Deliver a self-contained structural deck, not references into tmp/.
    // Device model cards remain supplied by the simulation testbench.
    if (!cli_options_.single_port && (cli_options_.use_openroad || cli_options_.openroad_only)) {
        if (!copy_file(flattened_output_path, output_file_path)) return false;
    }
    
    return true;
}
