#include "spice_templates.hpp"

#include <array>
#include <iostream>
#include <string>

int main() {
    using OpenFinRAM::SpiceTemplates;
    const std::array<std::string, 12> subcircuits = {
        SpiceTemplates::get_cell_8t(),
        SpiceTemplates::get_prech_v1(),
        SpiceTemplates::get_prech_v2(),
        SpiceTemplates::get_prech_ymux(),
        SpiceTemplates::get_write_driver(),
        SpiceTemplates::get_sense_amp(),
        SpiceTemplates::get_io_nand(),
        SpiceTemplates::get_tbuf(),
        SpiceTemplates::get_iocolgrp(),
        SpiceTemplates::get_ioprech_8t_a(),
        SpiceTemplates::get_ioprech_8t_b(),
        SpiceTemplates::get_iocolgrp_8t(),
    };

    for (const auto& subcircuit : subcircuits) {
        std::cout << "\n* ------------------------------------------------------------\n"
                  << subcircuit << '\n';
    }
    return 0;
}
