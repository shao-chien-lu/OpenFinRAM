#ifndef SPICE_TEMPLATE_HPP
#define SPICE_TEMPLATE_HPP

#include <string>

namespace OpenFinRAM {

class SpiceTemplates {
public:
    static std::string get_cell_6t();
    static std::string get_cell_8t();
    static std::string get_dummy_cell();
    static std::string get_dummy_cell_8t();
    static std::string get_dummy_topbot_v1();
    static std::string get_dummy_topbot_v2();
    static std::string get_prech_v1();
    static std::string get_prech_v2();
    static std::string get_prech_ymux();
    static std::string get_write_driver();
    static std::string get_sense_amp();
    static std::string get_or2();
    static std::string get_io_nand();
    static std::string get_tbuf();
    static std::string get_iocolgrp();
    static std::string get_ioprech_8t_a();
    static std::string get_ioprech_8t_b();
    static std::string get_iocolgrp_8t();
};
} // namespace OpenFinRAM

#endif // SPICE_TEMPLATE_HPP
