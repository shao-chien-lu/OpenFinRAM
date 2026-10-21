#ifndef LAYOUT_GENERATOR_HPP
#define LAYOUT_GENERATOR_HPP

#include <string>

#include "gdstk/gdstk.hpp"

#include "main_config_helpers.hpp"
#include "layermap.hpp"

struct SramCellSet {
    gdstk::Cell* filler      = nullptr;
    gdstk::Cell* bitcell     = nullptr;
    gdstk::Cell* dummy       = nullptr;
    gdstk::Cell* tapcell     = nullptr;
    gdstk::Cell* dummy_v1    = nullptr;
    gdstk::Cell* dummy_v2    = nullptr;
    gdstk::Cell* cgedge      = nullptr;
    gdstk::Cell* io_colgrp   = nullptr;

    // Sub-cells for procedurally building a deeper (M != 4) column-mux io_colgrp.
    gdstk::Cell* ymux_leg_v1 = nullptr;  // sram_prech_ymux_6t112_v1 (0.531 x 0.314)
    gdstk::Cell* ymux_leg_v2 = nullptr;  // sram_prech_ymux_6t112_v2
    gdstk::Cell* senseamp    = nullptr;  // senseamp_sram_6t122
    gdstk::Cell* write_drv   = nullptr;  // write_draslatch_02
    gdstk::Cell* srlatch     = nullptr;  // srlatch_sram_6t122

    // Bitcell-pitch dummy fill for the ctrl_decode region (well/implant/fin
    // continuity + clean array-boundary abutment). Restored into boundary_2
    // from the academic srambank via scripts/merge_cells_from_gds.py.
    gdstk::Cell* dummy_vertical      = nullptr;  // dummy_vertical_6t122 (0.162 x 0.350)
    gdstk::Cell* dummy_vertical_row  = nullptr;  // dummy_vertical_array_X64 (64-wide row)

    gdstk::Cell* sram_column = nullptr;
    gdstk::Cell* array_cell  = nullptr;
    gdstk::Cell* colgrp      = nullptr;
    gdstk::Cell* stacked_colgrp = nullptr;
    gdstk::Cell* muxed_colgrp = nullptr;
};

class LayoutGenerator {
public:
    LayoutGenerator(const MainCliOptions& options, const OpenFinRAM::LayerMap& layer_map);

    bool load_sram_gds();

    bool extract_required_cells();

    bool gen_layout();

    bool create_dual_port_macro();

    bool create_sram_column();

    bool create_sram_array();

    bool create_colgrp();

    // Procedurally tile an M-leg column mux (wrasst_prech_ymux_xM) from the
    // single-bitline legs, for deeper-than-4 column muxing. Returns nullptr if
    // the leg sub-cells were not extracted. mux = num_rows_per_mux.
    gdstk::Cell* create_wrasst_ymux(int mux);

    // Procedurally assemble a deeper-mux io_colgrp (2 M-leg ymuxes + shared
    // sense amp / write driver / srlatch). Falls back to the extracted 4:1
    // io_colgrp when mux == 4 or sub-cells are unavailable.
    gdstk::Cell* create_muxed_iocolgrp(int mux);

    bool create_stacked_colgrp();

    bool create_muxed_colgrp();

    bool write_layout();

    bool add_ctrl_decode_gate_fin_wrappers();

    bool create_and_add_sram_filler_cells();

    bool run_sram_gds_integration_and_writeback();

    // 將 cell 及其相依 cell 複製到目標 Library（避免重複加入）
    void add_cell_with_deps(gdstk::Library& target_lib, gdstk::Cell* source_cell);

private:
    MainCliOptions cli_options_;

    OpenFinRAM::LayerMap layer_map_;

    gdstk::Library sram_lib;

    SramCellSet sram_cells_;

    gdstk::Library ctrl_decode_gds;
};

#endif // LAYOUT_GENERATOR_HPP
