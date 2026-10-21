// Unit tests for the characterization-JSON loader and the Liberty emitter's
// handling of JSON-supplied strings: malformed input must come back as
// bool+error (never an exception or a silently truncated value), and strings
// that reach the .lib must not be able to break its grammar.
#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <string>

#include "characterization_data.hpp"
#include "liberty_estimator.hpp"

namespace {

namespace fs = std::filesystem;

fs::path scratch_dir() {
    const fs::path dir = fs::temp_directory_path() / "openfinram_char_unit";
    fs::create_directories(dir);
    return dir;
}

fs::path write_file(const std::string& name, const std::string& text) {
    const fs::path p = scratch_dir() / name;
    std::ofstream out(p);
    out << text;
    return p;
}

std::string read_file(const fs::path& p) {
    std::ifstream in(p);
    return std::string((std::istreambuf_iterator<char>(in)),
                       std::istreambuf_iterator<char>());
}

std::string mock_lef(bool single_port) {
    std::string lef = "MACRO sram_x4x4x1\n  SIZE 10.0 BY 20.0 ;\n";
    auto pin = [&lef](const std::string& name) {
        lef += "  PIN " + name + "\n  END " + name + "\n";
    };
    pin("vdd");
    pin("vss");
    pin("clk");
    if (single_port) {
        for (const char* name : {"ce_n", "oe_n", "we_n"}) pin(name);
        for (int bit = 0; bit < 4; ++bit) pin("sdel[" + std::to_string(bit) + "]");
        for (int bit = 0; bit < 4; ++bit) pin("A[" + std::to_string(bit) + "]");
        for (int bit = 0; bit < 4; ++bit) {
            pin("D[" + std::to_string(bit) + "]");
            pin("Q[" + std::to_string(bit) + "]");
        }
    } else {
        for (const char* name : {"rst_n", "ce_n_A", "ce_n_B", "we_n_A", "we_n_B",
                                 "oe_n_A", "oe_n_B"}) {
            pin(name);
        }
        for (int bit = 0; bit < 4; ++bit) {
            pin("A_A[" + std::to_string(bit) + "]");
            pin("A_B[" + std::to_string(bit) + "]");
        }
        for (int bit = 0; bit < 4; ++bit) {
            pin("D_A[" + std::to_string(bit) + "]");
            pin("D_B[" + std::to_string(bit) + "]");
            pin("Q_A[" + std::to_string(bit) + "]");
            pin("Q_B[" + std::to_string(bit) + "]");
        }
    }
    return lef + "END sram_x4x4x1\n";
}

bool load(const std::string& name, const std::string& json,
          OpenFinRAM::CharacterizationData& data, std::string& err) {
    return OpenFinRAM::load_characterization_json(
        write_file(name, json).string(), data, &err);
}

// Emit the .lib for `json` and return its text.
std::string emit_lib(const std::string& tag, const std::string& json) {
    OpenFinRAM::CharacterizationData data;
    std::string err;
    EXPECT_TRUE(load(tag + ".json", json, data, err)) << err;
    MainCliOptions opts;
    opts.single_port = true;
    opts.num_wls = 2;
    opts.num_data_bits = 4;
    opts.num_banks = 1;
    const fs::path lef = write_file(tag + ".lef", mock_lef(true));
    const fs::path lib = scratch_dir() / (tag + ".lib");
    EXPECT_TRUE(OpenFinRAM::export_estimated_liberty(
        opts, lef.string(), lib.string(), &err, &data)) << err;
    return read_file(lib);
}

constexpr const char* kSchema = R"j("schema": "openfinram-characterization-1")j";

std::string port_json(const std::string& axis = "0.01, 0.04") {
    const std::string a = R"({"index_1":[)" + axis + R"(], "index_2":[0.0005,0.002],
        "cell_rise":[[0.21,0.22],[0.23,0.24]], "cell_fall":[[0.31,0.32],[0.33,0.34]],
        "rise_transition":[[0.011,0.012],[0.013,0.014]],
        "fall_transition":[[0.021,0.022],[0.023,0.024]]})";
    std::string b = a;
    b.replace(b.find("0.21,0.22"), 9, "0.41,0.42");
    return R"({"schema":"openfinram-characterization-2", "cell":"sram_x4x4x1",
        "timing":{"port_delays":{"A":)" + a + ",\"B\":" + b + "}}}";
}

}  // namespace

TEST(CharacterizationJson, PortTablesRequireOrderedPositiveAxesAndBothPorts) {
    OpenFinRAM::CharacterizationData data;
    std::string err;
    for (const auto& axis : {"0.04, 0.01", "0.01, 0.01", "0, 0.04", "-0.01, 0.04"}) {
        EXPECT_FALSE(load("bad_axis.json", port_json(axis), data, err));
        EXPECT_NE(err.find("strictly increasing"), std::string::npos) << err;
    }
    std::string missing = port_json();
    missing.replace(missing.find("\"B\":"), 4, "\"C\":");
    EXPECT_FALSE(load("missing_port.json", missing, data, err));
    EXPECT_NE(err.find("exactly A and B"), std::string::npos);
    EXPECT_TRUE(load("valid_ports.json", port_json(), data, err)) << err;
}

TEST(CharacterizationJson, RejectsMixedTransitionDimensionsAndNegativeDelay) {
    OpenFinRAM::CharacterizationData data;
    std::string err;
    std::string mixed = port_json();
    const std::string matrix = "[[0.011,0.012],[0.013,0.014]]";
    mixed.replace(mixed.find(matrix), matrix.size(), "[0.011,0.012]");
    EXPECT_FALSE(load("mixed_slew.json", mixed, data, err));
    std::string negative = port_json();
    negative.replace(negative.find("0.21,0.22"), 9, "-0.21,0.22");
    EXPECT_FALSE(load("negative_delay.json", negative, data, err));
}

TEST(CharacterizationLiberty, UsesDistinctPortTablesAndTwoDimensionalSlews) {
    OpenFinRAM::CharacterizationData data;
    std::string err;
    ASSERT_TRUE(load("ports.json", port_json(), data, err)) << err;
    MainCliOptions opts;
    opts.num_data_bits = 4;
    const auto lef = write_file("ports.lef", mock_lef(false));
    const auto lib = scratch_dir() / "ports.lib";
    ASSERT_TRUE(OpenFinRAM::export_estimated_liberty(opts, lef.string(), lib.string(), &err, &data)) << err;
    const auto text = read_file(lib);
    const auto a = text.substr(text.find("bus (Q_A)"), text.find("bus (Q_B)") - text.find("bus (Q_A)"));
    const auto b = text.substr(text.find("bus (Q_B)"));
    EXPECT_NE(a.find("values (\"0.21, 0.22\", \"0.23, 0.24\")"), std::string::npos);
    EXPECT_EQ(a.find("0.41, 0.42"), std::string::npos);
    EXPECT_NE(b.find("values (\"0.41, 0.42\", \"0.23, 0.24\")"), std::string::npos);
    EXPECT_NE(a.find("rise_transition (sram_x4x4x1_estimated_delay)"), std::string::npos);
    EXPECT_NE(a.find("values (\"0.011, 0.012\", \"0.013, 0.014\")"), std::string::npos);
    EXPECT_NE(text.find("max_transition : 0.04;"), std::string::npos);
    data.cell = "sram_x8x4x1";
    EXPECT_FALSE(OpenFinRAM::export_estimated_liberty(opts, lef.string(), lib.string(), &err, &data));
    EXPECT_NE(err.find("does not match"), std::string::npos);
    data.cell = "sram_x4x4x1";
    std::string wrong_name = mock_lef(false);
    wrong_name.replace(wrong_name.find("sram_x4x4x1"), std::string("sram_x4x4x1").size(), "sram_x8x4x1");
    const auto wrong_lef = write_file("wrong_macro.lef", wrong_name);
    EXPECT_FALSE(OpenFinRAM::export_estimated_liberty(opts, wrong_lef.string(), lib.string(), &err, &data));
    EXPECT_NE(err.find("requested macro"), std::string::npos);
    opts.single_port = true;
    const auto single = write_file("ports_single.lef", mock_lef(true));
    EXPECT_FALSE(OpenFinRAM::export_estimated_liberty(opts, single.string(), lib.string(), &err, &data));
    EXPECT_NE(err.find("requires an 8T"), std::string::npos);
}

TEST(CharacterizationLiberty, LegacyPassthroughRemainsByteIdentical) {
    OpenFinRAM::CharacterizationData data;
    std::string err;
    ASSERT_TRUE(OpenFinRAM::load_characterization_json(
        std::string(REPO_ROOT) + "/tests/golden/characterization_estimated_passthrough.json", data, &err)) << err;
    MainCliOptions opts;
    opts.single_port = true;
    opts.num_data_bits = 4;
    const auto lef = write_file("passthrough.lef", mock_lef(true));
    const auto a = scratch_dir() / "estimated.lib", b = scratch_dir() / "passthrough.lib";
    ASSERT_TRUE(OpenFinRAM::export_estimated_liberty(opts, lef.string(), a.string(), &err)) << err;
    ASSERT_TRUE(OpenFinRAM::export_estimated_liberty(opts, lef.string(), b.string(), &err, &data)) << err;
    EXPECT_EQ(read_file(a), read_file(b));
}

// --- JSON parser robustness -------------------------------------------------

TEST(CharacterizationJson, MalformedUnicodeEscapeIsAnErrorNotAThrow) {
    OpenFinRAM::CharacterizationData data;
    std::string err;
    bool ok = true;
    EXPECT_NO_THROW(ok = load("bad_u.json",
        std::string("{") + kSchema + R"j(, "comment": "\uZZZZ"})j", data, err));
    EXPECT_FALSE(ok);
    EXPECT_FALSE(err.empty());
}

TEST(CharacterizationJson, ValidUnicodeEscapeDecodes) {
    OpenFinRAM::CharacterizationData data;
    std::string err;
    ASSERT_TRUE(load("good_u.json",
        std::string("{") + kSchema + R"j(, "comment": "\u0041\u00e9"})j", data, err)) << err;
    EXPECT_EQ(data.comment, "A\xC3\xA9");
}

TEST(CharacterizationJson, NumberWithTrailingGarbageIsRejected) {
    OpenFinRAM::CharacterizationData data;
    std::string err;
    EXPECT_FALSE(load("bad_num.json",
        std::string("{") + kSchema + R"j(, "clock_min_period": 1.2.3})j", data, err));
    EXPECT_FALSE(load("bad_exp.json",
        std::string("{") + kSchema + R"j(, "clock_min_period": 1e+e-2})j", data, err));
}

TEST(CharacterizationJson, WellFormedNumbersStillParse) {
    OpenFinRAM::CharacterizationData data;
    std::string err;
    ASSERT_TRUE(load("num.json",
        std::string("{") + kSchema + R"j(, "clock_min_period": -1.5e-1})j", data, err)) << err;
    EXPECT_DOUBLE_EQ(data.clock_min_period, -0.15);
}

TEST(CharacterizationJson, OperatingConditionsNameMustBeAnIdentifier) {
    OpenFinRAM::CharacterizationData data;
    std::string err;
    EXPECT_FALSE(load("bad_oc.json",
        std::string("{") + kSchema +
        R"j(, "operating_conditions": {"voltage": 0.63, "temperature": 25, "name": "SS (0.63V)"}})j",
        data, err));
    EXPECT_NE(err.find("operating_conditions.name"), std::string::npos) << err;
    ASSERT_TRUE(load("good_oc.json",
        std::string("{") + kSchema +
        R"j(, "operating_conditions": {"voltage": 0.63, "temperature": 25, "name": "SS_0P63V_25C"}})j",
        data, err)) << err;
    EXPECT_EQ(data.operating_conditions.name, "SS_0P63V_25C");
}

// --- Liberty emitter ----------------------------------------------------------

TEST(LibertyEstimator, DefaultCornerKeepsHistoricalName) {
    const std::string lib = emit_lib("oc_default",
        std::string("{") + kSchema +
        R"j(, "operating_conditions": {"voltage": 0.7, "temperature": 25}})j");
    EXPECT_NE(lib.find("operating_conditions (PVT_0P7V_25C) {"), std::string::npos);
    EXPECT_NE(lib.find("default_operating_conditions : PVT_0P7V_25C;"), std::string::npos);
}

TEST(LibertyEstimator, NominalVoltageAtOtherTemperatureIsNotNamed25C) {
    const std::string lib = emit_lib("oc_hot",
        std::string("{") + kSchema +
        R"j(, "operating_conditions": {"voltage": 0.7, "temperature": 125}})j");
    EXPECT_EQ(lib.find("PVT_0P7V_25C"), std::string::npos);
    EXPECT_NE(lib.find("operating_conditions (PVT_0P7V_125C) {"), std::string::npos);
    EXPECT_NE(lib.find("temperature : 125;"), std::string::npos);
}

TEST(LibertyEstimator, NegativeTemperatureYieldsValidIdentifier) {
    const std::string lib = emit_lib("oc_cold",
        std::string("{") + kSchema +
        R"j(, "operating_conditions": {"voltage": 0.77, "temperature": -40}})j");
    EXPECT_NE(lib.find("operating_conditions (PVT_0P77V_M40C) {"), std::string::npos);
    EXPECT_NE(lib.find("default_operating_conditions : PVT_0P77V_M40C;"), std::string::npos);
    EXPECT_EQ(lib.find("_-40C"), std::string::npos);
    EXPECT_NE(lib.find("temperature : -40;"), std::string::npos);
}

TEST(LibertyEstimator, CommentQuotesAndBackslashesAreEscaped) {
    const std::string lib = emit_lib("comment",
        std::string("{") + kSchema +
        R"j(, "comment": "path C:\\x \"snapshot\" line1\nline2"})j");
    EXPECT_NE(lib.find(R"j(comment : "path C:\\x \"snapshot\" line1 line2";)j"),
              std::string::npos) << lib.substr(0, 400);
}

TEST(LibertyEstimator, DualPortUsesBothWriteAddressesAndAsyncResetChecks) {
    MainCliOptions opts;
    opts.single_port = false;
    opts.num_wls = 2;
    opts.num_data_bits = 4;
    opts.num_banks = 1;

    const fs::path lef = write_file("dual_port.lef", mock_lef(false));
    const fs::path lib_path = scratch_dir() / "dual_port.lib";
    std::string err;
    ASSERT_TRUE(OpenFinRAM::export_estimated_liberty(
        opts, lef.string(), lib_path.string(), &err)) << err;

    const std::string lib = read_file(lib_path);
    EXPECT_NE(lib.find("bus (A_A)"), std::string::npos);
    EXPECT_NE(lib.find("bus (A_B)"), std::string::npos);
    EXPECT_NE(lib.find("bus (D_A)"), std::string::npos);
    EXPECT_NE(lib.find("bus (D_B)"), std::string::npos);
    EXPECT_NE(lib.find("address : A_A;"), std::string::npos);
    EXPECT_NE(lib.find("address : A_B;"), std::string::npos);
    EXPECT_NE(lib.find("pin (we_n_B)"), std::string::npos);
    EXPECT_EQ(lib.find("address : A;"), std::string::npos);
    EXPECT_NE(lib.find("Same-address concurrent A/B accesses are illegal when either port writes"),
              std::string::npos);
    EXPECT_NE(lib.find("contention_condition : \""), std::string::npos);
    EXPECT_NE(lib.find("(!ce_n_A) * (!ce_n_B)"), std::string::npos);
    EXPECT_NE(lib.find("((!we_n_A) + (!we_n_B))"), std::string::npos);
    for (int bit = 0; bit < 4; ++bit) {
        const std::string equal_bit =
            "((A_A[" + std::to_string(bit) + "] * A_B[" +
            std::to_string(bit) + "]) + ((!A_A[" + std::to_string(bit) +
            "]) * (!A_B[" + std::to_string(bit) + "])))";
        EXPECT_NE(lib.find(equal_bit), std::string::npos) << equal_bit;
    }

    const std::size_t reset_begin = lib.find("pin (rst_n)");
    const std::size_t reset_end = lib.find("pin (ce_n_A)", reset_begin);
    ASSERT_NE(reset_begin, std::string::npos);
    ASSERT_NE(reset_end, std::string::npos);
    const std::string reset = lib.substr(reset_begin, reset_end - reset_begin);
    EXPECT_NE(reset.find("timing_type : recovery_rising;"), std::string::npos);
    EXPECT_NE(reset.find("timing_type : removal_rising;"), std::string::npos);
    EXPECT_EQ(reset.find("timing_type : setup_rising;"), std::string::npos);
    EXPECT_EQ(reset.find("timing_type : hold_rising;"), std::string::npos);
}

TEST(LibertyEstimator, RejectsDualPortLibertyForSinglePortLef) {
    MainCliOptions opts;
    opts.single_port = false;
    opts.num_wls = 2;
    opts.num_data_bits = 4;
    opts.num_banks = 1;

    const fs::path lef = write_file("wrong_interface.lef", mock_lef(true));
    const fs::path lib_path = scratch_dir() / "wrong_interface.lib";
    std::string err;
    EXPECT_FALSE(OpenFinRAM::export_estimated_liberty(
        opts, lef.string(), lib_path.string(), &err));
    EXPECT_NE(err.find("missing PIN rst_n"), std::string::npos) << err;
}
