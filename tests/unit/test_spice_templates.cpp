#include <gtest/gtest.h>

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "spice_templates.hpp"
#include "spice_generator.hpp"

namespace {

std::string read_tech_spice(const std::string& name) {
    std::ifstream input(std::string(REPO_ROOT) + "/tech/spice/" + name);
    return std::string(std::istreambuf_iterator<char>(input),
                       std::istreambuf_iterator<char>());
}

size_t count_occurrences(const std::string& text, const std::string& needle) {
    size_t count = 0;
    size_t position = 0;
    while ((position = text.find(needle, position)) != std::string::npos) {
        ++count;
        position += needle.size();
    }
    return count;
}

std::vector<std::string> logical_statements(const std::string& text) {
    std::vector<std::string> statements;
    std::istringstream input(text);
    std::string line;
    while (std::getline(input, line)) {
        if (!line.empty() && line.front() == '+') {
            if (statements.empty()) {
                ADD_FAILURE() << "orphan SPICE continuation line";
                return {};
            }
            statements.back() += " " + line.substr(1);
        } else if (!line.empty() && line.front() != '*') {
            statements.push_back(line);
        }
    }
    return statements;
}

std::vector<std::string> tokens(const std::string& statement) {
    std::istringstream input(statement);
    return {std::istream_iterator<std::string>(input),
            std::istream_iterator<std::string>()};
}

size_t subckt_port_count(const std::string& text) {
    const auto statements = logical_statements(text);
    const auto fields = tokens(statements.front());
    EXPECT_EQ(fields.front(), ".SUBCKT");
    return fields.size() - 2;
}

size_t instance_node_count(const std::string& text, const std::string& name) {
    for (const auto& statement : logical_statements(text)) {
        const auto fields = tokens(statement);
        if (!fields.empty() && fields.front() == name) {
            return fields.size() - 2;  // instance name and referenced subcircuit
        }
    }
    ADD_FAILURE() << "missing instance " << name;
    return 0;
}

std::vector<std::string> subckt_body(const std::string& text,
                                     const std::string& name);

size_t subckt_instance_node_count(const std::string& text,
                                  const std::string& subckt,
                                  const std::string& instance) {
    const auto body = subckt_body(text, subckt);
    for (const auto& statement : body) {
        const auto fields = tokens(statement);
        if (!fields.empty() && fields.front() == instance) {
            return fields.size() - 2;
        }
    }
    ADD_FAILURE() << "missing instance " << instance << " in " << subckt;
    return 0;
}

size_t named_subckt_port_count(const std::string& text, const std::string& name) {
    const auto body = subckt_body(text, name);
    if (body.empty()) {
        return 0;
    }
    return tokens(body.front()).size() - 2;
}

std::string upper(std::string text) {
    for (char& character : text) {
        character = static_cast<char>(
            std::toupper(static_cast<unsigned char>(character)));
    }
    return text;
}

// Whitespace-normalized statements of one .SUBCKT block, with the optional
// name on .ENDS dropped so the tracked file and the template compare equal.
std::vector<std::string> subckt_body(const std::string& text,
                                     const std::string& name) {
    std::vector<std::string> body;
    bool inside = false;
    for (const auto& statement : logical_statements(text)) {
        const auto fields = tokens(statement);
        if (fields.empty()) {
            continue;
        }
        if (!inside) {
            inside = upper(fields.front()) == ".SUBCKT" && fields.size() >= 2 &&
                     fields[1] == name;
            if (!inside) {
                continue;
            }
        } else if (upper(fields.front()) == ".ENDS") {
            body.emplace_back(".ENDS");
            return body;
        }
        std::string joined;
        for (const auto& field : fields) {
            if (!joined.empty()) {
                joined += " ";
            }
            joined += field;
        }
        body.push_back(joined);
    }
    ADD_FAILURE() << "missing .SUBCKT " << name;
    return {};
}

std::map<std::string, std::string> instance_bindings(
    const std::string& parent, const std::string& instance,
    const std::string& child) {
    const auto child_statements = logical_statements(child);
    if (child_statements.empty()) {
        ADD_FAILURE() << "empty child subcircuit";
        return {};
    }
    const auto formal = tokens(child_statements.front());
    for (const auto& statement : logical_statements(parent)) {
        const auto actual = tokens(statement);
        if (actual.empty() || actual.front() != instance) {
            continue;
        }
        if (formal.size() != actual.size()) {
            ADD_FAILURE() << instance << " has " << actual.size() - 2
                          << " actual nodes for " << formal.size() - 2
                          << " formal ports";
            return {};
        }
        std::map<std::string, std::string> bindings;
        for (size_t index = 2; index < formal.size(); ++index) {
            bindings.emplace(formal[index], actual[index - 1]);
        }
        return bindings;
    }
    ADD_FAILURE() << "missing instance " << instance;
    return {};
}

// Although SPICE identifiers are case-insensitive, downstream netlist tools
// are not universally so.  Report two spellings of the same logical node
// within any generated subcircuit before that ambiguity can become a split
// net in a case-sensitive consumer.
std::vector<std::string> case_aliased_nets(const std::string& text) {
    std::set<std::string> collisions;
    std::map<std::string, std::string> spelling;
    std::string current;
    auto record = [&](const std::string& node) {
        const std::string key = upper(node);
        const auto found = spelling.find(key);
        if (found == spelling.end()) {
            spelling.emplace(key, node);
        } else if (found->second != node) {
            collisions.insert(current + ": " + found->second + " / " + node);
        }
    };

    for (const auto& statement : logical_statements(text)) {
        const auto fields = tokens(statement);
        if (fields.empty()) {
            continue;
        }
        if (upper(fields.front()) == ".SUBCKT") {
            current = fields.size() > 1 ? fields[1] : "<unnamed>";
            spelling.clear();
            for (size_t index = 2; index < fields.size(); ++index) {
                record(fields[index]);
            }
            continue;
        }
        if (upper(fields.front()) == ".ENDS") {
            current.clear();
            spelling.clear();
            continue;
        }
        if (current.empty()) {
            continue;
        }

        const char kind = static_cast<char>(
            std::toupper(static_cast<unsigned char>(fields.front().front())));
        if (kind == 'M') {
            for (size_t node = 1; node <= 4 && node < fields.size(); ++node) {
                record(fields[node]);
            }
        } else if (kind == 'X') {
            for (size_t node = 1; node + 1 < fields.size(); ++node) {
                record(fields[node]);
            }
        }
    }
    return {collisions.begin(), collisions.end()};
}

// A net used by exactly one device terminal and absent from the port list is
// floating: SPICE creates it silently as a subcircuit-local node.
std::vector<std::string> dangling_nets(const std::string& text,
                                       const std::string& name) {
    const auto body = subckt_body(text, name);
    if (body.empty()) {
        return {};
    }
    std::set<std::string> ports;
    const auto header = tokens(body.front());
    for (size_t index = 2; index < header.size(); ++index) {
        ports.insert(upper(header[index]));
    }

    std::map<std::string, size_t> uses;
    for (size_t line = 1; line < body.size(); ++line) {
        const auto fields = tokens(body[line]);
        if (fields.empty() || upper(fields.front()).front() != 'M') {
            continue;
        }
        if (fields.size() < 6) {
            ADD_FAILURE() << "malformed device line: " << body[line];
            continue;
        }
        for (size_t node = 1; node <= 4; ++node) {  // drain, gate, source, bulk
            ++uses[upper(fields[node])];
        }
    }

    std::vector<std::string> dangling;
    for (const auto& entry : uses) {
        if (entry.second == 1 && ports.find(entry.first) == ports.end()) {
            dangling.push_back(entry.first);
        }
    }
    return dangling;
}

}  // namespace

TEST(SpiceTemplates8T, PortWrappersPreserveIndependentControls) {
    const std::string port_a = OpenFinRAM::SpiceTemplates::get_ioprech_8t_a();
    const std::string port_b = OpenFinRAM::SpiceTemplates::get_ioprech_8t_b();

    EXPECT_NE(port_a.find(".SUBCKT ioprech_sram_8t_a"), std::string::npos);
    EXPECT_NE(port_a.find("WRENAN_A WRENA_A SAE_A SAPRECHN_A"), std::string::npos);
    EXPECT_NE(port_a.find("BLTN_A[0] BLTN_A[1] BLTN_A[2] BLTN_A[3]"),
              std::string::npos);
    EXPECT_NE(port_a.find("BLPRECHTN_A BLPRECHBN_A"), std::string::npos);
    EXPECT_EQ(count_occurrences(port_a, "iocolgrp_sram_6t122_v2"), 1U);

    EXPECT_NE(port_b.find(".SUBCKT ioprech_sram_8t_b"), std::string::npos);
    EXPECT_NE(port_b.find("WRENAN_B WRENA_B SAE_B SAPRECHN_B"), std::string::npos);
    EXPECT_NE(port_b.find("BLTN_B[0] BLTN_B[1] BLTN_B[2] BLTN_B[3]"),
              std::string::npos);
    EXPECT_NE(port_b.find("BLPRECHTN_B BLPRECHBN_B"), std::string::npos);
    EXPECT_EQ(count_occurrences(port_b, "iocolgrp_sram_6t122_v2"), 1U);
}

namespace {

// Decks are generated from the repository root: the 8T IO columns' netlist
// is tech collateral the generator reads at run time.
class ScopedCurrentPath {
public:
    explicit ScopedCurrentPath(const std::filesystem::path& path)
        : original_(std::filesystem::current_path()) {
        std::filesystem::current_path(path);
    }
    ~ScopedCurrentPath() { std::filesystem::current_path(original_); }

private:
    std::filesystem::path original_;
};

std::string generated_deck_8t() {
    ScopedCurrentPath cwd(REPO_ROOT);
    MainCliOptions config;
    config.single_port = false;
    config.num_wls = 2;
    config.num_data_bits = 4;
    config.num_banks = 1;
    return OpenFinRAM::SpiceGenerator(config).generate_spice_content();
}

// One subcircuit of a deck as its own text, so the binding helpers search
// only it.
std::string subckt_text(const std::string& deck, const std::string& name) {
    std::string text;
    for (const auto& statement : subckt_body(deck, name)) {
        text += statement + "\n";
    }
    return text;
}

// The column group's net for an IO column pin: ysel_A[2] -> yselA[2],
// blprechn_A -> blprechnA, sae_A -> sae_A, DA -> DA.
std::string colgrp_net(const std::string& pin) {
    for (const std::string& bus : {"BL_", "BLN_"}) {
        if (pin.rfind(bus, 0) == 0) return pin;
    }
    if (pin.rfind("sae_", 0) == 0 || pin == "VDD" || pin == "VSS" ||
        pin == "DA" || pin == "DB" || pin == "QA" || pin == "QB") {
        return pin;
    }
    const auto underscore = pin.rfind("_");
    return pin.substr(0, underscore) + pin.substr(underscore + 1);
}

}  // namespace

TEST(SpiceTemplates8T, ColumnGroupInstantiatesBothIoColumnBlocks) {
    const std::string deck = generated_deck_8t();
    const std::string colgrp = subckt_text(deck, "colgrp_sram_8t");

    EXPECT_EQ(count_occurrences(colgrp, "iocol_sram_8t_a"), 1U);
    EXPECT_EQ(count_occurrences(colgrp, "iocol_sram_8t_b"), 1U);
    EXPECT_EQ(count_occurrences(colgrp, "array_sram_8t"), 1U);  // one unsplit array
    EXPECT_EQ(colgrp.find("ioprech_sram_8t"), std::string::npos);
    EXPECT_EQ(deck.find("iocolgrp_sram_6t122_v2"), std::string::npos);  // the 6T core is gone
    for (const std::string& split_era : {"WLTA", "WLBA", "yselt", "yselb", "blprecht"}) {
        EXPECT_EQ(colgrp.find(split_era), std::string::npos) << split_era;
    }
    // The deck carries the blocks themselves: the parametric cells' devices.
    for (const std::string& block : {"iocol_block_a", "iocol_block_b"}) {
        const std::string text = subckt_text(deck, block);
        EXPECT_NE(text.find("Msa_"), std::string::npos) << block;  // sense amplifier
        EXPECT_NE(text.find("Mwd_"), std::string::npos) << block;  // write driver
        EXPECT_NE(text.find("Mol_"), std::string::npos) << block;  // output latch
        EXPECT_NE(text.find("M0_NT BL[0] YSEL[0] SA"), std::string::npos) << block;  // leaf 0's mux
    }
}

// The wordlines are driven at the array by strips of four-wordline slices,
// one strip per port on each side of the controller band, sized to the cells
// along the wordline in that half.
TEST(SpiceTemplates8T, DeckCarriesWordlineDriverStripsPerPortAndHalf) {
    const std::string deck = generated_deck_8t();  // NUM_WL 2 -> 4 wordlines, one slice; 4 bits -> 8 cells a half
    EXPECT_EQ(OpenFinRAM::SpiceGenerator::wordline_slice_class(8), 8);
    EXPECT_EQ(OpenFinRAM::SpiceGenerator::wordline_slice_class(9), 16);
    EXPECT_THROW(OpenFinRAM::SpiceGenerator::wordline_slice_class(65), std::runtime_error);
    const auto strip = subckt_body(deck, "wl_strip_c8_x1");
    ASSERT_FALSE(strip.empty());
    EXPECT_EQ(strip.front(), ".SUBCKT wl_strip_c8_x1 SEL[0] B[0] B[1] B[2] B[3] WL[0] WL[1] WL[2] WL[3] VDD VSS");
    EXPECT_NE(subckt_text(deck, "wl_strip_c8_x1")
                  .find("X_slice0 SEL[0] B[0] B[1] B[2] B[3] WL[0] WL[1] WL[2] WL[3] VDD VSS wl_slice_c8"),
              std::string::npos);
    // One pair of strips (both ports) on each side of the controller band.
    for (const std::string& pair : {"wl_strips_lo_c8_x1", "wl_strips_hi_c8_x1"}) {
        const auto body = subckt_body(deck, pair);
        ASSERT_FALSE(body.empty()) << pair;
        EXPECT_EQ(body.front(), ".SUBCKT " + pair + " SEL_A[0] B_A[0] B_A[1] B_A[2] B_A[3]"
                                " WL_A[0] WL_A[1] WL_A[2] WL_A[3] SEL_B[0] B_B[0] B_B[1] B_B[2] B_B[3]"
                                " WL_B[0] WL_B[1] WL_B[2] WL_B[3] VDD VSS");
        const std::string text = subckt_text(deck, pair);
        EXPECT_NE(text.find("X_a SEL_A[0] B_A[0] B_A[1] B_A[2] B_A[3] WL_A[0] WL_A[1] WL_A[2] WL_A[3] VDD VSS wl_strip_c8_x1"),
                  std::string::npos) << pair;
        EXPECT_NE(text.find("X_b SEL_B[0] B_B[0] B_B[1] B_B[2] B_B[3] WL_B[0] WL_B[1] WL_B[2] WL_B[3] VDD VSS wl_strip_c8_x1"),
                  std::string::npos) << pair;
    }
    // The slice ladder itself rides along, as the IO column blocks do.
    EXPECT_NE(subckt_text(deck, "wl_slice_c8").find("wl_slice_nand4n2p_inv2n2p"), std::string::npos);
    EXPECT_NE(subckt_text(deck, "wl_slice_nand4n2p_inv2n2p").find("nand2_fin_4n2p_2f"), std::string::npos);
}

TEST(SpiceTemplates8T, IoColumnInstanceAritiesMatchTheirBlocks) {
    const std::string deck = generated_deck_8t();
    const std::string colgrp = subckt_text(deck, "colgrp_sram_8t");
    for (const std::string& port : {"a", "b"}) {
        const std::string wrapper = subckt_text(deck, "iocol_sram_8t_" + port);
        const std::string block = subckt_text(deck, "iocol_block_" + port);
        ASSERT_FALSE(wrapper.empty());
        ASSERT_FALSE(block.empty());
        const std::string instance = port == "a" ? "XIO_A" : "XIO_B";
        EXPECT_EQ(instance_node_count(colgrp, instance), subckt_port_count(wrapper));
        EXPECT_EQ(instance_node_count(wrapper, "X_block"), subckt_port_count(block));
    }
}

TEST(SpiceTemplates8T, ColumnGroupBindsEveryIoColumnPinByName) {
    const std::string deck = generated_deck_8t();
    const std::string colgrp = subckt_text(deck, "colgrp_sram_8t");
    for (const std::string& port : {"A", "B"}) {
        const std::string wrapper = subckt_text(deck, std::string("iocol_sram_8t_") + (port == "A" ? "a" : "b"));
        const auto bindings = instance_bindings(colgrp, "XIO_" + port, wrapper);
        ASSERT_FALSE(bindings.empty());
        for (const auto& entry : bindings) {
            EXPECT_EQ(entry.second, colgrp_net(entry.first)) << entry.first;
        }
        EXPECT_EQ(bindings.at("wrena_" + port), "wrena" + port);
        EXPECT_EQ(bindings.at("wrenan_" + port), "wrenan" + port);
        EXPECT_EQ(bindings.at("D" + port), "D" + port);
        EXPECT_EQ(bindings.at("sae_" + port), "sae_" + port);
    }
}

// --share-port-b: banks in pairs, the pair's two columns mirrored about one
// two-sided port-B block; port B's enables one per pair, its precharges and
// selects per bank.
TEST(SpiceTemplates8T, SharedPortBPairsTwoHalvesAboutOneTwoSidedBlock) {
    ScopedCurrentPath cwd(REPO_ROOT);
    MainCliOptions config;
    config.num_wls = 2;
    config.num_data_bits = 4;
    config.num_banks = 2;
    config.share_port_b = true;
    const std::string deck = OpenFinRAM::SpiceGenerator(config).generate_spice_content();
    EXPECT_EQ(deck.find(".SUBCKT colgrp_sram_8t "), std::string::npos);

    const std::string half = subckt_text(deck, "colgrp_half_sram_8t");
    EXPECT_EQ(count_occurrences(half, "iocol_sram_8t_a"), 1U);
    EXPECT_EQ(half.find("iocol_sram_8t_b"), std::string::npos);
    EXPECT_EQ(instance_node_count(half, "X0"), named_subckt_port_count(deck, "array_sram_8t"));

    const std::string pair = subckt_text(deck, "colgrp_pair_sram_8t");
    const std::string wrapper = subckt_text(deck, "iocol_sram_8t_b2");
    ASSERT_FALSE(wrapper.empty());
    EXPECT_EQ(count_occurrences(pair, " colgrp_half_sram_8t"), 2U);
    EXPECT_EQ(instance_node_count(pair, "XIO_B"), subckt_port_count(wrapper));
    EXPECT_EQ(instance_node_count(wrapper, "X_block"), named_subckt_port_count(deck, "iocol_block_b2"));
    for (const auto& entry : instance_bindings(pair, "XIO_B", wrapper)) {
        EXPECT_EQ(entry.second, colgrp_net(entry.first)) << entry.first;
    }
    // The second half takes the second bank's wordlines, selects, controls
    // and the block's right group.
    EXPECT_NE(pair.find("XH1 WLA[4] WLA[5] WLA[6] WLA[7] WLB[4]"), std::string::npos);
    EXPECT_NE(pair.find("DA QA wrenaAR wrenanAR oeb_outAR oe_outAR blprechnAR yselnA[4]"), std::string::npos);
    EXPECT_NE(pair.find("sae_AR BL_B[4] BL_B[5] BL_B[6] BL_B[7] BLN_B[4]"), std::string::npos);

    const auto stacked = subckt_body(deck, "stacked_colgrp_x4x2x2");
    ASSERT_FALSE(stacked.empty());
    const auto header = tokens(stacked.front());
    auto has = [&header](const std::string& pin) {
        return std::find(header.begin(), header.end(), pin) != header.end();
    };
    for (const std::string& pin : {"sae_B[0]", "wrenaB[0]", "oe_outB[0]", "blprechnB[1]", "sae_A[1]", "yselB[7]"}) {
        EXPECT_TRUE(has(pin)) << pin;
    }
    for (const std::string& pin : {"sae_B[1]", "wrenaB[1]", "oe_outB[1]"}) {
        EXPECT_FALSE(has(pin)) << pin;
    }
    EXPECT_EQ(subckt_instance_node_count(deck, "stacked_colgrp_x4x2x2", "X0_0"),
              named_subckt_port_count(deck, "colgrp_pair_sram_8t"));
    EXPECT_EQ(subckt_text(deck, "stacked_colgrp_x4x2x2").find("X1_0 "), std::string::npos);  // one pair
}

TEST(SpiceTemplates8T, GeneratedDeckOmitsRetiredReplicaSaeScaffolding) {
    const std::string generated = generated_deck_8t();

    EXPECT_EQ(generated.find("buf_sram"), std::string::npos);
    EXPECT_EQ(generated.find("skewed_inv_sram"), std::string::npos);
    EXPECT_EQ(generated.find("replica_cell_8t"), std::string::npos);
    EXPECT_EQ(generated.find("sram_prech_ymux_8t_v1"), std::string::npos);
    EXPECT_EQ(generated.find("sram_prech_ymux_8t_v2"), std::string::npos);
    EXPECT_EQ(generated.find("wrasst_prech_ymux_x8_sram_8t"), std::string::npos);
}

TEST(SpiceTemplates, GeneratedDecksHaveNoCaseAliasedNets) {
    ScopedCurrentPath cwd(REPO_ROOT);
    MainCliOptions config;
    config.num_wls = 2;
    config.num_data_bits = 4;
    config.num_banks = 1;

    config.single_port = true;
    const auto single = case_aliased_nets(
        OpenFinRAM::SpiceGenerator(config).generate_spice_content());
    for (const auto& collision : single) {
        ADD_FAILURE() << "single-port " << collision;
    }

    config.single_port = false;
    const auto dual = case_aliased_nets(
        OpenFinRAM::SpiceGenerator(config).generate_spice_content());
    for (const auto& collision : dual) {
        ADD_FAILURE() << "dual-port " << collision;
    }
}

TEST(SpiceTemplates8T, GeneratedMacroHierarchyCarriesBothWritePorts) {
    const std::string generated = generated_deck_8t();

    const auto colgrp = subckt_body(generated, "colgrp_sram_8t");
    const auto stacked = subckt_body(generated, "stacked_colgrp_x4x2x1");
    ASSERT_FALSE(colgrp.empty());
    ASSERT_FALSE(stacked.empty());
    const auto colgrp_header = tokens(colgrp.front());
    const auto stacked_header = tokens(stacked.front());
    for (const std::string& pin : {"DB", "wrenaB", "wrenanB"}) {
        EXPECT_NE(std::find(colgrp_header.begin(), colgrp_header.end(), pin),
                  colgrp_header.end()) << pin;
    }
    for (const std::string& pin : {"DB[0]", "DB[1]", "wrenaB[0]", "wrenanB[0]"}) {
        EXPECT_NE(std::find(stacked_header.begin(), stacked_header.end(), pin),
                  stacked_header.end()) << pin;
    }
    EXPECT_EQ(subckt_instance_node_count(generated, "colgrp_sram_8t", "X0"),
              named_subckt_port_count(generated, "array_sram_8t"));
    EXPECT_EQ(subckt_instance_node_count(generated, "stacked_colgrp_x4x2x1", "X0_0"),
              named_subckt_port_count(generated, "colgrp_sram_8t"));

    const auto row = subckt_body(generated, "sram_cell_row_8t");
    size_t active_cells = 0;
    for (const auto& statement : row) {
        const auto fields = tokens(statement);
        ASSERT_FALSE(fields.empty());
        if (fields.front().front() != 'X') continue;
        EXPECT_EQ(fields.back(), "sram_cell_8t");
        ++active_cells;
    }
    // NUM_WL = 2: one unsplit array of four wordlines.  Physical end caps and
    // taps have no dummy devices.
    EXPECT_EQ(active_cells, 4U);
}

TEST(SpiceTemplates8T, TrackedBitcellNetlistMatchesTheTemplateContract) {
    const std::string cell = read_tech_spice("sram_cell_8t.sp");
    const std::string edges = read_tech_spice("sram_cell_8t_edges.sp");
    ASSERT_FALSE(cell.empty());
    ASSERT_FALSE(edges.empty());

    // tech/spice is the schematic the GDS topology check is documented
    // against, so it must not drift from the emitted deck.
    EXPECT_EQ(subckt_body(cell, "sram_cell_8t"),
              subckt_body(OpenFinRAM::SpiceTemplates::get_cell_8t(),
                          "sram_cell_8t"));
    EXPECT_EQ(subckt_body(edges, "dummy_cell_8t"),
              subckt_body(OpenFinRAM::SpiceTemplates::get_dummy_cell_8t(),
                          "dummy_cell_8t"));
}

TEST(SpiceTemplates8T, BitcellFamilyHasNoFloatingNets) {
    const std::vector<std::pair<std::string, std::string>> cells = {
        {OpenFinRAM::SpiceTemplates::get_cell_8t(), "sram_cell_8t"},
        {OpenFinRAM::SpiceTemplates::get_dummy_cell_8t(), "dummy_cell_8t"},
    };
    for (const auto& entry : cells) {
        const auto dangling = dangling_nets(entry.first, entry.second);
        std::string joined;
        for (const auto& net : dangling) {
            joined += (joined.empty() ? "" : ", ") + net;
        }
        EXPECT_TRUE(dangling.empty())
            << entry.second << " has floating nets: " << joined;
    }
}

// --bitcell 6t: the generated single-port macro's deck.
namespace {

std::string generated_deck_6t(unsigned banks = 1) {
    ScopedCurrentPath cwd(REPO_ROOT);
    MainCliOptions config;
    config.bitcell_6t = true;
    config.num_wls = 2;
    config.num_data_bits = 4;
    config.num_banks = banks;
    return OpenFinRAM::SpiceGenerator(config).generate_spice_content();
}

}  // namespace

TEST(SpiceTemplates6T, ColumnGroupIsTheArrayAndOneIoBlock) {
    const std::string deck = generated_deck_6t();
    const std::string colgrp = subckt_text(deck, "colgrp_sram_6t");
    ASSERT_FALSE(colgrp.empty());
    EXPECT_EQ(count_occurrences(colgrp, "iocol_sram_6t"), 1U);
    EXPECT_EQ(count_occurrences(colgrp, "array_sram_6t"), 1U);
    // No second port anywhere: no port-B IO, no 8T cell.
    EXPECT_EQ(deck.find("iocol_sram_8t"), std::string::npos);
    EXPECT_EQ(deck.find("sram_cell_8t"), std::string::npos);
    EXPECT_EQ(deck.find("WLB["), std::string::npos);

    const std::string wrapper = subckt_text(deck, "iocol_sram_6t");
    const std::string block = subckt_text(deck, "iocol_block_6t");
    ASSERT_FALSE(wrapper.empty());
    ASSERT_FALSE(block.empty());
    EXPECT_EQ(instance_node_count(colgrp, "XIO_A"), subckt_port_count(wrapper));
    EXPECT_EQ(instance_node_count(wrapper, "X_block"), subckt_port_count(block));
    const auto bindings = instance_bindings(colgrp, "XIO_A", wrapper);
    ASSERT_FALSE(bindings.empty());
    for (const auto& entry : bindings) {
        EXPECT_EQ(entry.second, entry.first) << "the wrapper's pins are the column group's nets";
    }
}

TEST(SpiceTemplates6T, RowsCarryTheirDummyAndCap) {
    const std::string deck = generated_deck_6t();  // NUM_WL 2 -> 4 wordlines
    const std::string row = subckt_text(deck, "sram_cell_row_6t");
    EXPECT_EQ(count_occurrences(row, "sram_cell_6t_122\n"), 4U);
    EXPECT_EQ(count_occurrences(row, "dummy_sram_6t122"), 1U);
    const std::string array = subckt_text(deck, "array_sram_6t");
    EXPECT_EQ(count_occurrences(array, "sram_cell_row_6t"), 4U);
    EXPECT_EQ(count_occurrences(array, "dummy_topbot_v1"), 2U);
    EXPECT_EQ(count_occurrences(array, "dummy_topbot_v2"), 2U);
}

TEST(SpiceTemplates6T, EveryBankHasADummyRowAtEachEndOfItsStack) {
    for (unsigned banks : {1U, 2U}) {
        const std::string deck = generated_deck_6t(banks);
        const std::string stack =
            subckt_text(deck, "stacked_colgrp_x4x2x" + std::to_string(banks));
        ASSERT_FALSE(stack.empty());
        EXPECT_EQ(count_occurrences(stack, "end_row_6t"), 2U * banks);
        EXPECT_EQ(count_occurrences(stack, "colgrp_sram_6t"), 2U * banks);
        // Each end row hangs a two-fin device on every wordline of the bank.
        const std::string end = subckt_text(deck, "end_row_6t");
        EXPECT_EQ(count_occurrences(end, "nfin=2\n"), 4U);
    }
}

TEST(SpiceTemplates6T, WordlineStripsArePortAAlone) {
    const std::string deck = generated_deck_6t();
    for (const char* half : {"lo", "hi"}) {
        const std::string pair = subckt_text(deck, std::string("wl_strips_") + half + "_c8_x1");
        ASSERT_FALSE(pair.empty()) << half;
        EXPECT_EQ(count_occurrences(pair, "X_a "), 1U);
        EXPECT_EQ(count_occurrences(pair, "X_b "), 0U);
    }
}

TEST(SpiceTemplates6T, DividedWordlinesSizeTheSlicesForASegment) {
    ScopedCurrentPath cwd(REPO_ROOT);
    MainCliOptions config;
    config.bitcell_6t = true;
    config.num_wls = 2;
    config.num_data_bits = 16;  // 8 a stack
    config.num_banks = 1;
    config.segment_bits = 2;    // 4 segments a stack, 8 cells a wordline
    EXPECT_EQ(config.wordline_segments(), 4U);
    const std::string deck = OpenFinRAM::SpiceGenerator(config).generate_spice_content();
    // One segment's tiles a subcircuit, with its two end rows.
    const std::string stack = subckt_text(deck, "stacked_colgrp_x4x2x1");
    ASSERT_FALSE(stack.empty());
    EXPECT_EQ(count_occurrences(stack, "colgrp_sram_6t"), 2U);
    EXPECT_EQ(count_occurrences(stack, "end_row_6t"), 2U);
    EXPECT_FALSE(subckt_text(deck, "wl_strip_c8_x1").empty());
}

TEST(SpiceTemplates6T, DeeperMuxesTakeTheirOwnIoBlock) {
    for (unsigned mux : {8U, 16U}) {
        ScopedCurrentPath cwd(REPO_ROOT);
        MainCliOptions config;
        config.bitcell_6t = true;
        config.num_wls = 2;
        config.num_data_bits = 2;
        config.num_banks = 2;
        config.num_rows_per_mux = mux;
        const std::string deck = OpenFinRAM::SpiceGenerator(config).generate_spice_content();
        const std::string io = "iocol_sram_6t_x" + std::to_string(mux);
        const std::string colgrp = subckt_text(deck, "colgrp_sram_6t");
        const std::string wrapper = subckt_text(deck, io);
        ASSERT_FALSE(wrapper.empty()) << io;
        EXPECT_EQ(count_occurrences(colgrp, io + "\n"), 1U);
        EXPECT_EQ(instance_node_count(colgrp, "XIO_A"), subckt_port_count(wrapper));
        EXPECT_EQ(count_occurrences(subckt_text(deck, "array_sram_6t"), "sram_cell_row_6t"), mux);
        // Every bank its own mux's selects, predecoded to 4 + mux/4 at 16:1.
        const int selects = mux >= 16 ? 4 + mux / 4 : mux;
        const std::string stack = subckt_text(deck, "stacked_colgrp_x4x1x2");
        EXPECT_NE(stack.find("yselA[" + std::to_string(2 * selects - 1) + "]"), std::string::npos);
        EXPECT_EQ(stack.find("yselA[" + std::to_string(2 * selects) + "]"), std::string::npos);
        // A cell per mux row along the wordline: one bit a stack, `mux` cells.
        EXPECT_FALSE(subckt_text(deck, "wl_strip_c" + std::to_string(mux) + "_x1").empty());
    }
}
