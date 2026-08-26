#include "layout_generator.hpp"

#include <algorithm>
#include <cfloat>
#include <fstream>

#include "plog/Log.h"

#include "cell_utils.hpp"
#include "layermap.hpp"
#include "utils.hpp"
#include "filler_generator.hpp"

LayoutGenerator::LayoutGenerator(const MainCliOptions& options, const OpenFinRAM::LayerMap& layer_map) : cli_options_(options), layer_map_(layer_map) {}

bool LayoutGenerator::load_sram_gds() {
    std::string path = join_path(get_current_dir_name(), "tech/gds/srambank_32b_boundary_2.gds");

    // gdstk only writes error_code on failure; it must start at NoError or a
    // successful read fails this check on uninitialized stack data.
    gdstk::ErrorCode error_code = gdstk::ErrorCode::NoError;
    sram_lib = gdstk::read_gds(path.c_str(), 0, 1e-2, nullptr, &error_code);
    
    if (error_code != gdstk::ErrorCode::NoError) {
        LOGE << "Error reading SRAM GDS file: " << path;
        LOGE << "Error code: " << static_cast<int>(error_code);
        return false;
    }
    
    LOGI << "Successfully read SRAM GDS file: " << path;
    LOGI << "Number of cells in library: " << sram_lib.cell_array.count;
    
    return true;
}

bool LayoutGenerator::extract_required_cells() {
    const char* cell_names[] = {"FILLER_BLANK_6t122", "sram_cell_6t_122", "dummy_sram_6t122", "tapcell_sram_6t122",
                                "dummy_topbot_v1", "dummy_topbot_v2", "FILLER_cgedge", "iocolgrp_sram_6t122_v2"};
    // Sub-cells only needed to build a deeper-mux io_colgrp (num_rows_per_mux != 4).
    // Missing = non-fatal (the fixed 4:1 iocolgrp path does not use them).
    const char* opt_cell_names[] = {"sram_prech_ymux_6t112_v1", "sram_prech_ymux_6t112_v2",
                                    "senseamp_sram_6t122", "write_draslatch_02", "srlatch_sram_6t122",
                                    "dummy_vertical_6t122", "dummy_vertical_array_X64"};
    for (const char* name : cell_names) {
        gdstk::Cell* cell = sram_lib.get_cell(name);
        if (cell == nullptr) {
            LOGW << "Cannot find cell '" << name << "' in SRAM library!";
            return false;
        } else {
            LOGI << "Found cell: " << name;
            if (strcmp(name, "FILLER_BLANK_6t122") == 0) {
                sram_cells_.filler = cell;
            } else if (strcmp(name, "sram_cell_6t_122") == 0) {
                sram_cells_.bitcell = cell;
            } else if (strcmp(name, "dummy_sram_6t122") == 0) {
                sram_cells_.dummy = cell;
            } else if (strcmp(name, "tapcell_sram_6t122") == 0) {
                sram_cells_.tapcell = cell;
            } else if (strcmp(name, "dummy_topbot_v1") == 0) {
                sram_cells_.dummy_v1 = cell;
            } else if (strcmp(name, "dummy_topbot_v2") == 0) {
                sram_cells_.dummy_v2 = cell;
            } else if (strcmp(name, "FILLER_cgedge") == 0) {
                sram_cells_.cgedge = cell;
            } else if (strcmp(name, "iocolgrp_sram_6t122_v2") == 0) {
                sram_cells_.io_colgrp = cell;
            }
        }
    }

    // Optional sub-cells for the deeper-mux io_colgrp builder (non-fatal).
    for (const char* name : opt_cell_names) {
        gdstk::Cell* cell = sram_lib.get_cell(name);
        if (cell == nullptr) {
            LOGW << "Optional mux sub-cell '" << name << "' not found (deeper-mux io_colgrp unavailable)";
            continue;
        }
        if (strcmp(name, "sram_prech_ymux_6t112_v1") == 0) sram_cells_.ymux_leg_v1 = cell;
        else if (strcmp(name, "sram_prech_ymux_6t112_v2") == 0) sram_cells_.ymux_leg_v2 = cell;
        else if (strcmp(name, "senseamp_sram_6t122") == 0) sram_cells_.senseamp = cell;
        else if (strcmp(name, "write_draslatch_02") == 0) sram_cells_.write_drv = cell;
        else if (strcmp(name, "srlatch_sram_6t122") == 0) sram_cells_.srlatch = cell;
        else if (strcmp(name, "dummy_vertical_6t122") == 0) sram_cells_.dummy_vertical = cell;
        else if (strcmp(name, "dummy_vertical_array_X64") == 0) sram_cells_.dummy_vertical_row = cell;
    }

    return true;
}

bool LayoutGenerator::create_sram_column() {
    int num_bits = cli_options_.num_wls;

    // Get SRAM cell size
    OpenFinRAM::CellSize cell_size = OpenFinRAM::get_cell_size(sram_cells_.bitcell, layer_map_);
    LOGD << cell_size.height;
    
    if (!cell_size.valid) {
        LOGW << "Cannot get SRAM cell size from BOUNDARY, using bounding box";
        gdstk::Vec2 bb_min, bb_max;
        sram_cells_.bitcell->bounding_box(bb_min, bb_max);
        cell_size.min = bb_min;
        cell_size.max = bb_max;
        cell_size.width = bb_max.x - bb_min.x;
        cell_size.height = bb_max.y - bb_min.y;
        cell_size.valid = true;
    }
    
    double cell_width = cell_size.width;
    double cell_height = cell_size.height;
    
    LOGI << "Creating SRAM column with " << num_bits << " bits";
    LOGI << "SRAM cell size: " << cell_width << " x " << cell_height;
    
    // 計算每組的數量（正著擺和翻轉各一半）
    uint64_t num_normal = num_bits / 2;
    uint64_t num_flipped = num_bits / 2;
    
    // 建立 cell 名稱: sramcol_x{bit}
    char cell_name[64];
    snprintf(cell_name, sizeof(cell_name), "sramcol_x%lu", (unsigned long)num_bits);
    
    // 建立新的 Cell
    gdstk::Cell* column_cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
    column_cell->init(cell_name);
    
    // ========================================================================
    // 放置正著擺的 cells（位於 x = 0, 2w, 4w, ...）
    // ========================================================================
    gdstk::Reference* ref_normal = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
    ref_normal->init(sram_cells_.bitcell);
    ref_normal->origin = {0.0 - cell_size.min.x, 0.0 - cell_size.min.y};
    ref_normal->magnification = 1.0;
    
    if (num_normal > 1) {
        ref_normal->repetition.type = gdstk::RepetitionType::Rectangular;
        ref_normal->repetition.columns = num_normal;
        ref_normal->repetition.rows = 1;
        ref_normal->repetition.spacing = {2.0 * cell_width, 0.0};  // 間距為 2 倍寬度（水平方向）
    }
    
    column_cell->reference_array.append(ref_normal);
    
    // ========================================================================
    // 放置 Y 軸翻轉的 cells（位於 x = w, 3w, 5w, ...）
    // Y 軸翻轉 = x_reflection = true，然後旋轉 180 度
    // 或者用 magnification = -1 on x (但 gdstk 用 x_reflection)
    // ========================================================================
    gdstk::Reference* ref_flipped = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
    ref_flipped->init(sram_cells_.bitcell);
    // Y 軸翻轉（mirror about Y axis）：需要 rotation = 180 度 + x_reflection
    // 或者直接用負的 magnification（但 gdstk 不支援）
    // 實際上 Y 軸鏡射 = 先 x_reflection 再旋轉 180 度
    // 但更簡單的方式：直接設定 origin 並翻轉
    // 
    // Y 軸翻轉後，cell 的右邊變成左邊
    // 要讓翻轉後的 cell 左邊界對齊 x = cell_width，需要調整 origin
    ref_flipped->origin = {2.0 * cell_width - cell_size.min.x, 0.0 - cell_size.min.y};
    ref_flipped->rotation = M_PI;  // 旋轉 180 度
    ref_flipped->x_reflection = true;
    ref_flipped->magnification = 1.0;
    
    if (num_flipped > 1) {
        ref_flipped->repetition.type = gdstk::RepetitionType::Rectangular;
        ref_flipped->repetition.columns = num_flipped;
        ref_flipped->repetition.rows = 1;
        ref_flipped->repetition.spacing = {2.0 * cell_width, 0.0};  // 間距為 2 倍寬度（水平方向）
    }
    
    column_cell->reference_array.append(ref_flipped);
    
    // ========================================================================
    // 計算 SRAM bitcell 總寬度
    // ========================================================================
    double sram_total_width = num_bits * cell_width;
    double current_x = sram_total_width;  // 下一個 cell 的 x 位置
    
    // ========================================================================
    // 放置 dummy_cell（在 SRAM row 最右邊）
    // ========================================================================
    double dummy_width = 0.0;
    if (sram_cells_.dummy != nullptr) {
        OpenFinRAM::CellSize dummy_size = OpenFinRAM::get_cell_size(sram_cells_.dummy, layer_map_);
        if (!dummy_size.valid) {
            gdstk::Vec2 bb_min, bb_max;
            sram_cells_.dummy->bounding_box(bb_min, bb_max);
            dummy_size.min = bb_min;
            dummy_size.max = bb_max;
            dummy_size.width = bb_max.x - bb_min.x;
            dummy_size.height = bb_max.y - bb_min.y;
        }
        dummy_width = dummy_size.width;
        
        gdstk::Reference* ref_dummy = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
        ref_dummy->init(sram_cells_.dummy);
        ref_dummy->origin = {current_x - dummy_size.min.x, 0.0 - dummy_size.min.y};
        ref_dummy->magnification = 1.0;
        column_cell->reference_array.append(ref_dummy);
        
        LOGI << "  Added dummy_cell at x = " << current_x << " (width: " << dummy_width << ")";
        current_x += dummy_width;
    }
    
    // ========================================================================
    // 放置 tapcell（在 dummy 右邊）
    // ========================================================================
    double tapcell_width = 0.0;
    if (sram_cells_.tapcell != nullptr) {
        OpenFinRAM::CellSize tapcell_size = OpenFinRAM::get_cell_size(sram_cells_.tapcell, layer_map_);
        if (!tapcell_size.valid) {
            gdstk::Vec2 bb_min, bb_max;
            sram_cells_.tapcell->bounding_box(bb_min, bb_max);
            tapcell_size.min = bb_min;
            tapcell_size.max = bb_max;
            tapcell_size.width = bb_max.x - bb_min.x;
            tapcell_size.height = bb_max.y - bb_min.y;
        }
        tapcell_width = tapcell_size.width;
        
        gdstk::Reference* ref_tapcell = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
        ref_tapcell->init(sram_cells_.tapcell);
        ref_tapcell->origin = {current_x - tapcell_size.min.x, 0.0 - tapcell_size.min.y};
        ref_tapcell->magnification = 1.0;
        column_cell->reference_array.append(ref_tapcell);
        
        LOGI << "  Added tapcell at x = " << current_x << " (width: " << tapcell_width << ")";
        current_x += tapcell_width;
    }
    
    // ========================================================================
    // 計算整體尺寸並加入 BOUNDARY
    // ========================================================================
    double total_width = current_x;  // 包含 dummy 和 tapcell
    
    gdstk::Vec2 boundary_min = {0.0, 0.0};
    gdstk::Vec2 boundary_max = {total_width, cell_height};
    gdstk::Polygon* boundary = OpenFinRAM::create_boundary_polygon(boundary_min, boundary_max, layer_map_);
    column_cell->polygon_array.append(boundary);
    
    // ========================================================================
    // 加入 WL (Word Line) Pins
    // 
    // WL 在 SRAM cell 中的資訊（從 sram_cell_6t_122 取得）:
    // - Label: 'WL' at (0.0555, -0.028) layer=30 (M3) texttype=251
    // 
    // 在 sramcol 中，每個 bitcell 有一個獨立的 WL
    // Pin 使用 label 在 pin layer (layer 30, datatype 251)，不繪製矩形
    // ========================================================================
    
    // 取得 M3 pin layer tag (layer 30, datatype 251)
    const OpenFinRAM::LayerDef* m3_pin_layer = layer_map_.get_layer("M3", OpenFinRAM::LayerPurpose::Pin);
    
    if (m3_pin_layer == nullptr) {
        LOGW << "Cannot find M3 pin layer definition, skipping WL pins";
    } else {
        LOGI << "  Adding " << num_bits << " WL pins (WL[0] to WL[" << (num_bits-1) << "])";
        
        const double wl_y = -0.028;  // WL 的 y 位置（從 sram_cell）
        
        for (uint64_t i = 0; i < num_bits; ++i) {
            // 計算這個 bitcell 的中心 x 位置
            double wl_x = (i + 0.5) * cell_width;
            
            // 建立 WL[i] pin label（使用 pin layer）
            char wl_name[32];
            snprintf(wl_name, sizeof(wl_name), "WL[%lu]", (unsigned long)i);
            
            gdstk::Label* wl_label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
            wl_label->init(wl_name);
            wl_label->origin = {wl_x, wl_y};
            wl_label->tag = gdstk::make_tag(m3_pin_layer->layer_number, m3_pin_layer->datatype);
            wl_label->magnification = 0.02; 
            column_cell->label_array.append(wl_label);
        }
        
        LOGI << "  Added " << num_bits << " WL pins on M3 pin layer (" 
             << m3_pin_layer->layer_number << ", " << m3_pin_layer->datatype << ")";
    }
    
    // ========================================================================
    // 加入 BL, BLN, VDD, VSS Pins (在 dummy_sram_6t122 位置)
    // 
    // 這些 pins 是整個 sramcol 共用的，所以只在 dummy_sram_6t122 位置打一個 pin
    // Pin 使用 label 在 pin layer (layer 20, datatype 251)，不繪製矩形
    // 
    // 從 dummy_sram_6t122 取得的 label 位置：
    // - BLN: (0.0175, 0.086) layer=20 (M2)
    // - vdd!: (0.082, 0.135) layer=20 (M2)
    // - vss!: (0.0735, 0.0365) layer=20 (M2) - 底部
    // - vss!: (0.0735, 0.235) layer=20 (M2) - 頂部
    // ========================================================================
    
    const OpenFinRAM::LayerDef* m2_pin_layer = layer_map_.get_layer("M2", OpenFinRAM::LayerPurpose::Pin);
    
    if (m2_pin_layer == nullptr) {
        LOGW << "Cannot find M2 pin layer definition, skipping BL/BLN/VDD/VSS pins";
    } else if (sram_cells_.dummy != nullptr) {
        double sram_total_width = num_bits * cell_width;  // sram_cells_.dummy 的 x 位置
        
        LOGI << "  Adding BL/BLN/VDD/VSS pins at dummy_sram position (x=" << sram_total_width << ")";
        
        // 從 dummy_sram_6t122 的 label 位置（相對於 dummy cell）
        struct PinInfo {
            const char* name;
            double x_offset;  // 相對於 dummy_cell 左邊界的 x offset
            double y;         // y 座標
        };
        
        PinInfo pins[] = {
            {"BLN", 0.0175, 0.086},      // BLN pin
            {"VDD", 0.082, 0.135},       // VDD pin
            {"VSS", 0.0735, 0.0365},     // VSS pin (底部)
            {"VSS", 0.0735, 0.235},      // VSS pin (頂部)
        };
        
        for (const auto& pin_info : pins) {
            // 計算 pin 在 sramcol 中的絕對位置
            double pin_x = sram_total_width + pin_info.x_offset;
            double pin_y = pin_info.y;
            
            // 建立 pin label（使用 pin layer）
            gdstk::Label* label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
            label->init(pin_info.name);
            label->origin = {pin_x, pin_y};
            label->tag = gdstk::make_tag(m2_pin_layer->layer_number, m2_pin_layer->datatype);
            label->magnification = 0.02;
            column_cell->label_array.append(label);
        }
        
        LOGI << "  Added BL/BLN/VDD/VSS pins on M2 pin layer (" 
             << m2_pin_layer->layer_number << ", " << m2_pin_layer->datatype << ")";
    }
    
    LOGI << "Created SRAM column '" << cell_name << "'";
    LOGI << "  Normal cells: " << num_normal << " (at x = 0, 2w, 4w, ...)";
    LOGI << "  Flipped cells: " << num_flipped << " (at x = w, 3w, 5w, ...)";
    LOGI << "  Dummy cell: " << (sram_cells_.dummy ? sram_cells_.dummy->name : "none");
    LOGI << "  Tapcell: " << (sram_cells_.tapcell ? sram_cells_.tapcell->name : "none");
    LOGI << "  Total size: " << total_width << " x " << cell_height;

    sram_cells_.sram_column = column_cell;
    
    return true;
}

bool LayoutGenerator::create_sram_array() {
    int num_rows = cli_options_.num_rows_per_mux;  // Use parameterized multiplexing factor
    if (sram_cells_.sram_column == nullptr || num_rows == 0) {
        LOGE << "Invalid parameters for create_sram_array";
        return false;
    }
    
    // 取得 sramcol 尺寸
    OpenFinRAM::CellSize col_size = OpenFinRAM::get_cell_size(sram_cells_.sram_column, layer_map_);
    
    if (!col_size.valid) {
        LOGW << "Cannot get SRAM column size from BOUNDARY, using bounding box";
        gdstk::Vec2 bb_min, bb_max;
        sram_cells_.sram_column->bounding_box(bb_min, bb_max);
        col_size.min = bb_min;
        col_size.max = bb_max;
        col_size.width = bb_max.x - bb_min.x;
        col_size.height = bb_max.y - bb_min.y;
        col_size.valid = true;
    }
    
    double col_width = col_size.width;
    double col_height = col_size.height;
    
    // 取得 dummy_topbot_v1 尺寸
    double dummy_v1_width = 0.0;
    OpenFinRAM::CellSize dummy_v1_size = {};
    if (sram_cells_.dummy_v1 != nullptr) {
        dummy_v1_size = OpenFinRAM::get_cell_size(sram_cells_.dummy_v1, layer_map_);
        if (!dummy_v1_size.valid) {
            gdstk::Vec2 bb_min, bb_max;
            sram_cells_.dummy_v1->bounding_box(bb_min, bb_max);
            dummy_v1_size.min = bb_min;
            dummy_v1_size.max = bb_max;
            dummy_v1_size.width = bb_max.x - bb_min.x;
            dummy_v1_size.height = bb_max.y - bb_min.y;
            dummy_v1_size.valid = true;
        }
        dummy_v1_width = dummy_v1_size.width;
        LOGI << "dummy_topbot_v1 size: " << dummy_v1_size.width << " x " << dummy_v1_size.height;
    }
    
    // 取得 dummy_topbot_v2 尺寸
    double dummy_v2_width = 0.0;
    OpenFinRAM::CellSize dummy_v2_size = {};
    if (sram_cells_.dummy_v2 != nullptr) {
        dummy_v2_size = OpenFinRAM::get_cell_size(sram_cells_.dummy_v2, layer_map_);
        if (!dummy_v2_size.valid) {
            gdstk::Vec2 bb_min, bb_max;
            sram_cells_.dummy_v2->bounding_box(bb_min, bb_max);
            dummy_v2_size.min = bb_min;
            dummy_v2_size.max = bb_max;
            dummy_v2_size.width = bb_max.x - bb_min.x;
            dummy_v2_size.height = bb_max.y - bb_min.y;
            dummy_v2_size.valid = true;
        }
        dummy_v2_width = dummy_v2_size.width;
        LOGI << "dummy_topbot_v2 size: " << dummy_v2_size.width << " x " << dummy_v2_size.height;
    }
    
    // 計算 dummy 的最大寬度（用於計算整體偏移）
    double max_dummy_width = std::max(dummy_v1_width, dummy_v2_width);
    
    LOGI << "Creating SRAM array with " << num_rows << " rows";
    LOGI << "SRAM column size: " << col_width << " x " << col_height;
    LOGI << "Max dummy width: " << max_dummy_width;
    
    // 建立 cell 名稱: array_x{bits}x{rows}
    // 從 sramcol 名稱中提取 bits 數量
    const char* col_name = sram_cells_.sram_column->name;
    int bits = 0;
    const char* x_pos = strstr(col_name, "_x");
    if (x_pos != nullptr) {
        bits = atoi(x_pos + 2);
    }
    
    char array_name[64];
    snprintf(array_name, sizeof(array_name), "array_x%dx%lu", (unsigned long)num_rows, (unsigned long)bits);
    
    // 建立新的 Cell
    gdstk::Cell* array_cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
    array_cell->init(array_name);
    
    // sramcol 的 X 起始位置（在 dummy 右邊）
    double sramcol_x_offset = max_dummy_width;
    
    // ========================================================================
    // 逐層放置 sramcol 和對應的 dummy
    // - 偶數層 (0, 2, 4, ...): 正著擺，加入 dummy_topbot_v1 (Y軸翻轉)
    // - 奇數層 (1, 3, 5, ...): X 軸翻轉，加入 dummy_topbot_v2 (旋轉180度)
    // ========================================================================
    for (uint64_t row = 0; row < num_rows; row++) {
        double y_pos = row * col_height;
        
        // 放置 sramcol
        gdstk::Reference* ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
        ref->init(sram_cells_.sram_column);
        ref->magnification = 1.0;
        
        if (row % 2 == 0) {
            // 偶數層：正著擺
            ref->origin = {sramcol_x_offset - col_size.min.x, y_pos - col_size.min.y};
            ref->x_reflection = false;
            LOGI << "  Row " << row << ": sramcol normal at x=" << sramcol_x_offset << ", y=" << y_pos;
            
            // 放置 dummy_topbot_v1 在最左邊 (Y軸翻轉: rotation = PI + x_reflection = true)
            if (sram_cells_.dummy_v1 != nullptr) {
                gdstk::Reference* dummy_ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
                dummy_ref->init(sram_cells_.dummy_v1);
                // Y軸翻轉後，cell 的右邊變左邊，需要調整 x 座標
                dummy_ref->origin = {dummy_v1_size.width - dummy_v1_size.min.x, y_pos - dummy_v1_size.min.y};
                dummy_ref->magnification = 1.0;
                dummy_ref->rotation = M_PI;  // 旋轉 180 度
                dummy_ref->x_reflection = true;  // 加上 x_reflection = Y軸翻轉
                array_cell->reference_array.append(dummy_ref);
                LOGI << "  Row " << row << ": dummy_topbot_v1 (Y-flipped) at x=0, y=" << y_pos;
            }
        } else {
            // 奇數層：X 軸翻轉
            ref->origin = {sramcol_x_offset - col_size.min.x, y_pos + col_height - col_size.min.y};
            ref->x_reflection = true;
            LOGI << "  Row " << row << ": sramcol X-flipped at x=" << sramcol_x_offset << ", y=" << y_pos;
            
            // 放置 dummy_topbot_v2 在最左邊 (旋轉 180 度)
            if (sram_cells_.dummy_v2 != nullptr) {
                gdstk::Reference* dummy_ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
                dummy_ref->init(sram_cells_.dummy_v2);
                // 旋轉 180 度後，需要調整 origin 使左下角對齊
                // 因為旋轉 180 度，Y 座標需要下調 1 倍 cell height
                dummy_ref->origin = {dummy_v2_size.width - dummy_v2_size.min.x, 
                                     y_pos - dummy_v2_size.min.y};
                dummy_ref->magnification = 1.0;
                dummy_ref->rotation = M_PI;  // 旋轉 180 度
                dummy_ref->x_reflection = true;
                array_cell->reference_array.append(dummy_ref);
                LOGI << "  Row " << row << ": dummy_topbot_v2 (180° rotated) at x=0, y=" << y_pos;
            }
        }
        
        array_cell->reference_array.append(ref);
    }
    
    // ========================================================================
    // 計算整體尺寸並加入 BOUNDARY
    // ========================================================================
    double total_width = max_dummy_width + col_width;
    double total_height = num_rows * col_height;
    
    gdstk::Vec2 boundary_min = {0.0, 0.0};
    gdstk::Vec2 boundary_max = {total_width, total_height};
    gdstk::Polygon* boundary = OpenFinRAM::create_boundary_polygon(boundary_min, boundary_max, layer_map_);
    array_cell->polygon_array.append(boundary);
    
    // ========================================================================
    // 加入 Pins for array_x{bit}x{rows}
    // 
    // 1. WL pins: 只在最下面的 row (row 0)，數量等於 bits
    //    - 從 sramcol 的 dummy_sram 位置推導
    //    - sramcol 中已經有 WL[0]~WL[bits-1]
    //    - 在 array 中，這些 WL 的位置需要加上 sramcol_x_offset
    // 
    // 2. BL/BLN pins: 每個 row 都有，所以有 num_rows 條
    //    - BL[0]~BL[num_rows-1], BLN[0]~BLN[num_rows-1]
    //    - 位置在每個 row 對應的 dummy_sram 位置
    // 
    // 3. VDD/VSS pins: 在 dummy_sram 位置（最下面的 row）
    // ========================================================================
    
    // 取得 M3 pin layer (for WL) 和 M2 pin layer (for BL/BLN/VDD/VSS)
    const OpenFinRAM::LayerDef* m3_pin_layer = layer_map_.get_layer("M3", OpenFinRAM::LayerPurpose::Pin);
    const OpenFinRAM::LayerDef* m2_pin_layer = layer_map_.get_layer("M2", OpenFinRAM::LayerPurpose::Pin);
    
    // === WL Pins (只在 row 0) ===
    if (m3_pin_layer != nullptr && bits > 0) {
        LOGI << "  Adding " << bits << " WL pins (WL[0] to WL[" << (bits-1) << "]) at row 0";
        
        const double wl_y = -0.028;  // WL 的 y 位置（從 sram_cell）
        const double cell_width = col_width / (bits + 1.216);  // 粗略估計 bitcell 寬度
        // 更精確的方式：從 sramcol 名稱推導
        // sramcol_x{bits} 包含 bits 個 SRAM cells + 1 dummy + 1 tapcell
        // SRAM cell width = cli_options_.bitcell_width, dummy = cli_options_.bitcell_width, tapcell = cli_options_.bitcell_width
        const double sram_cell_width = cli_options_.bitcell_width;
        
        for (int i = 0; i < bits; ++i) {
            // WL[i] 的 x 位置：sramcol_x_offset + (i + 0.5) * sram_cell_width
            double wl_x = sramcol_x_offset + (i + 0.5) * sram_cell_width;
            
            char wl_name[32];
            snprintf(wl_name, sizeof(wl_name), "WL[%d]", i);
            
            gdstk::Label* wl_label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
            wl_label->init(wl_name);
            wl_label->origin = {wl_x, wl_y};
            wl_label->tag = gdstk::make_tag(m3_pin_layer->layer_number, m3_pin_layer->datatype);
            wl_label->magnification = 0.02;
            array_cell->label_array.append(wl_label);
        }
        
        LOGI << "  Added " << bits << " WL pins on M3 pin layer";
    }
    
    // === BL/BLN Pins (每個 row 都有) ===
    if (m2_pin_layer != nullptr) {
        LOGI << "  Adding " << num_rows << " BL/BLN pin pairs (BL[0:" << (num_rows-1) 
             << "], BLN[0:" << (num_rows-1) << "])";
        
        // BLN 和 BL 在 dummy_sram_6t122 中的原始座標（相對於 dummy_sram 的原點）
        const double bln_x_in_dummy = 0.0175;  // dummy_sram 內的 x
        const double bln_y_in_dummy = 0.086;   // dummy_sram 內的 y
        
        // BL 在 sram_cell 中的位置（從 sram_cell_6t122 取得）
        const double bl_x_in_cell = 0.13;      // sram_cell 內的 x
        const double bl_y_in_cell = 0.1885;    // sram_cell 內的 y
        
        // dummy_sram 在 sramcol 中的位置
        const double dummy_x_in_col = bits * cli_options_.bitcell_width;  // sramcol 內，dummy 的 x 起點
        
        // BLN 相對於 sramcol 原點的座標
        const double bln_x_in_col = dummy_x_in_col + bln_x_in_dummy;
        const double bln_y_in_col = bln_y_in_dummy;
        
        // BL 在第一個 bitcell 中（x=0 開始）
        const double bl_x_in_col = bl_x_in_cell;
        const double bl_y_in_col = bl_y_in_cell;
        
        for (uint64_t row = 0; row < num_rows; ++row) {
            double bln_pin_x, bln_pin_y;
            double bl_pin_x, bl_pin_y;
            
            if (row % 2 == 0) {
                // 偶數 row (0, 2, ...): sramcol 正常放置
                // 排列：BL[row] 在下方，BLN[row] 在上方
                bln_pin_x = sramcol_x_offset + bln_x_in_col;
                bln_pin_y = row * col_height + bln_y_in_col;
                
                bl_pin_x = sramcol_x_offset + bl_x_in_col;
                bl_pin_y = row * col_height + bl_y_in_col;
                
                LOGI << "  Row " << row << " (normal): BL at (" << bl_pin_x << ", " << bl_pin_y 
                     << "), BLN at (" << bln_pin_x << ", " << bln_pin_y << ")";
            } else {
                // 奇數 row (1, 3, ...): sramcol X 軸翻轉
                // 排列：BLN[row] 在下方，BL[row] 在上方
                bln_pin_x = sramcol_x_offset + bln_x_in_col;
                bln_pin_y = (row + 1) * col_height - bln_y_in_col;
                
                bl_pin_x = sramcol_x_offset + bl_x_in_col;
                bl_pin_y = (row + 1) * col_height - bl_y_in_col;
                
                LOGI << "  Row " << row << " (X-flipped): BLN at (" << bln_pin_x << ", " << bln_pin_y 
                     << "), BL at (" << bl_pin_x << ", " << bl_pin_y << ")";
            }
            
            // BLN[row]
            char bln_name[32];
            snprintf(bln_name, sizeof(bln_name), "BLN[%lu]", (unsigned long)row);
            
            gdstk::Label* bln_label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
            bln_label->init(bln_name);
            bln_label->origin = {bln_pin_x, bln_pin_y};
            bln_label->tag = gdstk::make_tag(m2_pin_layer->layer_number, m2_pin_layer->datatype);
            bln_label->magnification = 0.02;
            array_cell->label_array.append(bln_label);
            
            // BL[row]
            char bl_name[32];
            snprintf(bl_name, sizeof(bl_name), "BL[%lu]", (unsigned long)row);
            
            gdstk::Label* bl_label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
            bl_label->init(bl_name);
            bl_label->origin = {bl_pin_x, bl_pin_y};
            bl_label->tag = gdstk::make_tag(m2_pin_layer->layer_number, m2_pin_layer->datatype);
            bl_label->magnification = 0.02;
            array_cell->label_array.append(bl_label);
        }
        
        LOGI << "  Added " << (num_rows * 2) << " BL/BLN pins on M2 pin layer";
        
        // === VDD/VSS Pins (在最下面的 row，dummy_sram 位置) ===
        const double vdd_x_offset = bits * cli_options_.bitcell_width + 0.082;   // dummy_sram 位置 + VDD offset
        const double vss_x_offset = bits * cli_options_.bitcell_width + 0.0735;  // dummy_sram 位置 + VSS offset
        const double vdd_y = 0.135;
        const double vss_y_bottom = 0.0365;
        const double vss_y_top = 0.235;
        
        // VDD pin
        gdstk::Label* vdd_label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
        vdd_label->init("VDD");
        vdd_label->origin = {sramcol_x_offset + vdd_x_offset, vdd_y};
        vdd_label->tag = gdstk::make_tag(m2_pin_layer->layer_number, m2_pin_layer->datatype);
        vdd_label->magnification = 0.02;
        array_cell->label_array.append(vdd_label);
        
        // VSS pin (底部)
        gdstk::Label* vss_label_bottom = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
        vss_label_bottom->init("VSS");
        vss_label_bottom->origin = {sramcol_x_offset + vss_x_offset, vss_y_bottom};
        vss_label_bottom->tag = gdstk::make_tag(m2_pin_layer->layer_number, m2_pin_layer->datatype);
        vss_label_bottom->magnification = 0.02;
        array_cell->label_array.append(vss_label_bottom);
        
        // VSS pin (頂部) 
        gdstk::Label* vss_label_top = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
        vss_label_top->init("VSS");
        vss_label_top->origin = {sramcol_x_offset + vss_x_offset, vss_y_top};
        vss_label_top->tag = gdstk::make_tag(m2_pin_layer->layer_number, m2_pin_layer->datatype);
        vss_label_top->magnification = 0.02;
        array_cell->label_array.append(vss_label_top);
        
        LOGI << "  Added VDD and VSS pins on M2 pin layer";
    }

    sram_cells_.array_cell = array_cell;
    
    LOGI << "Created SRAM array '" << array_name << "'";
    LOGI << "  Rows: " << num_rows;
    LOGI << "  Total size: " << total_width << " x " << total_height;
    
    return true;
}

gdstk::Cell* LayoutGenerator::create_wrasst_ymux(int mux) {
    if (sram_cells_.ymux_leg_v1 == nullptr || sram_cells_.ymux_leg_v2 == nullptr) {
        LOGW << "ymux legs unavailable; cannot build wrasst_prech_ymux_x" << mux;
        return nullptr;
    }
    // Legs stack vertically; adjacent legs abut so their M2 sa/san segments form
    // one shared rail (the extracted x4 wrapper has no pins of its own -> pure
    // abutment). Pitch/offset are taken from the reference 4:1 cell.
    const double kLegPitch = 0.2703;  // um: (0.824 - 0.013) / 3 from x4
    const double kY0 = 0.013;
    char name[80];
    snprintf(name, sizeof(name), "wrasst_prech_ymux_x%d_sram_6t122_v2", mux);
    gdstk::Cell* cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
    cell->init(name);
    for (int i = 0; i < mux; ++i) {
        gdstk::Cell* leg = (i % 2 == 0) ? sram_cells_.ymux_leg_v1 : sram_cells_.ymux_leg_v2;
        gdstk::Reference* ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
        ref->init(leg);
        ref->origin = {0.0, kY0 + i * kLegPitch};
        ref->magnification = 1.0;
        cell->reference_array.append(ref);
    }
    sram_lib.cell_array.append(cell);
    LOGI << "Built " << name << " (" << mux << " tiled legs, height ~"
         << (mux * kLegPitch) << " um)";
    return cell;
}

gdstk::Cell* LayoutGenerator::create_muxed_iocolgrp(int mux) {
    if (mux == 4 || sram_cells_.senseamp == nullptr || sram_cells_.write_drv == nullptr) {
        return sram_cells_.io_colgrp;  // use the extracted 4:1 cell
    }
    gdstk::Cell* ymux = create_wrasst_ymux(mux);
    if (ymux == nullptr) return sram_cells_.io_colgrp;

    // Geometry mirrors iocolgrp_sram_6t122_v2: two ymuxes (top blt @ x=0, bottom
    // blb @ x=RIGHT) sharing one central sense amp / write driver / srlatch.
    const double kRightYmuxX = 2.376;  // from the reference cell
    char name[64];
    snprintf(name, sizeof(name), "iocolgrp_sram_6t122_x%d", mux);
    gdstk::Cell* cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
    cell->init(name);
    auto add_ref = [&](gdstk::Cell* src, double x, double y) {
        gdstk::Reference* r = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
        r->init(src);
        r->origin = {x, y};
        r->magnification = 1.0;
        cell->reference_array.append(r);
    };
    add_ref(ymux, 0.0, 0.0);            // top (blt) mux
    add_ref(ymux, kRightYmuxX, 0.0);    // bottom (blb) mux
    add_ref(sram_cells_.srlatch, 1.026, 0.013);
    add_ref(sram_cells_.write_drv, 0.486, 0.418);
    add_ref(sram_cells_.senseamp, 0.486, 0.446);
    sram_lib.cell_array.append(cell);
    LOGW << "Built best-effort deeper-mux io_colgrp '" << name
         << "' (mux=" << mux << "). NOTE: leg tiling + shared sa/san rail is by "
         << "abutment, but ysel[4..] distribution and cross-ymux sa/san routing "
         << "are NOT yet drawn -> requires LVS validation before use.";
    return cell;
}

bool LayoutGenerator::create_colgrp() {
    // Deeper column mux (num_rows_per_mux != 4): swap in a procedurally built
    // io_colgrp before laying out the colgrp. Falls back to the extracted 4:1.
    if (cli_options_.num_rows_per_mux != 4) {
        gdstk::Cell* muxed = create_muxed_iocolgrp(cli_options_.num_rows_per_mux);
        if (muxed != nullptr) sram_cells_.io_colgrp = muxed;
    }
    if (sram_cells_.array_cell == nullptr || sram_cells_.cgedge == nullptr || sram_cells_.io_colgrp == nullptr) {
        LOGE << "Invalid parameters for create_colgrp";
        return false;
    }
    
    // 取得各 cell 尺寸
    OpenFinRAM::CellSize array_size = OpenFinRAM::get_cell_size(sram_cells_.array_cell, layer_map_);
    OpenFinRAM::CellSize filler_size = OpenFinRAM::get_cell_size(sram_cells_.cgedge, layer_map_);
    OpenFinRAM::CellSize io_size = OpenFinRAM::get_cell_size(sram_cells_.io_colgrp, layer_map_);
    
    if (!array_size.valid) {
        gdstk::Vec2 bb_min, bb_max;
        sram_cells_.array_cell->bounding_box(bb_min, bb_max);
        array_size.min = bb_min;
        array_size.max = bb_max;
        array_size.width = bb_max.x - bb_min.x;
        array_size.height = bb_max.y - bb_min.y;
        array_size.valid = true;
    }
    
    if (!filler_size.valid) {
        gdstk::Vec2 bb_min, bb_max;
        sram_cells_.cgedge->bounding_box(bb_min, bb_max);
        filler_size.min = bb_min;
        filler_size.max = bb_max;
        filler_size.width = bb_max.x - bb_min.x;
        filler_size.height = bb_max.y - bb_min.y;
        filler_size.valid = true;
    }
    
    if (!io_size.valid) {
        gdstk::Vec2 bb_min, bb_max;
        sram_cells_.io_colgrp->bounding_box(bb_min, bb_max);
        io_size.min = bb_min;
        io_size.max = bb_max;
        io_size.width = bb_max.x - bb_min.x;
        io_size.height = bb_max.y - bb_min.y;
        io_size.valid = true;
    }
    
    int bits = cli_options_.num_data_bits;
    LOGI << "Creating column group with " << bits << " bits per array";
    LOGI << "  SRAM array size: " << array_size.width << " x " << array_size.height;
    LOGI << "  FILLER_cgedge size: " << filler_size.width << " x " << filler_size.height;
    LOGI << "  io_colgrp size: " << io_size.width << " x " << io_size.height;
    
    // 建立 cell 名稱: colgrp_x{num_wls}x{num_data_bits/2}
    char colgrp_name[64];
    snprintf(colgrp_name, sizeof(colgrp_name), "colgrp_x%dx%d", cli_options_.num_wls, cli_options_.num_data_bits);
    
    // 建立新的 Cell
    gdstk::Cell* colgrp_cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
    colgrp_cell->init(colgrp_name);
    
    double current_x = 0.0;
    double cell_height = array_size.height;  // 使用 array 的高度作為整體高度
    
    // 記錄 iocolgrp 的起始 x 座標（用於後續 via 計算）
    double left_array_x_start = filler_size.width;
    double iocolgrp_x_start = left_array_x_start + array_size.width;
    
    // ========================================================================
    // 1. 放置左側 FILLER_cgedge
    // ========================================================================
    {
        gdstk::Reference* ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
        ref->init(sram_cells_.cgedge);
        ref->origin = {current_x - filler_size.min.x, 0.0 - filler_size.min.y};
        ref->magnification = 1.0;
        colgrp_cell->reference_array.append(ref);
        LOGI << "  Added left FILLER_cgedge at x=" << current_x;
        current_x += filler_size.width;
    }
    
    // ========================================================================
    // 2. 放置左側 array_x4_x{bit} (正常放置)
    // ========================================================================
    {
        gdstk::Reference* ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
        ref->init(sram_cells_.array_cell);
        ref->origin = {current_x - array_size.min.x, 0.0 - array_size.min.y};
        ref->magnification = 1.0;
        colgrp_cell->reference_array.append(ref);
        LOGI << "  Added left SRAM array at x=" << current_x;
        current_x += array_size.width;
    }
    
    // ========================================================================
    // 3. 放置 iocolgrp_sram_6t122_v2
    // ========================================================================
    {
        gdstk::Reference* ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
        ref->init(sram_cells_.io_colgrp);
        ref->origin = {current_x - io_size.min.x, 0.0 - io_size.min.y + 0.0135};
        ref->magnification = 1.0;
        colgrp_cell->reference_array.append(ref);
        LOGI << "  Added io_colgrp at x=" << current_x;
        current_x += io_size.width;
    }
    
    // ========================================================================
    // 4. 放置右側 array_x4_x{bit} (Y軸翻轉)
    // ========================================================================
    {
        gdstk::Reference* ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
        ref->init(sram_cells_.array_cell);
        // Y軸翻轉後，cell 的右邊變左邊，需要調整 x 座標
        // origin.x = current_x + array_width 使翻轉後的左邊界對齊 current_x
        ref->origin = {current_x + array_size.width - array_size.min.x, 0.0 - array_size.min.y};
        ref->magnification = 1.0;
        ref->rotation = M_PI;
        ref->x_reflection = true;
        colgrp_cell->reference_array.append(ref);
        LOGI << "  Added right SRAM array (Y-flipped) at x=" << current_x;
        current_x += array_size.width;
    }
    
    // ========================================================================
    // 5. 放置右側 FILLER_cgedge
    // ========================================================================
    {
        gdstk::Reference* ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
        ref->init(sram_cells_.cgedge);
        ref->origin = {current_x - filler_size.min.x, 0.0 - filler_size.min.y};
        ref->magnification = 1.0;
        colgrp_cell->reference_array.append(ref);
        LOGI << "  Added right FILLER_cgedge at x=" << current_x;
        current_x += filler_size.width;
    }
    
    // ========================================================================
    // 計算整體尺寸並加入 BOUNDARY
    // ========================================================================
    double total_width = current_x;
    
    gdstk::Vec2 boundary_min = {0.0, 0.0};
    gdstk::Vec2 boundary_max = {total_width, cell_height};
    gdstk::Polygon* boundary = OpenFinRAM::create_boundary_polygon(boundary_min, boundary_max, layer_map_);
    colgrp_cell->polygon_array.append(boundary);
    
    // ========================================================================
    // 加入 WL Pins for colgrp_x{bit*2}x4
    // 左側 array: WLT[bits-1:0]
    // 右側 array: WLB[bits-1:0]
    // ========================================================================
    const OpenFinRAM::LayerDef* m3_pin_layer = layer_map_.get_layer("M3", OpenFinRAM::LayerPurpose::Pin);
    
    if (m3_pin_layer != nullptr) {
        LOGI << "  Adding WL pins for colgrp";
        
        // WL 在 sram_cell 中的 y 位置（相對於 cell）
        const double wl_y = -0.028;
        
        // sram_cell 寬度
        const double cell_width = cli_options_.bitcell_width;
        
        // array 中 dummy_topbot 的寬度（在 sramcol 左側）
        const double dummy_width = cli_options_.bitcell_width;
        
        // 左側 array 的起始 x 位置（filler_cgedge 之後）
        const double left_array_x = filler_size.width;
        
        // 左側 array 中 sramcol 的起始 x（需要跳過 dummy_topbot）
        const double left_sramcol_x = left_array_x + dummy_width;
        
        // 右側 array 的起始 x 位置（filler + left_array + io_colgrp 之後）
        const double right_array_x = filler_size.width + array_size.width + io_size.width;
        
        // 右側 array 中 sramcol 的起始 x（Y軸翻轉後，dummy_topbot 在右側）
        // Y軸翻轉後：
        // 1. array 最左側是 dummy_topbot（寬度 cli_options_.bitcell_width）
        // 2. 然後是 sramcol，sramcol 內部左側也有 dummy（寬度 cli_options_.bitcell_width）
        // 3. 所以 bitcell 區域從 right_array_x + cli_options_.bitcell_width + cli_options_.bitcell_width = right_array_x + 0.216 開始
        const double right_sramcol_x = right_array_x + 2 * dummy_width;
        
        // 左側 array: WLT[num_wls-1:0]
        LOGI << "  Adding " << cli_options_.num_wls << " WLT pins (WLT[0] to WLT[" << (cli_options_.num_wls-1) << "])";
        LOGI << "  Left sramcol starts at x=" << left_sramcol_x;
        for (int i = 0; i < cli_options_.num_wls; ++i) {
            // WL 位於每個 bitcell 的中心
            double wl_x = left_sramcol_x + (i + 0.5) * cell_width;
            
            if (i == 0 || i == bits - 1) {
                LOGI << "    WLT[" << i << "] at x=" << wl_x;
            }
            
            char wl_name[32];
            snprintf(wl_name, sizeof(wl_name), "WLT[%d]", i);
            
            gdstk::Label* label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
            label->init(wl_name);
            label->origin = {wl_x, wl_y};
            label->tag = gdstk::make_tag(m3_pin_layer->layer_number, m3_pin_layer->datatype);
            label->magnification = 0.02;
            colgrp_cell->label_array.append(label);
        }
        
        // 右側 array (Y軸翻轉): WLB[num_wls-1:0]
        // Y軸翻轉 = rotation 180° + x_reflection
        // 翻轉後，原本在左側的 bitcell 變到右側
        LOGI << "  Adding " << cli_options_.num_wls << " WLB pins (WLB[0] to WLB[" << (cli_options_.num_wls-1) << "])";
        LOGI << "  Right sramcol starts at x=" << right_sramcol_x;
        for (int i = 0; i < cli_options_.num_wls; ++i) {
            // 右側 array Y軸翻轉後
            // sramcol 寬度（不含 dummy）= num_wls * cell_width + dummy_sram_width + tapcell_width
            // 但 WL 只在 bitcell 區域，所以是 num_wls * cell_width
            const double sramcol_bitcell_width = cli_options_.num_wls * cell_width;
            
            // 原本 bitcell i 在相對座標 (i * cell_width + cell_width/2, 0)
            // Y軸翻轉後：x' = sramcol_bitcell_width - (i+0.5) * cell_width
            double wl_x = right_sramcol_x + sramcol_bitcell_width - (i + 0.5) * cell_width;
            
            if (i == 0 || i == cli_options_.num_wls - 1) {
                LOGI << "    WLB[" << i << "] at x=" << wl_x;
            }
            
            // Y 軸翻轉後，wl_y (-0.028) 變成 array_height - (-0.028) = array_height + 0.028
            double wl_y_flipped = array_size.height + wl_y;
            
            char wl_name[32];
            snprintf(wl_name, sizeof(wl_name), "WLB[%d]", i);
            
            gdstk::Label* label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
            label->init(wl_name);
            label->origin = {wl_x, wl_y_flipped};
            label->tag = gdstk::make_tag(m3_pin_layer->layer_number, m3_pin_layer->datatype);
            label->magnification = 0.02;
            colgrp_cell->label_array.append(label);
        }
        
        LOGI << "  Added " << (cli_options_.num_wls * 2) << " WL pins on M3 pin layer (30, 251)";
    }
    
    // ========================================================================
    // 加入 iocolgrp 的信號 Pins
    // blprechtn, blprechbn, yselt[3:0], yseltn[3:0], yselb[3:0], yselbn[3:0]
    // 這些信號在 iocolgrp_sram_6t122_v2 中，需要加上 iocolgrp 的 x 偏移
    // ========================================================================
    if (m3_pin_layer != nullptr && sram_cells_.io_colgrp != nullptr) {
        LOGI << "  Adding iocolgrp signal pins";
        
        // iocolgrp 在 colgrp 中的 x 起始位置
        const double io_x_offset = filler_size.width + array_size.width;
        
        // 從 iocolgrp cell 中取得所有 M3 pin layer 的 labels
        for (uint64_t i = 0; i < sram_cells_.io_colgrp->label_array.count; ++i) {
            gdstk::Label* orig_label = sram_cells_.io_colgrp->label_array[i];
            uint16_t layer = gdstk::get_layer(orig_label->tag);
            uint16_t datatype = gdstk::get_type(orig_label->tag);
            
            // 只處理 M3 pin layer (30, 251) 的信號
            if (layer == 30 && datatype == 251) {
                const char* name = orig_label->text;
                
                // 檢查是否為我們需要的信號
                bool is_target_signal = false;
                if (strncmp(name, "BLPRECHTN", 9) == 0 || 
                    strncmp(name, "BLPRECHBN", 9) == 0 ||
                    strncmp(name, "yselt<", 6) == 0 ||
                    strncmp(name, "yseltn<", 7) == 0 ||
                    strncmp(name, "yselb<", 6) == 0 ||
                    strncmp(name, "yselbn<", 7) == 0 ||
                    strcasecmp(name, "wrena") == 0 ||
                    // strcasecmp(name, "vsswrite") == 0 ||
                    strcasecmp(name, "saprechn") == 0 ||
                    strcasecmp(name, "sae") == 0 ||
                    strcasecmp(name, "wrenan") == 0 ||
                    strcasecmp(name, "wd") == 0 ||
                    strcasecmp(name, "saob") == 0 || 
                    strcasecmp(name, "oe_n") == 0) {
                    is_target_signal = true;
                }
                
                if (is_target_signal) {
                    // 將 pin 名稱中的 <> 轉換為 []
                    // 並處理特殊重命名：WD -> D, saob -> Q
                    char new_name[64];
                    
                    // 特殊重命名
                    if (strcasecmp(name, "wd") == 0) {
                        strcpy(new_name, "D");
                    } else if (strcasecmp(name, "saob") == 0) {
                        strcpy(new_name, "Q");
                    } else {
                        // 一般處理：<> 轉 []
                        const char* src = name;
                        char* dst = new_name;
                        while (*src && (dst - new_name) < 63) {
                            if (*src == '<') {
                                *dst++ = '[';
                            } else if (*src == '>') {
                                *dst++ = ']';
                            } else {
                                *dst++ = *src;
                            }
                            src++;
                        }
                        *dst = '\0';
                    }
                    
                    // 建立新的 label，位置加上 io_x_offset
                    gdstk::Label* label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                    label->init(new_name);
                    label->origin = {io_x_offset + orig_label->origin.x, orig_label->origin.y};
                    label->tag = orig_label->tag;
                    label->magnification = 0.02;
                    colgrp_cell->label_array.append(label);
                    
                    LOGI << "    Added pin '" << new_name << "' at (" 
                         << label->origin.x << ", " << label->origin.y << ")";
                }
            }
        }
    }
    
    // ========================================================================
    // 加入三根 M3 metal rectangles
    // 位置相對於 iocolgrp 左邊 boundary
    // ========================================================================
    const OpenFinRAM::LayerDef* m3_drawing_layer = layer_map_.get_layer("M3", OpenFinRAM::LayerPurpose::Drawing);

    if (m3_drawing_layer != nullptr) {
        LOGI << "  Adding M3 metal rectangles in iocolgrp region";
        
        // iocolgrp 在 colgrp 中的 x 起始位置
        const double io_x_offset = filler_size.width + array_size.width;
        
        // Metal 參數：相對於 iocolgrp 左邊的 x 偏移、寬度
        const double metal_specs[][2] = {
            {0.531, 0.09},   // Metal 1: x_offset=0.531, width=0.09
            {1.071, 0.026}  // Metal 2: x_offset=1.071, width=0.026
            // {1.224, 0.036}   // Metal 3: x_offset=1.224, width=0.036
        };
        
        // Y 範圍：從 -0.0035 開始，高度 1.114
        const double metal_y_start = -0.0035;
        const double metal_height = 1.114;
        const double metal_y_end = metal_y_start + metal_height;
        
        for (int i = 0; i < 2; ++i) {
            double x_offset_from_io = metal_specs[i][0];
            double width = metal_specs[i][1];
            
            // 計算全局座標
            double x_left = io_x_offset + x_offset_from_io;
            double x_right = x_left + width;
            
            // 建立矩形 polygon
            gdstk::Polygon* rect = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
            gdstk::Vec2 points[4] = {
                {x_left, metal_y_start},
                {x_right, metal_y_start},
                {x_right, metal_y_end},
                {x_left, metal_y_end}
            };
            rect->point_array.extend({.capacity = 0, .count = 4, .items = points});
            rect->tag = gdstk::make_tag(m3_drawing_layer->layer_number, m3_drawing_layer->datatype);
            colgrp_cell->polygon_array.append(rect);
            
            LOGI << "    Added M3 metal " << (i+1) << " at x=[" << x_left << ", " << x_right 
                 << "], y=[" << metal_y_start << ", " << metal_y_end << "], width=" << width;
        }
    }
    
    sram_cells_.colgrp = colgrp_cell;

    LOGI << "Created column group '" << colgrp_name << "'";
    LOGI << "  Total size: " << total_width << " x " << cell_height;
    
    return true;
}

bool LayoutGenerator::create_stacked_colgrp() {
    int num_rows = cli_options_.num_data_bits / 2;
    if (sram_cells_.colgrp == nullptr || num_rows == 0) {
        LOGE << "Invalid parameters for create_stacked_colgrp";
        return false;
    }
    
    // 取得 colgrp 尺寸
    OpenFinRAM::CellSize colgrp_size = OpenFinRAM::get_cell_size(sram_cells_.colgrp, layer_map_);
    
    if (!colgrp_size.valid) {
        LOGW << "Cannot get colgrp size from BOUNDARY, using bounding box";
        gdstk::Vec2 bb_min, bb_max;
        sram_cells_.colgrp->bounding_box(bb_min, bb_max);
        colgrp_size.min = bb_min;
        colgrp_size.max = bb_max;
        colgrp_size.width = bb_max.x - bb_min.x;
        colgrp_size.height = bb_max.y - bb_min.y;
        colgrp_size.valid = true;
    }
    
    double colgrp_width = colgrp_size.width;
    double colgrp_height = colgrp_size.height - 0.0135;
    
    LOGI << "Creating stacked column group with " << num_rows << " rows";
    LOGI << "  colgrp size: " << colgrp_width << " x " << colgrp_height;
    
    // 建立新的 Cell
    char stacked_name[64];
    snprintf(stacked_name, sizeof(stacked_name), "stacked_colgrp_x%dx%lu", cli_options_.num_wls * 2, cli_options_.num_data_bits / 2);
    gdstk::Cell* stacked_cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
    stacked_cell->init(stacked_name);
    
    // 垂直堆疊 colgrp
    // 使用 repetition 來高效堆疊
    gdstk::Reference* ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
    ref->init(sram_cells_.colgrp);
    ref->origin = {0.0 - colgrp_size.min.x, 0.0 - colgrp_size.min.y};
    ref->magnification = 1.0;
    
    if (num_rows > 1) {
        ref->repetition.type = gdstk::RepetitionType::Rectangular;
        ref->repetition.columns = 1;
        ref->repetition.rows = num_rows;
        ref->repetition.spacing = {0.0, colgrp_height};  // 垂直方向間距為 colgrp 高度
    }
    
    stacked_cell->reference_array.append(ref);
    
    double total_height = num_rows * colgrp_height;
    
    // ========================================================================
    // 加入 VDD 和 VSS pins (M3 pin layer)
    // ========================================================================
    const OpenFinRAM::LayerDef* m3_pin_layer = layer_map_.get_layer("M3", OpenFinRAM::LayerPurpose::Pin);
    
    if (m3_pin_layer != nullptr) {
        // VDD pin - x=0.125, y=0
        gdstk::Label* vdd_label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
        vdd_label->init("VDD");
        vdd_label->origin = {0.125, 0.0};
        vdd_label->tag = gdstk::make_tag(m3_pin_layer->layer_number, m3_pin_layer->datatype);
        vdd_label->magnification = 0.02;
        stacked_cell->label_array.append(vdd_label);
        
        LOGI << "  Added VDD pin at (" << vdd_label->origin.x << ", " << vdd_label->origin.y 
             << ") on M3 pin layer";
        
        // VSS pin - x=0.162, y=0
        gdstk::Label* vss_label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
        vss_label->init("VSS");
        vss_label->origin = {0.162, 0.0};
        vss_label->tag = gdstk::make_tag(m3_pin_layer->layer_number, m3_pin_layer->datatype);
        vss_label->magnification = 0.02;
        stacked_cell->label_array.append(vss_label);
        
        LOGI << "  Added VSS pin at (" << vss_label->origin.x << ", " << vss_label->origin.y 
             << ") on M3 pin layer";
        
        // ====================================================================
        // 加入 WLT, WLB 和其他信號 pins - 只加最下面的 colgrp，位置跟 colgrp 一樣
        // ====================================================================
        LOGI << "  Adding WLT, WLB and signal pins from bottom colgrp";
        
        int wlt_count = 0;
        int wlb_count = 0;
        int signal_count = 0;
        
        // 遍歷 colgrp 的所有 labels，複製需要的 pins
        for (uint64_t i = 0; i < sram_cells_.colgrp->label_array.count; ++i) {
            gdstk::Label* orig_label = sram_cells_.colgrp->label_array[i];
            uint16_t layer = gdstk::get_layer(orig_label->tag);
            uint16_t datatype = gdstk::get_type(orig_label->tag);
            
            // 只處理 M3 pin layer (30, 251) 的 pins
            if (layer == 30 && datatype == 251) {
                const char* name = orig_label->text;
                bool should_copy = false;
                
                // 檢查是否為需要的信號
                if (strncmp(name, "WLT[", 4) == 0) {
                    should_copy = true;
                    wlt_count++;
                } else if (strncmp(name, "WLB[", 4) == 0) {
                    should_copy = true;
                    wlb_count++;
                } else if (strncmp(name, "yselt[", 6) == 0 ||
                           strncmp(name, "yseltn[", 7) == 0 ||
                           strncmp(name, "yselb[", 6) == 0 ||
                           strncmp(name, "yselbn[", 7) == 0 ||
                           strcasecmp(name, "blprechtn") == 0 ||
                           strcasecmp(name, "blprechbn") == 0 ||
                           strcasecmp(name, "wrena") == 0 ||
                        //    strcasecmp(name, "vsswrite") == 0 ||
                           strcasecmp(name, "sae") == 0 ||
                           strcasecmp(name, "saprechn") == 0 ||
                           strcasecmp(name, "wrenan") == 0) {
                    should_copy = true;
                    signal_count++;
                }
                
                if (should_copy) {
                    // 複製 label 到 stacked_cell，位置保持不變（最下面的 colgrp）
                    gdstk::Label* new_label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                    new_label->init(name);
                    new_label->origin = orig_label->origin;  // 位置跟 colgrp 一樣
                    new_label->tag = orig_label->tag;
                    new_label->magnification = 0.02;
                    stacked_cell->label_array.append(new_label);
                }
            }
        }
        
        LOGI << "  Added " << wlt_count << " WLT pins, " << wlb_count << " WLB pins, and " 
             << signal_count << " signal pins on M3 pin layer";
        
        // ====================================================================
        // 加入 D[n-1:0] 和 Q[n-1:0] pins - 每個 colgrp 一個，形成 bus
        // ====================================================================
        LOGI << "  Adding D[" << (num_rows-1) << ":0] and Q[" << (num_rows-1) << ":0] pins";
        
        // 先找到 colgrp 中 D 和 Q 的原始位置
        double d_x = 0.0, d_y = 0.0;
        double q_x = 0.0, q_y = 0.0;
        bool found_d = false, found_q = false;
        
        for (uint64_t i = 0; i < sram_cells_.colgrp->label_array.count; ++i) {
            gdstk::Label* orig_label = sram_cells_.colgrp->label_array[i];
            uint16_t layer = gdstk::get_layer(orig_label->tag);
            uint16_t datatype = gdstk::get_type(orig_label->tag);
            
            if (layer == 30 && datatype == 251) {
                const char* name = orig_label->text;
                if (strcasecmp(name, "D") == 0) {
                    d_x = orig_label->origin.x;
                    d_y = orig_label->origin.y;
                    found_d = true;
                } else if (strcasecmp(name, "Q") == 0) {
                    q_x = orig_label->origin.x;
                    q_y = orig_label->origin.y;
                    found_q = true;
                }
            }
        }
        
        if (found_d && found_q) {
            // 為每個堆疊的 colgrp 加入 D[i] 和 Q[i]
            for (uint64_t row = 0; row < num_rows; ++row) {
                // 計算當前 row 的 y 偏移
                double y_offset = row * colgrp_height;
                
                // 加入 D[i]
                char d_name[32];
                snprintf(d_name, sizeof(d_name), "D[%lu]", (unsigned long)row);
                gdstk::Label* d_label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                d_label->init(d_name);
                d_label->origin = {d_x, d_y + y_offset};
                d_label->tag = gdstk::make_tag(m3_pin_layer->layer_number, m3_pin_layer->datatype);
                d_label->magnification = 0.02;
                stacked_cell->label_array.append(d_label);
                
                // 加入 Q[i]
                char q_name[32];
                snprintf(q_name, sizeof(q_name), "Q[%lu]", (unsigned long)row);
                gdstk::Label* q_label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                q_label->init(q_name);
                q_label->origin = {q_x, q_y + y_offset};
                q_label->tag = gdstk::make_tag(m3_pin_layer->layer_number, m3_pin_layer->datatype);
                q_label->magnification = 0.02;
                stacked_cell->label_array.append(q_label);
            }
            
            LOGI << "  Added " << num_rows << " D pins (D[0] to D[" << (num_rows-1) << "]) and "
                 << num_rows << " Q pins (Q[0] to Q[" << (num_rows-1) << "]) on M3 pin layer";
        } else {
            LOGW << "Cannot find D or Q pins in colgrp (found_d=" << found_d << ", found_q=" << found_q << ")";
        }
    } else {
        LOGW << "Cannot find M3 pin layer, skipping VDD/VSS pins";
    }
    
    // ========================================================================
    // 加入 BOUNDARY
    // ========================================================================
    gdstk::Vec2 boundary_min = {0.0, 0.0};
    gdstk::Vec2 boundary_max = {colgrp_width, total_height};
    gdstk::Polygon* boundary = OpenFinRAM::create_boundary_polygon(boundary_min, boundary_max, layer_map_);
    stacked_cell->polygon_array.append(boundary);
    
    sram_cells_.stacked_colgrp = stacked_cell;
    LOGI << "Created stacked column group '" << stacked_name << "'";
    LOGI << "  Total size: " << colgrp_width << " x " << total_height;
    
    return true;
}

bool LayoutGenerator::create_muxed_colgrp() {
    if (sram_cells_.stacked_colgrp == nullptr || cli_options_.num_banks == 0) {
        LOGE << "Invalid parameters for create_muxed_colgrp";
        return false;
    }

    // 取得 stacked_colgrp 尺寸
    OpenFinRAM::CellSize stacked_size = OpenFinRAM::get_cell_size(sram_cells_.stacked_colgrp, layer_map_);

    if (!stacked_size.valid) {
        LOGW << "Cannot get stacked_colgrp size from BOUNDARY, using bounding box";
        gdstk::Vec2 bb_min, bb_max;
        sram_cells_.stacked_colgrp->bounding_box(bb_min, bb_max);
        stacked_size.min = bb_min;
        stacked_size.max = bb_max;
        stacked_size.width = bb_max.x - bb_min.x;
        stacked_size.height = bb_max.y - bb_min.y;
        stacked_size.valid = true;
    }

    double stacked_width = stacked_size.width;
    double stacked_height = stacked_size.height;

    LOGI << "Creating muxed column group with " << cli_options_.num_banks << " columns";
    LOGI << "  stacked_colgrp size: " << stacked_width << " x " << stacked_height;

    // 建立新的 Cell
    // char muxed_name[96];
    // snprintf(muxed_name, sizeof(muxed_name), "stacked_colgrp_x%dx%lux%lu", cli_options_.num_wls, cli_options_.num_data_bits, cli_options_.num_banks);
    std::string muxed_name = "stacked_colgrp_x" + std::to_string(cli_options_.num_wls * 2) + "x" + std::to_string(cli_options_.num_data_bits / 2) + "x" + std::to_string(cli_options_.num_banks);
    gdstk::Cell* muxed_cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
    muxed_cell->init(muxed_name.c_str());

    // 水平堆疊 stacked_colgrp
    gdstk::Reference* ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
    ref->init(sram_cells_.stacked_colgrp);
    ref->origin = {0.0 - stacked_size.min.x, 0.0 - stacked_size.min.y};
    ref->magnification = 1.0;

    if (cli_options_.num_banks > 1) {
        ref->repetition.type = gdstk::RepetitionType::Rectangular;
        ref->repetition.columns = cli_options_.num_banks;
        ref->repetition.rows = 1;
        ref->repetition.spacing = {stacked_width, 0.0};  // 水平方向間距為 stacked_colgrp 寬度
    }

    muxed_cell->reference_array.append(ref);

    double total_width = cli_options_.num_banks * stacked_width;

    // ========================================================================
    // 加入 BOUNDARY
    // ========================================================================
    gdstk::Vec2 boundary_min = {0.0, 0.0};
    gdstk::Vec2 boundary_max = {total_width, stacked_height};
    gdstk::Polygon* boundary = OpenFinRAM::create_boundary_polygon(boundary_min, boundary_max, layer_map_);
    muxed_cell->polygon_array.append(boundary);

    sram_cells_.muxed_colgrp = muxed_cell;

    LOGI << "Created muxed column group '" << muxed_name << "'";
    LOGI << "  Total size: " << total_width << " x " << stacked_height;

    return true;
}

bool LayoutGenerator::write_layout() {
    gdstk::Library output_sram_lib = {};
    output_sram_lib.init("SRAM_LIB", sram_lib.unit, sram_lib.precision);

    std::string sram_output_str = "tmp/sram_array_test_" + get_run_timestamp() + ".gds";
    const char* sram_output = sram_output_str.c_str();
    LOGD << "Writing SRAM library to: " << sram_output;
    
    add_cell_with_deps(output_sram_lib, sram_cells_.bitcell);
    add_cell_with_deps(output_sram_lib, sram_cells_.dummy);
    add_cell_with_deps(output_sram_lib, sram_cells_.tapcell);
    add_cell_with_deps(output_sram_lib, sram_cells_.dummy_v1);
    add_cell_with_deps(output_sram_lib, sram_cells_.dummy_v2);
    add_cell_with_deps(output_sram_lib, sram_cells_.cgedge);
    add_cell_with_deps(output_sram_lib, sram_cells_.io_colgrp);
    add_cell_with_deps(output_sram_lib, sram_cells_.filler);

    add_cell_with_deps(output_sram_lib, sram_cells_.sram_column);
    add_cell_with_deps(output_sram_lib, sram_cells_.array_cell);
    add_cell_with_deps(output_sram_lib, sram_cells_.colgrp);
    add_cell_with_deps(output_sram_lib, sram_cells_.stacked_colgrp);
    add_cell_with_deps(output_sram_lib, sram_cells_.muxed_colgrp);
    
    gdstk::ErrorCode error_code = output_sram_lib.write_gds(sram_output, 0, nullptr);
    
    if (error_code != gdstk::ErrorCode::NoError) {
        LOGE << "Error writing SRAM GDS!";
        return false;
    }
    
    LOGI << "Successfully created SRAM library: " << sram_output;
    return true;
}

void LayoutGenerator::add_cell_with_deps(gdstk::Library& target_lib, gdstk::Cell* source_cell) {
    if (source_cell == nullptr) return;

    // 1. 加入 cell 本身
    if (target_lib.get_cell(source_cell->name) == nullptr) {
        gdstk::Cell* cell_copy = static_cast<gdstk::Cell*>(gdstk::allocate_clear(sizeof(gdstk::Cell)));
        
        // 【修正重點】這裡必須設為 false
        // 因為我們稍後會手動處理相依性，且傳遞 nullptr 給 copy_from 會導致遞迴時崩潰
        cell_copy->copy_from(*source_cell, nullptr, false); 
        
        target_lib.cell_array.append(cell_copy);
    }

    gdstk::Map<gdstk::Cell*> deps = {};
    source_cell->get_dependencies(true, deps);
    for (auto* item = deps.next(nullptr); item != nullptr; item = deps.next(item)) {
        LOGD << "  Cell '" << source_cell->name << "' depends on '" << item->value->name << "'";
        if (target_lib.get_cell(item->value->name) == nullptr) {
            gdstk::Cell* dep_copy = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
            dep_copy->copy_from(*item->value, nullptr, true);
            target_lib.cell_array.append(dep_copy);
        }
    }
    deps.clear();
}

bool LayoutGenerator::add_ctrl_decode_gate_fin_wrappers() {
    // Support both Innovus and OpenROAD P&R (GDS or DEF) - OpenROAD now emits DEF
    std::string gds_innovus = join_path(get_current_dir_name(), "tmp/innovus_" + get_run_timestamp() + "/ctrl_decode.gds");
    std::string gds_openroad = join_path(get_current_dir_name(), "tmp/openroad_" + get_run_timestamp() + "/ctrl_decode.gds");
    std::string def_openroad = join_path(get_current_dir_name(), "tmp/openroad_" + get_run_timestamp() + "/ctrl_decode.def");
    const bool openroad_mode = cli_options_.use_openroad || cli_options_.openroad_only;
    std::string gds_path = gds_innovus;
    std::string def_path = "";
    if (openroad_mode) {
        if (file_exists(gds_openroad)) gds_path = gds_openroad;
        if (file_exists(def_openroad)) def_path = def_openroad;
    } else {
        if (!file_exists(gds_path) && file_exists(gds_openroad)) gds_path = gds_openroad;
        if (file_exists(def_openroad)) def_path = def_openroad;
    }
    if (!file_exists(gds_path) && file_exists(gds_openroad)) gds_path = gds_openroad;
    if (def_path.empty() && file_exists(def_openroad)) def_path = def_openroad;

    if (openroad_mode && !file_exists(gds_openroad)) {
        LOGE << "OpenROAD ctrl_decode.gds is missing; a DEF-derived placeholder is no longer accepted";
        return false;
    }

    // Retain the legacy DEF stub only for non-OpenROAD integration. OpenROAD
    // mode is required to consume the merged GDS checked above.
    bool use_def_fallback = false;
    if (!file_exists(gds_path) && !def_path.empty()) {
        LOGI << "OpenROAD DEF found at " << def_path << " but no GDS; using DEF-derived stub for ctrl_decode";
        use_def_fallback = true;
    } else {
        LOGI << "Reading GDS file: " << gds_path;
    }

    gdstk::ErrorCode gds_error_code = gdstk::ErrorCode::NoError;
    if (!use_def_fallback) {
        ctrl_decode_gds = gdstk::read_gds(gds_path.c_str(), 0, 1e-2, nullptr, &gds_error_code);
    } else {
        // Create a stub ctrl_decode cell sized from QoR/DEF bbox so layout can proceed
        gds_error_code = gdstk::ErrorCode::NoError;
        ctrl_decode_gds = gdstk::Library{};
        // gdstk requires unit/precision for valid GDS; copy from sram_lib or use ASAP7 defaults
        double lib_unit = (sram_lib.unit != 0) ? sram_lib.unit : 1e-6;
        double lib_prec = (sram_lib.precision != 0) ? sram_lib.precision : 1e-9;
        ctrl_decode_gds.init("ctrl_decode_lib", lib_unit, lib_prec);
        // Try to infer size from DEF die area or fallback to 3.5x1.6
        double w = 3.564, h = 1.62;
        // Parse DEF for DIEAREA if available
        std::ifstream def_in(def_path);
        std::string line;
        while (std::getline(def_in, line)) {
            if (line.find("DIEAREA") != std::string::npos) {
                // Format: DIEAREA ( 0 0 ) ( 3564 1620 )
                int x1,y1,x2,y2;
                if (sscanf(line.c_str(), " DIEAREA ( %d %d ) ( %d %d )", &x1,&y1,&x2,&y2)==4) {
                    w = x2 / 1000.0; h = y2 / 1000.0;
                } else if (sscanf(line.c_str(), "DIEAREA ( %d %d ) ( %d %d )", &x1,&y1,&x2,&y2)==4) {
                    w = x2 / 1000.0; h = y2 / 1000.0;
                }
            }
        }
        gdstk::Cell* stub = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
        stub->init("ctrl_decode");
        const OpenFinRAM::LayerDef* m1 = layer_map_.get_layer("M1", OpenFinRAM::LayerPurpose::Drawing);
        if (m1) {
            gdstk::Polygon* poly = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
            gdstk::Vec2 pts[4] = {{0,0},{w,0},{w,h},{0,h}};
            poly->point_array.extend({.capacity=0,.count=4,.items=pts});
            poly->tag = gdstk::make_tag(m1->layer_number, m1->datatype);
            stub->polygon_array.append(poly);
        }
        ctrl_decode_gds.cell_array.append(stub);
        LOGI << "Created DEF-derived stub ctrl_decode cell " << w << " x " << h;
    }

    if (gds_error_code == gdstk::ErrorCode::NoError && ctrl_decode_gds.cell_array.count > 0) {
        LOGI << "Successfully read/created GDS library";
        LOGI << "Number of cells in library: " << ctrl_decode_gds.cell_array.count;
    } else if (ctrl_decode_gds.cell_array.count == 0) {
        LOGW << "ctrl_decode GDS empty (no cells) after read/fallback, creating emergency stub";
        // Ensure library has valid unit/prec
        if (ctrl_decode_gds.unit == 0) {
            double lib_unit = (sram_lib.unit != 0) ? sram_lib.unit : 1e-6;
            double lib_prec = (sram_lib.precision != 0) ? sram_lib.precision : 1e-9;
            ctrl_decode_gds.init("ctrl_decode_lib", lib_unit, lib_prec);
        }
        // Compute width from current SRAM config (matches openroad_tcl_generator sram_width)
        double sram_w = 10.0;
        if (cli_options_.single_port) {
            sram_w = (cli_options_.bitcell_width * 2 + 2.376 + cli_options_.bitcell_width * ((cli_options_.num_wls + 3) * 2)) * cli_options_.num_banks - cli_options_.bitcell_width;
        } else {
            sram_w = 15.0;
        }
        double w = sram_w, h = 1.62;
        // Try to refine from DEF die area if available, else use computed
        gds_error_code = gdstk::ErrorCode::NoError;
        gdstk::Cell* stub = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
        stub->init("ctrl_decode");
        const OpenFinRAM::LayerDef* m1e = layer_map_.get_layer("M1", OpenFinRAM::LayerPurpose::Drawing);
        if (m1e) {
            gdstk::Polygon* poly = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
            gdstk::Vec2 pts[4] = {{0,0},{w,0},{w,h},{0,h}};
            poly->point_array.extend({.capacity=0,.count=4,.items=pts});
            poly->tag = gdstk::make_tag(m1e->layer_number, m1e->datatype);
            stub->polygon_array.append(poly);
        }
        ctrl_decode_gds.cell_array.append(stub);
        LOGI << "Created emergency stub ctrl_decode cell " << w << " x " << h;
    }

    gdstk::Cell* ctrl_decode_cell = ctrl_decode_gds.get_cell("ctrl_decode");

    if (ctrl_decode_cell == nullptr) {
        if (ctrl_decode_gds.cell_array.count == 0) {
            LOGE << "No cells available for ctrl_decode wrapper - aborting";
            return false;
        }
        LOGW << "ctrl_decode cell not found, using first cell";
        ctrl_decode_cell = ctrl_decode_gds.cell_array[0];
        if (ctrl_decode_cell == nullptr) {
            LOGE << "First cell is null - aborting wrapper";
            return false;
        }
    }

    LOGI << "Working with cell: " << ctrl_decode_cell->name;

    // Reduce the label size
    for (int i = 0; i < ctrl_decode_cell->label_array.count; ++i) {
        ctrl_decode_cell->label_array[i]->magnification = 0.02;
        ctrl_decode_cell->label_array[i]->rotation = M_PI_2;
    }

    OpenFinRAM::CellSize cell_size = OpenFinRAM::get_cell_size(ctrl_decode_cell, layer_map_);

    if (!cell_size.valid) {
        LOGW << "Cannot get cell size from BOUNDARY, using bounding box";
        gdstk::Vec2 bb_min, bb_max;
        ctrl_decode_cell->bounding_box(bb_min, bb_max);
        cell_size.min = bb_min;
        cell_size.max = bb_max;
        cell_size.width = bb_max.x - bb_min.x;
        cell_size.height = bb_max.y - bb_min.y;
        cell_size.valid = true;
    }

    LOGI << "========================================================================";
    LOGI << "Core Boundary Information:";
    LOGI << "  Lower-left corner (min):  (" << cell_size.min.x << ", " << cell_size.min.y << ")";
    LOGI << "  Upper-right corner (max): (" << cell_size.max.x << ", " << cell_size.max.y << ")";
    LOGI << "  Width:  " << cell_size.width;
    LOGI << "  Height: " << cell_size.height;
    LOGI << "========================================================================";

    LOGI << "========================================================================";
    LOGI << "Creating parameterized Gate polygons on left and right sides";
    LOGI << "========================================================================";

    const OpenFinRAM::LayerDef* gate_layer = layer_map_.get_layer("Gate", OpenFinRAM::LayerPurpose::Drawing);

    if (gate_layer == nullptr) {
        LOGE << "Cannot find Gate drawing layer definition, skipping Gate polygon creation";
        return false;
    } else {
        LOGI << "Found Gate layer: layer=" << gate_layer->layer_number
             << ", datatype=" << gate_layer->datatype;

        const char* new_cell_name = "ctrl_decode_with_filler";

        if (ctrl_decode_gds.get_cell(new_cell_name) != nullptr) {
            LOGW << "Cell '" << new_cell_name << "' already exists, removing it first";
            for (uint64_t i = 0; i < ctrl_decode_gds.cell_array.count; i++) {
                if (std::strcmp(ctrl_decode_gds.cell_array[i]->name, new_cell_name) == 0) {
                    ctrl_decode_gds.cell_array[i]->free_all();
                    ctrl_decode_gds.cell_array.remove(i);
                    break;
                }
            }
        }

        LOGI << "Creating new cell: " << new_cell_name;
        gdstk::Cell* gate_wrapper_cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
        gate_wrapper_cell->init(new_cell_name);

        LOGI << "Adding reference to original ctrl_decode cell...";
        gdstk::Reference* ctrl_ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
        ctrl_ref->init(ctrl_decode_cell);
        ctrl_ref->origin = {0.0, 0.0};
        ctrl_ref->magnification = 1.0;
        gate_wrapper_cell->reference_array.append(ctrl_ref);

        const double gate_spacing = 0.017;
        const double gate_width = 0.020;

        LOGI << "Gate polygon parameters:";
        LOGI << "  Spacing from core: " << gate_spacing << " um";
        LOGI << "  Gate width: " << gate_width << " um";
        LOGI << "  Gate height: " << cell_size.height << " um (matches core height)";

        LOGI << "Creating left Gate polygon...";
        gdstk::Polygon* left_gate = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));

        gdstk::Vec2 left_points[4] = {
            {cell_size.min.x - gate_spacing - gate_width, cell_size.min.y},
            {cell_size.min.x - gate_spacing, cell_size.min.y},
            {cell_size.min.x - gate_spacing, cell_size.max.y},
            {cell_size.min.x - gate_spacing - gate_width, cell_size.max.y}
        };

        left_gate->point_array.extend({.capacity = 0, .count = 4, .items = left_points});
        left_gate->tag = gate_layer->tag();
        gate_wrapper_cell->polygon_array.append(left_gate);

        LOGI << "  Left gate position: x=["
             << (cell_size.min.x - gate_spacing - gate_width) << ", "
             << (cell_size.min.x - gate_spacing) << "]";

        LOGI << "Creating right Gate polygon...";
        gdstk::Polygon* right_gate = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));

        gdstk::Vec2 right_points[4] = {
            {cell_size.max.x + gate_spacing, cell_size.min.y},
            {cell_size.max.x + gate_spacing + gate_width, cell_size.min.y},
            {cell_size.max.x + gate_spacing + gate_width, cell_size.max.y},
            {cell_size.max.x + gate_spacing, cell_size.max.y}
        };

        right_gate->point_array.extend({.capacity = 0, .count = 4, .items = right_points});
        right_gate->tag = gate_layer->tag();
        gate_wrapper_cell->polygon_array.append(right_gate);

        LOGI << "  Right gate position: x=["
             << (cell_size.max.x + gate_spacing) << ", "
             << (cell_size.max.x + gate_spacing + gate_width) << "]";

        LOGI << "========================================================================";
        LOGI << "Adding Fin polygons on left and right sides";
        LOGI << "========================================================================";

        const OpenFinRAM::LayerDef* fin_layer = layer_map_.get_layer("fin", OpenFinRAM::LayerPurpose::Drawing);

        if (fin_layer == nullptr) {
            LOGE << "Cannot find fin drawing layer definition, skipping Fin polygon creation";
            return false;
        } else {
            LOGI << "Found fin layer: layer=" << fin_layer->layer_number
                 << ", datatype=" << fin_layer->datatype;

            const double fin_start_y = 0.010;
            const double fin_spacing = 0.027;
            const double fin_height = 0.007;
            const double fin_width = 0.054;

            double available_height = cell_size.max.y - fin_start_y;
            uint64_t num_fins = (uint64_t)std::floor(available_height / fin_spacing) + 1;

            LOGI << "Fin polygon parameters:";
            LOGI << "  Start Y position: " << fin_start_y << " um";
            LOGI << "  Fin spacing: " << fin_spacing << " um";
            LOGI << "  Fin height: " << fin_height << " um";
            LOGI << "  Fin width: " << fin_width << " um";
            LOGI << "  Number of fins: " << num_fins;

            LOGI << "Creating left fin polygons...";
            for (uint64_t i = 0; i < num_fins; i++) {
                double fin_y_start = fin_start_y + i * fin_spacing;
                double fin_y_end = fin_y_start + fin_height;

                if (fin_y_end > cell_size.max.y) {
                    break;
                }

                gdstk::Polygon* left_fin = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));

                gdstk::Vec2 left_fin_points[4] = {
                    {cell_size.min.x - fin_width, fin_y_start},
                    {cell_size.min.x, fin_y_start},
                    {cell_size.min.x, fin_y_end},
                    {cell_size.min.x - fin_width, fin_y_end}
                };

                left_fin->point_array.extend({.capacity = 0, .count = 4, .items = left_fin_points});
                left_fin->tag = fin_layer->tag();
                gate_wrapper_cell->polygon_array.append(left_fin);
            }

            LOGI << "  Created " << num_fins << " left fin polygons";

            LOGI << "Creating right fin polygons...";
            for (uint64_t i = 0; i < num_fins; i++) {
                double fin_y_start = fin_start_y + i * fin_spacing;
                double fin_y_end = fin_y_start + fin_height;

                if (fin_y_end > cell_size.max.y) {
                    break;
                }

                gdstk::Polygon* right_fin = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));

                gdstk::Vec2 right_fin_points[4] = {
                    {cell_size.max.x, fin_y_start},
                    {cell_size.max.x + fin_width, fin_y_start},
                    {cell_size.max.x + fin_width, fin_y_end},
                    {cell_size.max.x, fin_y_end}
                };

                right_fin->point_array.extend({.capacity = 0, .count = 4, .items = right_fin_points});
                right_fin->tag = fin_layer->tag();
                gate_wrapper_cell->polygon_array.append(right_fin);
            }

            LOGI << "  Created " << num_fins << " right fin polygons";
            LOGI << "Fin polygons created successfully";
        }

        LOGI << "========================================================================";
        LOGI << "Adding Gate filler rows on top and bottom";
        LOGI << "========================================================================";

        const double gate_filler_height = 0.8;
        const double gate_filler_width = 0.02;
        const double gate_filler_spacing = 0.034;

        double row_start_x = cell_size.min.x - gate_spacing - gate_width;
        double row_end_x = cell_size.max.x + gate_spacing + gate_width;
        double row_width = row_end_x - row_start_x;

        double pitch = gate_filler_width + gate_filler_spacing;
        uint64_t num_gates = (uint64_t)std::ceil(row_width / pitch);

        LOGI << "Gate filler parameters:";
        LOGI << "  Gate height: " << gate_filler_height << " um";
        LOGI << "  Gate width: " << gate_filler_width << " um";
        LOGI << "  Gate spacing: " << gate_filler_spacing << " um";
        LOGI << "  Row start X: " << row_start_x << " um (left gate)";
        LOGI << "  Row end X: " << row_end_x << " um (right gate)";
        LOGI << "  Number of gates per row: " << num_gates;

        LOGI << "Creating bottom gate row...";
        for (uint64_t i = 0; i < num_gates; i++) {
            double gate_x_start = row_start_x + i * pitch;
            double gate_x_end = gate_x_start + gate_filler_width;

            if (gate_x_end > row_end_x) {
                gate_x_end = row_end_x;
                if (gate_x_end <= gate_x_start) {
                    break;
                }
            }

            gdstk::Polygon* bottom_gate = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));

            gdstk::Vec2 bottom_gate_points[4] = {
                {gate_x_start, cell_size.min.y - gate_filler_height},
                {gate_x_end, cell_size.min.y - gate_filler_height},
                {gate_x_end, cell_size.min.y + 0.02},
                {gate_x_start, cell_size.min.y + 0.02}
            };

            bottom_gate->point_array.extend({.capacity = 0, .count = 4, .items = bottom_gate_points});
            bottom_gate->tag = gate_layer->tag();
            gate_wrapper_cell->polygon_array.append(bottom_gate);
        }

        LOGI << "  Created " << num_gates << " bottom gate polygons";

        LOGI << "Creating top gate row...";
        for (uint64_t i = 0; i < num_gates; i++) {
            double gate_x_start = row_start_x + i * pitch;
            double gate_x_end = gate_x_start + gate_filler_width;

            if (gate_x_end > row_end_x) {
                gate_x_end = row_end_x;
                if (gate_x_end <= gate_x_start) {
                    break;
                }
            }

            gdstk::Polygon* top_gate = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));

            gdstk::Vec2 top_gate_points[4] = {
                {gate_x_start, cell_size.max.y - 0.02},
                {gate_x_end, cell_size.max.y - 0.02},
                {gate_x_end, cell_size.max.y + gate_filler_height},
                {gate_x_start, cell_size.max.y + gate_filler_height}
            };

            top_gate->point_array.extend({.capacity = 0, .count = 4, .items = top_gate_points});
            top_gate->tag = gate_layer->tag();
            gate_wrapper_cell->polygon_array.append(top_gate);
        }

        LOGI << "  Created " << num_gates << " top gate polygons";
        LOGI << "Gate filler rows created successfully";

        LOGI << "========================================================================";
        LOGI << "Adding Fin rows on top and bottom";
        LOGI << "========================================================================";

        if (fin_layer == nullptr) {
            LOGW << "Cannot find fin drawing layer definition, skipping top/bottom Fin creation";
        } else {
            const double fin_height = 0.007;
            const double fin_spacing = 0.020;
            const double fin_row_width = cell_size.width + 0.054 * 2;
            const double fin_row_start_x = cell_size.min.x - 0.054;
            const double fin_row_end_x = cell_size.max.x + 0.054;

            LOGI << "Fin row parameters:";
            LOGI << "  Fin row width: " << fin_row_width << " um";
            LOGI << "  Fin row X range: [" << fin_row_start_x << ", " << fin_row_end_x << "]";
            LOGI << "  Fin height: " << fin_height << " um";
            LOGI << "  Fin spacing: " << fin_spacing << " um";

            LOGI << "Creating bottom fin rows...";
            double base_y = cell_size.min.y - 3 * fin_spacing - 0.011;
            for (int i = 0; i < 3; i++) {
                double fin_y_start = base_y + i * (fin_height + fin_spacing);
                double fin_y_end = fin_y_start + fin_height;

                gdstk::Polygon* bottom_fin = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));

                gdstk::Vec2 bottom_fin_points[4] = {
                    {fin_row_start_x, fin_y_start},
                    {fin_row_end_x, fin_y_start},
                    {fin_row_end_x, fin_y_end},
                    {fin_row_start_x, fin_y_end}
                };

                bottom_fin->point_array.extend({.capacity = 0, .count = 4, .items = bottom_fin_points});
                bottom_fin->tag = fin_layer->tag();
                gate_wrapper_cell->polygon_array.append(bottom_fin);
            }

            LOGI << "  Created 3 bottom fin rows";

            LOGI << "Creating top fin rows...";
            double base_top_y = cell_size.max.y + 0.01;
            for (int i = 0; i < 3; i++) {
                double fin_y_start = base_top_y + i * (fin_height + fin_spacing);
                double fin_y_end = fin_y_start + fin_height;

                gdstk::Polygon* top_fin = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));

                gdstk::Vec2 top_fin_points[4] = {
                    {fin_row_start_x, fin_y_start},
                    {fin_row_end_x, fin_y_start},
                    {fin_row_end_x, fin_y_end},
                    {fin_row_start_x, fin_y_end}
                };

                top_fin->point_array.extend({.capacity = 0, .count = 4, .items = top_fin_points});
                top_fin->tag = fin_layer->tag();
                gate_wrapper_cell->polygon_array.append(top_fin);
            }

            LOGI << "  Created 3 top fin rows";
            LOGI << "Top and bottom fin rows created successfully";
        }

        // Full-layer dummy-bitcell fill at the array-facing boundary (well /
        // implant / active / fin continuity that bare fin rows can't provide).
        // Mirrors the academic ctrl_decode_32's dummy_vertical rows. Tiled at
        // the array column pitch (2 * bitcell_width); alignment to the array
        // column grid should be spot-checked in KLayout (offset via the row y).
        if (sram_cells_.dummy_vertical != nullptr) {
            add_cell_with_deps(ctrl_decode_gds, sram_cells_.dummy_vertical);
            gdstk::Vec2 dmin, dmax;
            sram_cells_.dummy_vertical->bounding_box(dmin, dmax);
            const double dummy_h = dmax.y - dmin.y;   // ~0.350 um (bitcell pitch)
            const double col_pitch = 2.0 * cli_options_.bitcell_width;  // ~0.216 um
            const double x0 = cell_size.min.x;
            const double x1 = cell_size.max.x;
            const double bottom_y = cell_size.min.y - dummy_h;  // abut below
            const double top_y = cell_size.max.y;               // abut above
            int n_dummy = 0;
            for (double y : {bottom_y, top_y}) {
                for (double x = x0; x < x1 - 1e-6; x += col_pitch) {
                    gdstk::Reference* r = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
                    r->init(sram_cells_.dummy_vertical);
                    r->origin = {x - dmin.x, y - dmin.y};
                    r->magnification = 1.0;
                    gate_wrapper_cell->reference_array.append(r);
                    ++n_dummy;
                }
            }
            LOGI << "  Added " << n_dummy << " dummy_vertical_6t122 boundary fill cells"
                 << " (2 rows @ " << col_pitch << " um pitch)";
        }

        ctrl_decode_gds.cell_array.append(gate_wrapper_cell);
        LOGI << "Added new cell to library: " << new_cell_name;
        LOGI << "Gate and Fin polygons created successfully";
    }

    return true;
}

bool LayoutGenerator::create_and_add_sram_filler_cells() {
    uint64_t test_num_bits = cli_options_.num_data_bits / 2;

    LOGI << "========================================================================";
    LOGI << "Creating SRAM Filler Cells (Top and Bottom)";
    LOGI << "========================================================================";

    OpenFinRAM::FillerCellLibrary filler_lib;
    if (OpenFinRAM::load_filler_cells_from_library(sram_lib, filler_lib)) {
        LOGI << "Successfully loaded filler cells from library";

        gdstk::Cell* ctrl_decode_for_filler = ctrl_decode_gds.get_cell("ctrl_decode");
        double filler_ctrl_width = 1.782;
        double filler_ctrl_height = 0.297;

        if (ctrl_decode_for_filler != nullptr) {
            OpenFinRAM::CellSize ctrl_size = OpenFinRAM::get_cell_size(ctrl_decode_for_filler, layer_map_);
            if (ctrl_size.valid) {
                filler_ctrl_width = ctrl_size.width;
                filler_ctrl_height = ctrl_size.height;
            } else {
                gdstk::Vec2 bb_min, bb_max;
                ctrl_decode_for_filler->bounding_box(bb_min, bb_max);
                filler_ctrl_width = bb_max.x - bb_min.x;
                filler_ctrl_height = bb_max.y - bb_min.y;
            }
        }

        LOGI << "Filler configuration:";
        LOGI << "  test_num_bits: " << test_num_bits;
        LOGI << "  ctrl_decode width: " << filler_ctrl_width;
        LOGI << "  ctrl_decode height: " << filler_ctrl_height;

        OpenFinRAM::FillerConfig filler_config;
        filler_config.test_num_bits = test_num_bits;
        filler_config.ctrl_decode_width = filler_ctrl_width + 0.054 * 2;
        filler_config.ctrl_decode_height = filler_ctrl_height;
        filler_config.is_top = true;
        filler_config.bitcell_width = cli_options_.bitcell_width;
        gdstk::Cell* filler_top = OpenFinRAM::create_filler_top(filler_lib, filler_config, layer_map_);

        filler_config.is_top = false;
        gdstk::Cell* filler_bottom = OpenFinRAM::create_filler_bottom(filler_lib, filler_config, layer_map_);

        if (filler_top != nullptr || filler_bottom != nullptr) {
            OpenFinRAM::add_filler_cells_to_library(ctrl_decode_gds, filler_top, filler_bottom, filler_lib);

            LOGI << "========================================================================";
            LOGI << "Filler cells created successfully!";
            if (filler_top) LOGI << "  - " << filler_top->name;
            if (filler_bottom) LOGI << "  - " << filler_bottom->name;
            LOGI << "========================================================================";
        } else {
            LOGW << "Failed to create filler cells";
        }
    } else {
        LOGW << "Failed to load filler cells from library";
        LOGW << "Skipping filler cell generation";
    }

    return true;
}

bool LayoutGenerator::run_sram_gds_integration_and_writeback() {
    int num_wls = cli_options_.num_wls;
    int num_data_bits = cli_options_.num_data_bits;
    int addr_width = get_addr_width(cli_options_);
    int num_banks = cli_options_.num_banks;
    std::string sram_cell_name_str = "sram_x" + std::to_string(num_wls * 2) + "x" + std::to_string(num_data_bits) + "x" + std::to_string(num_banks);

    // 創建 SRAM cell：組合 stacked_colgrp + ctrl_decode_with_filler + stacked_colgrp
    // ================================================================
    LOGI << "========================================================================";
    LOGI << "Creating integrated SRAM cell";
    LOGI << "========================================================================";
    
    // 讀取包含 stacked_colgrp 的 GDS 檔案
    std::string sram_array_gds_path_str = "tmp/sram_array_test_" + get_run_timestamp() + ".gds";
    const char* sram_array_gds_path = sram_array_gds_path_str.c_str();
    LOGI << "Reading SRAM array GDS file: " << sram_array_gds_path;
    
    gdstk::ErrorCode sram_array_error = gdstk::ErrorCode::NoError;
    gdstk::Library sram_array_lib = gdstk::read_gds(sram_array_gds_path, 0, 1e-2, nullptr, &sram_array_error);
    
    if (sram_array_error == gdstk::ErrorCode::NoError && sram_array_lib.cell_array.count > 0) {
        LOGI << "Successfully read SRAM array GDS file";
        
        // 尋找 stacked_colgrp cell
        gdstk::Cell* stacked_colgrp_cell = nullptr;
        std::string expected_name = "stacked_colgrp_x" + std::to_string(cli_options_.num_wls * 2) + "x" + std::to_string(cli_options_.num_data_bits / 2) + "x" + std::to_string(cli_options_.num_banks);
        for (uint64_t i = 0; i < sram_array_lib.cell_array.count; i++) {
            if (strcmp(sram_array_lib.cell_array[i]->name, expected_name.c_str()) == 0) {
                stacked_colgrp_cell = sram_array_lib.cell_array[i];
                LOGI << "Found stacked_colgrp cell: " << stacked_colgrp_cell->name;
                break;
            }
        }
        
        if (stacked_colgrp_cell == nullptr) {
            LOGW << "Cannot find stacked_colgrp cell in SRAM array GDS";
        } else {
            // 取得 stacked_colgrp 的尺寸
            OpenFinRAM::CellSize stacked_size = OpenFinRAM::get_cell_size(stacked_colgrp_cell, layer_map_);
            if (!stacked_size.valid) {
                gdstk::Vec2 bb_min, bb_max;
                stacked_colgrp_cell->bounding_box(bb_min, bb_max);
                stacked_size.min = bb_min;
                stacked_size.max = bb_max;
                stacked_size.width = bb_max.x - bb_min.x;
                stacked_size.height = bb_max.y - bb_min.y;
                stacked_size.valid = true;
            }
            
            // 取得 ctrl_decode_with_filler 的尺寸
            gdstk::Cell* ctrl_filler_cell = ctrl_decode_gds.get_cell("ctrl_decode");
            OpenFinRAM::CellSize ctrl_filler_size;
            ctrl_filler_size.valid = false;
            
            if (ctrl_filler_cell != nullptr) {
                ctrl_filler_size = OpenFinRAM::get_cell_size(ctrl_filler_cell, layer_map_);
                LOGD << ctrl_filler_size.height;
                if (!ctrl_filler_size.valid) {
                    gdstk::Vec2 bb_min, bb_max;
                    ctrl_filler_cell->bounding_box(bb_min, bb_max);
                    ctrl_filler_size.min = bb_min;
                    ctrl_filler_size.max = bb_max;
                    ctrl_filler_size.width = bb_max.x - bb_min.x;
                    ctrl_filler_size.height = bb_max.y - bb_min.y;
                    ctrl_filler_size.valid = true;
                }
            } else {
                LOGW << "Cannot find ctrl_decode_with_filler cell";
            }

            ctrl_filler_cell = ctrl_decode_gds.get_cell("ctrl_decode_with_filler");
            
            // 取得 filler_top 和 filler_bottom 的尺寸
            // 名稱格式: FILLER_{num_data_bits}x2_top/bottom
            char filler_top_name[128];
            char filler_bottom_name[128];
            snprintf(filler_top_name, sizeof(filler_top_name), "FILLER_%lux%lu_top", 
                        (unsigned long)(num_data_bits), 2UL);
            snprintf(filler_bottom_name, sizeof(filler_bottom_name), "FILLER_%lux%lu_bottom", 
                        (unsigned long)(num_data_bits), 2UL);
            
            LOGI << "Looking for filler cells:";
            LOGI << "  Top: " << filler_top_name;
            LOGI << "  Bottom: " << filler_bottom_name;
            
            gdstk::Cell* filler_top_cell = ctrl_decode_gds.get_cell(filler_top_name);
            gdstk::Cell* filler_bottom_cell = ctrl_decode_gds.get_cell(filler_bottom_name);
            
            OpenFinRAM::CellSize filler_top_size = {};
            OpenFinRAM::CellSize filler_bottom_size = {};
            
            if (filler_top_cell != nullptr) {
                filler_top_size = OpenFinRAM::get_cell_size(filler_top_cell, layer_map_);
                if (!filler_top_size.valid) {
                    gdstk::Vec2 bb_min, bb_max;
                    filler_top_cell->bounding_box(bb_min, bb_max);
                    filler_top_size.min = bb_min;
                    filler_top_size.max = bb_max;
                    filler_top_size.width = bb_max.x - bb_min.x;
                    filler_top_size.height = bb_max.y - bb_min.y;
                    filler_top_size.valid = true;
                }
            } else {
                LOGW << "filler_top cell not found";
            }
            
            if (filler_bottom_cell != nullptr) {
                filler_bottom_size = OpenFinRAM::get_cell_size(filler_bottom_cell, layer_map_);
                if (!filler_bottom_size.valid) {
                    gdstk::Vec2 bb_min, bb_max;
                    filler_bottom_cell->bounding_box(bb_min, bb_max);
                    filler_bottom_size.min = bb_min;
                    filler_bottom_size.max = bb_max;
                    filler_bottom_size.width = bb_max.x - bb_min.x;
                    filler_bottom_size.height = bb_max.y - bb_min.y;
                    filler_bottom_size.valid = true;
                }
            } else {
                LOGW << "filler_bottom cell not found";
            }
            
            LOGI << "Component dimensions:";
            LOGI << "  stacked_colgrp: " << stacked_size.width << " x " << stacked_size.height << " um";
            LOGI << "  ctrl_decode_with_filler: " << ctrl_filler_size.width << " x " << ctrl_filler_size.height << " um";
            if (filler_top_cell != nullptr) {
                LOGI << "  filler_top: " << filler_top_size.width << " x " << filler_top_size.height << " um";
            }
            if (filler_bottom_cell != nullptr) {
                LOGI << "  filler_bottom: " << filler_bottom_size.width << " x " << filler_bottom_size.height << " um";
            }
            
            // 使用 get_dependencies 取得 stacked_colgrp 的所有依賴 cells
            LOGI << "Getting dependencies of stacked_colgrp...";
            gdstk::Map<gdstk::Cell*> dependencies = {};
            stacked_colgrp_cell->get_dependencies(true, dependencies);
            
            LOGI << "Found " << dependencies.capacity << " dependent cells, copying to target library...";
            
            // 先複製所有依賴的 cells
            for (gdstk::MapItem<gdstk::Cell*>* item = dependencies.next(NULL); item; item = dependencies.next(item)) {
                gdstk::Cell* dep_cell = item->value;
                
                // 檢查是否已存在於目標 library
                if (ctrl_decode_gds.get_cell(dep_cell->name) == nullptr) {
                    gdstk::Cell* new_cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
                    new_cell->copy_from(*dep_cell, nullptr, true);
                    ctrl_decode_gds.cell_array.append(new_cell);
                    LOGI << "  Copied dependency: " << dep_cell->name;
                }
            }
            
            // 最後複製 stacked_colgrp 本身
            gdstk::Cell* stacked_colgrp_copy = nullptr;
            if (ctrl_decode_gds.get_cell(stacked_colgrp_cell->name) == nullptr) {
                stacked_colgrp_copy = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
                stacked_colgrp_copy->copy_from(*stacked_colgrp_cell, nullptr, true);
                ctrl_decode_gds.cell_array.append(stacked_colgrp_copy);
                LOGI << "  Copied main cell: " << stacked_colgrp_cell->name;
            } else {
                stacked_colgrp_copy = ctrl_decode_gds.get_cell(stacked_colgrp_cell->name);
            }
            
            // 清理 dependencies map
            dependencies.clear();
            
            if (stacked_colgrp_copy == nullptr) {
                LOGW << "Failed to copy stacked_colgrp cell";
            } else {
                LOGI << "Successfully copied stacked_colgrp and all dependencies";
            
                // 創建新的 SRAM cell
                const char* sram_cell_name = sram_cell_name_str.c_str();
                
                // 先檢查是否已存在
                if (ctrl_decode_gds.get_cell(sram_cell_name) != nullptr) {
                    LOGW << "Cell '" << sram_cell_name << "' already exists, removing it first";
                    for (uint64_t i = 0; i < ctrl_decode_gds.cell_array.count; i++) {
                        if (strcmp(ctrl_decode_gds.cell_array[i]->name, sram_cell_name) == 0) {
                            ctrl_decode_gds.cell_array[i]->free_all();
                            ctrl_decode_gds.cell_array.remove(i);
                            break;
                        }
                    }
                }
                
                LOGI << "Creating integrated SRAM cell: " << sram_cell_name;
                gdstk::Cell* sram_cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
                sram_cell->init(sram_cell_name);
                
                // 計算每個 component 的 Y 位置和最大寬度
                double y_offset = 0.0;
                double max_width = 0.0;
                
                // 1. 底部：filler_bottom
                if (filler_bottom_cell != nullptr && filler_bottom_size.valid) {
                    LOGI << "Adding filler_bottom at y=" << y_offset;
                    gdstk::Reference* filler_bottom_ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
                    filler_bottom_ref->init(filler_bottom_cell);
                    filler_bottom_ref->origin = {-filler_bottom_size.min.x - 0.054, y_offset - filler_bottom_size.min.y};
                    filler_bottom_ref->magnification = 1.0;
                    sram_cell->reference_array.append(filler_bottom_ref);

                    y_offset += filler_bottom_size.height - 0.054;
                    max_width = std::max(max_width, filler_bottom_size.width);
                }
                
                // 2. 底部：stacked_colgrp
                LOGI << "Adding bottom stacked_colgrp at y=" << y_offset;
                gdstk::Reference* bottom_ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
                bottom_ref->init(stacked_colgrp_copy);
                bottom_ref->origin = {-0.054, y_offset + stacked_size.height};
                // bottom_ref->rotation = M_PI;  // 旋轉 180 度
                bottom_ref->x_reflection = true;
                bottom_ref->magnification = 1.0;
                sram_cell->reference_array.append(bottom_ref);
                
                y_offset += stacked_size.height + 0.0675;
                max_width = std::max(max_width, stacked_size.width + 0.054);
                
                // 3. 中間：ctrl_decode_with_filler
                if (ctrl_filler_cell != nullptr) {
                    LOGI << "Adding ctrl_decode_with_filler at y=" << y_offset;
                    gdstk::Reference* ctrl_ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
                    ctrl_ref->init(ctrl_filler_cell);
                    ctrl_ref->origin = {0.0, y_offset};
                    ctrl_ref->magnification = 1.0;
                    sram_cell->reference_array.append(ctrl_ref);
                    
                    y_offset += ctrl_filler_size.height;
                    max_width = std::max(max_width, ctrl_filler_size.width);
                    LOGD << ctrl_filler_size.height;
                }
                
                // 4. 頂部：stacked_colgrp（再次引用）
                LOGI << "Adding top stacked_colgrp at y=" << y_offset;
                gdstk::Reference* top_ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
                top_ref->init(stacked_colgrp_copy);
                top_ref->origin = {-0.054, y_offset + 0.0675};
                top_ref->magnification = 1.0;
                sram_cell->reference_array.append(top_ref);
                
                y_offset += stacked_size.height + 0.0675 - 0.027;
                
                // 5. 頂部：filler_top
                if (filler_top_cell != nullptr && filler_top_size.valid) {
                    LOGI << "Adding filler_top at y=" << y_offset;
                    gdstk::Reference* filler_top_ref = (gdstk::Reference*)gdstk::allocate_clear(sizeof(gdstk::Reference));
                    filler_top_ref->init(filler_top_cell);
                    filler_top_ref->origin = {-filler_top_size.min.x - 0.054, y_offset - filler_top_size.min.y - 0.027};
                    filler_top_ref->magnification = 1.0;
                    sram_cell->reference_array.append(filler_top_ref);

                    y_offset += filler_top_size.height;
                    max_width = std::max(max_width, filler_top_size.width);
                }
                
                // ================================================================
                // 6. 添加 SRAM Top Level Pins
                // Pins: vdd, vss, clk, rst_n, ce_n, we_n, A[addr_width-1:0], D[num_data_bits-1:0], Q[num_data_bits-1:0]
                // ================================================================
                LOGI << "========================================================================";
                LOGI << "Adding SRAM Top Level Pins";
                LOGI << "  addr_width = " << addr_width;
                LOGI << "  data_bits (num_data_bits) = " << num_data_bits;
                LOGI << "========================================================================";
                
                // 取得 M4 pin layer 用於添加 pins
                const OpenFinRAM::LayerDef* sram_m3_pin_layer = layer_map_.get_layer("M3", OpenFinRAM::LayerPurpose::Pin);
                const OpenFinRAM::LayerDef* sram_m4_pin_layer = layer_map_.get_layer("M4", OpenFinRAM::LayerPurpose::Pin);
                
                if (sram_m4_pin_layer != nullptr) {
                    gdstk::Tag pin_tag_m3 = gdstk::make_tag(sram_m3_pin_layer->layer_number, sram_m3_pin_layer->datatype);
                    gdstk::Tag pin_tag_m4 = gdstk::make_tag(sram_m4_pin_layer->layer_number, sram_m4_pin_layer->datatype);
                    
                    // 首先，使用 flatten 將 sram_cell 展平以獲取所有 labels 的絕對位置
                    // 創建一個臨時的 sram_cell 副本用於 flatten
                    gdstk::Cell* temp_sram = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
                    temp_sram->copy_from(*sram_cell, "temp_sram_flatten", true);
                    
                    // Flatten 所有 references
                    gdstk::Array<gdstk::Reference*> removed_refs = {};
                    temp_sram->flatten(true, removed_refs);
                    
                    LOGI << "Flattened SRAM cell, found " << temp_sram->label_array.count << " labels";
                    
                    // 用於儲存找到的 pin 位置
                    struct PinLocation {
                        const char* name;
                        double x;
                        double y;
                        bool found;
                    };
                    
                    // 定義需要搜尋的 pins (vdd, vss 從 flattened labels 中尋找)
                    PinLocation fixed_pins[] = {
                        {"VDD", 0.0, 0.0, false},
                        {"VSS", 0.0, 0.0, false}
                    };
                    
                    // 從 flattened labels 中尋找 VDD 和 VSS 的位置
                    for (uint64_t i = 0; i < temp_sram->label_array.count; ++i) {
                        gdstk::Label* label = temp_sram->label_array[i];
                        if (label->text == nullptr) continue;
                        
                        if (strcasecmp(label->text, "VDD") == 0 && !fixed_pins[0].found) {
                            fixed_pins[0].x = label->origin.x;
                            fixed_pins[0].y = label->origin.y;
                            fixed_pins[0].found = true;
                            LOGI << "  Found VDD at (" << label->origin.x << ", " << label->origin.y << ")";
                        } else if (strcasecmp(label->text, "VSS") == 0 && !fixed_pins[1].found) {
                            fixed_pins[1].x = label->origin.x;
                            fixed_pins[1].y = label->origin.y;
                            fixed_pins[1].found = true;
                            LOGI << "  Found VSS at (" << label->origin.x << ", " << label->origin.y << ")";
                        }
                    }
                    
                    // 添加 VDD pin
                    if (fixed_pins[0].found) {
                        gdstk::Label* vdd_pin = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                        vdd_pin->init("vdd");
                        vdd_pin->origin = {fixed_pins[0].x, fixed_pins[0].y};
                        vdd_pin->tag = pin_tag_m3;
                        vdd_pin->magnification = 0.02;
                        sram_cell->label_array.append(vdd_pin);
                        LOGI << "  Added vdd pin at (" << fixed_pins[0].x << ", " << fixed_pins[0].y << ")";
                    } else {
                        // 使用預設位置
                        gdstk::Label* vdd_pin = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                        vdd_pin->init("vdd");
                        vdd_pin->origin = {0.125, y_offset / 2.0};
                        vdd_pin->tag = pin_tag_m3;
                        vdd_pin->magnification = 0.02;
                        sram_cell->label_array.append(vdd_pin);
                        LOGW << "  VDD not found, using default position";
                    }
                    
                    // 添加 VSS pin
                    if (fixed_pins[1].found) {
                        gdstk::Label* vss_pin = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                        vss_pin->init("vss");
                        vss_pin->origin = {fixed_pins[1].x, fixed_pins[1].y};
                        vss_pin->tag = pin_tag_m3;
                        vss_pin->magnification = 0.02;
                        sram_cell->label_array.append(vss_pin);
                        LOGI << "  Added vss pin at (" << fixed_pins[1].x << ", " << fixed_pins[1].y << ")";
                    } else {
                        // 使用預設位置
                        gdstk::Label* vss_pin = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                        vss_pin->init("vss");
                        vss_pin->origin = {0.162, y_offset / 2.0};
                        vss_pin->tag = pin_tag_m3;
                        vss_pin->magnification = 0.02;
                        sram_cell->label_array.append(vss_pin);
                        LOGW << "  VSS not found, using default position";
                    }
                    
                    // 從 ctrl_decode cell 中尋找 clk, rst_n, ce_n, we_n 的位置
                    // 首先從 flattened labels 中搜尋
                    struct CtrlPinInfo {
                        const char* search_name;  // 在 flattened labels 中搜尋的名稱
                        const char* pin_name;     // 最終 pin 的名稱
                        double x;
                        double y;
                        bool found;
                    };
                    
                    CtrlPinInfo ctrl_pins[] = {
                        {"clk", "clk", 0.0, 0.0, false},
                        {"ce_n", "ce_n", 0.0, 0.0, false},
                        {"oe_n", "oe_n", 0.0, 0.0, false},
                        {"we_n", "we_n", 0.0, 0.0, false},
                        {"sdel[0]", "sdel[0]", 0.0, 0.0, false},
                        {"sdel[1]", "sdel[1]", 0.0, 0.0, false},
                        {"sdel[2]", "sdel[2]", 0.0, 0.0, false},
                        {"sdel[3]", "sdel[3]", 0.0, 0.0, false}
                    };
                    
                    // 搜尋 ctrl pins
                    for (uint64_t i = 0; i < temp_sram->label_array.count; ++i) {
                        gdstk::Label* label = temp_sram->label_array[i];
                        if (label->text == nullptr) continue;
                        
                        for (int j = 0; j < 8; ++j) {
                            if (!ctrl_pins[j].found && strcasecmp(label->text, ctrl_pins[j].search_name) == 0) {
                                ctrl_pins[j].x = label->origin.x;
                                ctrl_pins[j].y = label->origin.y;
                                ctrl_pins[j].found = true;
                                LOGI << "  Found " << ctrl_pins[j].search_name << " at (" 
                                        << label->origin.x << ", " << label->origin.y << ")";
                                break;
                            }
                        }
                    }
                    
                    // 添加 ctrl pins
                    double ctrl_pin_x = max_width * 0.5;  // 預設 x 位置
                    double ctrl_pin_y_base = y_offset / 2.0;  // 預設 y 基準位置
                    double ctrl_pin_spacing = 0.05;
                    
                    for (int j = 0; j < 8; ++j) {
                        gdstk::Label* ctrl_pin = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                        ctrl_pin->init(ctrl_pins[j].pin_name);
                        
                        if (ctrl_pins[j].found) {
                            ctrl_pin->origin = {ctrl_pins[j].x, ctrl_pins[j].y};
                        } else {
                            // 使用預設位置
                            ctrl_pin->origin = {ctrl_pin_x, ctrl_pin_y_base + j * ctrl_pin_spacing};
                            LOGW << "  " << ctrl_pins[j].search_name << " not found, using default position";
                        }
                        
                        ctrl_pin->tag = pin_tag_m3;
                        ctrl_pin->magnification = 0.02;
                        sram_cell->label_array.append(ctrl_pin);
                        LOGI << "  Added " << ctrl_pins[j].pin_name << " pin at (" 
                                << ctrl_pin->origin.x << ", " << ctrl_pin->origin.y << ")";
                    }
                    
                    // 添加 Address pins: A[0] to A[addr_width-1]
                    // 從 flattened labels 中搜尋 A[i] 的位置
                    LOGI << "  Adding Address pins A[0:" << (addr_width - 1) << "]";
                    
                    double addr_pin_x_base = max_width * 0.3;  // 預設 x 位置
                    double addr_pin_y_base = y_offset * 0.8;   // 預設 y 基準位置
                    double addr_pin_spacing = 0.03;
                    
                    for (uint64_t a = 0; a < addr_width; ++a) {
                        char addr_name[32];
                        snprintf(addr_name, sizeof(addr_name), "A[%lu]", (unsigned long)a);
                        
                        // 在 flattened labels 中搜尋
                        bool found = false;
                        double ax = addr_pin_x_base;
                        double ay = addr_pin_y_base - a * addr_pin_spacing;
                        
                        for (uint64_t i = 0; i < temp_sram->label_array.count; ++i) {
                            gdstk::Label* label = temp_sram->label_array[i];
                            if (label->text != nullptr && strcmp(label->text, addr_name) == 0) {
                                ax = label->origin.x;
                                ay = label->origin.y;
                                found = true;
                                break;
                            }
                        }
                        
                        gdstk::Label* addr_pin = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                        addr_pin->init(addr_name);
                        addr_pin->origin = {ax, ay};
                        // M3 pin layer matches the Innovus-era pin map of the
                        // golden srambank_32b reference (all top-level
                        // signal pins on M3, datatype 251).
                        addr_pin->tag = pin_tag_m3;
                        addr_pin->magnification = 0.02;
                        sram_cell->label_array.append(addr_pin);
                        
                        if (found) {
                            LOGI << "    Added " << addr_name << " at (" << ax << ", " << ay << ") [from label]";
                        } else {
                            LOGD << "    Added " << addr_name << " at (" << ax << ", " << ay << ") [default]";
                        }
                    }
                    
                    // ================================================================
                    // 添加 Data Input/Output pins: D[0] to D[num_data_bits-1], Q[0] to Q[num_data_bits-1]
                    // 
                    // 由於設計使用兩個 stacked_colgrp（底部和頂部），每個都有 D[0:num_data_bits/2-1] 和 Q[0:num_data_bits/2-1]
                    // 需要區分它們：
                    // - 底部 stacked_colgrp 的 D[i]/Q[i] → 對應 top cell 的 D[i]/Q[i]
                    // - 頂部 stacked_colgrp 的 D[i]/Q[i] → 對應 top cell 的 D[i+num_data_bits/2]/Q[i+num_data_bits/2]
                    // 
                    // 使用 y 座標來區分：ctrl_decode 的 y 位置作為分界線
                    // ================================================================
                    
                    uint64_t total_data_bits = num_data_bits;
                    uint64_t half_data_bits = num_data_bits / 2;
                    LOGI << "  Adding Data pins: D[0:" << (total_data_bits - 1) << "] and Q[0:" << (total_data_bits - 1) << "]";
                    LOGI << "    Bottom stacked_colgrp: D[0:" << (half_data_bits - 1) << "], Q[0:" << (half_data_bits - 1) << "]";
                    LOGI << "    Top stacked_colgrp: D[" << half_data_bits << ":" << (total_data_bits - 1) << "], Q[" << half_data_bits << ":" << (total_data_bits - 1) << "]";
                    
                    // 計算 ctrl_decode 的 y 位置作為底部/頂部的分界線
                    // 底部 stacked_colgrp 在 ctrl_decode 下方，頂部在上方
                    double bottom_stacked_y_start = (filler_bottom_cell != nullptr && filler_bottom_size.valid) 
                                                    ? (filler_bottom_size.height - 0.054) : 0.0;
                    double bottom_stacked_y_end = bottom_stacked_y_start + stacked_size.height;
                    double ctrl_y_start = bottom_stacked_y_end + 0.0675;
                    double ctrl_y_end = ctrl_y_start + ctrl_filler_size.height;
                    double top_stacked_y_start = ctrl_y_end + 0.0675;
                    
                    LOGI << "    Y boundaries: bottom=[" << bottom_stacked_y_start << "," << bottom_stacked_y_end 
                            << "], ctrl=[" << ctrl_y_start << "," << ctrl_y_end 
                            << "], top_start=" << top_stacked_y_start;
                    
                    // 收集所有 D 和 Q labels，按 y 座標分類
                    struct DataPinInfo {
                        double x;
                        double y;
                        uint64_t original_index;  // 原始 index（從 label 名稱解析）
                        bool is_bottom;           // true = 底部 stacked_colgrp
                        bool found;
                    };
                    
                    // 為 D 和 Q 各創建 num_stacked_rows 個 pin 位置
                    std::vector<DataPinInfo> d_pins(total_data_bits);
                    std::vector<DataPinInfo> q_pins(total_data_bits);
                    
                    // 初始化
                    double data_pin_x_base = max_width * 0.6;
                    double q_pin_x_base = max_width * 0.7;
                    double data_pin_y_base = y_offset * 0.2;
                    double data_pin_spacing = 0.03;
                    
                    for (uint64_t i = 0; i < total_data_bits; ++i) {
                        d_pins[i] = {data_pin_x_base, data_pin_y_base + i * data_pin_spacing, i, (i < half_data_bits), false};
                        q_pins[i] = {q_pin_x_base, data_pin_y_base + i * data_pin_spacing, i, (i < half_data_bits), false};
                    }
                    
                    // 從 flattened labels 中搜尋 D[i] 和 Q[i]，根據 y 座標分類
                    for (uint64_t i = 0; i < temp_sram->label_array.count; ++i) {
                        gdstk::Label* label = temp_sram->label_array[i];
                        if (label->text == nullptr) continue;
                        
                        // 檢查是否為 D[n] 格式
                        if (strncmp(label->text, "D[", 2) == 0) {
                            // 解析 index
                            int idx = atoi(label->text + 2);
                            if (idx >= 0 && idx < (int)half_data_bits) {
                                // 根據 y 座標判斷是底部還是頂部
                                bool is_bottom = (label->origin.y < ctrl_y_start);
                                uint64_t target_idx = is_bottom ? idx : (idx + half_data_bits);
                                
                                if (!d_pins[target_idx].found) {
                                    d_pins[target_idx].x = label->origin.x;
                                    d_pins[target_idx].y = label->origin.y;
                                    d_pins[target_idx].found = true;
                                    LOGD << "    Found D[" << idx << "] at y=" << label->origin.y 
                                            << " -> " << (is_bottom ? "bottom" : "top") << " -> D[" << target_idx << "]";
                                }
                            }
                        }
                        // 檢查是否為 Q[n] 格式
                        else if (strncmp(label->text, "Q[", 2) == 0) {
                            // 解析 index
                            int idx = atoi(label->text + 2);
                            if (idx >= 0 && idx < (int)half_data_bits) {
                                // 根據 y 座標判斷是底部還是頂部
                                bool is_bottom = (label->origin.y < ctrl_y_start);
                                uint64_t target_idx = is_bottom ? idx : (idx + half_data_bits);
                                
                                if (!q_pins[target_idx].found) {
                                    q_pins[target_idx].x = label->origin.x;
                                    q_pins[target_idx].y = label->origin.y;
                                    q_pins[target_idx].found = true;
                                    LOGD << "    Found Q[" << idx << "] at y=" << label->origin.y 
                                            << " -> " << (is_bottom ? "bottom" : "top") << " -> Q[" << target_idx << "]";
                                }
                            }
                        }
                    }
                    
                    // 添加 D pins
                    LOGI << "  Adding Data Input pins D[0:" << (total_data_bits - 1) << "]";
                    for (uint64_t d = 0; d < total_data_bits; ++d) {
                        char d_name[32];
                        snprintf(d_name, sizeof(d_name), "D[%lu]", (unsigned long)d);
                        
                        gdstk::Label* d_pin = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                        d_pin->init(d_name);
                        d_pin->origin = {d_pins[d].x, d_pins[d].y};
                        if (cli_options_.num_banks >= 2) {
                            d_pin->tag = pin_tag_m4;
                        } else {
                            d_pin->tag = pin_tag_m3;
                        }
                        
                        d_pin->magnification = 0.02;
                        sram_cell->label_array.append(d_pin);
                        
                        if (d_pins[d].found) {
                            LOGI << "    Added " << d_name << " at (" << d_pins[d].x << ", " << d_pins[d].y << ") [from label]";
                        } else {
                            LOGD << "    Added " << d_name << " at (" << d_pins[d].x << ", " << d_pins[d].y << ") [default]";
                        }
                    }
                    
                    // 添加 Q pins
                    LOGI << "  Adding Data Output pins Q[0:" << (total_data_bits - 1) << "]";
                    for (uint64_t q = 0; q < total_data_bits; ++q) {
                        char q_name[32];
                        snprintf(q_name, sizeof(q_name), "Q[%lu]", (unsigned long)q);
                        
                        gdstk::Label* q_pin = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
                        q_pin->init(q_name);
                        q_pin->origin = {q_pins[q].x, q_pins[q].y};
                        if (cli_options_.num_banks >= 2) {
                            q_pin->tag = pin_tag_m4;
                        } else {
                            q_pin->tag = pin_tag_m3;
                        }
                        
                        q_pin->magnification = 0.02;
                        sram_cell->label_array.append(q_pin);
                        
                        if (q_pins[q].found) {
                            LOGI << "    Added " << q_name << " at (" << q_pins[q].x << ", " << q_pins[q].y << ") [from label]";
                        } else {
                            LOGD << "    Added " << q_name << " at (" << q_pins[q].x << ", " << q_pins[q].y << ") [default]";
                        }
                    }

                    // ================================================================
                    // 取得並列印所有 M4 metal 的位置 (使用 flattened temp_sram)
                    // ================================================================
                    const OpenFinRAM::LayerDef* m4_drawing_layer = layer_map_.get_layer("M4", OpenFinRAM::LayerPurpose::Drawing);
                    if (m4_drawing_layer == nullptr) {
                        LOGW << "Cannot find M4 drawing layer definition, skipping M4 metal logging";
                    } else {
                        LOGI << "  Logging all M4 metal polygons (layer=" << m4_drawing_layer->layer_number
                                << ", datatype=" << m4_drawing_layer->datatype << ")";

                        struct M4Track {
                            double y_bottom;
                            double y_top;
                        };

                        const double kTrackPitch = 0.024;  // M4/V3 routing track pitch
                        const double kEps = 1e-6;
                        std::vector<M4Track> m4_tracks;

                        uint64_t m4_count = 0;
                        for (uint64_t i = 0; i < temp_sram->polygon_array.count; ++i) {
                            gdstk::Polygon* poly = temp_sram->polygon_array[i];
                            if (poly == nullptr) continue;

                            if (gdstk::get_layer(poly->tag) == m4_drawing_layer->layer_number) {
                                gdstk::Vec2 bb_min, bb_max;
                                poly->bounding_box(bb_min, bb_max);
                                LOGD << "    M4 metal #" << m4_count
                                        << " bbox=(" << bb_min.x << ", " << bb_min.y << ")-"
                                        << "(" << bb_max.x << ", " << bb_max.y << ")"
                                        << " size=(" << (bb_max.x - bb_min.x) << " x " << (bb_max.y - bb_min.y) << ")"
                                        << " datatype=" << gdstk::get_type(poly->tag);

                                // Record unique M4 track (by y_bottom/y_top)
                                bool exists = false;
                                for (const auto& t : m4_tracks) {
                                    if (std::fabs(t.y_bottom - bb_min.y) < kEps && std::fabs(t.y_top - bb_max.y) < kEps) {
                                        exists = true;
                                        break;
                                    }
                                }
                                if (!exists) {
                                    m4_tracks.push_back({bb_min.y, bb_max.y});
                                }

                                m4_count++;
                            }
                        }

                        LOGI << "  Total M4 metal polygons found: " << m4_count;

                        // ================================================================
                        // 在 SRAM top cell 上加入 VDD/VSS 專用的橫向 M4 與 V3
                        // 位置需對齊既有 M4 routing tracks (0.024 pitch)
                        // ================================================================
                        const OpenFinRAM::LayerDef* v3_drawing_layer = layer_map_.get_layer("V3", OpenFinRAM::LayerPurpose::Drawing);
                        if (v3_drawing_layer == nullptr) {
                            LOGW << "Cannot find V3 drawing layer definition, skipping V3 creation";
                        } else if (m4_tracks.empty()) {
                            LOGW << "No M4 tracks found, skipping VDD/VSS M4/V3 creation";
                        } else {
                            // SRAM 寬度 (用目前已組裝的 sram_cell bounding box)
                            gdstk::Vec2 sram_bb_min, sram_bb_max;
                            sram_cell->bounding_box(sram_bb_min, sram_bb_max);
                            const double sram_x_left = sram_bb_min.x;
                            const double sram_x_right = sram_bb_max.x;

                            // ------------------------------------------------------------
                            // V3 x-positions (offset-based method, same as colgrp V3 logic)
                            // ------------------------------------------------------------
                            std::vector<double> vdd_via_xs;
                            std::vector<double> vss_via_xs;
                            struct ExtraVia {
                                double x;
                                double width;
                            };
                            std::vector<ExtraVia> vdd_extra_vias;
                            std::vector<ExtraVia> vss_extra_vias;
                            auto dedupe_xs = [&](std::vector<double>& xs) {
                                std::vector<double> unique;
                                for (double x : xs) {
                                    bool exists = false;
                                    for (double u : unique) {
                                        if (std::fabs(u - x) < 1e-6) {
                                            exists = true;
                                            break;
                                        }
                                    }
                                    if (!exists) unique.push_back(x);
                                }
                                xs.swap(unique);
                            };

                            // Use geometric offsets only if required cells are available
                            if (sram_cells_.array_cell != nullptr && sram_cells_.cgedge != nullptr && sram_cells_.io_colgrp != nullptr) {
                                OpenFinRAM::CellSize array_size = OpenFinRAM::get_cell_size(sram_cells_.array_cell, layer_map_);
                                OpenFinRAM::CellSize filler_size = OpenFinRAM::get_cell_size(sram_cells_.cgedge, layer_map_);
                                OpenFinRAM::CellSize io_size = OpenFinRAM::get_cell_size(sram_cells_.io_colgrp, layer_map_);

                                if (!array_size.valid) {
                                    gdstk::Vec2 bb_min, bb_max;
                                    sram_cells_.array_cell->bounding_box(bb_min, bb_max);
                                    array_size.min = bb_min;
                                    array_size.max = bb_max;
                                    array_size.width = bb_max.x - bb_min.x;
                                    array_size.height = bb_max.y - bb_min.y;
                                    array_size.valid = true;
                                }
                                if (!filler_size.valid) {
                                    gdstk::Vec2 bb_min, bb_max;
                                    sram_cells_.cgedge->bounding_box(bb_min, bb_max);
                                    filler_size.min = bb_min;
                                    filler_size.max = bb_max;
                                    filler_size.width = bb_max.x - bb_min.x;
                                    filler_size.height = bb_max.y - bb_min.y;
                                    filler_size.valid = true;
                                }
                                if (!io_size.valid) {
                                    gdstk::Vec2 bb_min, bb_max;
                                    sram_cells_.io_colgrp->bounding_box(bb_min, bb_max);
                                    io_size.min = bb_min;
                                    io_size.max = bb_max;
                                    io_size.width = bb_max.x - bb_min.x;
                                    io_size.height = bb_max.y - bb_min.y;
                                    io_size.valid = true;
                                }

                                const double cell_width = cli_options_.bitcell_width;
                                const double dummy_width = cell_width;
                                const double left_array_x_start = filler_size.width;
                                const double io_x_offset = left_array_x_start + array_size.width;
                                const double right_array_x_start = io_x_offset + io_size.width;

                                const double vss_x_in_dummy = 0.0735;
                                const double vdd_x_in_dummy = 0.082;
                                const double power_via_width = 0.018;

                                auto add_x_left = [&](std::vector<double>& xs, double x_left) {
                                    xs.push_back(x_left + power_via_width / 2.0);
                                };

                                // 左側 array 左邊 dummy
                                add_x_left(vss_via_xs, left_array_x_start + vss_x_in_dummy - 0.0195 - power_via_width / 2.0);
                                add_x_left(vdd_via_xs, left_array_x_start + vdd_x_in_dummy - 0.064 - power_via_width / 2.0);

                                // 右側 array 右邊 dummy
                                add_x_left(vss_via_xs, right_array_x_start + array_size.width - vss_x_in_dummy + 0.0195 - power_via_width / 2.0);
                                add_x_left(vdd_via_xs, right_array_x_start + array_size.width - vdd_x_in_dummy + 0.064 - power_via_width / 2.0);

                                // 左側 array 右邊 dummy (sramcol 右側)
                                const double left_array_right_dummy_x = left_array_x_start + num_wls * cell_width;
                                add_x_left(vss_via_xs, left_array_right_dummy_x + vss_x_in_dummy - 0.0195 - power_via_width / 2.0 + dummy_width);
                                add_x_left(vdd_via_xs, left_array_right_dummy_x + vdd_x_in_dummy - 0.064 - power_via_width / 2.0 + 0.198);

                                // 右側 array 左邊 dummy (Y-flipped)
                                const double right_array_left_dummy_x = right_array_x_start + dummy_width;
                                add_x_left(vss_via_xs, right_array_left_dummy_x + dummy_width - vss_x_in_dummy + 0.0195 - power_via_width / 2.0);
                                add_x_left(vdd_via_xs, right_array_left_dummy_x + dummy_width - vdd_x_in_dummy + 0.064 - power_via_width / 2.0 - 0.09);

                                // Apply stacked_colgrp x offset (matches reference placement)
                                for (double& x : vdd_via_xs) x += sram_x_left;
                                for (double& x : vss_via_xs) x += sram_x_left;

                                dedupe_xs(vdd_via_xs);
                                dedupe_xs(vss_via_xs);

                                std::sort(vdd_via_xs.begin(), vdd_via_xs.end());
                                std::sort(vss_via_xs.begin(), vss_via_xs.end());

                                if (vdd_via_xs.size() >= 2) {
                                    double second_vdd = vdd_via_xs[1];
                                    vdd_extra_vias.push_back({second_vdd + 0.684, 0.09});
                                }
                                if (vss_via_xs.size() >= 2) {
                                    double second_vss = vss_via_xs[1];
                                    vss_extra_vias.push_back({second_vss + 1.246, 0.026});
                                    // vss_extra_vias.push_back({second_vss + 1.2465 + 0.1575, 0.036});
                                }

                                // Expand via locations across all muxed colgrps
                                if (num_banks > 1) {
                                    double colgrp_width = 0.0;
                                    if (filler_size.valid && array_size.valid && io_size.valid) {
                                        colgrp_width = filler_size.width * 2.0 + array_size.width * 2.0 + io_size.width;
                                    } else if (stacked_size.width > kEps) {
                                        colgrp_width = stacked_size.width / (double)num_banks;
                                    }

                                    if (colgrp_width > kEps) {
                                        std::vector<double> expanded_vdd_via_xs;
                                        std::vector<double> expanded_vss_via_xs;
                                        std::vector<ExtraVia> expanded_vdd_extra_vias;
                                        std::vector<ExtraVia> expanded_vss_extra_vias;

                                        expanded_vdd_via_xs.reserve(vdd_via_xs.size() * num_banks);
                                        expanded_vss_via_xs.reserve(vss_via_xs.size() * num_banks);
                                        expanded_vdd_extra_vias.reserve(vdd_extra_vias.size() * num_banks);
                                        expanded_vss_extra_vias.reserve(vss_extra_vias.size() * num_banks);

                                        for (uint64_t mux = 0; mux < num_banks; ++mux) {
                                            double mux_offset = (double)mux * colgrp_width;

                                            for (double x : vdd_via_xs) {
                                                expanded_vdd_via_xs.push_back(x + mux_offset);
                                            }
                                            for (double x : vss_via_xs) {
                                                expanded_vss_via_xs.push_back(x + mux_offset);
                                            }
                                            for (const auto& extra : vdd_extra_vias) {
                                                expanded_vdd_extra_vias.push_back({extra.x + mux_offset, extra.width});
                                            }
                                            for (const auto& extra : vss_extra_vias) {
                                                expanded_vss_extra_vias.push_back({extra.x + mux_offset, extra.width});
                                            }
                                        }

                                        vdd_via_xs.swap(expanded_vdd_via_xs);
                                        vss_via_xs.swap(expanded_vss_via_xs);
                                        vdd_extra_vias.swap(expanded_vdd_extra_vias);
                                        vss_extra_vias.swap(expanded_vss_extra_vias);
                                    } else {
                                        LOGW << "  Cannot determine colgrp width for mux expansion; skipping extra V3 replication";
                                    }
                                }
                            }

                            // 取得 VDD/VSS label 位置 (from flattened temp_sram)
                            struct PowerLabel {
                                const char* name;
                                double x;
                                double y;
                            };
                            std::vector<PowerLabel> power_labels;

                            for (uint64_t i = 0; i < temp_sram->label_array.count; ++i) {
                                gdstk::Label* label = temp_sram->label_array[i];
                                if (label == nullptr || label->text == nullptr) continue;
                                if (strcasecmp(label->text, "VDD") == 0 || strcasecmp(label->text, "VSS") == 0) {
                                    power_labels.push_back({label->text, label->origin.x, label->origin.y});
                                }
                            }

                            // helper: snap to M4 track grid (base M4 + n * 0.024) within region
                            auto snap_track_in_region = [&](double target_y, double region_y_min, double region_y_max, M4Track& out_track) -> bool {
                                bool found = false;
                                double best_dist = DBL_MAX;

                                for (const auto& base : m4_tracks) {
                                    double base_center = 0.5 * (base.y_bottom + base.y_top);
                                    double height = base.y_top - base.y_bottom;
                                    if (height <= kEps) continue;

                                    // snap by integer multiples of pitch
                                    long n = lround((target_y - base_center) / kTrackPitch);
                                    double snapped_center = base_center + n * kTrackPitch;

                                    // adjust to fit within region if needed
                                    if (snapped_center < region_y_min - kEps) {
                                        n = (long)std::ceil((region_y_min - base_center) / kTrackPitch);
                                        snapped_center = base_center + n * kTrackPitch;
                                    } else if (snapped_center > region_y_max + kEps) {
                                        n = (long)std::floor((region_y_max - base_center) / kTrackPitch);
                                        snapped_center = base_center + n * kTrackPitch;
                                    }

                                    double y_bottom = snapped_center - height / 2.0;
                                    double y_top = snapped_center + height / 2.0;

                                    if (y_bottom < region_y_min - kEps || y_top > region_y_max + kEps) {
                                        continue;
                                    }

                                    double dist = std::fabs(snapped_center - target_y);
                                    if (dist < best_dist) {
                                        best_dist = dist;
                                        out_track = {y_bottom, y_top};
                                        found = true;
                                    }
                                }

                                return found;
                            };

                            // helper: snap to M4 track grid while avoiding a nearby track center
                            auto snap_track_in_region_avoiding = [&](double target_y,
                                                                        double region_y_min,
                                                                        double region_y_max,
                                                                        double avoid_center,
                                                                        double min_center_delta,
                                                                        M4Track& out_track) -> bool {
                                bool found = false;
                                double best_dist = DBL_MAX;
                                const double region_height = region_y_max - region_y_min;
                                const int max_steps = (region_height > kEps) ? (int)std::ceil(region_height / kTrackPitch) + 2 : 4;

                                for (int step = 0; step <= max_steps; ++step) {
                                    for (int sign_idx = 0; sign_idx < 3; ++sign_idx) {
                                        double sign = (sign_idx == 0) ? 0.0 : (sign_idx == 1 ? 1.0 : -1.0);
                                        if (step == 0 && sign_idx > 0) continue;
                                        double candidate_target = target_y + sign * step * kTrackPitch;

                                        M4Track candidate;
                                        if (!snap_track_in_region(candidate_target, region_y_min, region_y_max, candidate)) {
                                            continue;
                                        }

                                        double candidate_center = 0.5 * (candidate.y_bottom + candidate.y_top);
                                        if (std::fabs(candidate_center - avoid_center) < min_center_delta) {
                                            continue;
                                        }

                                        double dist = std::fabs(candidate_center - target_y);
                                        if (dist < best_dist) {
                                            best_dist = dist;
                                            out_track = candidate;
                                            found = true;
                                        }
                                    }
                                    if (found) break;
                                }

                                return found;
                            };

                            // helper: check if M4 already exists across full stacked width
                            auto has_existing_m4 = [&](double y_bottom, double y_top) -> bool {
                                for (uint64_t i = 0; i < sram_cell->polygon_array.count; ++i) {
                                    gdstk::Polygon* poly = sram_cell->polygon_array[i];
                                    if (poly == nullptr) continue;
                                    if (gdstk::get_layer(poly->tag) != m4_drawing_layer->layer_number) {
                                        continue;
                                    }
                                    gdstk::Vec2 bb_min, bb_max;
                                    poly->bounding_box(bb_min, bb_max);
                                    if (std::fabs(bb_min.y - y_bottom) < kEps && std::fabs(bb_max.y - y_top) < kEps) {
                                        if (bb_min.x <= sram_x_left + kEps && bb_max.x >= sram_x_right - kEps) {
                                            return true;
                                        }
                                    }
                                }
                                return false;
                            };

                            // helper: add horizontal M4 + multiple V3s per stacked row in a region
                            auto add_power_m4_v3_in_region = [&](double region_y_min, double region_y_max, const char* region_name) {
                                LOGI << "  Adding VDD/VSS M4/V3 in " << region_name
                                        << " region y=[" << region_y_min << ", " << region_y_max << "]";
                                const uint64_t rows_per_region = num_data_bits / 2;
                                const double row_pitch = (rows_per_region > 0) ? (stacked_size.height / rows_per_region) : (cli_options_.bitcell_height * cli_options_.num_rows_per_mux);

                                auto pick_label_in_row = [&](const char* target, double row_min, double row_max, double row_center) -> const PowerLabel* {
                                    const PowerLabel* best = nullptr;
                                    double best_dist = DBL_MAX;
                                    for (const auto& p : power_labels) {
                                        if (strcasecmp(p.name, target) != 0) continue;
                                        if (p.y < row_min - kEps || p.y > row_max + kEps) continue;
                                        double dist = std::fabs(p.y - row_center);
                                        if (dist < best_dist) {
                                            best_dist = dist;
                                            best = &p;
                                        }
                                    }
                                    return best;
                                };

                                auto add_power_with_multiple_vias_on_track = [&](const char* net_name, const M4Track& track, double row_min, double row_max) {
                                    double track_height = track.y_top - track.y_bottom;
                                    if (track_height <= kEps) {
                                        LOGW << "    Invalid M4 track height, skipping " << net_name;
                                        return;
                                    }

                                    if (!has_existing_m4(track.y_bottom, track.y_top)) {
                                        gdstk::Polygon* m4_rect = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
                                        gdstk::Vec2 points[4] = {
                                            {sram_x_left, track.y_bottom},
                                            {sram_x_right, track.y_bottom},
                                            {sram_x_right, track.y_top},
                                            {sram_x_left, track.y_top}
                                        };
                                        m4_rect->point_array.extend({.capacity = 0, .count = 4, .items = points});
                                        m4_rect->tag = gdstk::make_tag(m4_drawing_layer->layer_number, m4_drawing_layer->datatype);
                                        sram_cell->polygon_array.append(m4_rect);
                                        LOGD << "    Added M4 (" << net_name << ") at y=[" << track.y_bottom << ", " << track.y_top
                                                << "] x=[" << sram_x_left << ", " << sram_x_right << "]";
                                    } else {
                                        LOGD << "    M4 track already exists at y=[" << track.y_bottom << ", " << track.y_top << "]";
                                    }

                                    const double via_width = 0.018;
                                    double via_height = std::min(kTrackPitch, track_height);
                                    double via_y_bottom = track.y_bottom;
                                    double via_y_top = via_y_bottom + via_height;
                                    const bool is_vdd = (strcasecmp(net_name, "VDD") == 0);
                                    const std::vector<double>& via_xs = is_vdd ? vdd_via_xs : vss_via_xs;
                                    const std::vector<ExtraVia>& extra_vias = is_vdd ? vdd_extra_vias : vss_extra_vias;

                                    if (!via_xs.empty()) {
                                        for (double x : via_xs) {
                                            double via_x_left = x - via_width / 2.0;
                                            double via_x_right = x + via_width / 2.0;

                                            gdstk::Polygon* via = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
                                            gdstk::Vec2 via_points[4] = {
                                                {via_x_left, via_y_bottom},
                                                {via_x_right, via_y_bottom},
                                                {via_x_right, via_y_top},
                                                {via_x_left, via_y_top}
                                            };
                                            via->point_array.extend({.capacity = 0, .count = 4, .items = via_points});
                                            via->tag = gdstk::make_tag(v3_drawing_layer->layer_number, v3_drawing_layer->datatype);
                                            sram_cell->polygon_array.append(via);
                                        }

                                        for (const auto& extra : extra_vias) {
                                            double extra_x_left = extra.x - extra.width / 2.0;
                                            double extra_x_right = extra.x + extra.width / 2.0;

                                            gdstk::Polygon* via = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
                                            gdstk::Vec2 via_points[4] = {
                                                {extra_x_left, via_y_bottom},
                                                {extra_x_right, via_y_bottom},
                                                {extra_x_right, via_y_top},
                                                {extra_x_left, via_y_top}
                                            };
                                            via->point_array.extend({.capacity = 0, .count = 4, .items = via_points});
                                            via->tag = gdstk::make_tag(v3_drawing_layer->layer_number, v3_drawing_layer->datatype);
                                            sram_cell->polygon_array.append(via);
                                        }
                                    } else {
                                        for (const auto& p : power_labels) {
                                            if (strcasecmp(p.name, net_name) != 0) continue;
                                            if (p.y < row_min - kEps || p.y > row_max + kEps) continue;

                                            double via_x_left = p.x - via_width / 2.0;
                                            double via_x_right = p.x + via_width / 2.0;

                                            gdstk::Polygon* via = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
                                            gdstk::Vec2 via_points[4] = {
                                                {via_x_left, via_y_bottom},
                                                {via_x_right, via_y_bottom},
                                                {via_x_right, via_y_top},
                                                {via_x_left, via_y_top}
                                            };
                                            via->point_array.extend({.capacity = 0, .count = 4, .items = via_points});
                                            via->tag = gdstk::make_tag(v3_drawing_layer->layer_number, v3_drawing_layer->datatype);
                                            sram_cell->polygon_array.append(via);
                                        }
                                    }
                                };

                                for (uint64_t row = 0; row < rows_per_region; ++row) {
                                    double row_min = region_y_min + row * row_pitch;
                                    double row_max = row_min + row_pitch;
                                    double row_center = 0.5 * (row_min + row_max);

                                    const PowerLabel* vdd = pick_label_in_row("VDD", row_min, row_max, row_center);
                                    const PowerLabel* vss = pick_label_in_row("VSS", row_min, row_max, row_center);

                                    double vdd_target_y = vdd ? vdd->y : row_center;
                                    double vss_target_y = vss ? vss->y : row_center;

                                    const double min_center_delta = 2.0 * kTrackPitch - kEps; // avoid adjacent tracks (touching)
                                    auto track_center = [&](const M4Track& t) { return 0.5 * (t.y_bottom + t.y_top); };

                                    M4Track vdd_track;
                                    M4Track vss_track;
                                    bool vdd_ok = snap_track_in_region(vdd_target_y, region_y_min, region_y_max, vdd_track);
                                    bool vss_ok = false;

                                    if (vdd_ok) {
                                        double vdd_center = track_center(vdd_track);
                                        vss_ok = snap_track_in_region_avoiding(vss_target_y, region_y_min, region_y_max,
                                                                                vdd_center, min_center_delta, vss_track);
                                    }

                                    if (!vss_ok) {
                                        bool vss_first_ok = snap_track_in_region(vss_target_y, region_y_min, region_y_max, vss_track);
                                        if (vss_first_ok) {
                                            double vss_center = track_center(vss_track);
                                            vdd_ok = snap_track_in_region_avoiding(vdd_target_y, region_y_min, region_y_max,
                                                                                    vss_center, min_center_delta, vdd_track);
                                        }
                                        vss_ok = vss_first_ok;
                                    }

                                    if (vdd_ok) {
                                        add_power_with_multiple_vias_on_track("VDD", vdd_track, row_min, row_max);
                                    } else {
                                        LOGW << "    No M4 track found near VDD at y=" << vdd_target_y;
                                    }

                                    if (vss_ok) {
                                        add_power_with_multiple_vias_on_track("VSS", vss_track, row_min, row_max);
                                    } else {
                                        LOGW << "    No M4 track found near VSS at y=" << vss_target_y;
                                    }
                                }
                            };

                            // bottom / top stacked_colgrp regions
                            const double bottom_region_min = bottom_stacked_y_start;
                            const double bottom_region_max = bottom_stacked_y_end;
                            const double top_region_min = top_stacked_y_start;
                            const double top_region_max = top_stacked_y_start + stacked_size.height;

                            add_power_m4_v3_in_region(bottom_region_min, bottom_region_max, "bottom stacked_colgrp");
                            add_power_m4_v3_in_region(top_region_min, top_region_max, "top stacked_colgrp");

                            // =============================================================
                            // Add horizontal D/Q straps on M4 when muxed (num_mux >= 2)
                            // =============================================================
                            if (num_banks >= 2) {
                                struct DqLabel {
                                    bool is_d;
                                    double x;
                                    double y;
                                };
                                std::vector<DqLabel> d_labels;
                                std::vector<DqLabel> q_labels;
                                d_labels.reserve(temp_sram->label_array.count);
                                q_labels.reserve(temp_sram->label_array.count);

                                for (uint64_t i = 0; i < temp_sram->label_array.count; ++i) {
                                    gdstk::Label* label = temp_sram->label_array[i];
                                    if (label == nullptr || label->text == nullptr) continue;
                                    const char* text = label->text;
                                    if (text[1] != '[') continue;
                                    if (text[0] == 'D') {
                                        d_labels.push_back({true, label->origin.x, label->origin.y});
                                    } else if (text[0] == 'Q') {
                                        q_labels.push_back({false, label->origin.x + 0.0025, label->origin.y});
                                    }
                                }

                                auto snap_track_in_region_avoiding_existing = [&](double target_y,
                                                                                    double region_y_min,
                                                                                    double region_y_max,
                                                                                    M4Track& out_track) -> bool {
                                    const double region_height = region_y_max - region_y_min;
                                    const int max_steps = (region_height > kEps) ? (int)std::ceil(region_height / kTrackPitch) + 2 : 4;

                                    for (int step = 0; step <= max_steps; ++step) {
                                        for (int sign_idx = 0; sign_idx < 3; ++sign_idx) {
                                            double sign = (sign_idx == 0) ? 0.0 : (sign_idx == 1 ? 1.0 : -1.0);
                                            if (step == 0 && sign_idx > 0) continue;
                                            double candidate_target = target_y + sign * step * kTrackPitch;

                                            M4Track candidate;
                                            if (!snap_track_in_region(candidate_target, region_y_min, region_y_max, candidate)) {
                                                continue;
                                            }

                                            if (has_existing_m4(candidate.y_bottom, candidate.y_top)) {
                                                continue;
                                            }

                                            out_track = candidate;
                                            return true;
                                        }
                                    }

                                    return false;
                                };

                                auto snap_track_in_region_avoiding_existing_and_center = [&](double target_y,
                                                                                                double region_y_min,
                                                                                                double region_y_max,
                                                                                                double avoid_center,
                                                                                                double min_center_delta,
                                                                                                M4Track& out_track) -> bool {
                                    const double region_height = region_y_max - region_y_min;
                                    const int max_steps = (region_height > kEps) ? (int)std::ceil(region_height / kTrackPitch) + 2 : 4;

                                    for (int step = 0; step <= max_steps; ++step) {
                                        for (int sign_idx = 0; sign_idx < 3; ++sign_idx) {
                                            double sign = (sign_idx == 0) ? 0.0 : (sign_idx == 1 ? 1.0 : -1.0);
                                            if (step == 0 && sign_idx > 0) continue;
                                            double candidate_target = target_y + sign * step * kTrackPitch;

                                            M4Track candidate;
                                            if (!snap_track_in_region(candidate_target, region_y_min, region_y_max, candidate)) {
                                                continue;
                                            }

                                            double candidate_center = 0.5 * (candidate.y_bottom + candidate.y_top);
                                            if (std::fabs(candidate_center - avoid_center) < min_center_delta) {
                                                continue;
                                            }

                                            if (has_existing_m4(candidate.y_bottom, candidate.y_top)) {
                                                continue;
                                            }

                                            out_track = candidate;
                                            return true;
                                        }
                                    }

                                    return false;
                                };

                                auto add_m4_full_width = [&](const M4Track& track) {
                                    if (has_existing_m4(track.y_bottom, track.y_top)) {
                                        return;
                                    }
                                    gdstk::Polygon* m4_rect = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
                                    gdstk::Vec2 points[4] = {
                                        {sram_x_left, track.y_bottom},
                                        {sram_x_right, track.y_bottom},
                                        {sram_x_right, track.y_top},
                                        {sram_x_left, track.y_top}
                                    };
                                    m4_rect->point_array.extend({.capacity = 0, .count = 4, .items = points});
                                    m4_rect->tag = gdstk::make_tag(m4_drawing_layer->layer_number, m4_drawing_layer->datatype);
                                    sram_cell->polygon_array.append(m4_rect);
                                };

                                auto add_vias_for_labels = [&](const std::vector<DqLabel>& labels, const M4Track& track,
                                                                double region_y_min, double region_y_max) {
                                    double track_height = track.y_top - track.y_bottom;
                                    if (track_height <= kEps) return;
                                    const double via_width = 0.018;
                                    double via_height = std::min(kTrackPitch, track_height);
                                    double via_y_bottom = track.y_bottom;
                                    double via_y_top = via_y_bottom + via_height;

                                    for (const auto& label : labels) {
                                        if (label.y < region_y_min - kEps || label.y > region_y_max + kEps) continue;
                                        double via_x_left = label.x - via_width / 2.0;
                                        double via_x_right = label.x + via_width / 2.0;

                                        gdstk::Polygon* via = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
                                        gdstk::Vec2 via_points[4] = {
                                            {via_x_left, via_y_bottom},
                                            {via_x_right, via_y_bottom},
                                            {via_x_right, via_y_top},
                                            {via_x_left, via_y_top}
                                        };
                                        via->point_array.extend({.capacity = 0, .count = 4, .items = via_points});
                                        via->tag = gdstk::make_tag(v3_drawing_layer->layer_number, v3_drawing_layer->datatype);
                                        sram_cell->polygon_array.append(via);
                                    }
                                };

                                auto add_dq_pair_in_region = [&](double region_y_min, double region_y_max, const char* region_name) {
                                    (void)region_name;
                                    const uint64_t rows_per_region = num_data_bits / 2;
                                    const double row_pitch = (rows_per_region > 0) ? (stacked_size.height / rows_per_region) : (cli_options_.bitcell_height * cli_options_.num_rows_per_mux);

                                    for (uint64_t row = 0; row < rows_per_region; ++row) {
                                        double row_min = region_y_min + row * row_pitch;
                                        double row_max = row_min + row_pitch;

                                        double d_target_y = 0.0;
                                        double q_target_y = 0.0;
                                        size_t d_count = 0;
                                        size_t q_count = 0;

                                        for (const auto& label : d_labels) {
                                            if (label.y < row_min - kEps || label.y > row_max + kEps) continue;
                                            d_target_y += label.y;
                                            ++d_count;
                                        }
                                        for (const auto& label : q_labels) {
                                            if (label.y < row_min - kEps || label.y > row_max + kEps) continue;
                                            q_target_y += label.y;
                                            ++q_count;
                                        }

                                        if (d_count == 0 || q_count == 0) {
                                            continue;
                                        }

                                        d_target_y /= (double)d_count;
                                        q_target_y /= (double)q_count;

                                        M4Track d_track;
                                        if (!snap_track_in_region_avoiding_existing(d_target_y, row_min, row_max, d_track)) {
                                            continue;
                                        }

                                        const double min_center_delta = 2.0 * kTrackPitch - kEps;
                                        M4Track q_track;
                                        double d_center = 0.5 * (d_track.y_bottom + d_track.y_top);
                                        if (!snap_track_in_region_avoiding_existing_and_center(q_target_y, row_min, row_max,
                                                                                                d_center, min_center_delta, q_track)) {
                                            continue;
                                        }

                                        LOGD << "  Adding D/Q M4 straps in " << region_name
                                                << " row " << row
                                                << " at D y=[" << d_track.y_bottom << ", " << d_track.y_top << "]"
                                                << " and Q y=[" << q_track.y_bottom << ", " << q_track.y_top << "]";

                                        add_m4_full_width(d_track);
                                        add_m4_full_width(q_track);

                                        add_vias_for_labels(d_labels, d_track, row_min, row_max);
                                        add_vias_for_labels(q_labels, q_track, row_min, row_max);
                                    }
                                };

                                LOGD << "Adding D/Q M4 straps for muxed SRAM (num_mux = " << num_banks << ")"
                                        << bottom_region_min << " to " << bottom_region_max
                                        << " and " << top_region_min << " to " << top_region_max;
                                add_dq_pair_in_region(bottom_region_min, bottom_region_max, "bottom stacked_colgrp");
                                add_dq_pair_in_region(top_region_min - 0.31, top_region_max, "top stacked_colgrp");
                            }
                        }
                    }
                    
                    // 清理臨時 cell 和 removed references
                    for (uint64_t i = 0; i < removed_refs.count; ++i) {
                        removed_refs[i]->clear();
                        gdstk::free_allocation(removed_refs[i]);
                    }
                    removed_refs.clear();
                    
                    temp_sram->clear();
                    gdstk::free_allocation(temp_sram);
                    
                    // 統計添加的 pins
                    uint64_t total_pins = 2 + 4 + addr_width + total_data_bits * 2;  // vdd, vss + clk, rst_n, ce_n, we_n + A[] + D[] + Q[]
                    LOGI << "========================================================================";
                    LOGI << "Total pins added to SRAM top cell: " << total_pins;
                    LOGI << "  Power: vdd, vss";
                    LOGI << "  Control: clk, rst_n, ce_n, we_n";
                    LOGI << "  Address: A[0:" << (addr_width - 1) << "] (" << addr_width << " bits)";
                    LOGI << "  Data In: D[0:" << (total_data_bits - 1) << "] (" << total_data_bits << " bits)";
                    LOGI << "  Data Out: Q[0:" << (total_data_bits - 1) << "] (" << total_data_bits << " bits)";
                    LOGI << "========================================================================";
                    
                } else {
                    LOGW << "Cannot find M3 pin layer, skipping SRAM top level pins";
                }
                
                // 加入 BOUNDARY（包住所有 reference 的最外圍）
                OpenFinRAM::CellSize sram_size = OpenFinRAM::get_cell_size_from_boundary(sram_cell, layer_map_);
                if (!sram_size.valid) {
                    LOGW << "Cannot get SRAM size from BOUNDARY, using overall bounding box";
                    gdstk::Vec2 bb_min, bb_max;
                    sram_cell->bounding_box(bb_min, bb_max);
                    sram_size.min = bb_min;
                    sram_size.max = bb_max;
                    sram_size.width = bb_max.x - bb_min.x;
                    sram_size.height = bb_max.y - bb_min.y;
                    sram_size.valid = true;
                }
                gdstk::Vec2 boundary_min = sram_size.min;
                gdstk::Vec2 boundary_max = sram_size.max;
                gdstk::Polygon* boundary = OpenFinRAM::create_boundary_polygon(boundary_min, boundary_max, layer_map_);
                sram_cell->polygon_array.append(boundary);

                // 在最外層 boundary 上下各加一根 full-width fin
                const OpenFinRAM::LayerDef* sram_fin_layer = layer_map_.get_layer("fin", OpenFinRAM::LayerPurpose::Drawing);
                if (sram_fin_layer == nullptr) {
                    LOGW << "Cannot find fin drawing layer definition, skipping SRAM top/bottom full-width fin";
                } else {
                    const double full_fin_height = 0.007;
                    const double full_fin_bottom_y = boundary_min.y - 0.017;
                    const double full_fin_top_y = boundary_max.y + 0.01;

                    // bottom full-width fin
                    {
                        gdstk::Polygon* bottom_full_fin = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
                        gdstk::Vec2 points[4] = {
                            {boundary_min.x, full_fin_bottom_y},
                            {boundary_max.x, full_fin_bottom_y},
                            {boundary_max.x, full_fin_bottom_y + full_fin_height},
                            {boundary_min.x, full_fin_bottom_y + full_fin_height}
                        };
                        bottom_full_fin->point_array.extend({.capacity = 0, .count = 4, .items = points});
                        bottom_full_fin->tag = sram_fin_layer->tag();
                        sram_cell->polygon_array.append(bottom_full_fin);
                    }

                    // top full-width fin
                    {
                        gdstk::Polygon* top_full_fin = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
                        gdstk::Vec2 points[4] = {
                            {boundary_min.x, full_fin_top_y},
                            {boundary_max.x, full_fin_top_y},
                            {boundary_max.x, full_fin_top_y + full_fin_height},
                            {boundary_min.x, full_fin_top_y + full_fin_height}
                        };
                        top_full_fin->point_array.extend({.capacity = 0, .count = 4, .items = points});
                        top_full_fin->tag = sram_fin_layer->tag();
                        sram_cell->polygon_array.append(top_full_fin);
                    }
                }
                
                // 將 SRAM cell 加入 library
                ctrl_decode_gds.cell_array.append(sram_cell);
                
                LOGI << "========================================================================";
                LOGI << "SRAM cell created successfully!";
                LOGI << "  Cell name: " << sram_cell_name;
                LOGI << "  Total height: " << y_offset << " um";
                LOGI << "  Total width: " << max_width << " um";
                LOGI << "  Components:";
                if (filler_bottom_cell != nullptr) {
                    LOGI << "    - Bottom: filler_bottom (" << filler_bottom_size.height << " um)";
                }
                LOGI << "    - Bottom: stacked_colgrp (" << stacked_size.height << " um)";
                LOGI << "    - Middle: ctrl_decode_with_filler (" << ctrl_filler_size.height << " um)";
                LOGI << "    - Top: stacked_colgrp (" << stacked_size.height << " um)";
                if (filler_top_cell != nullptr) {
                    LOGI << "    - Top: filler_top (" << filler_top_size.height << " um)";
                }
                LOGI << "========================================================================";
            }
        }
    } else {
        LOGW << "Failed to read SRAM array GDS file: " << sram_array_gds_path;
    }
    
    // 寫回 GDS 檔案（在釋放 sram_array_lib 之前）- ensure results dir exists (SpiceIntegrator also creates it, but ensure for standalone GDS flow)
    std::string results_dir = join_path(get_current_dir_name(), "results");
    if (!directory_exists(results_dir)) create_directory(results_dir, nullptr);
    std::string cell_results_dir = join_path(results_dir, sram_cell_name_str + "_" + get_run_timestamp());
    if (!directory_exists(cell_results_dir)) create_directory(cell_results_dir, nullptr);
    LOGI << "Writing modified GDS back to file...";
    std::string output_gds_path = join_path(cell_results_dir, sram_cell_name_str + ".gds");
    LOGD << "Output GDS to: " << output_gds_path;
    // Use max_points=199 (default) for fracturing, not 0
    gdstk::ErrorCode write_error = ctrl_decode_gds.write_gds(output_gds_path.c_str(), 199, nullptr);
    
    if (write_error == gdstk::ErrorCode::NoError) {
        LOGI << "========================================================================";
        LOGI << "Successfully updated: " << output_gds_path;
        LOGI << "  New cells created:";
        LOGI << "    - ctrl_decode_with_filler (original ctrl_decode + fillers)";
        LOGI << "    - sram (integrated: stacked_colgrp + ctrl_decode_with_filler + stacked_colgrp)";
        LOGI << "    - FILLER_*_top (top filler row for SRAM array)";
        LOGI << "    - FILLER_*_bottom (bottom filler row for SRAM array)";
        LOGI << "  Original ctrl_decode cell preserved";
        LOGI << "  Added parameterized Gate polygons on left and right sides";
        LOGI << "  Added parameterized Fin polygons on left and right sides";
        LOGI << "  Added parameterized Gate filler rows on top and bottom";
        LOGI << "  Added parameterized Fin rows on top and bottom";
        LOGI << "========================================================================";
    } else {
        LOGW << "Failed to write GDS file";
    }
    
    // 寫入完成後才清理 libraries
    // 注意：必須在寫入後才釋放 sram_array_lib，因為複製的 cells 中的 references
    //       仍然指向 sram_array_lib 中的 cells
    if (sram_array_error == gdstk::ErrorCode::NoError && sram_array_lib.cell_array.count > 0) {
        LOGI << "Cleaning up SRAM array library...";
        // sram_array_lib.free_all();
    }
    
    // 清理 ctrl_decode_gds
    // ctrl_decode_gds.free_all();
    return true;
}

bool LayoutGenerator::gen_layout() {
    LOGD << "Generating layout...";

    if (!load_sram_gds()) {
        LOGE << "Failed to load SRAM GDS!";
        return false;
    }
    if (!extract_required_cells()) {
        LOGE << "Failed to extract required cells!";
        return false;
    }
    if (!create_sram_column()) {
        LOGE << "Failed to create SRAM column!";
        return false;
    }
    if (!create_sram_array()) {
        LOGE << "Failed to create SRAM array!";
        return false;
    }
    if (!create_colgrp()) {
        LOGE << "Failed to create column group!";
        return false;
    }
    if (!create_stacked_colgrp()) {
        LOGE << "Failed to create stacked column group!";
        return false;
    }
    if (!create_muxed_colgrp()) {
        LOGE << "Failed to create muxed column group!";
        return false;
    }
    if (!write_layout()) {
        LOGE << "Failed to write layout!";
        return false;
    }

    if (!add_ctrl_decode_gate_fin_wrappers()) {
        LOGE << "Failed to add control decode gate/fin wrappers!";
        return false;
    }
    if (!create_and_add_sram_filler_cells()) {
        LOGE << "Failed to create and add SRAM filler cells!";
        return false;
    }
    if (!run_sram_gds_integration_and_writeback()) {
        LOGE << "Failed to run SRAM GDS integration and writeback!";
        return false;
    }

    return true;
}
