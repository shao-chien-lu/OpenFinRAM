#include "lef_extractor.hpp"

#include <gdstk/gdstk.hpp>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#include "plog/Log.h"

namespace OpenFinRAM {
namespace {

constexpr uint16_t kBoundaryLayer = 100;
constexpr uint16_t kDrawingPurpose = 0;
constexpr uint16_t kPinPurpose = 251;

struct Rect {
    double x_min = 0;
    double y_min = 0;
    double x_max = 0;
    double y_max = 0;
};

struct PortShape {
    std::string layer;
    Rect rect;
};

struct MacroPin {
    std::string name;
    std::string direction;
    std::string use;
    std::vector<PortShape> shapes;
};

void free_polygon_array(gdstk::Array<gdstk::Polygon*>& polygons) {
    for (uint64_t i = 0; i < polygons.count; ++i) {
        polygons[i]->clear();
        gdstk::free_allocation(polygons[i]);
    }
    polygons.clear();
}

struct LayerGeometry {
    gdstk::Array<gdstk::Polygon*> pin_polygons = {};
    gdstk::Array<gdstk::Polygon*> drawing_polygons = {};

    ~LayerGeometry() {
        free_polygon_array(pin_polygons);
        free_polygon_array(drawing_polygons);
    }
};

std::string lowercase(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return value;
}

const char* metal_layer_name(uint16_t layer) {
    switch (layer) {
        case 19: return "M1";
        case 20: return "M2";
        case 30: return "M3";
        case 40: return "M4";
        case 50: return "M5";
        case 60: return "M6";
        case 70: return "M7";
        case 80: return "M8";
        case 90: return "M9";
        default: return nullptr;
    }
}

double minimum_metal_width(uint16_t layer) {
    if (layer == 19 || layer == 20 || layer == 30) return 0.018;
    if (layer == 40 || layer == 50) return 0.024;
    if (layer == 60 || layer == 70) return 0.032;
    return 0.040;
}

void extend_bounds(const Rect& rect, Rect& bounds, bool& valid) {
    if (!valid) {
        bounds = rect;
        valid = true;
        return;
    }
    bounds.x_min = std::min(bounds.x_min, rect.x_min);
    bounds.y_min = std::min(bounds.y_min, rect.y_min);
    bounds.x_max = std::max(bounds.x_max, rect.x_max);
    bounds.y_max = std::max(bounds.y_max, rect.y_max);
}

bool polygon_bounds(const gdstk::Polygon* polygon, Rect& rect) {
    gdstk::Vec2 min;
    gdstk::Vec2 max;
    polygon->bounding_box(min, max);
    if (!(min.x < max.x && min.y < max.y)) return false;
    rect = {min.x, min.y, max.x, max.y};
    return true;
}

bool find_macro_bounds(const gdstk::Cell* top, Rect& bounds, bool& used_boundary) {
    bool valid = false;
    const gdstk::Tag boundary_tag = gdstk::make_tag(kBoundaryLayer, kDrawingPurpose);

    // The final OpenFinRAM top cell has a direct BOUNDARY polygon.  Prefer it
    // so intentional fin/metal overhang does not enlarge the placement size.
    for (uint64_t i = 0; i < top->polygon_array.count; ++i) {
        gdstk::Polygon* polygon = top->polygon_array[i];
        if (polygon->tag != boundary_tag) continue;
        Rect rect;
        if (polygon_bounds(polygon, rect)) extend_bounds(rect, bounds, valid);
    }

    if (!valid) {
        gdstk::Array<gdstk::Polygon*> boundaries = {};
        top->get_polygons(true, true, -1, true, boundary_tag, boundaries);
        for (uint64_t i = 0; i < boundaries.count; ++i) {
            Rect rect;
            if (polygon_bounds(boundaries[i], rect)) extend_bounds(rect, bounds, valid);
        }
        free_polygon_array(boundaries);
    }

    used_boundary = valid;
    if (!valid) {
        gdstk::Vec2 min;
        gdstk::Vec2 max;
        top->bounding_box(min, max);
        if (min.x < max.x && min.y < max.y) {
            bounds = {min.x, min.y, max.x, max.y};
            valid = true;
        }
    }
    return valid;
}

bool clip_to_macro(Rect& rect, const Rect& macro) {
    rect.x_min = std::max(rect.x_min, macro.x_min);
    rect.y_min = std::max(rect.y_min, macro.y_min);
    rect.x_max = std::min(rect.x_max, macro.x_max);
    rect.y_max = std::min(rect.y_max, macro.y_max);
    return rect.x_min < rect.x_max && rect.y_min < rect.y_max;
}

bool find_containing_polygon(const gdstk::Array<gdstk::Polygon*>& polygons,
                             const gdstk::Vec2& point,
                             const Rect& macro,
                             Rect& result) {
    bool found = false;
    double best_area = std::numeric_limits<double>::infinity();
    for (uint64_t i = 0; i < polygons.count; ++i) {
        gdstk::Polygon* polygon = polygons[i];
        if (!polygon->contain(point)) continue;

        Rect candidate;
        if (!polygon_bounds(polygon, candidate) || !clip_to_macro(candidate, macro)) continue;
        const double area = (candidate.x_max - candidate.x_min) *
                            (candidate.y_max - candidate.y_min);
        if (area < best_area) {
            best_area = area;
            result = candidate;
            found = true;
        }
    }
    return found;
}

Rect fallback_pin_rect(const gdstk::Vec2& point, uint16_t layer, const Rect& macro) {
    const double width = minimum_metal_width(layer);
    Rect rect = {point.x - width / 2.0, point.y - width / 2.0,
                 point.x + width / 2.0, point.y + width / 2.0};

    // Shift, instead of merely clipping, a fallback rectangle at an edge so
    // it retains a legal minimum width whenever the macro is large enough.
    if (rect.x_min < macro.x_min) {
        rect.x_max += macro.x_min - rect.x_min;
        rect.x_min = macro.x_min;
    }
    if (rect.x_max > macro.x_max) {
        rect.x_min -= rect.x_max - macro.x_max;
        rect.x_max = macro.x_max;
    }
    if (rect.y_min < macro.y_min) {
        rect.y_max += macro.y_min - rect.y_min;
        rect.y_min = macro.y_min;
    }
    if (rect.y_max > macro.y_max) {
        rect.y_min -= rect.y_max - macro.y_max;
        rect.y_max = macro.y_max;
    }
    clip_to_macro(rect, macro);
    return rect;
}

void classify_pin(MacroPin& pin) {
    const std::string name = lowercase(pin.name);
    if (name == "vdd" || name == "vcc" || name == "vpwr") {
        pin.direction = "INOUT";
        pin.use = "POWER";
    } else if (name == "vss" || name == "gnd" || name == "vgnd") {
        pin.direction = "INOUT";
        pin.use = "GROUND";
    } else if (name == "q" || name.rfind("q[", 0) == 0 ||
               name == "dataout" || name.rfind("dataout[", 0) == 0) {
        pin.direction = "OUTPUT";
        pin.use = "SIGNAL";
    } else {
        pin.direction = "INPUT";
        pin.use = "SIGNAL";
    }
}

bool rect_touches_boundary(const Rect& rect, double width, double height) {
    constexpr double epsilon = 1e-6;
    return rect.x_min <= epsilon || rect.y_min <= epsilon ||
           rect.x_max >= width - epsilon || rect.y_max >= height - epsilon;
}

std::string build_lef(const std::string& cell_name,
                      const Rect& macro,
                      const std::vector<MacroPin>& pins,
                      bool used_boundary,
                      int highest_metal) {
    const double width = macro.x_max - macro.x_min;
    const double height = macro.y_max - macro.y_min;
    const double foreign_x = std::abs(macro.x_min) < 5e-10 ? 0.0 : -macro.x_min;
    const double foreign_y = std::abs(macro.y_min) < 5e-10 ? 0.0 : -macro.y_min;
    std::ostringstream out;
    out << std::fixed << std::setprecision(6);
    out << "# Generated directly from GDS by OpenFinRAM (gdstk)\n";
    if (!used_boundary) {
        out << "# WARNING: no GDS BOUNDARY geometry was found; SIZE uses the full GDS bounding box.\n";
    }
    out << "VERSION 5.8 ;\n";
    out << "BUSBITCHARS \"[]\" ;\n";
    out << "DIVIDERCHAR \"/\" ;\n\n";
    out << "MACRO " << cell_name << "\n";
    out << "  CLASS BLOCK ;\n";
    out << "  ORIGIN 0 0 ;\n";
    // FOREIGN locates the original GDS cell origin in the normalized LEF
    // coordinate system.  This preserves stream-out alignment when GDS has a
    // negative BOUNDARY origin (for example, the SRAM filler overhang).
    out << "  FOREIGN " << cell_name << " " << foreign_x << " " << foreign_y << " ;\n";
    out << "  SIZE " << width << " BY " << height << " ;\n";
    out << "  SYMMETRY X Y ;\n";

    for (const MacroPin& pin : pins) {
        out << "  PIN " << pin.name << "\n";
        out << "    DIRECTION " << pin.direction << " ;\n";
        out << "    USE " << pin.use << " ;\n";
        bool abutment = false;
        for (const PortShape& shape : pin.shapes) {
            if (rect_touches_boundary(shape.rect, width, height)) {
                abutment = true;
                break;
            }
        }
        if (abutment) out << "    SHAPE ABUTMENT ;\n";
        out << "    PORT\n";
        std::string current_layer;
        for (const PortShape& shape : pin.shapes) {
            if (shape.layer != current_layer) {
                current_layer = shape.layer;
                out << "      LAYER " << current_layer << " ;\n";
            }
            out << "        RECT " << shape.rect.x_min << " " << shape.rect.y_min << " "
                << shape.rect.x_max << " " << shape.rect.y_max << " ;\n";
        }
        out << "    END\n";
        out << "  END " << pin.name << "\n";
    }

    // Match the widely used FakeRAM abstraction convention: the hard macro
    // blocks routing across every layer used internally.  Explicit PIN shapes
    // remain available to the router even where they overlap this OBS box.
    out << "  OBS\n";
    for (int level = 1; level <= highest_metal; ++level) {
        out << "    LAYER M" << level << " ;\n";
        out << "      RECT 0 0 " << width << " " << height << " ;\n";
    }
    out << "  END\n";
    out << "END " << cell_name << "\n\n";
    out << "END LIBRARY\n";
    return out.str();
}

}  // namespace

bool export_lef(const std::string& project_root,
                const std::string& cell_name,
                const std::string& gds_path,
                std::string* output_path,
                std::string* error) {
    gdstk::ErrorCode error_code = gdstk::ErrorCode::NoError;
    gdstk::Library library = gdstk::read_gds(gds_path.c_str(), 0, 1e-3, nullptr, &error_code);
    if (error_code != gdstk::ErrorCode::NoError) {
        if (error) {
            *error = "Failed to read GDS '" + gds_path + "' (gdstk error " +
                     std::to_string(static_cast<int>(error_code)) + ")";
        }
        library.free_all();
        return false;
    }

    gdstk::Cell* top = library.get_cell(cell_name.c_str());
    if (top == nullptr) {
        if (error) *error = "GDS does not contain top cell '" + cell_name + "'";
        library.free_all();
        return false;
    }

    Rect macro;
    bool used_boundary = false;
    if (!find_macro_bounds(top, macro, used_boundary)) {
        if (error) *error = "Cannot determine macro bounds for '" + cell_name + "'";
        library.free_all();
        return false;
    }

    std::map<uint16_t, std::unique_ptr<LayerGeometry>> geometry;
    std::vector<MacroPin> pins;
    std::unordered_map<std::string, size_t> pin_indices;
    unsigned fallback_count = 0;

    // Interface labels are deliberately copied onto the final top cell by the
    // layout generator.  Restricting extraction to direct pin-purpose labels
    // avoids accidentally promoting internal hierarchy labels to macro pins.
    for (uint64_t i = 0; i < top->label_array.count; ++i) {
        const gdstk::Label* label = top->label_array[i];
        if (label->text == nullptr || label->text[0] == '\0') continue;
        const uint16_t layer = gdstk::get_layer(label->tag);
        const uint16_t purpose = gdstk::get_type(label->tag);
        const char* layer_name = metal_layer_name(layer);
        if (purpose != kPinPurpose || layer_name == nullptr) continue;

        auto geometry_it = geometry.find(layer);
        if (geometry_it == geometry.end()) {
            auto layer_geometry = std::make_unique<LayerGeometry>();
            top->get_polygons(true, true, -1, true,
                              gdstk::make_tag(layer, kPinPurpose),
                              layer_geometry->pin_polygons);
            top->get_polygons(true, true, -1, true,
                              gdstk::make_tag(layer, kDrawingPurpose),
                              layer_geometry->drawing_polygons);
            geometry_it = geometry.emplace(layer, std::move(layer_geometry)).first;
        }

        Rect pin_rect;
        bool found_geometry = find_containing_polygon(
            geometry_it->second->pin_polygons, label->origin, macro, pin_rect);
        if (!found_geometry) {
            found_geometry = find_containing_polygon(
                geometry_it->second->drawing_polygons, label->origin, macro, pin_rect);
        }
        if (!found_geometry) {
            pin_rect = fallback_pin_rect(label->origin, layer, macro);
            ++fallback_count;
            LOGW << "No metal polygon contains LEF pin label " << label->text
                 << " on " << layer_name << "; using a minimum-width rectangle";
        }

        // Normalize all geometry to the lower-left BOUNDARY corner.
        pin_rect.x_min -= macro.x_min;
        pin_rect.x_max -= macro.x_min;
        pin_rect.y_min -= macro.y_min;
        pin_rect.y_max -= macro.y_min;

        const std::string pin_name(label->text);
        auto pin_it = pin_indices.find(pin_name);
        if (pin_it == pin_indices.end()) {
            MacroPin pin;
            pin.name = pin_name;
            classify_pin(pin);
            pins.push_back(std::move(pin));
            pin_it = pin_indices.emplace(pin_name, pins.size() - 1).first;
        }
        pins[pin_it->second].shapes.push_back({layer_name, pin_rect});
    }

    if (pins.empty()) {
        if (error) {
            *error = "No direct metal pin labels (GDS datatype 251) found on top cell '" +
                     cell_name + "'";
        }
        library.free_all();
        return false;
    }

    // Preserve the legacy M1-M5 abstraction, but also reserve every upper
    // routing layer occupied by the new macro-level interconnect hierarchy.
    int highest_metal = 5;
    for (int level = 9; level > 5; --level) {
        bool occupied = false;
        for (uint16_t purpose : {kDrawingPurpose, kPinPurpose}) {
            gdstk::Array<gdstk::Polygon*> polygons = {};
            top->get_polygons(true, true, -1, true,
                gdstk::make_tag(static_cast<uint16_t>(level * 10), purpose), polygons);
            occupied = occupied || polygons.count > 0;
            free_polygon_array(polygons);
        }
        if (occupied) { highest_metal = level; break; }
    }

    const std::string lef_path = project_root + "/" + cell_name + ".lef";
    std::ofstream out(lef_path, std::ios::out | std::ios::trunc);
    if (!out) {
        if (error) *error = "Failed to open LEF for writing: " + lef_path;
        library.free_all();
        return false;
    }
    out << build_lef(cell_name, macro, pins, used_boundary, highest_metal);
    out.close();
    if (!out) {
        if (error) *error = "Failed while writing LEF: " + lef_path;
        library.free_all();
        return false;
    }

    if (output_path) *output_path = lef_path;
    LOGI << "Native LEF export wrote " << pins.size() << " pins to " << lef_path
         << " (SIZE " << (macro.x_max - macro.x_min) << " x "
         << (macro.y_max - macro.y_min) << " um, " << fallback_count
         << " fallback pin rectangles)";
    library.free_all();
    return true;
}

}  // namespace OpenFinRAM
