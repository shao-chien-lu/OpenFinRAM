#include "cell_utils.hpp"
#include "filler_generator.hpp"
#include "gdstk/gdstk.hpp"
#include "layermap.hpp"
#include "spice_generator.hpp"
#include "synthesis_manager.hpp"
#include "yosys_manager.hpp"
#include "spice_converter.hpp"
#include "spice_integrator.hpp"
#include "spice_include_resolver.hpp"
#include "spice_simulator.hpp"
#include "innovus_tcl_generator.hpp"
#include "innovus_manager.hpp"
#include "openroad_manager.hpp"
#include "siliconsmart_generator.hpp"
#include "siliconsmart_manager.hpp"
#include "lvs_runner.hpp"
#include "lvs_manager.hpp"
#include "lef_extractor.hpp"
#include "lef_manager.hpp"
#include "liberty_estimator.hpp"
#include "layout_generator.hpp"
#include "main_config_helpers.hpp"
#include "main_flow_helpers.hpp"
#include "main_layout_helpers.hpp"
#include "utils.hpp"
#include "plog/Appenders/ColorConsoleAppender.h"
#include "plog/Formatters/TxtFormatter.h"
#include "plog/Init.h"
#include "plog/Log.h"

#include <algorithm>
#include <cfloat>
#include <cstdlib>
#include <cmath>
#include <string>
#include <vector>

// ============================================================================
// Global layer map instance
// ============================================================================
static OpenFinRAM::LayerMap g_layer_map;


// ============================================================================
// Main
// ============================================================================
int main(int argc, char **argv) {
    // Initialize plog
    static plog::ColorConsoleAppender<plog::TxtFormatter> consoleAppender;
    plog::init(plog::debug, &consoleAppender);

    LOGI << "Starting OpenFinRAM application";

    // Parse CLI options
    MainCliOptions cli_options = parseMainCliOptions(argc, argv);

    if (!cli_options.liberty_only_lef.empty()) {
        OpenFinRAM::CharacterizationData data;
        std::string error;
        const bool supplied = !cli_options.liberty_from_json.empty();
        if (supplied && !OpenFinRAM::load_characterization_json(cli_options.liberty_from_json, data, &error)) {
            LOGE << error;
            return 1;
        }
        if (!OpenFinRAM::export_estimated_liberty(cli_options, cli_options.liberty_only_lef,
                cli_options.liberty_output, &error, supplied ? &data : nullptr)) {
            LOGE << error;
            return 1;
        }
        return 0;
    }

    // The 2RW and generated 6T macros are built from generated cell libraries
    // (tech/gds, scripts/tech_gds.py), which the build writes; write any that
    // are missing, as a run straight from a checkout has none.
    if (!cli_options.single_port || cli_options.bitcell_6t) {
        const std::string root = get_current_dir_name();
        const std::string venv = join_path(root, ".venv/bin/python");
        const std::string command = (file_exists(venv) ? "'" + venv + "'" : std::string("python3"))
            + " '" + join_path(root, "scripts/tech_gds.py") + "' --if-missing";
        if (std::system(command.c_str()) != 0) {
            LOGE << "Cannot generate the cell libraries in tech/gds: " << command;
            return 1;
        }
    }

    // Generate SPICE netlist
    LOGI << "=== Generating SPICE Netlist ===";
    OpenFinRAM::SpiceGenerator gen(cli_options);
    if (gen.generate()) {
        LOGI << "SPICE netlist generation completed successfully.";
    } else {
        LOGE << "SPICE netlist generation failed.";
        return 1;
    }

    // Run synthesis flow (open-source Yosys or commercial Design Compiler)
    if (cli_options.use_yosys || cli_options.openroad_only) {
        LOGI << "=== Running Yosys Synthesis (OpenROAD ASAP7"
             << (cli_options.single_port ? " single-port"
                 : cli_options.bitcell_6t ? " single-port 6T" : " dual-port") << ") ===";
        YosysManager yosys_manager(cli_options);
        if (!yosys_manager.run_synthesis()) {
            LOGE << "Yosys periphery synthesis/signoff failed.";
            return 1;
        }
    } else {
        SynthesisManager synth_manager(cli_options);
        if (!synth_manager.run_synthesis()) {
            LOGW << "Synthesis flow failed, skipping due to missing EDA tools.";
        }
    }

    // Run P&R flow (OpenROAD or Innovus)
    if (cli_options.use_openroad || cli_options.openroad_only) {
        LOGI << "=== Running OpenROAD P&R ("
             << (cli_options.single_port ? "single-port"
                 : cli_options.bitcell_6t ? "single-port 6T" : "dual-port")
             << " ASAP7, platform/asap7) ===";
        OpenRoadManager openroad_manager(cli_options);
        if (!openroad_manager.run_openroad_flow()) {
            LOGE << "OpenROAD periphery implementation/STA failed.";
            return 1;
        }
    } else {
        InnovusManager innovus_manager(cli_options);
        if (!innovus_manager.run_innovus_flow()) {
            LOGW << "Innovus flow failed, skipping due to missing EDA tools.";
        }
    }

    SpiceIntegrator integrator(cli_options);
    if (!integrator.integrate_sram()) {
        LOGW << "SRAM integration failed, skipping.";
        if (!cli_options.single_port && (cli_options.use_openroad || cli_options.openroad_only))
            return 1;
    }

    // Run siliconsmart characterization
    SiliconSmartManager sis_manager(cli_options);
    const bool characterization_ok = sis_manager.run_siliconsmart();
    if (!characterization_ok) {
        LOGW << "SiliconSmart characterization failed, skipping.";
    }

    // Initialize ASAP7 Layer Map (hardcoded)
    LOGI << "Initializing ASAP7 layermap (hardcoded)...";
    g_layer_map.init_asap7_layermap();
    
    if (g_layer_map.empty()) {
        LOGE << "Failed to initialize layer map!";
        return 1;
    }

    LayoutGenerator layout_gen(cli_options, g_layer_map);
    if (!layout_gen.gen_layout()) {
        LOGE << "Layout generation failed!";
        return 1;
    }

    // Run LVS (create lvs folder, generate _run_control.svrf, run calibre)
    LvsManager lvs_manager(cli_options);
    if (!lvs_manager.run_lvs()) {
        LOGW << "LVS failed, skipping.";
    }

    // Export LEF file for the generated SRAM layout
    LefManager lef_manager(cli_options);
    if (!lef_manager.export_lef()) {
        LOGE << "LEF export failed!";
        return 1;
    }

    // Leave a usable early-PPA timing model when characterization was
    // explicitly skipped or could not run. A successful SiliconSmart model is
    // never overwritten by this estimated fallback.
    if (cli_options.skip_characterization || !characterization_ok) {
        const std::string cell_name =
            "sram_x" + std::to_string(cli_options.num_wls * 2) + "x" +
            std::to_string(cli_options.num_data_bits) + "x" +
            std::to_string(cli_options.num_banks);
        const std::string result_dir =
            "./results/" + cell_name + "_" + get_run_timestamp();
        std::string liberty_error;
        OpenFinRAM::CharacterizationData char_data;
        OpenFinRAM::CharacterizationData* char_ptr = nullptr;
        std::string liberty_from = cli_options.liberty_from_json;
        // The 6T macro's timing and energy follow its size: with no measured
        // JSON, scripts/timing_model_6t.py estimates them for this geometry
        // from the model fit to whole-macro simulations (tech/timing/).
        const std::string model = "tech/timing/sram_6t_timing.json";
        if (liberty_from.empty() && cli_options.bitcell_6t && file_exists(model)) {
            const std::string local_python = ".venv/bin/python";
            const std::string estimate = result_dir + "/" + cell_name + ".timing.json";
            const std::string command =
                (file_exists(local_python) ? local_python : std::string("python3")) +
                " scripts/timing_model_6t.py estimate --model " + model +
                " --wordlines " + std::to_string(cli_options.num_wls) +
                " --bits " + std::to_string(cli_options.num_data_bits) +
                " --mux " + std::to_string(cli_options.num_rows_per_mux) +
                " --segment-bits " + std::to_string(cli_options.segment_bits) +
                " --output '" + estimate + "' > /dev/null";
            if (std::system(command.c_str()) == 0) {
                liberty_from = estimate;
            } else {
                LOGW << "6T timing model estimate failed; keeping the estimated constants";
            }
        }
        if (!liberty_from.empty()) {
            if (OpenFinRAM::load_characterization_json(
                    liberty_from, char_data, &liberty_error)) {
                char_ptr = &char_data;
            } else {
                if (!cli_options.liberty_from_json.empty()) {
                    LOGE << "Requested characterization JSON is invalid: " << liberty_error;
                    return 1;
                }
                LOGW << "Characterization JSON load failed, falling back to "
                        "estimated constants: " << liberty_error;
            }
        }
        if (!OpenFinRAM::export_estimated_liberty(
                cli_options,
                result_dir + "/" + cell_name + ".lef",
                result_dir + "/" + cell_name + ".lib",
                &liberty_error,
                char_ptr)) {
            LOGW << "Estimated Liberty export failed: " << liberty_error;
            if (!cli_options.single_port && (cli_options.use_openroad || cli_options.openroad_only))
                return 1;
        }
    }
}
