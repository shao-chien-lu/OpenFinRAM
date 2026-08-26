#ifndef MAIN_CONFIG_HELPERS_HPP
#define MAIN_CONFIG_HELPERS_HPP

#include <cstdint>
#include <string>

struct MainCliOptions {
    unsigned num_wls = 2;
    unsigned num_data_bits = 2;
    unsigned num_banks = 1;
    bool single_port = false;
    bool skip_characterization = true;
    std::string output_sp_name = "sram.sp";
    std::string output_gds_name = "sram.gds";
    bool spice_only = false;
    unsigned num_wl_buf = 5;
    unsigned num_sae_buf = 10;
    // Open-source flow options (Yosys / OpenROAD / OpenSTA)
    bool use_yosys = false;       // use Yosys instead of Design Compiler
    bool use_openroad = false;    // use OpenROAD instead of Innovus
    bool openroad_only = false;   // alias: enable both use_yosys + use_openroad for single-port ASAP7
    std::string openroad_path = ""; // OpenROAD binary; default resolves 'openroad' from $PATH
    std::string platform_path = ""; // path to ASAP7 platform (default: <openroad>/platform/asap7)
    // Characterization JSON feeding measured values into the Liberty emitter
    // (--liberty-from). Empty keeps the pure estimated-constant model.
    std::string liberty_from_json = "";
    
    // Custom Push-Rule Bitcell configuration
    double bitcell_width = 0.108;
    double bitcell_height = 0.27;
    unsigned num_rows_per_mux = 4;
};

MainCliOptions parseMainCliOptions(int argc, char** argv);

#endif // MAIN_CONFIG_HELPERS_HPP
