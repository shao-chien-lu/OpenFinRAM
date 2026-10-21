#include "openroad_manager.hpp"

#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <numeric>
#include <sstream>
#include <sys/stat.h>
#include "plog/Log.h"
#include "openroad_tcl_generator.hpp"
#include "utils.hpp"
#include "cell_utils.hpp"
#include "layermap.hpp"

OpenRoadManager::OpenRoadManager(const MainCliOptions& cli_options)
    : cli_options_(cli_options) {}

bool OpenRoadManager::run_openroad_flow() {
    OpenFinRAM::OpenRoadTclGenerator gen;
    gen.set_design_name("ctrl_decode");
    gen.set_site_name("asap7sc7p5t");
    gen.set_site_height(0.27);
    // 40% was safe while the two-port controller carried a buffer stage per
    // wordline; since the driver slices took the wordlines it is small
    // logic plus its delay chains; 50% leaves hold repair its buffers (60%
    // did not).
    gen.set_max_utilization(cli_options_.single_port ? 0.40 : 0.50);
    gen.set_bitcell_width(cli_options_.bitcell_width);
    gen.set_one_port(cli_options_.bitcell_6t);
    if (cli_options_.bitcell_6t) {
        // Port A's inputs (address, ce_n, we_n, oe_n) stand in order on the
        // band's left edge; pin placement found six slots in 2.16 um, so
        // give each 0.4 um and two to spare.  A wide multi-bank band of
        // little logic is otherwise too short for them.
        gen.set_min_height(0.4 * (get_addr_width(cli_options_) + 3 + 2));
        // The array-wide outputs get a driver for their counted load (the
        // SDC's set_load): BUFx24 holds 170 fF under the 150 ps slew limit.
        std::map<std::string, std::string> drivers;
        for (const auto& [prefix, pf] : six_t_port_loads_pf(cli_options_)) {
            if (pf > 0.060) drivers[prefix] = "BUFx24_ASAP7_75t_R";
            else if (pf > 0.020) drivers[prefix] = "BUFx12f_ASAP7_75t_R";
        }
        gen.set_port_drivers(drivers);
    }

    // QoR and work_dir must match YosysManager's CWD-based tmp (repo/tmp) for consistency
    std::string qor_path_cwd = join_path(get_current_dir_name(), "tmp/syn_" + get_run_timestamp() + "/qor_report.txt");
    std::string qor_path_exe = join_path(get_executable_directory(), "tmp/syn_" + get_run_timestamp() + "/qor_report.txt");
    std::string qor_path = file_exists(qor_path_cwd) ? qor_path_cwd : qor_path_exe;
    if (!file_exists(qor_path)) qor_path = qor_path_cwd;
    bool qor_parsed = gen.parse_qor_report(qor_path);
    if (!qor_parsed) {
        LOGE << "Failed to parse QoR report for OpenROAD flow at " << qor_path;
        return false;
    }

    std::string work_dir = join_path(get_current_dir_name(), "tmp/openroad_" + get_run_timestamp());
    if (!directory_exists(work_dir)) {
        if (!create_directory(work_dir, nullptr) && !directory_exists(work_dir)) {
            std::string tmp_root = join_path(get_current_dir_name(), "tmp");
            if (!directory_exists(tmp_root)) create_directory(tmp_root, nullptr);
            if (!create_directory(work_dir, nullptr)) {
                LOGE << "Failed to create OpenROAD work dir: " << work_dir;
                return false;
            }
        }
    }
    // also ensure executable tmp exists for fallback
    if (!directory_exists(join_path(get_executable_directory(), "tmp"))) {
        create_directory(join_path(get_executable_directory(), "tmp"), nullptr);
    }

    std::string output_tcl = join_path(work_dir, "run.tcl");
    int addr_width = get_addr_width(cli_options_);
    int num_ysel = cli_options_.num_rows_per_mux;  // column-mux ratio (was 4)
    double sram_width = 10.0;
    if (cli_options_.single_port) {
        sram_width = (cli_options_.bitcell_width * 2 + 2.376 + cli_options_.bitcell_width * ((cli_options_.num_wls + 3) * 2)) * cli_options_.num_banks - cli_options_.bitcell_width;
    } else if (cli_options_.bitcell_6t) {
        // A column tile, as scripts/generate_asap7_6t_iocolumn.py lays it:
        // edge filler | cap | 2*NUM_WL bitcells | dummy | tap | IO block,
        // the first four and every bitcell one slot wide.
        OpenFinRAM::LayerMap map;
        map.init_asap7_layermap();
        gdstk::ErrorCode error = gdstk::ErrorCode::NoError;
        auto lib = gdstk::read_gds(join_path(get_current_dir_name(),
            "tech/gds/sram_6t_iocolumn.gds").c_str(), 0, 1e-2, nullptr, &error);
        const std::string io_name = cli_options_.num_rows_per_mux == 4
            ? std::string("iocol_sram_6t") : "iocol_sram_6t_x" + std::to_string(cli_options_.num_rows_per_mux);
        auto* io = lib.get_cell(io_name.c_str());
        auto* bitcell = lib.get_cell("sram_cell_6t_122");
        auto io_size = io ? OpenFinRAM::get_cell_size_from_boundary(io, map) : OpenFinRAM::CellSize{};
        auto bit_size = bitcell ? OpenFinRAM::get_cell_size_from_boundary(bitcell, map) : OpenFinRAM::CellSize{};
        if (error != gdstk::ErrorCode::NoError || !io_size.valid || !bit_size.valid) {
            LOGE << "Cannot measure the 6T IO/bitcell geometry (tech/gds/sram_6t_iocolumn.gds)";
            lib.free_all();
            return false;
        }
        const unsigned slots = 2 * cli_options_.num_wls + 4;
        sram_width = (slots * bit_size.width + io_size.width) * cli_options_.num_banks;
        lib.free_all();
    } else {
        OpenFinRAM::LayerMap map;
        map.init_asap7_layermap();
        gdstk::ErrorCode error = gdstk::ErrorCode::NoError;
        auto lib = gdstk::read_gds(join_path(get_current_dir_name(),
            "tech/gds/sram_8t_iocolumn.gds").c_str(), 0, 1e-2, nullptr, &error);
        // One IO per end of the bitlines: port A | cap | one array | port B.
        auto* io_a = lib.get_cell("iocol_sram_8t_a");
        auto* io_b = lib.get_cell("iocol_sram_8t_b");
        auto* cap = lib.get_cell("col_cap_x4_sram_8t");
        auto* bitcell = lib.get_cell("sram_cell_8t");
        if (error != gdstk::ErrorCode::NoError || !io_a || !io_b || !cap || !bitcell) {
            LOGE << "Cannot measure the 8T IO/bitcell geometry for controller placement";
            lib.free_all();
            return false;
        }
        auto io_a_size = OpenFinRAM::get_cell_size_from_boundary(io_a, map);
        auto io_b_size = OpenFinRAM::get_cell_size_from_boundary(io_b, map);
        auto cap_size = OpenFinRAM::get_cell_size_from_boundary(cap, map);
        auto bit_size = OpenFinRAM::get_cell_size_from_boundary(bitcell, map);
        if (!io_a_size.valid || !io_b_size.valid || !cap_size.valid || !bit_size.valid) {
            LOGE << "8T IO/bitcell has no valid placement boundary";
            lib.free_all();
            return false;
        }
        // Same tap policy as compile_asap7_2rw.py, over the whole array.
        const unsigned rows = 2 * cli_options_.num_wls;
        const unsigned tap_pitch = std::gcd(rows, 16u);
        const unsigned slots = rows + rows / tap_pitch;
        const double half = io_a_size.width + cap_size.width + slots * bit_size.width;
        sram_width = (half + io_b_size.width) * cli_options_.num_banks;
        if (cli_options_.share_port_b) {
            // Banks in pairs, mirrored about one two-sided port-B block.
            auto* io_b2 = lib.get_cell("iocol_sram_8t_b2");
            auto io_b2_size = io_b2 ? OpenFinRAM::get_cell_size_from_boundary(io_b2, map)
                                    : decltype(io_b_size){};
            if (!io_b2 || !io_b2_size.valid) {
                LOGE << "tech/gds/sram_8t_iocolumn.gds has no two-sided port-B block (iocol_sram_8t_b2)";
                lib.free_all();
                return false;
            }
            sram_width = (2 * half + io_b2_size.width) * (cli_options_.num_banks / 2);
        }
        lib.free_all();
    }
    // With the strips in the controller's band, the assembler plans the band
    // first: its width is the tiles', and the strips' keep-outs are known.
    if (!cli_options_.single_port && cli_options_.strips_in_controller) {
        const std::string band_dir = join_path(work_dir, "band");
        const std::string local_python = join_path(get_current_dir_name(), ".venv/bin/python");
        std::ostringstream plan;
        plan << (file_exists(local_python) ? local_python : std::string("python3"))
             << " '" << join_path(get_current_dir_name(), "scripts/compile_asap7_2rw.py") << "'"
             << " --wordlines " << cli_options_.num_wls
             << " --bits " << cli_options_.num_data_bits
             << " --banks " << cli_options_.num_banks
             << (cli_options_.share_port_b ? " --share-port-b" : "")
             << (cli_options_.bitcell_6t ? " --bitcell 6t --mux " + std::to_string(cli_options_.num_rows_per_mux) : "")
             << (cli_options_.segment_bits ? " --segment-bits " + std::to_string(cli_options_.segment_bits) : "")
             << " --plan-band '" << band_dir << "' > '" << join_path(work_dir, "band_plan.log") << "' 2>&1";
        OpenFinRAM::BandPlan band;
        if (std::system(plan.str().c_str()) != 0 || !OpenFinRAM::read_band_plan(band_dir, band)) {
            LOGE << "Planning the controller band failed; see " << join_path(work_dir, "band_plan.log");
            return false;
        }
        gen.set_band(band);
        sram_width = band.width;
        LOGI << "Controller band: " << band.width << " um wide, " << band.reserved
             << " um^2 kept out for the wordline strips";
    }
    double col_width = (sram_width + cli_options_.bitcell_width) / cli_options_.num_banks;
    // Prefer CWD tech (repo root) for tech_root; OpenROAD flow may be run from repo root
    std::string tech_root_cwd = join_path(get_current_dir_name(), "tech");
    std::string tech_root_exe = join_path(get_executable_directory(), "tech");
    std::string tech_root = directory_exists(tech_root_cwd) ? tech_root_cwd : tech_root_exe;
    std::string platform_path = cli_options_.platform_path;
    if (!platform_path.empty() && platform_path.front() != '/') {
        platform_path = join_path(get_current_dir_name(), platform_path);
    }
    if (platform_path.empty()) platform_path = tech_root;
    // If platform_path was default ~/iv3/repos/OpenROAD/platform/asap7 but doesn't exist, fallback to tech_root
    if (!directory_exists(platform_path) && !file_exists(join_path(platform_path, "lef/asap7_tech.lef"))) {
        platform_path = tech_root;
    }

    if (!gen.generate_run_tcl(sram_width, 0.0, output_tcl,
                              cli_options_.num_wls, cli_options_.num_wls,
                              num_ysel, addr_width,
                              cli_options_.num_banks,
                              cli_options_.spice_only, col_width,
                              platform_path, tech_root,
                              cli_options_.single_port)) {
        LOGE << "Failed to generate OpenROAD run.tcl";
        return false;
    }
    LOGI << "OpenROAD run.tcl generated at: " << output_tcl;

    std::string log_file = join_path(work_dir, "openroad.log");
    std::string openroad_bin = cli_options_.openroad_path;
    if (openroad_bin.empty()) openroad_bin = "openroad";
    // Clear early failure when the binary cannot be resolved. A bare name is
    // looked up in $PATH; a filesystem path must exist (directories are
    // searched for build/src/openroad by run_openroad).
    {
        bool looks_like_path = openroad_bin.find('/') != std::string::npos;
        bool found = false;
        if (looks_like_path) {
            struct stat st;
            found = stat(openroad_bin.c_str(), &st) == 0;
        } else {
            std::string which = "which \"" + openroad_bin + "\" >/dev/null 2>&1";
            found = std::system(which.c_str()) == 0;
        }
        if (!found) {
            LOGE << "OpenROAD binary '" << openroad_bin
                 << "' not found. Install OpenROAD or point --openroad-path at "
                 << "the binary (or a checkout directory).";
            return false;
        }
    }
    bool or_ok = gen.run_openroad(output_tcl, work_dir, log_file, openroad_bin);
    // The utilization cap is a target, and the controller's place-and-route
    // can still end one short of a clean route (a two-bank controller left a
    // single 1 nm M1 spacing at 50 %).  Retry with a little more height
    // rather than lowering the target for every build.
    double utilization = cli_options_.single_port ? 0.40 : 0.50;
    for (int retry = 0; retry < 2 && !or_ok; ++retry) {
        LOGW << "OpenROAD periphery flow failed at " << utilization
             << " utilization (see " << log_file << "); retrying lower";
        utilization -= 0.06;
        gen.set_max_utilization(utilization);
        if (!gen.generate_run_tcl(sram_width, 0.0, output_tcl,
                                  cli_options_.num_wls, cli_options_.num_wls,
                                  num_ysel, addr_width,
                                  cli_options_.num_banks,
                                  cli_options_.spice_only, col_width,
                                  platform_path, tech_root,
                                  cli_options_.single_port)) {
            LOGE << "Failed to generate OpenROAD run.tcl";
            return false;
        }
        or_ok = gen.run_openroad(output_tcl, work_dir, log_file, openroad_bin);
    }
    std::string def_path = join_path(work_dir, "ctrl_decode.def");
    std::string v_path = join_path(work_dir, "netlist_for_lvs.v");
    if (!or_ok) {
        LOGE << "OpenROAD periphery flow failed (see " << log_file
             << "); refusing to use partial DEF/netlist artifacts";
        return false;
    }

    if (!file_exists(def_path) || !file_exists(v_path)) {
        LOGE << "OpenROAD completed without required DEF/Verilog outputs";
        return false;
    }
    if (cli_options_.strips_in_controller && !cli_options_.single_port) {
        // The assembler stacks the tiles on the band's edges: tell it the die.
        std::ofstream die(join_path(work_dir, "band/die.txt"));
        die << std::fixed << std::setprecision(4) << "width " << sram_width << "\nheight "
            << gen.band_die_height(sram_width) << "\n";
    }

    std::string gds_path = join_path(work_dir, "ctrl_decode.gds");
    if (file_exists(def_path)) {
        auto resolve_platform_file = [&](const std::string& relative_path) {
            std::string platform_file = join_path(platform_path, relative_path);
            if (file_exists(platform_file)) return platform_file;
            return join_path(tech_root, relative_path);
        };

        std::string tech_lef = resolve_platform_file("lef/asap7_tech.lef");
        std::string cell_lef = resolve_platform_file("lef/asap7sc7p5t_28_R.lef");
        std::string macro_gds = resolve_platform_file("gds/asap7sc7p5t_28_R_220121a.gds");
        if (!gen.stream_def_to_gds(
                def_path, tech_lef, cell_lef, macro_gds, gds_path)) {
            LOGE << "Controller GDS stream-out failed; refusing to use the old DEF placeholder";
            return false;
        }
    }

    LOGI << "OpenROAD flow completed successfully";
    if (!file_exists(def_path)) {
        LOGE << "ctrl_decode.def not found at " << def_path;
        return false;
    }
    if (!file_exists(gds_path)) {
        LOGE << "ctrl_decode.gds not found at " << gds_path;
        return false;
    }
    if (!file_exists(v_path)) {
        LOGE << "netlist_for_lvs.v not found at " << v_path;
        return false;
    }

    std::string cdl_path = join_path(tech_root, "cdl/asap7sc7p5t_28_R.cdl");
    if (!file_exists(cdl_path)) {
        cdl_path = join_path(platform_path, "cdl/asap7sc7p5t_28_R.cdl");
    }
    if (!file_exists(cdl_path)) {
        cdl_path = join_path(get_current_dir_name(), "tech/cdl/asap7sc7p5t_28_R.cdl");
    }
    if (!file_exists(cdl_path)) {
        cdl_path = join_path(get_executable_directory(), "tech/cdl/asap7sc7p5t_28_R.cdl");
    }
    if (!gen.run_v2lvs(work_dir, "netlist_for_lvs.v", "netlist_for_lvs.sp", cdl_path)) {
        LOGE << "v2lvs (OpenROAD) failed";
        return false;
    }
    if (!gen.post_process_netlist(work_dir, "netlist_for_lvs.sp", cdl_path)) {
        LOGE << "Post-process (OpenROAD) failed";
        return false;
    }
    return true;
}
