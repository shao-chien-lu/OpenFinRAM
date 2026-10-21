// Golden-file unit tests for the deterministic TCL generators.
//
// YosysTclGenerator::generate_script and OpenRoadTclGenerator::generate_run_tcl
// are pure string emitters given fixed inputs; the only nondeterminism is the
// timestamped synthesis-work-directory path embedded in the OpenROAD script
// (netlist.v / timing.sdc locations).  The tests normalize machine-specific
// absolute paths and that timestamp before comparing against the golden files
// in tests/golden/, so the comparison is portable across machines.
//
// Regenerate goldens after an intentional generator change:
//   UPDATE_GOLDEN=1 ctest -R tcl_generator_golden --output-on-failure

#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <regex>
#include <sstream>
#include <string>

#include <gtest/gtest.h>

#include "main_config_helpers.hpp"
#include "openroad_tcl_generator.hpp"
#include "yosys_tcl_generator.hpp"

#ifndef REPO_ROOT
#define REPO_ROOT "."
#endif

namespace {

std::string read_file(const std::string& path) {
    std::ifstream in(path);
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

void write_file(const std::string& path, const std::string& content) {
    std::ofstream out(path);
    out << content;
}

// Replace machine-specific fragments so golden files stay portable.
std::string normalize(const std::string& text) {
    std::string out = text;
    const std::string repo = REPO_ROOT;
    // Absolute repo/build paths -> stable placeholders.
    std::size_t pos;
    while ((pos = out.find(repo)) != std::string::npos) {
        out.replace(pos, repo.size(), "@REPO_ROOT@");
    }
    // Timestamped synthesis dirs: tmp/syn_<date>_<time> -> tmp/syn_TS.
    out = std::regex_replace(out, std::regex("tmp/syn_[0-9]+_[0-9]+"), "tmp/syn_TS");
    // Whatever prefix remains on the synthesis root (build dir, exe dir).
    out = std::regex_replace(out, std::regex("[^ @\\n]*/tmp/syn_TS"), "@SYN_ROOT@/tmp/syn_TS");
    return out;
}

std::string golden_path(const std::string& name) {
    return std::string(REPO_ROOT) + "/tests/golden/" + name;
}

// Compare (or update, with UPDATE_GOLDEN=1) generated text against a golden file.
void expect_matches_golden(const std::string& name, const std::string& raw_generated) {
    std::string generated = normalize(raw_generated);
    if (std::getenv("UPDATE_GOLDEN") != nullptr) {
        write_file(golden_path(name), generated);
        GTEST_SUCCEED() << name << " golden updated";
        return;
    }
    ASSERT_TRUE(std::ifstream(golden_path(name)).good())
        << "missing golden file: " << golden_path(name);
    std::string expected = read_file(golden_path(name));
    ASSERT_EQ(expected, generated)
        << "Generated TCL differs from " << golden_path(name)
        << ". If the change is intentional, regenerate with UPDATE_GOLDEN=1.";
}

}  // namespace

namespace OpenFinRAM {

// ---------------------------------------------------------------------------
// YosysTclGenerator::generate_script
// ---------------------------------------------------------------------------

TEST(YosysTclGeneratorGoldenTest, GenerateScriptMatchesGolden) {
    YosysTclGenerator gen;

    const std::string rtl = std::string(REPO_ROOT) + "/tech/verilog_sp";
    const std::string tech_lib = std::string(REPO_ROOT) + "/tech/lib";
    // Empty platform path forces the tech/lib fallback branch, keeping the
    // emitted library paths inside the repo (and thus normalizable).
    const std::string platform = "";

    const std::string script = gen.generate_script(
        /*rtl_path=*/rtl,
        /*syn_path=*/"tmp/syn_fixed",
        /*param_str=*/"ADDR_WIDTH=6,NUM_WL=8,NUM_BANK=2",
        /*addr_width=*/6,
        /*num_wls=*/8,
        /*num_banks=*/2,
        /*column_mux=*/4,
        /*num_wl_buf=*/3,
        /*num_sae_buf=*/2,
        /*abc_load_ff=*/24.5,
        /*abc_delay_ps=*/2500.0,
        /*platform_path=*/platform,
        /*tech_lib_path=*/tech_lib);

    EXPECT_NE(script.find("chparam -set ADDR_WIDTH 6 -set NUM_WL 8 "
                          "-set NUM_BANK 2 -set COLUMN_MUX 4 ctrl_decode"),
              std::string::npos);
    EXPECT_NE(script.find("hierarchy -check -top ctrl_decode"), std::string::npos);
    EXPECT_NE(script.find("select -assert-count 13 t:DFFHQNx1_ASAP7_75t_R"),
              std::string::npos);  // addr_width + 7 state bits
    EXPECT_NE(script.find("select -assert-min 108 t:INVx1_ASAP7_75t_R"),
              std::string::npos);
    EXPECT_NE(script.find("select -assert-min 32 t:BUFx4_ASAP7_75t_R"),
              std::string::npos);  // 2 * num_wls * num_banks
    EXPECT_NE(script.find("write_verilog"), std::string::npos);

    expect_matches_golden("yosys_synth_ref.ys", script);
}

TEST(YosysTclGeneratorTest, DualPortSharesPortBIoOnlyWhenAsked) {
    YosysTclGenerator gen;
    auto script = [&](bool shared) {
        return gen.generate_script(
            std::string(REPO_ROOT) + "/tech/verilog_dp", "tmp/syn_dp_fixed",
            "ADDR_WIDTH=7,NUM_WL=8,NUM_BANK=2,COLUMN_MUX=4,WL_BUF=3,SAE_BUF=2",
            7, 8, 2, 4, 3, 2, 24.5, 2500.0, "", std::string(REPO_ROOT) + "/tech/lib",
            /*single_port=*/false, /*shared_port_b=*/shared);
    };
    EXPECT_NE(script(true).find("-set SAE_BUF 2 -set SHARED_B 1 ctrl_decode"), std::string::npos);
    EXPECT_EQ(script(false).find("SHARED_B"), std::string::npos);
}

TEST(YosysTclGeneratorTest, RowPredecodeGroupSetOnlyWhenNotThree) {
    YosysTclGenerator gen;
    auto script = [&](bool one_port, unsigned bits) {
        return gen.generate_script(
            std::string(REPO_ROOT) + "/tech/verilog_dp", "tmp/syn_dp_fixed",
            "ADDR_WIDTH=7,NUM_WL=8,NUM_BANK=2,COLUMN_MUX=4,WL_BUF=3,SAE_BUF=2",
            7, 8, 2, 4, 3, 2, 24.5, 2500.0, "", std::string(REPO_ROOT) + "/tech/lib",
            /*single_port=*/false, /*shared_port_b=*/false, one_port, bits);
    };
    EXPECT_NE(script(false, 4).find("-set SAE_BUF 2 -set ROW_PREDECODE 4 ctrl_decode"),
              std::string::npos);
    EXPECT_NE(script(true, 2).find("-set ROW_PREDECODE 2 ctrl_decode"), std::string::npos);
    EXPECT_EQ(script(false, 3).find("ROW_PREDECODE"), std::string::npos);
}

TEST(YosysTclGeneratorTest, DualPortChecksAndNamesExactPhysicalStructure) {
    YosysTclGenerator gen;
    const std::string script = gen.generate_script(
        /*rtl_path=*/std::string(REPO_ROOT) + "/tech/verilog_dp",
        /*syn_path=*/"tmp/syn_dp_fixed",
        /*param_str=*/"ADDR_WIDTH=7,NUM_WL=8,NUM_BANK=2,COLUMN_MUX=4,WL_BUF=3,SAE_BUF=2",
        /*addr_width=*/7,
        /*num_wls=*/8,
        /*num_banks=*/2,
        /*column_mux=*/4,
        /*num_wl_buf=*/3,
        /*num_sae_buf=*/2,
        /*abc_load_ff=*/24.5,
        /*abc_delay_ps=*/2500.0,
        /*platform_path=*/"",
        /*tech_lib_path=*/std::string(REPO_ROOT) + "/tech/lib",
        /*single_port=*/false);

    EXPECT_NE(script.find("-set WL_BUF 3 -set SAE_BUF 2 ctrl_decode"),
              std::string::npos);
    EXPECT_NE(script.find("/row_decoder.v"), std::string::npos);
    // The wordlines are driven by the slice strips at the array, not by a
    // buffer stage in the controller.
    EXPECT_EQ(script.find("physical_wl_driver"), std::string::npos);
    EXPECT_EQ(script.find("physical_wl_gate"), std::string::npos);
    EXPECT_NE(script.find("select -assert-count 18 t:*DFF*ASAP7_75t_R"),
              std::string::npos);  // 2 * addr_width + 4 state bits
    EXPECT_NE(script.find("select -assert-count 10 t:BUFx2_ASAP7_75t_R "
                          "a:physical_dp_delay %i"),
              std::string::npos);  // 2 * (WL_BUF + SAE_BUF)
    EXPECT_NE(script.find("rename -enumerate -pattern physical_dp_delay_%"),
              std::string::npos);
    EXPECT_EQ(script.find("select -assert-min 108"), std::string::npos);
}

// ---------------------------------------------------------------------------
// OpenRoadTclGenerator: floorplan math + QoR parsing (pure functions)
// ---------------------------------------------------------------------------

TEST(OpenRoadTclGeneratorUnitTest, ParseQorReportCellArea) {
    OpenRoadTclGenerator gen;
    const std::string qor = golden_path("qor_report.txt");
    ASSERT_TRUE(gen.parse_qor_report(qor));
    EXPECT_NEAR(gen.get_qor_report().cell_area, 123.45, 1e-9);
}

// A failed or silent controller conversion must stop the build rather than
// leave a partial or empty netlist for LVS (upstream PR #8).
TEST(OpenRoadTclGeneratorUnitTest, RejectsFailedOrMissingSpiceOutput) {
    char temporary[] = "/tmp/openfinram-spice-XXXXXX";
    ASSERT_NE(mkdtemp(temporary), nullptr);
    const std::filesystem::path dir(temporary);
    write_file((dir / "cells.cdl").string(), ".SUBCKT cell A Y\n.ENDS\n");
    const char* path = std::getenv("PATH");
    const std::string original_path = path ? path : "";
    const std::string test_path = dir.string() + ":" + original_path;
    setenv("PATH", test_path.c_str(), 1);
    OpenRoadTclGenerator gen;
    // The converter runs as `python3 scripts/mapped_verilog_to_spice.py ...`;
    // a stand-in python3 earlier on PATH plays a failing converter.
    for (const char* script : {
            "#!/bin/sh\nprintf 'partial netlist' > \"$7\"\nexit 7\n",
            "#!/bin/sh\nexit 0\n"}) {
        std::filesystem::remove(dir / "output.sp");
        write_file((dir / "python3").string(), script);
        std::filesystem::permissions(dir / "python3", std::filesystem::perms::owner_all);
        EXPECT_FALSE(gen.run_v2lvs(dir.string(), "input.v", "output.sp",
                                   (dir / "cells.cdl").string()));
    }
    if (path) setenv("PATH", original_path.c_str(), 1);
    else unsetenv("PATH");
    std::filesystem::remove_all(dir);
}

TEST(OpenRoadTclGeneratorUnitTest, AlignToSiteHeightSnapsToEvenRows) {
    OpenRoadTclGenerator gen;
    gen.set_site_height(0.27);
    // Must round up to an even multiple of the site height.
    EXPECT_NEAR(gen.align_to_site_height(0.54), 0.54, 1e-9);
    EXPECT_NEAR(gen.align_to_site_height(0.55), 1.08, 1e-9);
    EXPECT_NEAR(gen.align_to_site_height(0.01), 0.54, 1e-9);
}

TEST(OpenRoadTclGeneratorUnitTest, FloorplanHeightRespectsUtilizationCap) {
    OpenRoadTclGenerator gen;
    gen.set_site_height(0.27);
    ASSERT_TRUE(gen.parse_qor_report(golden_path("qor_report.txt")));
    // area=123.45, width=10 -> min_h=12.345um; at <=40% utilization the core
    // must be at least area/(width*0.40)=30.86um tall (snapped to site grid).
    const double h = gen.calculate_floorplan_height(10.0);
    EXPECT_GE(h, 30.0);
    EXPECT_NEAR(h / 0.54, std::nearbyint(h / 0.54), 1e-9);  // even row count
}

TEST(OpenRoadTclGeneratorUnitTest, FloorplanCommandFormat) {
    OpenRoadTclGenerator gen;
    gen.set_site_name("asap7sc7p5t");
    double w = 10.0, h = 1.08;
    const std::string cmd = gen.generate_floorplan_command(w, h);
    EXPECT_EQ(cmd,
              "initialize_floorplan -die_area \"0 0 10.000 1.080\""
              " -core_area \"0 0 10.000 1.080\" -site asap7sc7p5t");
    EXPECT_NEAR(w, 10.0, 1e-9);
    EXPECT_NEAR(h, 1.08, 1e-9);
}

// ---------------------------------------------------------------------------
// OpenRoadTclGenerator::generate_run_tcl (golden file)
// ---------------------------------------------------------------------------

TEST(OpenRoadTclGeneratorGoldenTest, RunTclMatchesGolden) {
    OpenRoadTclGenerator gen;
    gen.set_design_name("ctrl_decode");
    gen.set_site_name("asap7sc7p5t");
    gen.set_site_height(0.27);
    gen.set_cpu_count(8);
    // Resolve everything from tech/ (no external platform checkout needed).
    const std::string tech_root = std::string(REPO_ROOT) + "/tech";
    const std::string fake_platform = "/nonexistent/platform/asap7";

    ASSERT_TRUE(gen.parse_qor_report(golden_path("qor_report.txt")));

    const std::string out =
        (std::filesystem::temp_directory_path() / "or_run_tcl_test" / "run.tcl").string();
    // NUM_WLT=8, NUM_MUX=4 -> 64 dedicated wordline drivers expected.
    ASSERT_TRUE(gen.generate_run_tcl(
        /*width=*/10.0, /*height=*/0.0, /*output_file=*/out,
        /*num_wlt=*/8, /*num_wlb=*/8, /*num_ysel=*/4,
        /*addr_width=*/6, /*num_mux=*/4,
        /*spice_only=*/false, /*col_width=*/9.396,
        /*platform_path=*/fake_platform, /*tech_root=*/tech_root));

    expect_matches_golden("openroad_run_ref.tcl", read_file(out));
}

TEST(OpenRoadTclGeneratorTest, DualPortProtectsDelayCellsAndRejectsNegativeHold) {
    OpenRoadTclGenerator gen;
    gen.set_design_name("ctrl_decode");
    gen.set_site_name("asap7sc7p5t");
    gen.set_site_height(0.27);
    ASSERT_TRUE(gen.parse_qor_report(golden_path("qor_report.txt")));

    const std::string out =
        (std::filesystem::temp_directory_path() / "or_run_tcl_dp_test" / "run.tcl").string();
    ASSERT_TRUE(gen.generate_run_tcl(
        /*width=*/15.0, /*height=*/0.0, /*output_file=*/out,
        /*num_wlt=*/8, /*num_wlb=*/8, /*num_ysel=*/4,
        /*addr_width=*/6, /*num_mux=*/2,
        /*spice_only=*/false, /*col_width=*/7.5,
        /*platform_path=*/"/nonexistent/platform/asap7",
        /*tech_root=*/std::string(REPO_ROOT) + "/tech",
        /*single_port=*/false));

    const std::string script = read_file(out);
    EXPECT_NE(script.find("set dp_delay_cells [get_cells -hierarchical -quiet "
                          "{physical_dp_delay_*}]"),
              std::string::npos);
    EXPECT_NE(script.find("set_dont_touch $dp_delay_cells"), std::string::npos);
    EXPECT_NE(script.find("buffer_ports -outputs -buffer_cell BUFx2_ASAP7_75t_R"),
              std::string::npos);
    EXPECT_NE(script.find("DP delay topology changed during implementation"),
              std::string::npos);
    EXPECT_NE(script.find("if {$hold_slack < -0.001}"), std::string::npos);
    EXPECT_EQ(script.find("if {$hold_slack < -0.010}"), std::string::npos);
    const auto hold = script.find("repair_timing -hold");
    EXPECT_NE(hold, std::string::npos);
    EXPECT_NE(script.find("repair_design -max_utilization 90", hold), std::string::npos);
}

// The 6T controller's array-wide outputs: loads counted from the geometry,
// and the port buffer the TCL sizes for them (x256x2x1 read the wrong row
// with a BUFx2 on a 170 fF sel_lo).
TEST(SixTPortLoadsTest, SelLoCountsEverySliceOfEveryStrip) {
    MainCliOptions small, tall, segmented;
    for (auto* o : {&small, &tall, &segmented}) o->bitcell_6t = true;
    small.num_wls = 2;
    tall.num_wls = 128;
    segmented.num_wls = 32;
    segmented.num_data_bits = 64;
    segmented.segment_bits = 8;
    // 256 rows: 64 slices a strip, two strips, 12 fins and 0.1 fF of wire each.
    EXPECT_NEAR(six_t_port_loads_pf(tall).at("sel_lo_A"), 128 * (12 * 0.103 + 0.1) / 1000, 1e-9);
    // 64 rows in four segments: 16 slices x 2 halves x 4 segments, the same 128 loads.
    EXPECT_NEAR(six_t_port_loads_pf(segmented).at("sel_lo_A"),
                six_t_port_loads_pf(tall).at("sel_lo_A"), 1e-9);
    EXPECT_LT(six_t_port_loads_pf(small).at("sel_lo_A"), 0.005);
    // The IO controls scale with the IO blocks: one per data bit.
    EXPECT_NEAR(six_t_port_loads_pf(segmented).at("sae_A"), 64 * (18 * 0.103 + 0.1) / 1000, 1e-9);
}

TEST(OpenRoadTclGeneratorTest, PortDriversAreSizedAndFrozen) {
    OpenRoadTclGenerator gen;
    gen.set_design_name("ctrl_decode");
    gen.set_site_name("asap7sc7p5t");
    gen.set_site_height(0.27);
    gen.set_one_port(true);
    gen.set_port_drivers({{"sel_lo_A", "BUFx24_ASAP7_75t_R"}});
    ASSERT_TRUE(gen.parse_qor_report(golden_path("qor_report.txt")));
    const std::string out =
        (std::filesystem::temp_directory_path() / "or_run_tcl_drivers_test" / "run.tcl").string();
    ASSERT_TRUE(gen.generate_run_tcl(
        /*width=*/15.0, /*height=*/0.0, /*output_file=*/out,
        /*num_wlt=*/8, /*num_wlb=*/8, /*num_ysel=*/4,
        /*addr_width=*/6, /*num_mux=*/4,
        /*spice_only=*/false, /*col_width=*/7.5,
        /*platform_path=*/"/nonexistent/platform/asap7",
        /*tech_root=*/std::string(REPO_ROOT) + "/tech",
        /*single_port=*/false));
    const std::string script = read_file(out);
    const auto ports = script.find("buffer_ports -outputs");
    const auto sized = script.find("foreach port [get_ports -quiet {sel_lo_A*}]");
    ASSERT_NE(sized, std::string::npos);
    EXPECT_LT(ports, sized);  // after the port buffers exist
    EXPECT_LT(sized, script.find("global_placement"));
    EXPECT_NE(script.find("replace_cell $inst BUFx24_ASAP7_75t_R", sized), std::string::npos);
    EXPECT_NE(script.find("set_dont_touch $net", sized), std::string::npos);
}

// --strips-in-controller: the die is the band between the stacks, the strips
// fixed instances in it, routed to and then removed.
TEST(OpenRoadTclGeneratorTest, BandTakesTheStripsInAndHandsTheirPinsTheSelects) {
    const auto dir = std::filesystem::temp_directory_path() / "or_band_plan_test";
    std::filesystem::create_directories(dir);
    {
        std::ofstream plan(dir / "plan.txt");
        plan << "width 7.3440\nreserved 7.3551\ninset 0.108\nhalo_x 0.216\nhalo_y 0.27\n";
    }
    BandPlan band;
    ASSERT_TRUE(read_band_plan(dir.string(), band));
    EXPECT_NEAR(band.reserved, 7.3551, 1e-9);

    OpenRoadTclGenerator gen;
    gen.set_design_name("ctrl_decode");
    gen.set_site_name("asap7sc7p5t");
    gen.set_site_height(0.27);
    gen.set_max_utilization(0.5);
    ASSERT_TRUE(gen.parse_qor_report(golden_path("qor_report.txt")));
    gen.set_band(band);
    // Rows for the cells at 50 % and the strips' keep-outs, then both insets.
    const double h = gen.band_die_height(band.width);
    EXPECT_GE(h - 2 * 0.108, (123.45 / 0.5 + 7.3551) / 7.344 - 1e-9);
    EXPECT_NEAR((h - 2 * 0.108) / 0.54, std::nearbyint((h - 2 * 0.108) / 0.54), 1e-9);

    const std::string out = (dir / "run.tcl").string();
    ASSERT_TRUE(gen.generate_run_tcl(band.width, 0.0, out, 2, 2, 4, 6, 2, false, 3.7,
                                     "/nonexistent/platform/asap7",
                                     std::string(REPO_ROOT) + "/tech", /*single_port=*/false));
    const std::string script = read_file(out);
    EXPECT_NE(script.find("-core_area \"0 0.1080 7.3440 "), std::string::npos);
    const auto place = script.find("band_place_strips ");
    const auto cut = script.find("cut_rows -halo_width_x 0.216 -halo_width_y 0.270");
    const auto pins = script.find("place_pins -hor_layers M4 -ver_layers M5 -exclude bottom:* -exclude top:*");
    const auto tap = script.find("tapcell -distance 3.672 -tapcell_master TAPCELL_ASAP7_75t_R -halo_width_x");
    const auto connect = script.find("band_connect_strips");
    const auto route = script.find("global_route -critical_nets_percentage 0");
    const auto remove = script.find("band_remove_strips");
    const auto def = script.find("write_def ctrl_decode.def");
    for (auto at : {place, cut, pins, tap, connect, route, remove, def}) ASSERT_NE(at, std::string::npos);
    EXPECT_LT(place, cut);
    EXPECT_LT(cut, pins);
    EXPECT_LT(pins, tap);
    EXPECT_LT(script.find("repair_timing -hold"), connect);  // timing closed on the SDC loads first
    EXPECT_LT(connect, route);
    EXPECT_LT(remove, def);
}

}  // namespace OpenFinRAM
