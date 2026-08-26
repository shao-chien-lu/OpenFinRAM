#ifndef CHARACTERIZATION_DATA_HPP
#define CHARACTERIZATION_DATA_HPP

#include <cmath>
#include <limits>
#include <string>
#include <vector>

namespace OpenFinRAM {

// Row-major 2-D Liberty table: index_1 is the outer dimension.
using Table2D = std::vector<std::vector<double>>;

// clk -> Q arc, mirrors the lu_table_templates emitted by
// liberty_estimator.cpp (input_net_transition x total_output_net_capacitance
// for delays; total_output_net_capacitance alone for transitions).
struct CharacterizationDelay {
    bool present = false;
    std::vector<double> index_1;  // input_net_transition
    std::vector<double> index_2;  // total_output_net_capacitance
    Table2D cell_rise;
    Table2D cell_fall;
    std::vector<double> rise_transition;
    std::vector<double> fall_transition;
};

// Setup/hold constraints against clk (related_pin_transition x
// constrained_pin_transition).
struct CharacterizationConstraints {
    bool present = false;
    std::vector<double> index_1;
    std::vector<double> index_2;
    Table2D setup_rise;
    Table2D setup_fall;
    Table2D hold_rise;
    Table2D hold_fall;
};

struct CharacterizationTiming {
    CharacterizationDelay delay;
    CharacterizationConstraints constraints;
};

// Measured values that override the estimated early-PPA constants in
// liberty_estimator.cpp. A field left absent falls back to the historical
// constant, so a JSON carrying exactly today's defaults reproduces the
// estimated .lib byte-for-byte (the P0 equivalence gate).
struct CharacterizationData {
    // NaN means "not provided".
    double cell_leakage_power = std::numeric_limits<double>::quiet_NaN();
    double clock_min_period = std::numeric_limits<double>::quiet_NaN();
    double clk_capacitance = std::numeric_limits<double>::quiet_NaN();
    double default_input_pin_capacitance = std::numeric_limits<double>::quiet_NaN();
    double output_max_capacitance = std::numeric_limits<double>::quiet_NaN();
    // Non-empty replaces the "ESTIMATED EARLY-PPA MODEL" library comment.
    std::string comment;
    CharacterizationTiming timing;

    // Corner identity for the emitted library. Absent fields keep the
    // estimated model's literals (0.7 V / 25 C, PVT_0P7V_25C).
    struct OperatingConditions {
        bool present = false;
        double voltage = std::numeric_limits<double>::quiet_NaN();
        double temperature = std::numeric_limits<double>::quiet_NaN();
        std::string name;  // e.g. "PVT_0P63V_25C_SS"; empty -> derived
    };
    OperatingConditions operating_conditions;

    // Measured power. Leakage feeds cell_leakage_power; the per-access
    // energies become internal_power() tables on clk under when:"write" /
    // when:"!write", expressed at a documented reference cycle.
    struct PowerData {
        bool present = false;
        double leakage_uw = std::numeric_limits<double>::quiet_NaN();
        double read_access_pj = std::numeric_limits<double>::quiet_NaN();
        double write_access_pj = std::numeric_limits<double>::quiet_NaN();
        double reference_cycle_ns = std::numeric_limits<double>::quiet_NaN();
    };
    PowerData power;

    // Per-sdel-tie-off variants: each entry produces an additional complete
    // .lib named <stem>_<name>.lib whose timing tables come from the variant
    // instead of the top-level timing. sdel is programmable in the netlist,
    // so a characterized flow emits one Liberty per tie-off that consumers
    // bind to.
    struct Variant {
        std::string name;
        CharacterizationTiming timing;
    };
    std::vector<Variant> variants;

    static bool provided(double v) { return !std::isnan(v); }
};

bool load_characterization_json(const std::string& path,
                                CharacterizationData& out,
                                std::string* error);

}  // namespace OpenFinRAM

#endif  // CHARACTERIZATION_DATA_HPP
