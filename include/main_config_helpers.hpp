#ifndef MAIN_CONFIG_HELPERS_HPP
#define MAIN_CONFIG_HELPERS_HPP

#include <cstdint>
#include <map>
#include <string>

struct MainCliOptions {
    unsigned num_wls = 2;
    unsigned num_data_bits = 2;
    unsigned num_banks = 1;
    bool single_port = false;
    // The generated single-port macro (--bitcell 6t): the released 6T array
    // with chipforge_asap7's staggered IO, built by the two-port machinery
    // with port A alone.  `single_port` stays the legacy srambank flow.
    bool bitcell_6t = false;
    // Divided wordlines (--segment-bits): each stack of data bits cut into
    // segments of this many bits, a mid strip pair between two.  0: whole
    // stacks.
    unsigned segment_bits = 0;
    // The bits of one wordline segment, and the segments in a stack.
    unsigned wordline_segment_bits() const {
        return segment_bits ? segment_bits : num_data_bits / 2;
    }
    unsigned wordline_segments() const {
        return wordline_segment_bits() ? (num_data_bits / 2) / wordline_segment_bits() : 1;
    }
    // Two-port only: banks in pairs share port B's IO block, the pair's
    // arrays mirrored about it (--share-port-b).
    bool share_port_b = false;
    // Two-port only: the wordline driver strips go into the controller's band
    // as placement keep-outs, the band abutting both stacks of column tiles
    // (--strips-in-controller).
    bool strips_in_controller = false;
    // Address bits per predecode group of the controller's slice decode
    // (sel_hi): 2, 3 or 4 (--row-predecode-bits).
    unsigned row_predecode_bits = 3;
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
    // Export against an existing LEF without regenerating the macro.
    std::string liberty_only_lef;
    std::string liberty_output;
    
    // Custom Push-Rule Bitcell configuration
    double bitcell_width = 0.108;
    double bitcell_height = 0.27;
    unsigned num_rows_per_mux = 4;
};

MainCliOptions parseMainCliOptions(int argc, char** argv);

// The generated single-port (--bitcell 6t) controller's outputs fan out
// across the whole array: sel_lo to every driver slice of every strip, the
// IO controls to every IO block.  Their loads in pF, counted in gate fins
// plus a wire allowance per load, by output port prefix (sel_lo_A, sae_A, ...).
std::map<std::string, double> six_t_port_loads_pf(const MainCliOptions& options);

#endif // MAIN_CONFIG_HELPERS_HPP
