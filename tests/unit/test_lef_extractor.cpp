// Unit tests for the native GDSTK LEF exporter (src/lef_extractor.cpp).
//
// The fixtures are tiny synthetic GDS files built in-process with gdstk:
// a top cell with a BOUNDARY rectangle (layer 100) and metal pin shapes
// carrying direct pin-purpose labels (datatype 251) -- exactly the
// conventions the final OpenFinRAM macro follows.  The tests assert the
// emitted LEF structure (SIZE from BOUNDARY, PORT geometry normalized to
// the BOUNDARY corner) so the Cadence-Abstract replacement stays covered.

#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>

#include <gtest/gtest.h>
#include <gdstk/gdstk.hpp>

#include "lef_extractor.hpp"

#ifndef REPO_ROOT
#define REPO_ROOT "."
#endif

namespace {

char* dup_gds_string(const char* text) {
    uint64_t len = 0;
    return gdstk::copy_string(text, &len);
}

std::string read_file(const std::string& path) {
    std::ifstream in(path);
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

// Build a minimal macro GDS.  When with_boundary is false the BOUNDARY
// polygon is omitted to exercise the bounding-box fallback path.
std::string write_fixture_gds(const std::filesystem::path& dir,
                              const std::string& cell_name,
                              bool with_boundary,
                              uint16_t internal_layer = 0) {
    gdstk::Library lib = {};
    lib.init("fixture_lib", 1e-6, 1e-9);

    gdstk::Cell* cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
    cell->name = dup_gds_string(cell_name.c_str());
    lib.cell_array.append(cell);

    if (with_boundary) {
        gdstk::Polygon* boundary =
            (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
        boundary->tag = gdstk::make_tag(100, 0);  // BOUNDARY / drawing
        boundary->point_array.append(gdstk::Vec2{0.0, 0.0});
        boundary->point_array.append(gdstk::Vec2{10.0, 0.0});
        boundary->point_array.append(gdstk::Vec2{10.0, 5.0});
        boundary->point_array.append(gdstk::Vec2{0.0, 5.0});
        cell->polygon_array.append(boundary);
    }

    struct PinSpec {
        const char* name;
        uint16_t layer;
        double x0, y0, x1, y1;
    };
    const PinSpec pins[] = {
        {"CLK", 19, 2.000, 2.000, 2.054, 2.108},   // M1
        {"WLT0", 30, 8.000, 3.000, 8.162, 3.054},  // M3
    };
    for (const PinSpec& spec : pins) {
        gdstk::Polygon* shape = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
        shape->tag = gdstk::make_tag(spec.layer, 0);  // drawing purpose
        shape->point_array.append(gdstk::Vec2{spec.x0, spec.y0});
        shape->point_array.append(gdstk::Vec2{spec.x1, spec.y0});
        shape->point_array.append(gdstk::Vec2{spec.x1, spec.y1});
        shape->point_array.append(gdstk::Vec2{spec.x0, spec.y1});
        cell->polygon_array.append(shape);

        gdstk::Label* label = (gdstk::Label*)gdstk::allocate_clear(sizeof(gdstk::Label));
        label->tag = gdstk::make_tag(spec.layer, 251);  // pin purpose
        label->origin = gdstk::Vec2{(spec.x0 + spec.x1) / 2, (spec.y0 + spec.y1) / 2};
        label->text = dup_gds_string(spec.name);
        cell->label_array.append(label);
    }

    if (internal_layer) {
        auto* shape = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
        shape->tag = gdstk::make_tag(internal_layer, 0);
        shape->point_array.append(gdstk::Vec2{3, 3});
        shape->point_array.append(gdstk::Vec2{4, 3});
        shape->point_array.append(gdstk::Vec2{4, 4});
        shape->point_array.append(gdstk::Vec2{3, 4});
        cell->polygon_array.append(shape);
    }
    const std::string gds_path = (dir / (cell_name + ".gds")).string();
    gdstk::ErrorCode ec = lib.write_gds(gds_path.c_str(), 0, nullptr);
    EXPECT_EQ(ec, gdstk::ErrorCode::NoError);
    return gds_path;
}

}  // namespace

namespace OpenFinRAM {

TEST(LefExtractorTest, ExportsSizeAndPinsFromBoundary) {
    namespace fs = std::filesystem;
    fs::path dir = fs::temp_directory_path() / "openfinram_lef_test";
    fs::create_directories(dir);

    const std::string gds = write_fixture_gds(dir, "lef_test_macro", /*with_boundary=*/true);

    std::string out_path, error;
    ASSERT_TRUE(export_lef(dir.string(), "lef_test_macro", gds, &out_path, &error))
        << error;

    const std::string lef = read_file(out_path);
    EXPECT_NE(lef.find("MACRO lef_test_macro"), std::string::npos);
    EXPECT_NE(lef.find("CLASS BLOCK ;"), std::string::npos);
    // SIZE comes from the BOUNDARY polygon.
    EXPECT_NE(lef.find("SIZE 10.000000 BY 5.000000 ;"), std::string::npos);
    // No fallback warning when a BOUNDARY exists.
    EXPECT_EQ(lef.find("# WARNING"), std::string::npos);

    // Both pin labels become macro PINs on their metal layers.
    EXPECT_NE(lef.find("PIN CLK"), std::string::npos);
    EXPECT_NE(lef.find("LAYER M1 ;"), std::string::npos);
    EXPECT_NE(lef.find("PIN WLT0"), std::string::npos);
    EXPECT_NE(lef.find("LAYER M3 ;"), std::string::npos);
}

TEST(LefExtractorTest, BlocksUpperMetalUsedByMacroRouting) {
    auto dir = std::filesystem::temp_directory_path() / "openfinram_lef_upper_test";
    std::filesystem::create_directories(dir);
    const auto gds = write_fixture_gds(dir, "upper_macro", true, 80);
    std::string out_path, error;
    ASSERT_TRUE(export_lef(dir.string(), "upper_macro", gds, &out_path, &error)) << error;
    const auto lef = read_file(out_path);
    const auto obs = lef.substr(lef.find("  OBS"));
    EXPECT_NE(obs.find("LAYER M6 ;"), std::string::npos);
    EXPECT_NE(obs.find("LAYER M8 ;"), std::string::npos);
    EXPECT_EQ(obs.find("LAYER M9 ;"), std::string::npos);
}

TEST(LefExtractorTest, PinGeometryNormalizedToBoundaryCorner) {
    namespace fs = std::filesystem;
    fs::path dir = fs::temp_directory_path() / "openfinram_lef_test";
    fs::create_directories(dir);

    const std::string gds = write_fixture_gds(dir, "lef_norm_macro", true);

    std::string out_path, error;
    ASSERT_TRUE(export_lef(dir.string(), "lef_norm_macro", gds, &out_path, &error))
        << error;

    // Every RECT must fall inside the macro SIZE box (coordinates are
    // relative to the lower-left BOUNDARY corner).
    const std::string lef = read_file(out_path);
    std::istringstream in(lef);
    std::string line;
    bool in_pin = false;
    while (std::getline(in, line)) {
        if (line.rfind("  PIN ", 0) == 0) in_pin = true;
        if (line.find("RECT ") == std::string::npos || !in_pin) continue;
        double x0, y0, x1, y1;
        ASSERT_EQ(std::sscanf(line.c_str(), " %*s %lf %lf %lf %lf", &x0, &y0, &x1, &y1), 4u)
            << line;
        EXPECT_GE(x0, -1e-9);
        EXPECT_GE(y0, -1e-9);
        EXPECT_LE(x1, 10.0 + 1e-9);
        EXPECT_LE(y1, 5.0 + 1e-9);
    }
}

TEST(LefExtractorTest, MissingBoundaryFallsBackToBoundingBox) {
    namespace fs = std::filesystem;
    fs::path dir = fs::temp_directory_path() / "openfinram_lef_test";
    fs::create_directories(dir);

    const std::string gds = write_fixture_gds(dir, "lef_nobound_macro", /*with_boundary=*/false);

    std::string out_path, error;
    ASSERT_TRUE(export_lef(dir.string(), "lef_nobound_macro", gds, &out_path, &error))
        << error;

    const std::string lef = read_file(out_path);
    // Fallback: warning emitted, SIZE equals the full GDS bounding box
    // (pins span x=[2,8.162], y=[2,3.054]).
    EXPECT_NE(lef.find("# WARNING: no GDS BOUNDARY geometry was found"), std::string::npos);
    EXPECT_NE(lef.find("SIZE 6.162000 BY 1.054000 ;"), std::string::npos);
}

TEST(LefExtractorTest, MissingPinLabelsFailsWithError) {
    namespace fs = std::filesystem;
    fs::path dir = fs::temp_directory_path() / "openfinram_lef_test";
    fs::create_directories(dir);

    // Boundary-only fixture: no datatype-251 labels -> export must fail.
    gdstk::Library lib = {};
    lib.init("fixture_lib", 1e-6, 1e-9);
    gdstk::Cell* cell = (gdstk::Cell*)gdstk::allocate_clear(sizeof(gdstk::Cell));
    cell->name = dup_gds_string("lef_nopins_macro");
    lib.cell_array.append(cell);
    gdstk::Polygon* boundary = (gdstk::Polygon*)gdstk::allocate_clear(sizeof(gdstk::Polygon));
    boundary->tag = gdstk::make_tag(100, 0);
    boundary->point_array.append(gdstk::Vec2{0.0, 0.0});
    boundary->point_array.append(gdstk::Vec2{1.0, 0.0});
    boundary->point_array.append(gdstk::Vec2{1.0, 1.0});
    boundary->point_array.append(gdstk::Vec2{0.0, 1.0});
    cell->polygon_array.append(boundary);
    const std::string gds = (dir / "lef_nopins_macro.gds").string();
    ASSERT_EQ(lib.write_gds(gds.c_str(), 0, nullptr), gdstk::ErrorCode::NoError);

    std::string out_path, error;
    EXPECT_FALSE(export_lef(dir.string(), "lef_nopins_macro", gds, &out_path, &error));
    EXPECT_NE(error.find("pin labels"), std::string::npos);
}

}  // namespace OpenFinRAM
