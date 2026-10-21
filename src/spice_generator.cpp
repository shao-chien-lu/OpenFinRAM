#include "spice_generator.hpp"

#include <fstream>
#include <sstream>
#include <stdexcept>
#include <iomanip>
#include <cctype>

#include "plog/Log.h"

#include "spice_templates.hpp"
#include "utils.hpp"

namespace {
void append_ports(std::vector<std::string>& ports, std::initializer_list<const char*> names) {
    ports.reserve(ports.size() + names.size());
    for (const char* name : names) {
        ports.emplace_back(name);
    }
}

void append_ports(std::vector<std::string>& ports, const std::vector<std::string>& names) {
    ports.insert(ports.end(), names.begin(), names.end());
}

void append_indexed_ports(std::vector<std::string>& ports,
                          const std::string& prefix,
                          int count,
                          const std::string& suffix = "",
                          int offset = 0) {
    ports.reserve(ports.size() + count);
    for (int i = 0; i < count; ++i) {
        ports.emplace_back(prefix + std::to_string(i + offset) + suffix);
    }
}

void append_tokens(std::stringstream& ss, std::initializer_list<const char*> tokens) {
    for (const char* token : tokens) {
        ss << token << ' ';
    }
}

void append_indexed_tokens(std::stringstream& ss,
                           const std::string& prefix,
                           int count,
                           const std::string& suffix = "",
                           int offset = 0) {
    for (int i = 0; i < count; ++i) {
        ss << prefix << (i + offset) << suffix << ' ';
    }
}
} // namespace

namespace OpenFinRAM {
SpiceGenerator::SpiceGenerator(const MainCliOptions& config)
    : config_(config)
{
}

std::string SpiceGenerator::format_ports(const std::vector<std::string>& ports, int max_per_line) {
    if (ports.empty()) return "";
    
    std::stringstream ss;
    ss << ports[0];
    
    for (size_t i = 1; i < ports.size(); ++i) {
        if (i % max_per_line == 0) {
            ss << "\n+ ";
        } else {
            ss << " ";
        }
        ss << ports[i];
    }
    
    return ss.str();
}

std::string SpiceGenerator::create_subckt(const std::string& name,
                                          const std::vector<std::string>& ports,
                                          const std::string& instances) {
    std::stringstream ss;
    ss << ".SUBCKT " << name << " " << format_ports(ports) << "\n";
    ss << instances;
    ss << ".ENDS\n";
    return ss.str();
}

std::string SpiceGenerator::generate_cell_row() {
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WL[", config_.num_wls, "]");
    append_ports(ports, {"BLN", "BL", "VDD", "VSS"});

    std::stringstream instances;
    for (int i = 0; i < config_.num_wls; ++i) {
        instances << "X" << i << " WL[" << i << "] BLN BL VDD VSS sram_cell_6t_122\n";
    }
    instances << "X" << config_.num_wls << " BLN VDD VSS dummy_sram_6t122\n";
    
    return create_subckt("sram_cell_row", ports, instances.str());
}

std::string SpiceGenerator::generate_sramcol() {
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WL[", config_.num_wls, "]");
    append_ports(ports, {"BLN", "BL", "VDD", "VSS"});

    std::stringstream instances;
    instances << "M0 69 VSS BLN VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2\n";
    instances << "M1 70 VDD VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2\n";
    instances << "M2 70 VDD VDD VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1\n";
    instances << "X1 ";
    for (int i = 0; i < config_.num_wls; ++i) {
        instances << "WL[" << i << "] ";
    }
    instances << "BLN BL VDD VSS sram_cell_row\n";
    
    std::string cell_name = "sramcol_x" + std::to_string(config_.num_wls) + "_sram_6t122";
    return create_subckt(cell_name, ports, instances.str());
}

std::string SpiceGenerator::generate_array() {
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WL[", config_.num_wls, "]");
    append_indexed_ports(ports, "BLN[", 4, "]");
    append_indexed_ports(ports, "BL[", 4, "]");
    append_ports(ports, {"VDD", "VSS"});

    std::stringstream instances;
    instances << "X0 BLN[0] VDD VSS dummy_topbot_v1\n";
    instances << "X1 BLN[1] VDD VSS dummy_topbot_v1\n";
    instances << "X2 BLN[2] VDD VSS dummy_topbot_v2\n";
    instances << "X3 BLN[3] VDD VSS dummy_topbot_v2\n";
    
    for (int i = 0; i < 4; ++i) {
        instances << "X" << (4 + i) << " ";
        for (int j = 0; j < config_.num_wls; ++j) {
            instances << "WL[" << j << "] ";
        }
        instances << "BLN[" << i << "] BL[" << i << "] VDD VSS sram_cell_row\n";
    }
    
    return create_subckt("array_sram_6t122", ports, instances.str());
}

std::string SpiceGenerator::generate_colgrp() {
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WLT[", config_.num_wls, "]");
    append_indexed_ports(ports, "WLB[", config_.num_wls, "]");
    append_indexed_ports(ports, "yseltn[", 4, "]");
    append_indexed_ports(ports, "yselt[", 4, "]");
    append_indexed_ports(ports, "yselbn[", 4, "]");
    append_indexed_ports(ports, "yselb[", 4, "]");
    append_ports(ports, {"D", "Q", "wrena", "wrenan", "saprechn", "sae", "oeb_out", "oe_out",
                         "blprechtn", "blprechbn", "VDD", "VSS"});

    std::stringstream instances;
    instances << "X0 ";
    append_indexed_tokens(instances, "WLT[", config_.num_wls, "]");
    instances << "BLTN[0] BLTN[1] BLTN[2] BLTN[3] BLT[0] BLT[1] BLT[2] BLT[3] VDD VSS array_sram_6t122\n";
    instances << "X1 ";
    append_indexed_tokens(instances, "WLB[", config_.num_wls, "]");
    instances << "BLBN[0] BLBN[1] BLBN[2] BLBN[3] BLB[0] BLB[1] BLB[2] BLB[3] VDD VSS array_sram_6t122\n";
    instances << "X2 ";
    append_tokens(instances, {"wrenan", "wrena", "sae", "saprechn", "oeb_out", "oe_out", "D", "Q"});
    instances << "BLTN[0] BLTN[1] BLTN[2]\n";
    instances << "+ BLTN[3] BLT[0] BLT[1] BLT[2] BLT[3] BLBN[0] BLBN[1] BLBN[2] BLBN[3] BLB[0]\n";
    instances << "+ BLB[1] BLB[2] BLB[3] blprechtn blprechbn yseltn[0] yseltn[1] yseltn[2] yseltn[3] yselt[0]\n";
    instances << "+ yselt[1] yselt[2] yselt[3] yselbn[0] yselbn[1] yselbn[2] yselbn[3] yselb[0] yselb[1] yselb[2]\n";
    instances << "+ yselb[3] VDD VSS iocolgrp_sram_6t122_v2\n";
    
    return create_subckt("colgrp_sram_6t122", ports, instances.str());
}

std::string SpiceGenerator::generate_stacked_colgrp() {
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WLT[", config_.num_wls, "]");
    append_indexed_ports(ports, "WLB[", config_.num_wls, "]");
    append_indexed_ports(ports, "yseltn[", 4, "]");
    append_indexed_ports(ports, "yselt[", 4, "]");
    append_indexed_ports(ports, "yselbn[", 4, "]");
    append_indexed_ports(ports, "yselb[", 4, "]");
    append_indexed_ports(ports, "D[", config_.num_data_bits / 2, "]");
    append_indexed_ports(ports, "Q[", config_.num_data_bits / 2, "]");
    append_ports(ports, {"wrena", "wrenan", "saprechn", "sae", "oeb_out", "oe_out",
                         "blprechtn", "blprechbn", "VDD", "VSS"});

    std::stringstream instances;
    for (int bit = 0; bit < config_.num_data_bits / 2; ++bit) {
        instances << "X" << bit << " ";
        append_indexed_tokens(instances, "WLT[", config_.num_wls, "]");
        append_indexed_tokens(instances, "WLB[", config_.num_wls, "]");
        append_indexed_tokens(instances, "yseltn[", 4, "]");
        append_indexed_tokens(instances, "yselt[", 4, "]");
        append_indexed_tokens(instances, "yselbn[", 4, "]");
        append_indexed_tokens(instances, "yselb[", 4, "]");
        instances << "D[" << bit << "] Q[" << bit << "] ";
        instances << "wrena wrenan saprechn sae oeb_out oe_out blprechtn blprechbn VDD VSS ";
        instances << "colgrp_sram_6t122\n";
    }
    
    // Generate bank name matching GDS: stacked_colgrp_x{bits}x{num_data_bits / 2}
    // bits = num_wls (since each colgrp has 2 arrays, each with num_wls)
    std::string bank_name = "stacked_colgrp_x" + std::to_string(config_.num_wls * 2) + "x" + std::to_string(config_.num_data_bits / 2);
    return create_subckt(bank_name, ports, instances.str());
}

std::string SpiceGenerator::generate_stacked_colgrp_mux() {
    std::vector<std::string> ports;
    for (int mux = 0; mux < config_.num_banks; ++mux) {
        append_indexed_ports(ports, "WLT[", config_.num_wls, "]", mux * config_.num_wls);
        append_indexed_ports(ports, "WLB[", config_.num_wls, "]", mux * config_.num_wls);
    }

    append_indexed_ports(ports, "D[", config_.num_data_bits / 2, "]");
    append_indexed_ports(ports, "Q[", config_.num_data_bits / 2, "]");

    const std::vector<std::string> ctrl_port_names = {
        "wrena", "wrenan", "saprechn", "sae", "oeb_out", "oe_out",
        "blprechtn", "blprechbn"
    };
    for (const auto& name : ctrl_port_names) {
        for (int mux = 0; mux < config_.num_banks; ++mux) {
            ports.push_back(name + "[" + std::to_string(mux) + "]");
        }
    }

    for (int mux = 0; mux < config_.num_banks; ++mux) {
        append_indexed_ports(ports, "yseltn[", 4, "]", mux * 4);
        append_indexed_ports(ports, "yselt[", 4, "]", mux * 4);
        append_indexed_ports(ports, "yselbn[", 4, "]", mux * 4);
        append_indexed_ports(ports, "yselb[", 4, "]", mux * 4);
    }
    append_ports(ports, {"VDD", "VSS"});

    std::stringstream instances;
    for (int mux = 0; mux < config_.num_banks; ++mux) {
        for (int bit = 0; bit < config_.num_data_bits / 2; ++bit) {
            instances << "X" << mux << "_" << bit << " ";
            append_indexed_tokens(instances, "WLT[", config_.num_wls, "]", mux * config_.num_wls);
            append_indexed_tokens(instances, "WLB[", config_.num_wls, "]", mux * config_.num_wls);
            append_indexed_tokens(instances, "yseltn[", 4, "]", mux * 4);
            append_indexed_tokens(instances, "yselt[", 4, "]", mux * 4);
            append_indexed_tokens(instances, "yselbn[", 4, "]", mux * 4);
            append_indexed_tokens(instances, "yselb[", 4, "]", mux * 4);
            instances << "D[" << bit << "] Q[" << bit << "] ";
            instances << "wrena[" << mux << "] wrenan[" << mux << "] saprechn[" << mux << "] sae[" << mux << "] oeb_out[" << mux << "] oe_out[" << mux << "] blprechtn[" << mux << "] blprechbn[" << mux << "] VDD VSS ";
            instances << "colgrp_sram_6t122\n";
        }
    }
    
    // Generate bank name matching GDS: stacked_colgrp_x{bits}x{num_data_bits / 2}
    // bits = num_wls (since each colgrp has 2 arrays, each with num_wls)
    std::string bank_name = "stacked_colgrp_x" + std::to_string(config_.num_wls * 2) + "x" + std::to_string(config_.num_data_bits / 2) + "x" + std::to_string(config_.num_banks);
    return create_subckt(bank_name, ports, instances.str());
}

// The 8T column is one unsplit array with port A's IO at one end of the
// bitlines and port B's at the other.  config_.num_wls stays NUM_WL, the rows
// one row-select address field covers; the array has twice that.
std::string SpiceGenerator::load_tech_netlist(const std::string& relative, const std::string& generator) {
    // The same tech root the other tech collateral is found under: the
    // working directory's, else the executable's.
    const std::string names[] = {
        join_path(get_current_dir_name(), relative),
        join_path(get_executable_directory(), relative),
    };
    for (const auto& path : names) {
        if (!file_exists(path)) continue;
        std::ifstream in(path);
        std::stringstream text;
        text << in.rdbuf();
        std::string netlist = text.str();
        // The file ends the way a standalone deck does; the deck it joins ends itself.
        const std::string end = ".END\n";
        if (netlist.size() >= end.size() && netlist.compare(netlist.size() - end.size(), end.size(), end) == 0) {
            netlist.erase(netlist.size() - end.size());
        }
        return netlist;
    }
    throw std::runtime_error(relative + " is missing; regenerate it with " + generator);
}

std::string SpiceGenerator::load_io_column_netlist() {
    return load_tech_netlist("tech/spice/sram_8t_iocolumn.sp", "scripts/generate_asap7_8t_iocolumn.py");
}

std::string SpiceGenerator::load_wl_slice_netlist() {
    return load_tech_netlist("tech/spice/sram_8t_wl_slices.sp", "scripts/generate_asap7_8t_wl_slices.py");
}

// The wordlines are driven at the array by four-wordline slices (WL<i> =
// SEL . B<i>), a strip of them per port: one slice per four wordlines of a
// bank, sized to the cells along the wordline in its half of the data bits
// (the four mux columns of each of that half's bits).  The two ports' strips
// are a pair on each side of the controller band, the `lo` pair facing down
// into the bits below the band and the `hi` pair up into the bits above; the
// assembler draws the pair as one cell, so the deck has it as one subcircuit.
int SpiceGenerator::wordline_cells_per_half() const {
    // A cell per mux row of every data bit along one wordline segment (a
    // whole stack unless --segment-bits divides it).
    return static_cast<int>(config_.num_rows_per_mux * config_.wordline_segment_bits());
}

int SpiceGenerator::wordline_slice_class(int cells) {
    // The ladder scripts/generate_asap7_8t_wl_slices.py writes; the first
    // entry at or above the load.
    for (int entry : {4, 8, 16, 32, 64}) {
        if (cells <= entry) return entry;
    }
    throw std::runtime_error(
        "a " + std::to_string(cells) + "-cell wordline is past the 64-cell driver slice; "
        "fewer data bits per macro");
}

std::string SpiceGenerator::wordline_strip_pair_name(const std::string& half) const {
    const int rows = 2 * config_.num_wls;
    return "wl_strips_" + half + "_c" + std::to_string(wordline_slice_class(wordline_cells_per_half()))
        + "_x" + std::to_string(rows / 4);
}

std::string SpiceGenerator::wordline_strip_name() const {
    const int rows = 2 * config_.num_wls;
    return "wl_strip_c" + std::to_string(wordline_slice_class(wordline_cells_per_half()))
        + "_x" + std::to_string(rows / 4);
}

std::string SpiceGenerator::generate_wl_strips_8t() {
    const int rows = 2 * config_.num_wls;
    if (rows % 4 != 0) {
        throw std::runtime_error("the array's wordlines must be a multiple of four (one driver slice each)");
    }
    const int slices = rows / 4;
    const int cells = wordline_slice_class(wordline_cells_per_half());
    const std::string strip = "wl_strip_c" + std::to_string(cells) + "_x" + std::to_string(slices);
    std::stringstream out;
    out << "* Wordline driver strips: " << slices << " slice(s) of wl_slice_c" << cells
        << " for " << wordline_cells_per_half() << " cells along the wordline\n";
    {
        std::vector<std::string> ports;
        append_indexed_ports(ports, "SEL[", slices, "]");
        append_indexed_ports(ports, "B[", 4, "]");
        append_indexed_ports(ports, "WL[", rows, "]");
        append_ports(ports, {"VDD", "VSS"});
        std::stringstream instances;
        for (int k = 0; k < slices; ++k) {
            instances << "X_slice" << k << " SEL[" << k << "] B[0] B[1] B[2] B[3]";
            for (int j = 0; j < 4; ++j) instances << " WL[" << 4 * k + j << "]";
            instances << " VDD VSS wl_slice_c" << cells << "\n";
        }
        out << create_subckt(strip, ports, instances.str()) << "\n";
    }
    // The single-port macro has port A's strip alone on each side.
    const std::vector<const char*> strip_ports = config_.bitcell_6t
        ? std::vector<const char*>{"A"} : std::vector<const char*>{"A", "B"};
    for (const char* half : {"lo", "hi"}) {
        std::vector<std::string> ports;
        std::stringstream instances;
        for (const char* port : strip_ports) {
            const std::string suffix = std::string("_") + port + "[";
            append_indexed_ports(ports, "SEL" + suffix, slices, "]");
            append_indexed_ports(ports, "B" + suffix, 4, "]");
            append_indexed_ports(ports, "WL" + suffix, rows, "]");
            instances << "X_" << static_cast<char>(std::tolower(port[0])) << " ";
            append_indexed_tokens(instances, "SEL" + suffix, slices, "]");
            append_indexed_tokens(instances, "B" + suffix, 4, "]");
            append_indexed_tokens(instances, "WL" + suffix, rows, "]");
            instances << "VDD VSS " << strip << "\n";
        }
        append_ports(ports, {"VDD", "VSS"});
        out << create_subckt(wordline_strip_pair_name(half), ports, instances.str()) << "\n";
    }
    return out.str();
}

std::string SpiceGenerator::generate_cell_row_8t() {
    const int rows = 2 * config_.num_wls;
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WLA[", rows, "]");
    append_indexed_ports(ports, "WLB[", rows, "]");
    append_ports(ports, {"BLA", "BLAN", "BLB", "BLBN", "VDD", "VSS"});

    std::stringstream instances;
    for (int i = 0; i < rows; ++i) {
        instances << "X" << i << " WLA[" << i << "] WLB[" << i << "] BLA BLAN BLB BLBN VDD VSS sram_cell_8t\n";
    }

    // The generated 8T array terminates in device-free column caps and
    // well/substrate taps. There is no active dummy bitcell in this row.

    return create_subckt("sram_cell_row_8t", ports, instances.str());
}

std::string SpiceGenerator::generate_array_8t() {
    const int rows = 2 * config_.num_wls;
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WLA[", rows, "]");
    append_indexed_ports(ports, "WLB[", rows, "]");
    append_indexed_ports(ports, "BLA[", 4, "]");
    append_indexed_ports(ports, "BLAN[", 4, "]");
    append_indexed_ports(ports, "BLB[", 4, "]");
    append_indexed_ports(ports, "BLBN[", 4, "]");
    append_ports(ports, {"VDD", "VSS"});

    std::stringstream instances;
    for (int i = 0; i < 4; ++i) {
        instances << "X" << i << " ";
        for (int j = 0; j < rows; ++j) {
            instances << "WLA[" << j << "] ";
        }
        for (int j = 0; j < rows; ++j) {
            instances << "WLB[" << j << "] ";
        }
        instances << "BLA[" << i << "] BLAN[" << i << "] BLB[" << i << "] BLBN[" << i << "] VDD VSS sram_cell_row_8t\n";
    }

    return create_subckt("array_sram_8t", ports, instances.str());
}

std::string SpiceGenerator::generate_colgrp_8t() {
    const int rows = 2 * config_.num_wls;
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WLA[", rows, "]");
    append_indexed_ports(ports, "WLB[", rows, "]");
    append_ports(ports, {"DA", "QA", "DB", "QB"});
    append_ports(ports, {
        "wrenaA", "wrenanA", "wrenaB", "wrenanB",
        "oeb_outA", "oe_outA", "oeb_outB", "oe_outB",
        "blprechnA", "blprechnB"
    });
    append_indexed_ports(ports, "yselnA[", 4, "]");
    append_indexed_ports(ports, "yselA[", 4, "]");
    append_indexed_ports(ports, "yselnB[", 4, "]");
    append_indexed_ports(ports, "yselB[", 4, "]");
    append_ports(ports, {"sae_A", "sae_B", "VDD", "VSS"});

    std::stringstream instances;
    instances << "X0 ";
    append_indexed_tokens(instances, "WLA[", rows, "]");
    append_indexed_tokens(instances, "WLB[", rows, "]");
    append_indexed_tokens(instances, "BL_A[", 4, "]");
    append_indexed_tokens(instances, "BLN_A[", 4, "]");
    append_indexed_tokens(instances, "BL_B[", 4, "]");
    append_indexed_tokens(instances, "BLN_B[", 4, "]");
    instances << " VDD VSS array_sram_8t\n";

    // Each port's IO is the parametric column block (chipforge_asap7's
    // IoColumnSpec) wrapped as iocol_sram_8t_{a,b} in tech/spice/
    // sram_8t_iocolumn.sp, in the pin order generate_asap7_8t_iocolumn.py
    // writes: bitlines, complements, selects, complement selects, then
    // precharge, sense enable, write enables, output enables, data in, data
    // out, supplies.
    for (const std::string& port : {"A", "B"}) {
        instances << "XIO_" << port << " ";
        append_indexed_tokens(instances, "BL_" + port + "[", 4, "]");
        append_indexed_tokens(instances, "BLN_" + port + "[", 4, "]");
        append_indexed_tokens(instances, "ysel" + port + "[", 4, "]");
        append_indexed_tokens(instances, "yseln" + port + "[", 4, "]");
        instances << "blprechn" << port << " sae_" << port
                  << " wrena" << port << " wrenan" << port
                  << " oe_out" << port << " oeb_out" << port
                  << " D" << port << " Q" << port
                  << " VDD VSS iocol_sram_8t_" << (port == "A" ? "a" : "b") << "\n";
    }

    return create_subckt("colgrp_sram_8t", ports, instances.str());
}

// With --share-port-b: one bank's column without port B's IO, its port-B
// bitlines out to the block it shares with the other bank of its pair.
std::string SpiceGenerator::generate_colgrp_half_8t() {
    const int rows = 2 * config_.num_wls;
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WLA[", rows, "]");
    append_indexed_ports(ports, "WLB[", rows, "]");
    append_ports(ports, {"DA", "QA", "wrenaA", "wrenanA", "oeb_outA", "oe_outA", "blprechnA"});
    append_indexed_ports(ports, "yselnA[", 4, "]");
    append_indexed_ports(ports, "yselA[", 4, "]");
    append_ports(ports, {"sae_A"});
    append_indexed_ports(ports, "BL_B[", 4, "]");
    append_indexed_ports(ports, "BLN_B[", 4, "]");
    append_ports(ports, {"VDD", "VSS"});

    std::stringstream instances;
    instances << "X0 ";
    append_indexed_tokens(instances, "WLA[", rows, "]");
    append_indexed_tokens(instances, "WLB[", rows, "]");
    append_indexed_tokens(instances, "BL_A[", 4, "]");
    append_indexed_tokens(instances, "BLN_A[", 4, "]");
    append_indexed_tokens(instances, "BL_B[", 4, "]");
    append_indexed_tokens(instances, "BLN_B[", 4, "]");
    instances << " VDD VSS array_sram_8t\n";
    instances << "XIO_A ";
    append_indexed_tokens(instances, "BL_A[", 4, "]");
    append_indexed_tokens(instances, "BLN_A[", 4, "]");
    append_indexed_tokens(instances, "yselA[", 4, "]");
    append_indexed_tokens(instances, "yselnA[", 4, "]");
    instances << "blprechnA sae_A wrenaA wrenanA oe_outA oeb_outA DA QA VDD VSS iocol_sram_8t_a\n";
    return create_subckt("colgrp_half_sram_8t", ports, instances.str());
}

// A pair of banks' columns for one data bit: the two halves mirrored about
// port B's two-sided block (iocol_sram_8t_b2).  The second bank's wordlines
// and selects continue the first's indices; its one-per-bank controls end in
// R.  Port B's enables are the pair's, its precharges and selects per bank.
std::string SpiceGenerator::generate_colgrp_pair_8t() {
    const int rows = 2 * config_.num_wls;
    const std::vector<std::string> port_a = {"wrenaA", "wrenanA", "oeb_outA", "oe_outA", "blprechnA", "sae_A"};
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WLA[", 2 * rows, "]");
    append_indexed_ports(ports, "WLB[", 2 * rows, "]");
    append_ports(ports, {"DA", "QA", "DB", "QB"});
    append_ports(ports, port_a);
    for (const auto& name : port_a) ports.push_back(name + "R");
    append_ports(ports, {"wrenaB", "wrenanB", "oeb_outB", "oe_outB", "sae_B", "blprechnB", "blprechnBR"});
    append_indexed_ports(ports, "yselnA[", 8, "]");
    append_indexed_ports(ports, "yselA[", 8, "]");
    append_indexed_ports(ports, "yselnB[", 8, "]");
    append_indexed_ports(ports, "yselB[", 8, "]");
    append_ports(ports, {"VDD", "VSS"});

    std::stringstream instances;
    for (int bank = 0; bank < 2; ++bank) {
        const std::string r = bank ? "R" : "";
        instances << "XH" << bank << " ";
        append_indexed_tokens(instances, "WLA[", rows, "]", bank * rows);
        append_indexed_tokens(instances, "WLB[", rows, "]", bank * rows);
        instances << "DA QA ";
        for (const char* name : {"wrenaA", "wrenanA", "oeb_outA", "oe_outA", "blprechnA"}) {
            instances << name << r << ' ';
        }
        append_indexed_tokens(instances, "yselnA[", 4, "]", bank * 4);
        append_indexed_tokens(instances, "yselA[", 4, "]", bank * 4);
        instances << "sae_A" << r << ' ';
        append_indexed_tokens(instances, "BL_B[", 4, "]", bank * 4);
        append_indexed_tokens(instances, "BLN_B[", 4, "]", bank * 4);
        instances << "VDD VSS colgrp_half_sram_8t\n";
    }
    instances << "XIO_B ";
    append_indexed_tokens(instances, "BL_B[", 8, "]");
    append_indexed_tokens(instances, "BLN_B[", 8, "]");
    append_indexed_tokens(instances, "yselB[", 8, "]");
    append_indexed_tokens(instances, "yselnB[", 8, "]");
    instances << "blprechnB blprechnBR sae_B wrenaB wrenanB oe_outB oeb_outB DB QB VDD VSS iocol_sram_8t_b2\n";
    return create_subckt("colgrp_pair_sram_8t", ports, instances.str());
}

std::string SpiceGenerator::generate_stacked_colgrp_8t() {
    const int rows = 2 * config_.num_wls;
    const bool shared = config_.share_port_b;
    std::vector<std::string> ports;
    for (int mux = 0; mux < config_.num_banks; ++mux) {
        append_indexed_ports(ports, "WLA[", rows, "]", mux * rows);
        append_indexed_ports(ports, "WLB[", rows, "]", mux * rows);
    }

    append_indexed_ports(ports, "DA[", config_.num_data_bits / 2, "]");
    append_indexed_ports(ports, "QA[", config_.num_data_bits / 2, "]");
    append_indexed_ports(ports, "DB[", config_.num_data_bits / 2, "]");
    append_indexed_ports(ports, "QB[", config_.num_data_bits / 2, "]");

    const std::vector<std::string> ctrl_port_names = {
        "wrenaA", "wrenanA", "wrenaB", "wrenanB",
        "oeb_outA", "oe_outA", "oeb_outB", "oe_outB",
        "blprechnA", "blprechnB",
        "sae_A", "sae_B"
    };
    // Shared, port B's enables are one per pair of banks (the controller's
    // SHARED_B), its precharges still one per bank.
    auto per_pair = [shared](const std::string& name) {
        return shared && (name == "wrenaB" || name == "wrenanB" || name == "oeb_outB" ||
                          name == "oe_outB" || name == "sae_B");
    };
    for (const auto& name : ctrl_port_names) {
        const int count = per_pair(name) ? config_.num_banks / 2 : config_.num_banks;
        for (int mux = 0; mux < count; ++mux) {
            ports.push_back(name + "[" + std::to_string(mux) + "]");
        }
    }

    append_indexed_ports(ports, "yselnA[", 4 * config_.num_banks, "]");
    append_indexed_ports(ports, "yselA[", 4 * config_.num_banks, "]");
    append_indexed_ports(ports, "yselnB[", 4 * config_.num_banks, "]");
    append_indexed_ports(ports, "yselB[", 4 * config_.num_banks, "]");
    append_ports(ports, {"VDD", "VSS"});

    std::stringstream instances;
    // X<pair>_<bit>: both banks of the pair, as colgrp_pair_sram_8t orders them.
    for (int pair = 0; shared && pair < config_.num_banks / 2; ++pair) {
        for (int bit = 0; bit < config_.num_data_bits / 2; ++bit) {
            const std::string p = std::to_string(pair);
            instances << "X" << pair << "_" << bit << " ";
            append_indexed_tokens(instances, "WLA[", 2 * rows, "]", 2 * pair * rows);
            append_indexed_tokens(instances, "WLB[", 2 * rows, "]", 2 * pair * rows);
            instances << "DA[" << bit << "] QA[" << bit << "] DB[" << bit << "] QB[" << bit << "] ";
            for (int bank = 2 * pair; bank < 2 * pair + 2; ++bank) {
                const std::string b = std::to_string(bank);
                instances << "wrenaA[" << b << "] wrenanA[" << b << "] oeb_outA[" << b << "] oe_outA[" << b
                          << "] blprechnA[" << b << "] sae_A[" << b << "] ";
            }
            instances << "wrenaB[" << p << "] wrenanB[" << p << "] oeb_outB[" << p << "] oe_outB[" << p
                      << "] sae_B[" << p << "] blprechnB[" << 2 * pair << "] blprechnB[" << 2 * pair + 1 << "] ";
            append_indexed_tokens(instances, "yselnA[", 8, "]", 8 * pair);
            append_indexed_tokens(instances, "yselA[", 8, "]", 8 * pair);
            append_indexed_tokens(instances, "yselnB[", 8, "]", 8 * pair);
            append_indexed_tokens(instances, "yselB[", 8, "]", 8 * pair);
            instances << "VDD VSS colgrp_pair_sram_8t\n";
        }
    }
    for (int mux = 0; !shared && mux < config_.num_banks; ++mux) {
        for (int bit = 0; bit < config_.num_data_bits / 2; ++bit) {
            instances << "X" << mux << "_" << bit << " ";
            append_indexed_tokens(instances, "WLA[", rows, "]", mux * rows);
            append_indexed_tokens(instances, "WLB[", rows, "]", mux * rows);

            instances << "DA[" << bit << "] QA[" << bit << "] "
                      << "DB[" << bit << "] QB[" << bit << "] ";
            instances << "wrenaA[" << mux << "] wrenanA[" << mux
                      << "] wrenaB[" << mux << "] wrenanB[" << mux
                      << "] oeb_outA[" << mux << "] oe_outA[" << mux << "] oeb_outB[" << mux << "] oe_outB[" << mux
                      << "] blprechnA[" << mux << "] blprechnB[" << mux
                      << "] ";

            append_indexed_tokens(instances, "yselnA[", 4, "]", mux * 4);
            append_indexed_tokens(instances, "yselA[", 4, "]", mux * 4);
            append_indexed_tokens(instances, "yselnB[", 4, "]", mux * 4);
            append_indexed_tokens(instances, "yselB[", 4, "]", mux * 4);

            instances << "sae_A[" << mux << "] sae_B[" << mux << "] ";
            instances << " VDD VSS colgrp_sram_8t\n";
        }
    }

    // Bank name: stacked_colgrp_x{wordlines of the array}x{num_data_bits / 2}x{num_banks}
    std::string bank_name = "stacked_colgrp_x" + std::to_string(rows) + "x" + std::to_string(config_.num_data_bits / 2) + "x" + std::to_string(config_.num_banks);
    return create_subckt(bank_name, ports, instances.str());
}

// --bitcell 6t: the released 6T row, its dummy and cap, as
// scripts/generate_asap7_6t_iocolumn.py lays a column tile out:
//   edge filler | cap | 2*NUM_WL bitcells | dummy | tap | IO block
// The filler and the tap have no devices.  The dummy and the cap each hang a
// pass device (gate tied off) on the row's BLN.
std::string SpiceGenerator::generate_cell_row_6t() {
    const int rows = 2 * config_.num_wls;
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WL[", rows, "]");
    append_ports(ports, {"BL", "BLN", "VDD", "VSS"});
    std::stringstream instances;
    for (int i = 0; i < rows; ++i) {
        instances << "X" << i << " WL[" << i << "] BLN BL VDD VSS sram_cell_6t_122\n";
    }
    instances << "XD BLN VDD VSS dummy_sram_6t122\n";
    return create_subckt("sram_cell_row_6t", ports, instances.str());
}

std::string SpiceGenerator::generate_array_6t() {
    const int rows = 2 * config_.num_wls;
    const int mux = config_.num_rows_per_mux;
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WL[", rows, "]");
    append_indexed_ports(ports, "BL[", mux, "]");
    append_indexed_ports(ports, "BLN[", mux, "]");
    append_ports(ports, {"VDD", "VSS"});
    std::stringstream instances;
    for (int r = 0; r < mux; ++r) {
        instances << "X" << r << " ";
        append_indexed_tokens(instances, "WL[", rows, "]");
        instances << " BL[" << r << "] BLN[" << r << "] VDD VSS sram_cell_row_6t\n";
        // Even rows take the v1 cap, odd (mirrored) rows the v2.
        instances << "XC" << r << " BLN[" << r << "] VDD VSS dummy_topbot_v" << 1 + r % 2 << "\n";
    }
    return create_subckt("array_sram_6t", ports, instances.str());
}

// The dummy row at each end of a stack of tiles: the released
// dummy_vertical_6t122 over each bitcell column (two fins gated by the
// column's wordline, both ends on VSS) and corners over the cap, the dummy
// and the tap.  Every other device of the row has all of its terminals on
// VSS (the p devices' bodies on VDD): 6 n fins a column and 12 for the
// corners, 2 p fins a column and 3 for the corners, as extracted.
std::string SpiceGenerator::generate_end_row_6t() {
    const int rows = 2 * config_.num_wls;
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WL[", rows, "]");
    append_ports(ports, {"VDD", "VSS"});
    std::stringstream instances;
    for (int i = 0; i < rows; ++i) {
        instances << "MW" << i << " VSS WL[" << i << "] VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2\n";
    }
    instances << "MN VSS VSS VSS VSS nmos_rvt L=2e-08 W=" << 27 * (6 * rows + 12) << "e-09 nfin="
              << 6 * rows + 12 << "\n";
    instances << "MP VSS VSS VSS VDD pmos_rvt L=2e-08 W=" << 27 * (2 * rows + 3) << "e-09 nfin="
              << 2 * rows + 3 << "\n";
    return create_subckt("end_row_6t", ports, instances.str());
}

std::string SpiceGenerator::generate_colgrp_6t() {
    const int rows = 2 * config_.num_wls;
    const int mux = config_.num_rows_per_mux;
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WLA[", rows, "]");
    append_ports(ports, {"DA", "QA", "wrenaA", "wrenanA", "oeb_outA", "oe_outA", "blprechnA"});
    // A bank's select bus: a line a column, or from 16:1 the two predecoded
    // groups (the controller's YSEL_W).
    const int ysel_w = mux >= 16 ? 4 + mux / 4 : mux;
    append_indexed_ports(ports, "yselnA[", ysel_w, "]");
    append_indexed_ports(ports, "yselA[", ysel_w, "]");
    append_ports(ports, {"sae_A", "VDD", "VSS"});
    std::stringstream instances;
    instances << "X0 ";
    append_indexed_tokens(instances, "WLA[", rows, "]");
    append_indexed_tokens(instances, "BL_A[", mux, "]");
    append_indexed_tokens(instances, "BLN_A[", mux, "]");
    instances << " VDD VSS array_sram_6t\n";
    // The staggered IO block (chipforge_asap7's StaggeredIoColumnSpec),
    // wrapped as iocol_sram_6t in tech/spice/sram_6t_iocolumn.sp, in the pin
    // order generate_asap7_6t_iocolumn.py writes: per leaf bitline,
    // complement, select, complement select; then precharge, sense enable,
    // data in, write enables, output enables, data out, supplies.  From 8:1
    // each leaf makes its own select from the complement (local_ysel): the
    // block has no select pins, and yselA stays a port of the tile only.
    // From 16:1 the leaves decode it from two predecoded groups the block
    // takes once, after the leaves' bitlines: yselA[0..3] and yselA[4..].
    const bool local_ysel = mux >= 8;
    const bool predecode = mux >= 16;
    instances << "XIO_A";
    for (int r = 0; r < mux; ++r) {
        instances << " BL_A[" << r << "] BLN_A[" << r << "]";
        if (predecode) continue;
        if (!local_ysel) instances << " yselA[" << r << "]";
        instances << " yselnA[" << r << "]";
    }
    if (predecode) {
        instances << " ";
        append_indexed_tokens(instances, "yselA[", 4 + mux / 4, "]");
    }
    // iocol_sram_6t at 4:1, iocol_sram_6t_x<mux> otherwise.
    instances << " blprechnA sae_A DA wrenaA wrenanA oe_outA oeb_outA QA VDD VSS iocol_sram_6t"
              << (mux == 4 ? std::string() : "_x" + std::to_string(mux)) << "\n";
    return create_subckt("colgrp_sram_6t", ports, instances.str());
}

// One wordline segment's tiles (a whole stack unless --segment-bits divides
// it): its own wordlines, a dummy row at each end.
std::string SpiceGenerator::generate_stacked_colgrp_6t() {
    const int rows = 2 * config_.num_wls;
    const int bits = config_.wordline_segment_bits();
    const int mux = config_.num_rows_per_mux;
    std::vector<std::string> ports;
    append_indexed_ports(ports, "WLA[", rows * config_.num_banks, "]");
    append_indexed_ports(ports, "DA[", bits, "]");
    append_indexed_ports(ports, "QA[", bits, "]");
    for (const char* name : {"wrenaA", "wrenanA", "oeb_outA", "oe_outA", "blprechnA", "sae_A"}) {
        append_indexed_ports(ports, std::string(name) + "[", config_.num_banks, "]");
    }
    const int ysel_w = mux >= 16 ? 4 + mux / 4 : mux;  // a bank's select bus (generate_colgrp_6t)
    append_indexed_ports(ports, "yselnA[", ysel_w * config_.num_banks, "]");
    append_indexed_ports(ports, "yselA[", ysel_w * config_.num_banks, "]");
    append_ports(ports, {"VDD", "VSS"});

    std::stringstream instances;
    for (int bank = 0; bank < static_cast<int>(config_.num_banks); ++bank) {
        const std::string b = std::to_string(bank);
        for (int bit = 0; bit < bits; ++bit) {
            instances << "X" << bank << "_" << bit << " ";
            append_indexed_tokens(instances, "WLA[", rows, "]", bank * rows);
            instances << " DA[" << bit << "] QA[" << bit << "] wrenaA[" << b << "] wrenanA[" << b
                      << "] oeb_outA[" << b << "] oe_outA[" << b << "] blprechnA[" << b << "] ";
            append_indexed_tokens(instances, "yselnA[", ysel_w, "]", bank * ysel_w);
            append_indexed_tokens(instances, "yselA[", ysel_w, "]", bank * ysel_w);
            instances << " sae_A[" << b << "] VDD VSS colgrp_sram_6t\n";
        }
        // The stack's two ends each carry a dummy row on its wordlines.
        for (const char* end : {"B", "T"}) {
            instances << "XE" << bank << "_" << end << " ";
            append_indexed_tokens(instances, "WLA[", rows, "]", bank * rows);
            instances << " VDD VSS end_row_6t\n";
        }
    }
    const std::string bank_name = "stacked_colgrp_x" + std::to_string(rows) + "x" +
        std::to_string(bits) + "x" + std::to_string(config_.num_banks);
    return create_subckt(bank_name, ports, instances.str());
}

std::string SpiceGenerator::generate_spice_content(bool single_port) {
    std::stringstream content;
    std::string sep = std::string(70, '*') + "\n";
    
    if (single_port){
        LOGD << "Generating single-port SRAM SPICE netlist...";

        // Add all basic cell templates
        content << sep << SpiceTemplates::get_cell_6t() << "\n\n";
        content << sep << SpiceTemplates::get_dummy_cell() << "\n\n";
        content << sep << SpiceTemplates::get_dummy_topbot_v1() << "\n\n";
        content << sep << SpiceTemplates::get_dummy_topbot_v2() << "\n\n";
        content << sep << SpiceTemplates::get_prech_v1() << "\n\n";
        content << sep << SpiceTemplates::get_prech_v2() << "\n\n";
        content << sep << SpiceTemplates::get_prech_ymux() << "\n\n";
        content << sep << SpiceTemplates::get_io_nand() << "\n\n";
        content << sep << SpiceTemplates::get_tbuf() << "\n\n";
        content << sep << SpiceTemplates::get_write_driver() << "\n\n";
        content << sep << SpiceTemplates::get_sense_amp() << "\n\n";
        content << sep << SpiceTemplates::get_iocolgrp() << "\n\n";
        
        // Generate hierarchy
        content << sep << generate_cell_row() << "\n";
        content << sep << generate_sramcol() << "\n";
        content << sep << generate_array() << "\n";
        content << sep << generate_colgrp() << "\n";
        // content << sep << generate_stacked_colgrp() << "\n";
        content << sep << generate_stacked_colgrp_mux() << "\n";
    } else if (config_.bitcell_6t) {
        LOGD << "Generating single-port 6T SRAM SPICE netlist (generated flow)...";

        content << sep << SpiceTemplates::get_cell_6t() << "\n\n";
        content << sep << SpiceTemplates::get_dummy_cell() << "\n\n";
        content << sep << SpiceTemplates::get_dummy_topbot_v1() << "\n\n";
        content << sep << SpiceTemplates::get_dummy_topbot_v2() << "\n\n";
        // The IO block, written beside its GDS by
        // scripts/generate_asap7_6t_iocolumn.py; the wordline slices as the
        // two-port macro's.
        content << sep << load_tech_netlist("tech/spice/sram_6t_iocolumn.sp",
                                            "scripts/generate_asap7_6t_iocolumn.py") << "\n\n";
        content << sep << load_wl_slice_netlist() << "\n\n";
        content << sep << generate_wl_strips_8t() << "\n";

        content << sep << generate_cell_row_6t() << "\n";
        content << sep << generate_array_6t() << "\n";
        content << sep << generate_end_row_6t() << "\n";
        content << sep << generate_colgrp_6t() << "\n";
        content << sep << generate_stacked_colgrp_6t() << "\n";
    } else {
        LOGD << "Generating dual-port SRAM SPICE netlist...";

        content << sep << SpiceTemplates::get_cell_8t() << "\n\n";
        content << sep << SpiceTemplates::get_dummy_cell_8t() << "\n\n";

        // The IO columns are the parametric blocks, written beside their GDS
        // by scripts/generate_asap7_8t_iocolumn.py.
        content << sep << load_io_column_netlist() << "\n\n";
        // So are the wordline driver slices; the strips are one per port and
        // side of the controller band (scripts/generate_asap7_8t_wl_slices.py).
        content << sep << load_wl_slice_netlist() << "\n\n";
        content << sep << generate_wl_strips_8t() << "\n";

        content << sep << generate_cell_row_8t() << "\n";
        content << sep << generate_array_8t() << "\n";
        if (config_.share_port_b) {
            content << sep << generate_colgrp_half_8t() << "\n";
            content << sep << generate_colgrp_pair_8t() << "\n";
        } else {
            content << sep << generate_colgrp_8t() << "\n";
        }
        content << sep << generate_stacked_colgrp_8t() << "\n";
    }
    
    return content.str();
}

std::string SpiceGenerator::generate_spice_content() {
    return generate_spice_content(config_.single_port);
}

bool SpiceGenerator::generate() {
    // Ensure tmp exists at both executable dir and CWD (supports running from repo root with /tmp/build binary)
    std::string tmp_exec = join_path(get_executable_directory(), "tmp");
    std::string tmp_cwd = join_path(get_current_dir_name(), "tmp");
    if (!directory_exists(tmp_exec)) {
        LOGD << "Creating tmp directory: " << tmp_exec;
        create_directory(tmp_exec, nullptr);
    }
    if (!directory_exists(tmp_cwd)) {
        LOGD << "Creating tmp directory: " << tmp_cwd;
        create_directory(tmp_cwd, nullptr);
        // Also create via executable dir as fallback if CWD tmp creation failed due to permissions
        if (!directory_exists(tmp_cwd) && !directory_exists(tmp_exec)) {
            create_directory(tmp_exec, nullptr);
        }
    }

    std::string output_path = join_path(get_current_dir_name(), "tmp/sram_colgrp_" + get_run_timestamp() + ".sp");
    std::string content = generate_spice_content();
    
    std::ofstream outfile(output_path);
    if (!outfile.is_open()) {
        LOGE << "Failed to open output file: " << output_path;
        return false;
    }
    
    outfile << content;
    outfile.close();
    
    LOGI << "✓ Generated SPICE netlist: " << output_path;
    LOGI << "  Number of wordlines: " << config_.num_wls;
    LOGI << "  Number of data bits: " << config_.num_data_bits / 2;
    LOGI << "  Number of banks: " << config_.num_banks;
        
    return true;
}

} // namespace OpenFinRAM
