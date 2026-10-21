#include "main_config_helpers.hpp"

#include "argparse/argparse.hpp"
#include "plog/Log.h"

MainCliOptions parseMainCliOptions(int argc, char** argv) {
    argparse::ArgumentParser program("OpenFinRAM");

    program.add_argument("--num-wls")
        .help("Number of wordlines. Must be an even number.")
        .default_value(unsigned{2})
        .scan<'u', unsigned>();

    program.add_argument("--num-data-bits")
        .help("Number of data bits (D/Q port width). Must be an even number.")
        .default_value(unsigned{2})
        .scan<'u', unsigned>();

    program.add_argument("--num-banks")
        .help("Number of banks. Must be a power of 2.")
        .default_value(unsigned{1})
        .scan<'u', unsigned>();

    program.add_argument("--single-port")
        .help("Generate single-port SRAM (default: dual-port).  With --openroad this is the "
              "generated 6T macro (--bitcell 6t); otherwise the legacy srambank flow.")
        .default_value(false)
        .implicit_value(true);

    program.add_argument("--bitcell")
        .help("8t (default): the two-port 8T macro. 6t: a single-port macro of the released "
              "6T cell, built like the two-port one with port A alone.")
        .default_value(std::string("8t"));

    program.add_argument("--segment-bits")
        .help("Divided wordlines (--bitcell 6t): cut each stack of data bits into segments of "
              "this many bits, each with its own wordlines and a strip pair between two. "
              "0 (default): whole stacks.")
        .default_value(unsigned{0})
        .scan<'u', unsigned>();

    program.add_argument("--share-port-b")
        .help("Two-port: banks in pairs share port B's sense amplifier, write driver "
              "and output latch, the pair's arrays mirrored about one two-sided IO block. "
              "Needs an even number of banks.")
        .default_value(false)
        .implicit_value(true);

    program.add_argument("--row-predecode-bits")
        .help("Address bits per predecode group of the controller's wordline-slice decode: "
              "2, 3 (default) or 4.  4-bit groups are for 256 or more wordlines a bank, where "
              "they drop a group: smaller arrays decode slower and larger with them.")
        .default_value(unsigned{3})
        .scan<'u', unsigned>();

    program.add_argument("--strips-in-controller")
        .help("Place and route the controller around the wordline driver strips, its band "
              "filling the space between the two stacks of column tiles.  One tile column: "
              "one bank, or (two-port) a pair sharing port B.")
        .default_value(false)
        .implicit_value(true);

    program.add_argument("--skip-characterization")
        .help("Skip SiliconSmart and emit an explicitly estimated early-PPA Liberty model.")
        .default_value(false)
        .implicit_value(true);

    program.add_argument("--output-sp-name")
        .help("Output name for generated SPICE netlist.")
        .default_value(std::string("sram.sp"));

    program.add_argument("--output-gds-name")
        .help("Output name for generated GDS file.")
        .default_value(std::string("sram.gds"));

    program.add_argument("--spice-only")
        .help("Only generate SPICE netlist but also run Innovus.")
        .default_value(false)
        .implicit_value(true);

    program.add_argument("--num-wl-buf")
        .help("Number of buffers in the wordline (WL) chain for delay estimation.")
        .default_value(unsigned{5})
        .scan<'u', unsigned>();

    program.add_argument("--num-sae-buf")
        .help("Number of buffers in the sense amplifier enable (SAE) chain for delay estimation.")
        .default_value(unsigned{10})
        .scan<'u', unsigned>();

    program.add_argument("--use-yosys")
        .help("Use Yosys for synthesis (open-source, instead of Design Compiler).")
        .default_value(false)
        .implicit_value(true);

    program.add_argument("--use-openroad")
        .help("Use OpenROAD for P&R (open-source, instead of Innovus). Pairs with --use-yosys.")
        .default_value(false)
        .implicit_value(true);

    program.add_argument("--openroad")
        .help("Alias for --use-yosys --use-openroad (ASAP7 open-source flow).")
        .default_value(false)
        .implicit_value(true);

    program.add_argument("--openroad-path")
        .help("OpenROAD binary name or path (default: 'openroad' resolved from $PATH)")
        .default_value(std::string(""))
        ;

    program.add_argument("--platform-path")
        .help("Path to ASAP7 platform (default: <openroad>/platform/asap7)")
        .default_value(std::string(""));

    program.add_argument("--liberty-from")
        .help("Characterization JSON with measured values overriding the "
              "estimated Liberty constants (timing tables, leakage, caps).")
        .default_value(std::string(""));

    program.add_argument("--liberty-only")
        .help("Existing macro LEF: export Liberty only, using the supplied geometry and --liberty-from JSON.")
        .default_value(std::string(""));
    program.add_argument("--liberty-output")
        .help("Output .lib path; required with --liberty-only.")
        .default_value(std::string(""));

    program.add_argument("--bitcell-width")
        .help("Custom push-rule bitcell width (um). Default: 0.108 for logic-rule ASAP7.")
        .default_value(double{0.108})
        .scan<'f', double>();

    program.add_argument("--bitcell-height")
        .help("Custom push-rule bitcell height (um). Default: 0.27 for logic-rule ASAP7.")
        .default_value(double{0.27})
        .scan<'f', double>();

    program.add_argument("--num-rows-per-mux")
        .help("Number of bitcell rows per IO column group (multiplexing factor). Default: 4.")
        .default_value(unsigned{4})
        .scan<'u', unsigned>();

    try {
        program.parse_args(argc, argv);
    } catch (const std::exception& e) {
        LOGE << "CLI parse error: " << e.what();
        LOGE << program;
        std::exit(1);
    }

    MainCliOptions options;
    options.num_wls               = program.get<unsigned>("--num-wls");
    options.num_data_bits         = program.get<unsigned>("--num-data-bits");
    options.num_banks             = program.get<unsigned>("--num-banks");
    options.single_port           = program.get<bool>("--single-port");
    options.share_port_b          = program.get<bool>("--share-port-b");
    const std::string bitcell     = program.get<std::string>("--bitcell");
    if (bitcell != "8t" && bitcell != "6t") {
        LOGE << "Error: --bitcell is 8t or 6t, got " << bitcell;
        std::exit(1);
    }
    options.bitcell_6t            = bitcell == "6t";
    options.segment_bits          = program.get<unsigned>("--segment-bits");
    options.strips_in_controller  = program.get<bool>("--strips-in-controller");
    options.row_predecode_bits    = program.get<unsigned>("--row-predecode-bits");
    options.skip_characterization = program.get<bool>("--skip-characterization");
    options.num_wl_buf            = program.get<unsigned>("--num-wl-buf");
    options.num_sae_buf           = program.get<unsigned>("--num-sae-buf");
    options.output_sp_name        = program.get<std::string>("--output-sp-name");
    options.output_gds_name       = program.get<std::string>("--output-gds-name");
    options.spice_only            = program.get<bool>("--spice-only");
    options.use_yosys             = program.get<bool>("--use-yosys");
    options.use_openroad          = program.get<bool>("--use-openroad");
    options.openroad_only         = program.get<bool>("--openroad");
    options.openroad_path         = program.get<std::string>("--openroad-path");
    options.platform_path         = program.get<std::string>("--platform-path");
    options.liberty_from_json     = program.get<std::string>("--liberty-from");
    options.liberty_only_lef      = program.get<std::string>("--liberty-only");
    options.liberty_output        = program.get<std::string>("--liberty-output");
    if (options.liberty_only_lef.empty() != options.liberty_output.empty()) {
        LOGE << "--liberty-only and --liberty-output must be supplied together";
        std::exit(1);
    }
    options.bitcell_width         = program.get<double>("--bitcell-width");
    options.bitcell_height        = program.get<double>("--bitcell-height");
    options.num_rows_per_mux      = program.get<unsigned>("--num-rows-per-mux");

    if (options.openroad_only) {
        options.use_yosys = true;
        options.use_openroad = true;
    }
    // Default: resolve 'openroad' from $PATH at run time. A directory path
    // pointing at an OpenROAD checkout is still accepted and searched for
    // build/src/openroad by OpenRoadTclGenerator::run_openroad.
    if (options.openroad_path.empty()) {
        options.openroad_path = "openroad";
    }
    if (options.platform_path.empty()) {
        options.platform_path = options.openroad_path + "/platform/asap7";
    }
    // On the open-source flow --single-port is the generated 6T macro: the
    // legacy srambank path there left its wordlines unconnected.  The
    // commercial (Innovus/SiliconSmart) flow keeps the legacy macro.
    if (options.single_port && (options.bitcell_6t || options.use_yosys || options.use_openroad)) {
        if (!options.bitcell_6t) LOGI << "--single-port on the open-source flow: the generated 6T macro (--bitcell 6t)";
        options.bitcell_6t = true;
        options.single_port = false;
    }
    if (options.bitcell_6t && options.share_port_b) {
        LOGE << "Error: --bitcell 6t has one port and takes no --share-port-b.";
        std::exit(1);
    }
    if (options.segment_bits && options.segment_bits < options.num_data_bits / 2) {
        if (!options.bitcell_6t) {
            LOGE << "Error: --segment-bits is for the single-port macro (--bitcell 6t) so far.";
            std::exit(1);
        }
        if ((options.num_data_bits / 2) % options.segment_bits != 0) {
            LOGE << "Error: --segment-bits " << options.segment_bits << " does not divide a stack of "
                 << options.num_data_bits / 2 << " bits.";
            std::exit(1);
        }
    }
    if (options.segment_bits >= options.num_data_bits / 2) options.segment_bits = 0;
    if (options.bitcell_6t && options.num_rows_per_mux != 4 && options.num_rows_per_mux != 8 &&
        options.num_rows_per_mux != 16) {
        LOGE << "Error: --bitcell 6t's IO block is 4:1, 8:1 or 16:1 (--num-rows-per-mux).";
        std::exit(1);
    }
    if ((options.use_yosys || options.use_openroad) && options.bitcell_6t) {
        LOGI << "Open-source flow in generated single-port (6T) mode";
    } else if ((options.use_yosys || options.use_openroad) && !options.single_port) {
        LOGI << "Open-source flow in dual-port (8T) mode";
    } else if ((options.use_yosys || options.use_openroad) && options.single_port) {
        LOGI << "Open-source flow in single-port (6T) mode";
    }

    if (options.num_wls % 2 != 0) {
        LOGE << "Error: --num-wls must be an even number.";
        std::exit(1);
    }
    if (options.num_data_bits % 2 != 0) {
        LOGE << "Error: --num-data-bits must be an even number.";
        std::exit(1);
    }
    if ((options.num_banks & (options.num_banks - 1)) != 0) {
        LOGE << "Error: --num-banks must be a power of 2.";
        std::exit(1);
    }
    if (options.strips_in_controller && options.single_port) {
        LOGE << "Error: --strips-in-controller is for the two-port macro.";
        std::exit(1);
    }
    if (options.row_predecode_bits < 2 || options.row_predecode_bits > 4) {
        LOGE << "Error: --row-predecode-bits is 2, 3 or 4.";
        std::exit(1);
    }
    if (options.row_predecode_bits != 3 && options.single_port) {
        LOGE << "Error: --row-predecode-bits is for the slice-driven macros (two-port or --bitcell 6t).";
        std::exit(1);
    }
    if (options.strips_in_controller &&
        options.num_banks > (options.share_port_b ? 2u : 1u)) {
        LOGE << "Error: --strips-in-controller abuts one tile column: one bank, or a pair with --share-port-b.";
        std::exit(1);
    }
    if (options.share_port_b && (options.single_port || options.num_banks < 2)) {
        LOGE << "Error: --share-port-b needs the two-port macro and at least two banks.";
        std::exit(1);
    }

    LOGD << "Configuration: num_wls=" << options.num_wls
         << ", num_data_bits=" << options.num_data_bits
         << ", num_banks=" << options.num_banks
         << ", single_port=" << (options.single_port ? 1 : 0)
         << ", skip_characterization=" << (options.skip_characterization ? 1 : 0)
         << ", output_sp_name=" << options.output_sp_name
         << ", output_gds_name=" << options.output_gds_name
         << ", spice_only=" << (options.spice_only ? 1 : 0)
         << ", use_yosys=" << (options.use_yosys ? 1 : 0)
         << ", use_openroad=" << (options.use_openroad ? 1 : 0)
         << ", openroad_path=" << options.openroad_path
         << ", platform_path=" << options.platform_path;

    return options;
}

std::map<std::string, double> six_t_port_loads_pf(const MainCliOptions& options) {
    constexpr double kGateFfPerFin = 0.103;  // INVx1_ASAP7: 0.62 fF on 3n+3p fins
    constexpr double kWireFfPerLoad = 0.1;
    const unsigned rows = 2 * options.num_wls;
    const unsigned mux = options.num_rows_per_mux;
    const unsigned strips = 2 * options.wordline_segments();  // a strip per segment, both halves
    const unsigned slices = rows / 4;
    // A slice's predecode input is two NAND2 gates; the c64 slice (past 32
    // cells a wordline) doubles them (scripts/generate_asap7_8t_wl_slices.py).
    const unsigned nand_fins = mux * options.wordline_segment_bits() > 32 ? 24 : 12;
    const unsigned ios = options.num_data_bits;  // IO blocks a bank's port drives
    auto pf = [](double fins, double loads) {
        return (fins * kGateFfPerFin + loads * kWireFfPerLoad) / 1000.0;
    };
    const double lo_loads = double(slices) * strips * options.num_banks;
    std::map<std::string, double> loads = {
        {"sel_lo_A", pf(lo_loads * nand_fins, lo_loads)},
        {"sel_hi_A", pf(strips * 4.0 * nand_fins, strips)},
    };
    // Fins per IO block (tech/spice/sram_6t_iocolumn.sp, iocol_block_6t*).
    // The compact block's 270 nm output latch drives Q through one 3-fin
    // tristate (every mux ratio).
    const double oe_fins = 3;
    // From 8:1 a leaf makes YSEL itself (local_ysel): YSELN drives its
    // transmission gates' pFETs and the inverter, YSEL leaves the block.
    // From 16:1 ysel_A carries two predecoded groups, each line a NAND input
    // in four leaves (3n + 3p fins each), and yseln_A leaves the block.
    const bool local_ysel = mux >= 8;
    const bool predecode = mux >= 16;
    const std::map<std::string, double> io_fins = {
        {"ysel_A", predecode ? 24 : local_ysel ? 0 : 6},
        {"yseln_A", predecode ? 0 : local_ysel ? 12 : 6},
        {"blprechn_A", 6.0 * mux}, {"sae_A", 18},
        {"wrena_A", 6}, {"wrenan_A", 12}, {"oe_out_A", oe_fins}, {"oeb_out_A", oe_fins}};
    for (const auto& [pin, fins] : io_fins) loads[pin] = pf(fins * ios, ios);
    return loads;
}
