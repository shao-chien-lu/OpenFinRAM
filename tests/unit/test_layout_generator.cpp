#include <gtest/gtest.h>

#include <filesystem>

#include "layout_generator.hpp"
#include "layermap.hpp"
#include "main_config_helpers.hpp"

namespace {

class ScopedCurrentPath {
public:
    explicit ScopedCurrentPath(const std::filesystem::path& path)
        : original_(std::filesystem::current_path()) {
        std::filesystem::current_path(path);
    }

    ~ScopedCurrentPath() {
        std::filesystem::current_path(original_);
    }

private:
    std::filesystem::path original_;
};

MainCliOptions dual_port_options(unsigned wordlines = 2, unsigned mux_rows = 4) {
    MainCliOptions options;
    options.single_port = false;
    options.num_wls = wordlines;
    options.num_rows_per_mux = mux_rows;
    return options;
}

}  // namespace

TEST(LayoutGenerator8T, LoadsConfiguredRoutedIoColumn) {
    ScopedCurrentPath cwd(REPO_ROOT);
    OpenFinRAM::LayerMap layer_map;
    layer_map.init_asap7_layermap();
    // A column is one unsplit array of 2*NUM_WL wordlines; the tracked library
    // carries the 2/32/64/128 ladder, so NUM_WL = 16 asks for its x32 column.
    LayoutGenerator generator(dual_port_options(16), layer_map);

    ASSERT_TRUE(generator.load_sram_gds());
    EXPECT_TRUE(generator.extract_required_cells());
}

TEST(LayoutGenerator8T, RejectsMissingParameterizedColumn) {
    ScopedCurrentPath cwd(REPO_ROOT);
    OpenFinRAM::LayerMap layer_map;
    layer_map.init_asap7_layermap();
    LayoutGenerator generator(dual_port_options(18), layer_map);

    ASSERT_TRUE(generator.load_sram_gds());
    EXPECT_FALSE(generator.extract_required_cells());
}

TEST(LayoutGenerator8T, RejectsUnsupportedMuxHeight) {
    ScopedCurrentPath cwd(REPO_ROOT);
    OpenFinRAM::LayerMap layer_map;
    layer_map.init_asap7_layermap();
    LayoutGenerator generator(dual_port_options(2, 2), layer_map);

    ASSERT_TRUE(generator.load_sram_gds());
    EXPECT_FALSE(generator.extract_required_cells());
}
