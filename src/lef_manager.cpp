#include "lef_manager.hpp"

#include "plog/Log.h"

#include "lef_extractor.hpp"
#include "utils.hpp"

LefManager::LefManager(const MainCliOptions& cli_options)
    : cli_options_(cli_options) {}

bool LefManager::export_lef() {
    std::string sram_cell_name = "sram_x" + std::to_string(cli_options_.num_wls * 2) + "x" + std::to_string(cli_options_.num_data_bits) + "x" + std::to_string(cli_options_.num_banks);
    std::string generated_lef_path;
    std::string lef_error;
    const std::string results_dir = "./results/" + sram_cell_name + "_" + get_run_timestamp();
    if (!cli_options_.single_port && !directory_exists(results_dir) &&
        !create_directory(results_dir, nullptr)) return false;

    LOGI << "Exporting LEF for cell: " << sram_cell_name;
    bool lef_ok = OpenFinRAM::export_lef(cli_options_.single_port ? "." : results_dir,
        sram_cell_name, results_dir + "/" + sram_cell_name + ".gds", &generated_lef_path, &lef_error);

    if (lef_ok) {
        LOGI << "LEF export completed: " << generated_lef_path;
    } else {
        LOGW << "LEF export failed";
        if (!lef_error.empty()) {
            LOGW << "LEF export error: " << lef_error;
        }
        return false;
    }

    if (!cli_options_.single_port) return true;

    // mv ./{sram_cell_name}.lef ./results/{sram_cell_name}_{timestamp}/
    std::string src_path = join_path(get_current_dir_name(), sram_cell_name + ".lef");
    std::string dst_dir = join_path(get_current_dir_name(), "results/" + sram_cell_name + "_" + get_run_timestamp());
    if (!directory_exists(dst_dir)) {
        if (!create_directory(dst_dir, nullptr)) {
            LOGE << "Failed to create directory: " << dst_dir;
            return false;
        }
    }
    std::string dst_path = join_path(dst_dir, sram_cell_name + ".lef");
    if (!copy_file(src_path, dst_path)) {
        LOGE << "Failed to copy LEF file from " << src_path << " to " << dst_path;
        return false;
    }
    
    return true;
}
