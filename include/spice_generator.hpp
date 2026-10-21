#ifndef SPICE_GENERATOR_HPP
#define SPICE_GENERATOR_HPP

#include <string>
#include <vector>
#include <fstream>

#include "main_config_helpers.hpp"

namespace OpenFinRAM {
class SpiceGenerator {
public:
    explicit SpiceGenerator(const MainCliOptions& config);

    bool generate();
    std::string generate_spice_content();
    // The driver-slice load class (cells along the wordline) a two-port
    // macro's wordline strips are built from; throws past the ladder.
    static int wordline_slice_class(int cells);
    // The strip pair subcircuit on one side (`lo` or `hi`) of the controller band.
    std::string wordline_strip_pair_name(const std::string& half) const;
    // One strip: what a pair of either kind is made of.
    std::string wordline_strip_name() const;

private:
    MainCliOptions config_;

    std::string format_ports(const std::vector<std::string>& ports, int max_per_line = 10);
    std::string create_subckt(const std::string& name,
                              const std::vector<std::string>& ports,
                              const std::string& instances);

    std::string generate_cell_row();
    std::string generate_sramcol();
    std::string generate_array();
    std::string generate_colgrp();
    std::string generate_stacked_colgrp();
    std::string generate_stacked_colgrp_mux();

    std::string load_tech_netlist(const std::string& relative, const std::string& generator);
    std::string load_io_column_netlist();
    std::string load_wl_slice_netlist();
    std::string generate_wl_strips_8t();
    int wordline_cells_per_half() const;
    std::string generate_cell_row_8t();
    std::string generate_array_8t();
    std::string generate_colgrp_8t();
    std::string generate_colgrp_half_8t();
    std::string generate_colgrp_pair_8t();
    std::string generate_stacked_colgrp_8t();
    // The generated single-port 6T macro (--bitcell 6t).
    std::string generate_cell_row_6t();
    std::string generate_array_6t();
    std::string generate_end_row_6t();
    std::string generate_colgrp_6t();
    std::string generate_stacked_colgrp_6t();

    std::string generate_spice_content(bool single_port);
};

} // namespace OpenFinRAM

#endif // SPICE_GENERATOR_HPP
