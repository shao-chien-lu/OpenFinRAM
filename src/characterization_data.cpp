#include "characterization_data.hpp"

#include <fstream>
#include <sstream>

namespace OpenFinRAM {
namespace {

// ---------------------------------------------------------------------------
// Minimal JSON parser. Supports the full JSON grammar for the value types the
// characterization schema uses (objects, arrays, strings, numbers, booleans,
// null) but no duplicate-key detection beyond last-wins, and no unicode
// escape validation beyond \" \\ \/ \b \f \n \r \t \uXXXX.
// ---------------------------------------------------------------------------

struct JValue {
    enum Type { NUL, BOOL, NUM, STR, ARR, OBJ };
    Type type = NUL;
    bool boolean = false;
    double number = 0;
    std::string string;
    std::vector<JValue> array;
    std::vector<std::pair<std::string, JValue>> object;

    const JValue* find(const char* key) const {
        if (type != OBJ) return nullptr;
        for (const auto& kv : object) {
            if (kv.first == key) return &kv.second;
        }
        return nullptr;
    }
};

class JsonParser {
public:
    explicit JsonParser(std::string text) : text_(std::move(text)) {}

    bool parse(JValue& out, std::string* error) {
        pos_ = 0;
        skip_ws();
        if (!parse_value(out)) {
            if (error) *error = "JSON parse error at offset " + std::to_string(pos_);
            return false;
        }
        skip_ws();
        if (pos_ != text_.size()) {
            if (error) *error = "Trailing content after JSON document at offset " + std::to_string(pos_);
            return false;
        }
        return true;
    }

private:
    // Owned copy: binding a member reference to a temporary (e.g. the result
    // of stringstream::str()) leaves it dangling.
    std::string text_;
    size_t pos_ = 0;

    void skip_ws() {
        while (pos_ < text_.size()) {
            const char c = text_[pos_];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') ++pos_;
            else break;
        }
    }

    bool peek(char c) { return pos_ < text_.size() && text_[pos_] == c; }

    bool eat(char c) {
        if (peek(c)) { ++pos_; return true; }
        return false;
    }

    bool parse_value(JValue& out) {
        skip_ws();
        if (peek('{')) return parse_object(out);
        if (peek('[')) return parse_array(out);
        if (peek('"')) { out.type = JValue::STR; return parse_string(out.string); }
        if (text_.compare(pos_, 4, "true") == 0) {
            out.type = JValue::BOOL; out.boolean = true; pos_ += 4; return true;
        }
        if (text_.compare(pos_, 5, "false") == 0) {
            out.type = JValue::BOOL; out.boolean = false; pos_ += 5; return true;
        }
        if (text_.compare(pos_, 4, "null") == 0) {
            out.type = JValue::NUL; pos_ += 4; return true;
        }
        return parse_number(out);
    }

    bool parse_object(JValue& out) {
        out.type = JValue::OBJ;
        ++pos_;  // '{'
        skip_ws();
        if (eat('}')) return true;
        while (true) {
            skip_ws();
            std::string key;
            if (!peek('"') || !parse_string(key)) return false;
            skip_ws();
            if (!eat(':')) return false;
            JValue value;
            if (!parse_value(value)) return false;
            out.object.emplace_back(std::move(key), std::move(value));
            skip_ws();
            if (eat(',')) continue;
            return eat('}');
        }
    }

    bool parse_array(JValue& out) {
        out.type = JValue::ARR;
        ++pos_;  // '['
        skip_ws();
        if (eat(']')) return true;
        while (true) {
            JValue value;
            if (!parse_value(value)) return false;
            out.array.push_back(std::move(value));
            skip_ws();
            if (eat(',')) continue;
            return eat(']');
        }
    }

    bool parse_string(std::string& out) {
        if (!eat('"')) return false;
        out.clear();
        while (pos_ < text_.size()) {
            const char c = text_[pos_++];
            if (c == '"') return true;
            if (c != '\\') { out.push_back(c); continue; }
            if (pos_ >= text_.size()) return false;
            const char esc = text_[pos_++];
            switch (esc) {
                case '"': out.push_back('"'); break;
                case '\\': out.push_back('\\'); break;
                case '/': out.push_back('/'); break;
                case 'b': out.push_back('\b'); break;
                case 'f': out.push_back('\f'); break;
                case 'n': out.push_back('\n'); break;
                case 'r': out.push_back('\r'); break;
                case 't': out.push_back('\t'); break;
                case 'u': {
                    // Encode as UTF-8; only BMP handling, no surrogate pairs.
                    if (pos_ + 4 > text_.size()) return false;
                    // Hand-decode the four hex digits: std::stoul would throw
                    // on "\uZZZZ" (and accept "0x1", sign, whitespace), and
                    // the loader's contract is bool + error, never an exception.
                    unsigned cp = 0;
                    for (int k = 0; k < 4; ++k) {
                        const char h = text_[pos_ + k];
                        unsigned v = 0;
                        if (h >= '0' && h <= '9') v = static_cast<unsigned>(h - '0');
                        else if (h >= 'a' && h <= 'f') v = 10u + static_cast<unsigned>(h - 'a');
                        else if (h >= 'A' && h <= 'F') v = 10u + static_cast<unsigned>(h - 'A');
                        else return false;
                        cp = (cp << 4) | v;
                    }
                    pos_ += 4;
                    if (cp < 0x80) {
                        out.push_back(static_cast<char>(cp));
                    } else if (cp < 0x800) {
                        out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
                        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
                    } else {
                        out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
                        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
                        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
                    }
                    break;
                }
                default: return false;
            }
        }
        return false;
    }

    bool parse_number(JValue& out) {
        const size_t start = pos_;
        if (peek('-')) ++pos_;
        while (pos_ < text_.size() &&
               ((text_[pos_] >= '0' && text_[pos_] <= '9') ||
                text_[pos_] == '.' || text_[pos_] == 'e' || text_[pos_] == 'E' ||
                text_[pos_] == '+' || text_[pos_] == '-')) {
            ++pos_;
        }
        if (pos_ == start) return false;
        try {
            const std::string literal = text_.substr(start, pos_ - start);
            size_t consumed = 0;
            out.type = JValue::NUM;
            out.number = std::stod(literal, &consumed);
            // stod stops at the first char it cannot use; "1.2.3" or "1e+e-2"
            // would otherwise silently parse as a prefix.
            if (consumed != literal.size()) return false;
        } catch (const std::exception&) {
            return false;
        }
        return true;
    }
};

// --- schema helpers ---------------------------------------------------------

bool is_liberty_identifier(const std::string& s) {
    if (s.empty()) return false;
    auto alpha_ = [](char c) {
        return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '_';
    };
    if (!alpha_(s[0])) return false;
    for (const char c : s) {
        if (!alpha_(c) && !(c >= '0' && c <= '9')) return false;
    }
    return true;
}

bool get_number(const JValue& obj, const char* key, double& out) {
    const JValue* v = obj.find(key);
    if (!v || v->type != JValue::NUM) return false;
    out = v->number;
    return true;
}

bool get_vector(const JValue& obj, const char* key, std::vector<double>& out) {
    const JValue* v = obj.find(key);
    if (!v) return false;
    if (v->type != JValue::ARR) return false;
    out.clear();
    for (const auto& e : v->array) {
        if (e.type != JValue::NUM) return false;
        out.push_back(e.number);
    }
    return true;
}

bool get_matrix(const JValue& obj, const char* key, Table2D& out,
                size_t rows, size_t cols) {
    const JValue* v = obj.find(key);
    if (!v) return false;
    if (v->type != JValue::ARR || v->array.size() != rows) return false;
    out.clear();
    for (const auto& row : v->array) {
        if (row.type != JValue::ARR || row.array.size() != cols) return false;
        std::vector<double> r;
        r.reserve(cols);
        for (const auto& e : row.array) {
            if (e.type != JValue::NUM) return false;
            r.push_back(e.number);
        }
        out.push_back(std::move(r));
    }
    return true;
}

bool parse_delay(const JValue& obj, CharacterizationDelay& out, std::string* error) {
    if (!get_vector(obj, "index_1", out.index_1) ||
        !get_vector(obj, "index_2", out.index_2)) {
        if (error) *error = "delay tables need index_1 and index_2 arrays";
        return false;
    }
    const size_t r = out.index_1.size(), c = out.index_2.size();
    if (!get_matrix(obj, "cell_rise", out.cell_rise, r, c) ||
        !get_matrix(obj, "cell_fall", out.cell_fall, r, c)) {
        if (error) *error = "cell_rise/cell_fall must be index_1 x index_2 matrices";
        return false;
    }
    if (!get_vector(obj, "rise_transition", out.rise_transition) ||
        !get_vector(obj, "fall_transition", out.fall_transition) ||
        out.rise_transition.size() != c || out.fall_transition.size() != c) {
        if (error) *error = "rise/fall_transition must have one entry per index_2 point";
        return false;
    }
    out.present = true;
    return true;
}

bool parse_constraints(const JValue& obj, CharacterizationConstraints& out,
                       std::string* error) {
    if (!get_vector(obj, "index_1", out.index_1) ||
        !get_vector(obj, "index_2", out.index_2)) {
        if (error) *error = "constraint tables need index_1 and index_2 arrays";
        return false;
    }
    const size_t r = out.index_1.size(), c = out.index_2.size();
    if (!get_matrix(obj, "setup_rise", out.setup_rise, r, c) ||
        !get_matrix(obj, "setup_fall", out.setup_fall, r, c) ||
        !get_matrix(obj, "hold_rise", out.hold_rise, r, c) ||
        !get_matrix(obj, "hold_fall", out.hold_fall, r, c)) {
        if (error) *error = "constraint matrices must be index_1 x index_2";
        return false;
    }
    out.present = true;
    return true;
}

bool parse_timing(const JValue& timing_obj, CharacterizationTiming& out,
                  std::string* error) {
    if (const JValue* d = timing_obj.find("delay")) {
        if (!parse_delay(*d, out.delay, error)) return false;
    } else {
        if (error) *error = "timing block missing 'delay'";
        return false;
    }
    if (const JValue* c = timing_obj.find("constraints")) {
        // Constraints are optional within a timing block.
        std::string local_error;
        if (!parse_constraints(*c, out.constraints, &local_error)) {
            if (error) *error = "constraints: " + local_error;
            return false;
        }
    }
    return true;
}

}  // namespace

bool load_characterization_json(const std::string& path,
                                CharacterizationData& out,
                                std::string* error) {
    std::ifstream in(path);
    if (!in) {
        if (error) *error = "Failed to open characterization JSON: " + path;
        return false;
    }
    std::stringstream buf;
    buf << in.rdbuf();

    JValue root;
    JsonParser parser(buf.str());
    if (!parser.parse(root, error)) return false;
    if (root.type != JValue::OBJ) {
        if (error) *error = "Characterization JSON root must be an object";
        return false;
    }

    double num = 0;
    if (get_number(root, "cell_leakage_power", num)) out.cell_leakage_power = num;
    if (get_number(root, "clock_min_period", num)) out.clock_min_period = num;
    if (const JValue* caps = root.find("pin_capacitance")) {
        if (get_number(*caps, "clk", num)) out.clk_capacitance = num;
        if (get_number(*caps, "default_input", num)) out.default_input_pin_capacitance = num;
    }
    if (get_number(root, "output_max_capacitance", num)) out.output_max_capacitance = num;
    if (const JValue* pw = root.find("power")) {
        double num2 = 0;
        if (get_number(*pw, "leakage_uw", num2)) {
            out.power.leakage_uw = num2;
            out.power.present = true;
            // Leakage is the authoritative cell_leakage_power when present.
            out.cell_leakage_power = num2;
        }
        if (get_number(*pw, "read_access_pj", num2)) {
            out.power.read_access_pj = num2;
            out.power.present = true;
        }
        if (get_number(*pw, "write_access_pj", num2)) {
            out.power.write_access_pj = num2;
            out.power.present = true;
        }
        if (get_number(*pw, "reference_cycle_ns", num2)) {
            out.power.reference_cycle_ns = num2;
        }
    }
    if (const JValue* c = root.find("comment")) {
        if (c->type == JValue::STR) out.comment = c->string;
    }
    if (const JValue* oc = root.find("operating_conditions")) {
        double num2 = 0;
        if (get_number(*oc, "voltage", num2)) {
            out.operating_conditions.voltage = num2;
            out.operating_conditions.present = true;
        }
        if (get_number(*oc, "temperature", num2)) {
            out.operating_conditions.temperature = num2;
            out.operating_conditions.present = true;
        }
        if (const JValue* n = oc->find("name")) {
            if (n->type == JValue::STR && !n->string.empty()) {
                // Emitted unquoted as the operating_conditions group name and
                // default_operating_conditions value, so it must lex as an
                // identifier; "SS (0.63V)" would break the .lib.
                if (!is_liberty_identifier(n->string)) {
                    if (error) {
                        *error = "operating_conditions.name '" + n->string +
                                 "' is not a Liberty identifier ([A-Za-z_][A-Za-z0-9_]*)";
                    }
                    return false;
                }
                out.operating_conditions.name = n->string;
                out.operating_conditions.present = true;
            }
        }
    }

    if (const JValue* t = root.find("timing")) {
        if (!parse_timing(*t, out.timing, error)) return false;
    }

    if (const JValue* vars = root.find("variants")) {
        if (vars->type != JValue::ARR) {
            if (error) *error = "'variants' must be an array";
            return false;
        }
        for (const auto& entry : vars->array) {
            const JValue* name = entry.find("name");
            const JValue* timing = entry.find("timing");
            if (!name || name->type != JValue::STR || !timing) {
                if (error) *error = "each variant needs 'name' and 'timing'";
                return false;
            }
            CharacterizationData::Variant variant;
            variant.name = name->string;
            if (!parse_timing(*timing, variant.timing, error)) return false;
            out.variants.push_back(std::move(variant));
        }
    }

    return true;
}

}  // namespace OpenFinRAM
