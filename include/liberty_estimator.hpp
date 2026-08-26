#pragma once

#include <string>

#include "characterization_data.hpp"
#include "main_config_helpers.hpp"

namespace OpenFinRAM {

/**
 * Write an explicitly estimated, single-port SRAM Liberty model.
 *
 * The model is intended for early synthesis/STA integration when transistor-
 * level characterization is skipped or unavailable. Timing values follow a
 * coarse FakeRAM-style ASAP7 baseline and are not characterized values.
 * Macro area is taken from the generated LEF.
 *
 * When `char_data` is provided, measured values from a characterization JSON
 * (see characterization_data.hpp) replace the estimated constants wherever
 * present; absent fields keep the historical constants. Any per-sdel variants
 * in the data are emitted as additional complete .lib files named
 * <liberty_path_stem>_<variant>.lib.
 */
bool export_estimated_liberty(const MainCliOptions& options,
                              const std::string& lef_path,
                              const std::string& liberty_path,
                              std::string* error = nullptr,
                              const CharacterizationData* char_data = nullptr);

}  // namespace OpenFinRAM
