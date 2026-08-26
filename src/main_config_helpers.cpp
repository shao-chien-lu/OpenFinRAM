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
        .help("Generate single-port SRAM (default: dual-port).")
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
        .help("Use Yosys for synthesis (open-source, instead of Design Compiler). Implies --single-port ASAP7 flow.")
        .default_value(false)
        .implicit_value(true);

    program.add_argument("--use-openroad")
        .help("Use OpenROAD for P&R (open-source, instead of Innovus). Pairs with --use-yosys.")
        .default_value(false)
        .implicit_value(true);

    program.add_argument("--openroad")
        .help("Alias for --use-yosys --use-openroad (single-port ASAP7 open-source flow).")
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
    // open-source flow is single-port focused; warn if dual-port requested
    if ((options.use_yosys || options.use_openroad) && !options.single_port) {
        LOGW << "OpenROAD/Yosys flow is validated for --single-port; forcing single_port=true";
        options.single_port = true;
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
