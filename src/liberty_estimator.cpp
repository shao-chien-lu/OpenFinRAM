#include "liberty_estimator.hpp"

#include <algorithm>
#include <exception>
#include <fstream>
#include <iomanip>
#include <regex>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include "characterization_data.hpp"
#include "plog/Log.h"
#include "utils.hpp"

namespace OpenFinRAM {
namespace {

// Single Liberty number formatter used by BOTH the estimated-constant and
// measured-value paths, so a characterization JSON carrying exactly today's
// constants regenerates the estimated model byte-for-byte.
std::string num(double v) {
    std::ostringstream s;
    s << v;
    return s.str();
}

double scalar_or(const CharacterizationData* data, double provided_value,
                 double fallback) {
    if (!data) return fallback;
    return CharacterizationData::provided(provided_value) ? provided_value : fallback;
}

struct MacroSize {
    double width = 0;
    double height = 0;
};

bool read_lef_size(const std::string& lef_path, MacroSize& size, std::string* error) {
    std::ifstream lef(lef_path);
    if (!lef) {
        if (error) *error = "Failed to open LEF: " + lef_path;
        return false;
    }

    const std::regex size_pattern(
        R"(^\s*SIZE\s+([-+0-9.eE]+)\s+BY\s+([-+0-9.eE]+)\s*;)",
        std::regex::icase);
    std::string line;
    std::smatch match;
    while (std::getline(lef, line)) {
        if (!std::regex_search(line, match, size_pattern)) continue;
        try {
            size.width = std::stod(match[1].str());
            size.height = std::stod(match[2].str());
        } catch (const std::exception&) {
            if (error) *error = "Invalid SIZE statement in LEF: " + line;
            return false;
        }
        if (size.width > 0 && size.height > 0) return true;
    }

    if (error) *error = "No valid SIZE statement found in LEF: " + lef_path;
    return false;
}

bool validate_lef_interface(const MainCliOptions& options,
                            const std::string& lef_path,
                            std::string* error) {
    std::ifstream lef(lef_path);
    if (!lef) {
        if (error) *error = "Failed to open LEF: " + lef_path;
        return false;
    }

    const std::regex pin_pattern(R"(^\s*PIN\s+(\S+))", std::regex::icase);
    const std::regex macro_pattern(R"(^\s*MACRO\s+(\S+))", std::regex::icase);
    const std::string expected_cell = "sram_x" + std::to_string(options.num_wls * 2) + "x" +
        std::to_string(options.num_data_bits) + "x" + std::to_string(options.num_banks);
    unsigned macros = 0;
    std::set<std::string> pins;
    std::string line;
    std::smatch match;
    while (std::getline(lef, line)) {
        if (std::regex_search(line, match, macro_pattern)) {
            if (++macros != 1 || match[1].str() != expected_cell) {
                if (error) *error = "LEF must contain only the requested macro " + expected_cell;
                return false;
            }
        }
        if (std::regex_search(line, match, pin_pattern)) {
            pins.insert(match[1].str());
        }
    }

    if (macros != 1) {
        if (error) *error = "LEF is missing MACRO " + expected_cell;
        return false;
    }

    std::vector<std::string> required{"vdd", "vss", "clk"};
    const int addr_width = get_addr_width(options);
    if (options.single_port) {
        required.insert(required.end(), {"ce_n", "oe_n", "we_n"});
        for (int bit = 0; bit < 4; ++bit) {
            required.push_back("sdel[" + std::to_string(bit) + "]");
        }
        for (int bit = 0; bit < addr_width; ++bit) {
            required.push_back("A[" + std::to_string(bit) + "]");
        }
        for (unsigned bit = 0; bit < options.num_data_bits; ++bit) {
            required.push_back("D[" + std::to_string(bit) + "]");
            required.push_back("Q[" + std::to_string(bit) + "]");
        }
    } else {
        // The generated single-port macro is the two-port one's port A,
        // named at its boundary without the port (ce_n, A[], D[], Q[]).
        const std::vector<std::string> ports = options.bitcell_6t
            ? std::vector<std::string>{""} : std::vector<std::string>{"_A", "_B"};
        required.push_back("rst_n");
        for (const auto& port : ports) {
            for (const char* pin : {"ce_n", "we_n", "oe_n"}) required.push_back(pin + port);
            for (int bit = 0; bit < addr_width; ++bit) {
                required.push_back("A" + port + "[" + std::to_string(bit) + "]");
            }
            for (unsigned bit = 0; bit < options.num_data_bits; ++bit) {
                required.push_back("D" + port + "[" + std::to_string(bit) + "]");
                required.push_back("Q" + port + "[" + std::to_string(bit) + "]");
            }
        }
    }

    for (const auto& pin : required) {
        if (pins.find(pin) == pins.end()) {
            if (error) {
                *error = "LEF interface does not match " +
                         std::string(options.single_port || options.bitcell_6t ? "single-port" : "dual-port") +
                         " macro; missing PIN " + pin + " in " + lef_path;
            }
            return false;
        }
    }
    return true;
}

void emit_constraint_arcs(std::ostringstream& out,
                          const std::string& template_name,
                          const std::string& indent,
                          const CharacterizationConstraints* measured,
                          const char* first_timing_type = "setup_rising",
                          const char* second_timing_type = "hold_rising") {
    auto join = [&out](const std::vector<double>& v) {
        for (size_t i = 0; i < v.size(); ++i) {
            if (i) out << ", ";
            out << num(v[i]);
        }
    };
    const char* timing_types[2] = {first_timing_type, second_timing_type};
    for (int arc = 0; arc < 2; ++arc) {
        const char* timing_type = timing_types[arc];
        // Fallback constants double as the measured-path shape so identical
        // values emit identical bytes on either path.
        std::vector<double> index_1{0.009, 0.227};
        std::vector<double> index_2{0.009, 0.227};
        const Table2D kFlat{{0.05, 0.05}, {0.05, 0.05}};
        const Table2D* rise = &kFlat;
        const Table2D* fall = &kFlat;
        if (measured && measured->present) {
            const bool is_setup = arc == 0;
            rise = is_setup ? &measured->setup_rise : &measured->hold_rise;
            fall = is_setup ? &measured->setup_fall : &measured->hold_fall;
            index_1 = measured->index_1;
            index_2 = measured->index_2;
        }
        out << indent << "timing () {\n";
        out << indent << "  related_pin : \"clk\";\n";
        out << indent << "  timing_type : " << timing_type << ";\n";
        const Table2D* mats[2] = {rise, fall};
        const char* tables[2] = {"rise_constraint", "fall_constraint"};
        for (int t = 0; t < 2; ++t) {
            out << indent << "  " << tables[t] << " (" << template_name << ") {\n";
            out << indent << "    index_1 (\"";
            join(index_1);
            out << "\");\n";
            out << indent << "    index_2 (\"";
            join(index_2);
            out << "\");\n";
            out << indent << "    values (";
            for (size_t i = 0; i < mats[t]->size(); ++i) {
                if (i) out << ", ";
                out << '"';
                join((*mats[t])[i]);
                out << '"';
            }
            out << ");\n";
            out << indent << "  }\n";
        }
        out << indent << "}\n";
    }
}

void emit_input_pin(std::ostringstream& out,
                    const std::string& name,
                    const std::string& constraint_template,
                    const CharacterizationData* data,
                    bool async_active_low = false) {
    out << "    pin (" << name << ") {\n";
    out << "      direction : input;\n";
    out << "      capacitance : "
        << num(scalar_or(data, data ? data->default_input_pin_capacitance : 0.005, 0.005))
        << ";\n";
    if (async_active_low) {
        // An active-low asynchronous control is checked when it is released
        // (rising edge), using recovery/removal rather than synchronous
        // setup/hold arcs.
        emit_constraint_arcs(out, constraint_template, "      ",
                             data ? &data->timing.constraints : nullptr,
                             "recovery_rising", "removal_rising");
    } else {
        emit_constraint_arcs(out, constraint_template, "      ",
                             data ? &data->timing.constraints : nullptr);
    }
    out << "    }\n";
}

void emit_input_bus(std::ostringstream& out,
                    const std::string& name,
                    const std::string& bus_type,
                    const std::string& constraint_template,
                    const std::string& write_address,
                    const CharacterizationData* data) {
    out << "    bus (" << name << ") {\n";
    out << "      bus_type : " << bus_type << ";\n";
    out << "      direction : input;\n";
    out << "      capacitance : "
        << num(scalar_or(data, data ? data->default_input_pin_capacitance : 0.005, 0.005))
        << ";\n";
    if (!write_address.empty()) {
        out << "      memory_write () {\n";
        out << "        address : " << write_address << ";\n";
        out << "        clocked_on : \"clk\";\n";
        out << "      }\n";
    }
    emit_constraint_arcs(out, constraint_template, "      ",
                         data ? &data->timing.constraints : nullptr);
    out << "    }\n";
}

// Body of a Liberty string literal: escape the delimiters and flatten control
// characters so a JSON-supplied comment cannot unbalance the .lib grammar.
std::string liberty_quote(const std::string& s) {
    std::string r;
    r.reserve(s.size() + 8);
    for (const char ch : s) {
        if (ch == '"' || ch == '\\') {
            r.push_back('\\');
            r.push_back(ch);
        } else if (ch == '\n' || ch == '\r' || ch == '\t') {
            r.push_back(' ');
        } else {
            r.push_back(ch);
        }
    }
    return r;
}

// Numeric text as a Liberty identifier fragment: '.' -> 'P', '-' -> 'M'.
// '-' is not an identifier character, so a -40 C corner must read M40C.
std::string liberty_identifier_token(std::string s) {
    for (auto& ch : s) {
        if (ch == '.') ch = 'P';
        else if (ch == '-') ch = 'M';
    }
    return s;
}

// Liberty has no memory-group attribute for port-collision behavior.  The
// cell-level contention_condition is the standard machine-readable way to
// describe an electrically forbidden input combination.  Spell out address
// equality per bit rather than relying on vector operators, which are not
// interpreted consistently by Liberty readers outside a bus function.
std::string dual_port_contention_condition(int address_width) {
    std::ostringstream condition;
    condition
        << "(!ce_n_A) * (!ce_n_B)"
        << " * (((!we_n_A) * oe_n_A) + (we_n_A * (!oe_n_A)))"
        << " * (((!we_n_B) * oe_n_B) + (we_n_B * (!oe_n_B)))"
        << " * ((!we_n_A) + (!we_n_B))";
    for (int bit = 0; bit < address_width; ++bit) {
        condition << " * ((A_A[" << bit << "] * A_B[" << bit
                  << "]) + ((!A_A[" << bit << "]) * (!A_B[" << bit << "])))";
    }
    return condition.str();
}

std::string build_estimated_liberty(const MainCliOptions& options,
                                    const MacroSize& size,
                                    const CharacterizationData* data,
                                    const CharacterizationTiming* timing,
                                    const std::string& suffix) {
    const std::string cell_name =
        "sram_x" + std::to_string(options.num_wls * 2) + "x" +
        std::to_string(options.num_data_bits) + "x" +
        std::to_string(options.num_banks) + suffix;
    const int addr_width = get_addr_width(options);
    const std::string data_type = cell_name + "_DATA";
    const std::string address_type = cell_name + "_ADDRESS";
    const std::string sdel_type = cell_name + "_SDEL";
    const std::string delay_template = cell_name + "_estimated_delay";
    const std::string slew_template = cell_name + "_estimated_slew";
    const std::string constraint_template = cell_name + "_estimated_constraint";

    // These are deliberately coarse early-PPA defaults. They match the
    // compact ASAP7 FakeRAM convention (2x2 tables) and are intentionally not
    // scaled until there is a calibrated model for OpenFinRAM's actual
    // decoder, muxing, bit-line, and interconnect architecture.
    constexpr double kClockToQ = 0.218;   // ns
    constexpr double kMinPeriod = 0.157;  // ns
    const double min_period = scalar_or(data, data ? data->clock_min_period : kMinPeriod, kMinPeriod);

    std::ostringstream out;
    out << "library (" << cell_name << "_estimated) {\n";
    out << "  technology (cmos);\n";
    out << "  delay_model : table_lookup;\n";
    out << "  revision : \"OpenFinRAM estimated-1\";\n";
    const std::string port_comment =
        options.bitcell_6t
            ? "ESTIMATED EARLY-PPA MODEL; NOT SPICE/SILICONSMART CHARACTERIZED. Single-port 6T macro (one read/write port). Timing uses a coarse FakeRAM-style ASAP7 baseline; power is not modeled."
        : options.single_port
            ? "ESTIMATED EARLY-PPA MODEL; NOT SPICE/SILICONSMART CHARACTERIZED. Timing uses a coarse FakeRAM-style ASAP7 baseline; power is not modeled."
            : "ESTIMATED EARLY-PPA DUAL-PORT MODEL; NOT SPICE/SILICONSMART CHARACTERIZED. Timing uses a coarse FakeRAM-style ASAP7 baseline per port; power is not modeled. True-dual-port 8T bitcell; ports A and B both support read and write. Same-address concurrent A/B accesses are illegal when either port writes; same-address read/read is supported.";
    if (data && !data->comment.empty()) {
        out << "  comment : \"" << liberty_quote(data->comment);
        if (!options.single_port && !options.bitcell_6t) {
            out << " Same-address concurrent A/B accesses are illegal when either port writes; same-address read/read is supported.";
        }
        out << "\";\n";
    } else {
        out << "  comment : \"" << port_comment << "\";\n";
    }
    const bool measured_power = data && data->power.present;
    if (measured_power) {
        out << "  power_unit : \"1uW\";\n";
    }
    out << "  time_unit : \"1ns\";\n";
    out << "  voltage_unit : \"1V\";\n";
    out << "  current_unit : \"1uA\";\n";
    out << "  leakage_power_unit : \"1uW\";\n";
    out << "  capacitive_load_unit (1, pf);\n";
    const double oc_volt = scalar_or(data, data ? data->operating_conditions.voltage
                                                : 0.7, 0.7);
    const double oc_temp = scalar_or(data, data ? data->operating_conditions.temperature
                                                : 25.0, 25.0);
    out << "  nom_process : 1;\n";
    out << "  nom_temperature : " << num(oc_temp) << ";\n";
    out << "  nom_voltage : " << num(oc_volt) << ";\n";
    out << "  default_cell_leakage_power : "
        << num(scalar_or(data, data ? data->cell_leakage_power : 0.0, 0.0)) << ";\n";
    out << "  default_input_pin_cap : "
        << num(scalar_or(data, data ? data->default_input_pin_capacitance : 0.005, 0.005))
        << ";\n";
    out << "  default_output_pin_cap : 0.0;\n";
    out << "  default_inout_pin_cap : 0.0;\n";
    out << "  default_max_transition : 0.227;\n";
    out << "  input_threshold_pct_fall : 50;\n";
    out << "  input_threshold_pct_rise : 50;\n";
    out << "  output_threshold_pct_fall : 50;\n";
    out << "  output_threshold_pct_rise : 50;\n";
    out << "  slew_lower_threshold_pct_fall : 20;\n";
    out << "  slew_upper_threshold_pct_fall : 80;\n";
    out << "  slew_lower_threshold_pct_rise : 20;\n";
    out << "  slew_upper_threshold_pct_rise : 80;\n";
    std::string oc_name =
        data && !data->operating_conditions.name.empty()
            ? data->operating_conditions.name
            : "";
    if (oc_name.empty()) {
        // Derive PVT_<V>V_<T>C from the actual (voltage, temperature) pair --
        // never from a hardcoded default, so 0.70 V @ 125 C is not mislabelled
        // as the 25 C corner. Two decimals with trailing zeros trimmed keeps
        // the historical default literal (0.70 -> 0P7V; 0.63 -> 0P63V).
        std::ostringstream vn;
        vn << std::fixed << std::setprecision(2) << oc_volt;
        std::string vs = vn.str();
        while (vs.size() > 2 && vs.back() == '0' && vs[vs.size() - 2] != '.') {
            vs.pop_back();
        }
        oc_name = "PVT_" + liberty_identifier_token(vs) + "V_" +
                  liberty_identifier_token(num(oc_temp)) + "C";
    }
    out << "  operating_conditions (" << oc_name << ") {\n";
    out << "    process : 1;\n";
    out << "    temperature : " << num(oc_temp) << ";\n";
    out << "    voltage : " << num(oc_volt) << ";\n";
    out << "    tree_type : balanced_tree;\n";
    out << "  }\n";
    out << "  default_operating_conditions : " << oc_name << ";\n\n";

    out << "  lu_table_template (" << delay_template << ") {\n";
    out << "    variable_1 : input_net_transition;\n";
    out << "    variable_2 : total_output_net_capacitance;\n";
    out << "    index_1 (\"0.009, 0.227\");\n";
    out << "    index_2 (\"0.005, 0.500\");\n";
    out << "  }\n";
    out << "  lu_table_template (" << slew_template << ") {\n";
    out << "    variable_1 : total_output_net_capacitance;\n";
    out << "    index_1 (\"0.005, 0.500\");\n";
    out << "  }\n";
    const std::string power_template = cell_name + "_power_1pt";
    if (measured_power) {
        out << "  power_lut_template (" << power_template << ") {\n";
        out << "    variable_1 : input_transition_time;\n";
        out << "    index_1 (\"1.0\");\n";
        out << "  }\n";
    }
    out << "  lu_table_template (" << constraint_template << ") {\n";
    out << "    variable_1 : related_pin_transition;\n";
    out << "    variable_2 : constrained_pin_transition;\n";
    out << "    index_1 (\"0.009, 0.227\");\n";
    out << "    index_2 (\"0.009, 0.227\");\n";
    out << "  }\n\n";

    auto emit_bus_type = [&](const std::string& name, int width) {
        out << "  type (" << name << ") {\n";
        out << "    base_type : array;\n";
        out << "    data_type : bit;\n";
        out << "    bit_width : " << width << ";\n";
        out << "    bit_from : " << (width - 1) << ";\n";
        out << "    bit_to : 0;\n";
        out << "    downto : true;\n";
        out << "  }\n";
    };
    emit_bus_type(data_type, static_cast<int>(options.num_data_bits));
    emit_bus_type(address_type, addr_width);
    if (options.single_port) {
        emit_bus_type(sdel_type, 4);
    }
    out << "\n";

    out << "  cell (" << cell_name << ") {\n";
    out << "    area : " << num(size.width * size.height) << ";\n";
    out << "    interface_timing : true;\n";
    if (!options.single_port && !options.bitcell_6t) {
        out << "    contention_condition : \""
            << dual_port_contention_condition(addr_width) << "\";\n";
    }
    out << "    cell_leakage_power : "
        << num(scalar_or(data, data ? data->cell_leakage_power : 0.0, 0.0)) << ";\n";
    out << "    memory () {\n";
    out << "      type : ram;\n";
    out << "      address_width : " << addr_width << ";\n";
    out << "      word_width : " << options.num_data_bits << ";\n";
    out << "    }\n";
    out << "    pg_pin (vdd) {\n";
    out << "      direction : inout;\n";
    out << "      pg_type : primary_power;\n";
    out << "      voltage_name : \"VDD\";\n";
    out << "    }\n";
    out << "    pg_pin (vss) {\n";
    out << "      direction : inout;\n";
    out << "      pg_type : primary_ground;\n";
    out << "      voltage_name : \"VSS\";\n";
    out << "    }\n";
    out << "    pin (clk) {\n";
    out << "      direction : input;\n";
    out << "      capacitance : "
        << num(scalar_or(data, data ? data->clk_capacitance : 0.025, 0.025)) << ";\n";
    out << "      clock : true;\n";
    out << "      min_period : " << num(min_period) << ";\n";
    if (timing && !timing->port_delays.empty()) {
        // Stay inside the characterized clock-slew range for both ports.
        double max_slew = std::numeric_limits<double>::infinity();
        for (const auto& port : timing->port_delays)
            max_slew = std::min(max_slew, port.second.index_1.back());
        out << "      max_transition : " << num(max_slew) << ";\n";
    }
    if (measured_power) {
        // Vendor-srambank convention: internal_power groups on clk gated by
        // the write-enable pin (we_n is active-low: "we_n" = read access,
        // "!we_n" = write access), values in library power units (uW here),
        // derived from per-access energy at the documented reference cycle.
        constexpr double kDefaultCycleNs = 4.0;
        const double cyc = CharacterizationData::provided(data->power.reference_cycle_ns)
                               ? data->power.reference_cycle_ns
                               : kDefaultCycleNs;
        auto uw = [cyc](double pj) {
            return CharacterizationData::provided(pj)
                       ? num(pj * 1000.0 / cyc)   // pJ/ns = mW -> uW
                       : std::string("0");
        };
        std::vector<std::pair<std::string, double>> ops;
        if (options.single_port || options.bitcell_6t) {
            ops = {{"\"we_n\"", data->power.read_access_pj},
                   {"\"!we_n\"", data->power.write_access_pj}};
        } else {
            ops = {{"\"we_n_A\"", data->power.read_access_pj},
                   {"\"!we_n_A\"", data->power.write_access_pj},
                   {"\"we_n_B\"", data->power.read_access_pj},
                   {"\"!we_n_B\"", data->power.write_access_pj}};
        }
        for (const auto& op : ops) {
            out << "      internal_power () {\n";
            out << "        when : " << op.first << ";\n";
            out << "        related_pg_pin : vdd;\n";
            for (const char* t : {"rise_power", "fall_power"}) {
                out << "        " << t << " (" << power_template << ") {\n";
                out << "          index_1 (\"1.0\");\n";
                out << "          values (\"" << uw(op.second) << "\");\n";
                out << "        }\n";
            }
            out << "      }\n";
        }
    }
    out << "    }\n";

    if (options.single_port) {
        emit_input_pin(out, "ce_n", constraint_template, data);
        emit_input_pin(out, "oe_n", constraint_template, data);
        emit_input_pin(out, "we_n", constraint_template, data);
        emit_input_bus(out, "sdel", sdel_type, constraint_template, "", data);
        emit_input_bus(out, "A", address_type, constraint_template, "", data);
        emit_input_bus(out, "D", data_type, constraint_template, "A", data);
    } else if (options.bitcell_6t) {
        emit_input_pin(out, "rst_n", constraint_template, data, true);
        emit_input_pin(out, "ce_n", constraint_template, data);
        emit_input_pin(out, "we_n", constraint_template, data);
        emit_input_pin(out, "oe_n", constraint_template, data);
        emit_input_bus(out, "A", address_type, constraint_template, "", data);
        emit_input_bus(out, "D", data_type, constraint_template, "A", data);
    } else {
        emit_input_pin(out, "rst_n", constraint_template, data, true);
        emit_input_pin(out, "ce_n_A", constraint_template, data);
        emit_input_pin(out, "ce_n_B", constraint_template, data);
        emit_input_pin(out, "we_n_A", constraint_template, data);
        emit_input_pin(out, "we_n_B", constraint_template, data);
        emit_input_pin(out, "oe_n_A", constraint_template, data);
        emit_input_pin(out, "oe_n_B", constraint_template, data);
        // Both dual-port addresses select independent read/write accesses.
        emit_input_bus(out, "A_A", address_type, constraint_template, "", data);
        emit_input_bus(out, "A_B", address_type, constraint_template, "", data);
        emit_input_bus(out, "D_A", data_type, constraint_template, "A_A", data);
        emit_input_bus(out, "D_B", data_type, constraint_template, "A_B", data);
    }

    // Helper to emit Q-bus timing block (shared logic for SP Q and DP Q_A/Q_B)
    auto emit_q_timing = [&](const std::string& port) {
        const CharacterizationDelay* measured = timing && timing->delay.present ? &timing->delay : nullptr;
        if (timing && timing->port_delays.count(port)) measured = &timing->port_delays.at(port);
        out << "      timing () {\n";
        out << "        related_pin : \"clk\";\n";
        out << "        timing_type : rising_edge;\n";
        out << "        timing_sense : non_unate;\n";
        std::vector<double> d_idx1{0.009, 0.227};
        std::vector<double> d_idx2{0.005, 0.5};
        Table2D cell_rise{{kClockToQ, kClockToQ}, {kClockToQ, kClockToQ}};
        Table2D cell_fall = cell_rise;
        std::vector<double> rise_tr{0.009, 0.227};
        std::vector<double> fall_tr{0.009, 0.227};
        if (measured) {
            const CharacterizationDelay& d = *measured;
            d_idx1 = d.index_1;
            d_idx2 = d.index_2;
            cell_rise = d.cell_rise;
            cell_fall = d.cell_fall;
            rise_tr = d.rise_transition;
            fall_tr = d.fall_transition;
        }
        auto join = [&out](const std::vector<double>& v) {
            for (size_t i = 0; i < v.size(); ++i) {
                if (i) out << ", ";
                out << num(v[i]);
            }
        };
        std::vector<std::pair<const char*, const Table2D*>> delay_tables = {
            {"cell_rise", &cell_rise}, {"cell_fall", &cell_fall}};
        const bool slew_2d = measured && !measured->rise_transition_2d.empty();
        if (slew_2d) {
            delay_tables.emplace_back("rise_transition", &measured->rise_transition_2d);
            delay_tables.emplace_back("fall_transition", &measured->fall_transition_2d);
        }
        for (const auto& entry : delay_tables) {
            out << "        " << entry.first << " (" << delay_template << ") {\n";
            out << "          index_1 (\"";
            join(d_idx1);
            out << "\");\n";
            out << "          index_2 (\"";
            join(d_idx2);
            out << "\");\n";
            out << "          values (";
            for (size_t i = 0; i < entry.second->size(); ++i) {
                if (i) out << ", ";
                out << '"';
                join((*entry.second)[i]);
                out << '"';
            }
            out << ");\n";
            out << "        }\n";
        }
        const std::pair<const char*, const std::vector<double>*> slew_tables[2] = {
            {"rise_transition", &rise_tr}, {"fall_transition", &fall_tr}};
        for (const auto& entry : slew_tables) {
            if (slew_2d) break;
            out << "        " << entry.first << " (" << slew_template << ") {\n";
            out << "          index_1 (\"";
            join(d_idx2);
            out << "\");\n";
            out << "          values (\"";
            join(*entry.second);
            out << "\");\n";
            out << "        }\n";
        }
        out << "      }\n";
    };

    if (options.single_port) {
        out << "    bus (Q) {\n";
        out << "      bus_type : " << data_type << ";\n";
        out << "      direction : output;\n";
        out << "      max_capacitance : "
            << num(scalar_or(data, data ? data->output_max_capacitance : 0.5, 0.5)) << ";\n";
        out << "      memory_read () {\n";
        out << "        address : A;\n";
        out << "      }\n";
        emit_q_timing("A");
        out << "    }\n";
    } else {
        // Dual-port: Q_A driven by A_A, Q_B driven by A_B
        std::vector<std::pair<std::string, std::string>> outputs{{"Q_A", "A_A"}, {"Q_B", "A_B"}};
        if (options.bitcell_6t) outputs = {{"Q", "A"}};
        for (const auto& qb : outputs) {
            out << "    bus (" << qb.first << ") {\n";
            out << "      bus_type : " << data_type << ";\n";
            out << "      direction : output;\n";
            out << "      max_capacitance : "
                << num(scalar_or(data, data ? data->output_max_capacitance : 0.5, 0.5)) << ";\n";
            out << "      memory_read () {\n";
            out << "        address : " << qb.second << ";\n";
            out << "      }\n";
            emit_q_timing(qb.first == "Q_B" ? "B" : "A");
            out << "    }\n";
        }
    }
    out << "  }\n";
    out << "}\n";
    return out.str();
}

}  // namespace

bool export_estimated_liberty(const MainCliOptions& options,
                              const std::string& lef_path,
                              const std::string& liberty_path,
                              std::string* error,
                              const CharacterizationData* char_data) {

    MacroSize size;
    if (!read_lef_size(lef_path, size, error)) return false;
    if (!validate_lef_interface(options, lef_path, error)) return false;

    const std::string expected_cell = "sram_x" + std::to_string(options.num_wls * 2) + "x" +
        std::to_string(options.num_data_bits) + "x" + std::to_string(options.num_banks);
    if (char_data && !char_data->cell.empty() && char_data->cell != expected_cell) {
        if (error) *error = "Characterization cell '" + char_data->cell + "' does not match '" + expected_cell + "'";
        return false;
    }
    auto valid_ports = [&](const CharacterizationTiming& timing) {
        if (timing.port_delays.empty()) return true;
        return !options.single_port && !options.bitcell_6t && timing.port_delays.size() == 2 &&
            timing.port_delays.count("A") && timing.port_delays.count("B");
    };
    if (char_data) {
        bool valid = valid_ports(char_data->timing);
        for (const auto& v : char_data->variants) valid = valid && valid_ports(v.timing);
        if (!valid) {
            if (error) *error = "Per-port characterization requires an 8T macro and both A and B";
            return false;
        }
    }

    auto write_lib = [&](const std::string& path,
                         const CharacterizationTiming* timing,
                         const std::string& suffix) -> bool {
        std::ofstream liberty(path, std::ios::out | std::ios::trunc);
        if (!liberty) {
            if (error) *error = "Failed to open Liberty file for writing: " + path;
            return false;
        }
        liberty << build_estimated_liberty(options, size, char_data, timing, suffix);
        liberty.close();
        if (!liberty) {
            if (error) *error = "Failed while writing Liberty file: " + path;
            return false;
        }
        LOGI << "Liberty model written to " << path
             << " (area " << num(size.width * size.height)
             << (char_data ? " um^2; characterization-JSON sourced)"
                           : " um^2; timing is not characterized)");
        return true;
    };

    if (!write_lib(liberty_path, char_data ? &char_data->timing : nullptr, "")) {
        return false;
    }

    // Per-sdel-tie-off variants each get a complete standalone .lib: sdel is
    // programmable in the netlist, so a characterized flow binds consumers to
    // the Liberty matching their tie-off.
    if (char_data && !char_data->variants.empty()) {
        std::string base = liberty_path;
        const std::string ext = ".lib";
        if (base.size() > ext.size() &&
            base.compare(base.size() - ext.size(), ext.size(), ext) == 0) {
            base.erase(base.size() - ext.size());
        }
        for (const auto& variant : char_data->variants) {
            if (!write_lib(base + "_" + variant.name + ".lib",
                           &variant.timing, "_" + variant.name)) {
                return false;
            }
        }
    }
    return true;
}

}  // namespace OpenFinRAM
