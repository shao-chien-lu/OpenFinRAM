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
    const fs::path lef = write_file(tag + ".lef",
        "MACRO sram_x4x4x1\n  SIZE 10.0 BY 20.0 ;\nEND sram_x4x4x1\n");
    const fs::path lib = scratch_dir() / (tag + ".lib");
    EXPECT_TRUE(OpenFinRAM::export_estimated_liberty(
        opts, lef.string(), lib.string(), &err, &data)) << err;
    return read_file(lib);
}

constexpr const char* kSchema = R"j("schema": "openfinram-characterization-1")j";

}  // namespace

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
