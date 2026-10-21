#ifndef OPENFINRAM_OPENROAD_TCL_GENERATOR_HPP_
#define OPENFINRAM_OPENROAD_TCL_GENERATOR_HPP_

#include <string>
#include <vector>
#include <map>

namespace OpenFinRAM {

struct PinInfo2 {
    std::string name;
    double x;
};

// Two-port: the wordline driver strips taken into the controller's band
// (compile_asap7_2rw.py --plan-band).  The die is the band between the two
// stacks of column tiles; the strips sit at its bottom and top edges, as
// fixed physical instances during place-and-route, and its rows stop `inset`
// short of both edges.
struct BandPlan {
    bool enabled = false;
    std::string dir;        // plan.txt, band.tcl, strips.lef; die.txt is written back
    double width = 0.0;     // the band's width, um
    double reserved = 0.0;  // core area the strips and their halos take, um^2
    double inset = 0.0;
    double halo_x = 0.0;
    double halo_y = 0.0;
};

// Reads <dir>/plan.txt; false if it is missing or incomplete.
bool read_band_plan(const std::string& dir, BandPlan& plan);

struct QoRReport2 {
    double cell_area = 0.0;
    bool valid = false;
};

class OpenRoadTclGenerator {
public:
    OpenRoadTclGenerator();
    ~OpenRoadTclGenerator() = default;

    void set_design_name(const std::string& n) { design_name_ = n; }
    void set_site_name(const std::string& n) { site_name_ = n; }
    void set_site_height(double height);
    // Cap on cell area over core area when the floorplan height is derived.
    void set_max_utilization(double utilization);
    void set_bitcell_width(double width);
    void set_cpu_count(int local_cpu, int remote_cpu = 0);
    void set_band(const BandPlan& band) { band_ = band; }
    // The generated single-port macro's controller: port A's inputs only.
    void set_one_port(bool one_port) { one_port_ = one_port; }
    // A floor on the derived die height (um): room on a side edge for a
    // port's pins when the die is wide and its logic small.
    void set_min_height(double height) { min_height_ = height; }
    // Output ports whose buffer is sized here, not by repair_design: port
    // prefix -> cell.  repair_design leaves a 170 fF array-wide port on a
    // BUFx8 (OpenROAD does not grow a port's own buffer past its slew).
    void set_port_drivers(std::map<std::string, std::string> drivers) { port_drivers_ = std::move(drivers); }
    // The band's die height for `width`: rows for the cells at the
    // utilization cap plus the strips' keep-outs, and the two insets.
    double band_die_height(double width) const;

    bool parse_qor_report(const std::string& qor_file);
    const QoRReport2& get_qor_report() const { return qor_; }

    double calculate_floorplan_height(double width) const;
    double align_to_site_height(double h) const;
    std::string generate_floorplan_command(double& w, double& h) const;

    // main TCL for OpenROAD flow (replaces Innovus run.tcl)
    // single_port=true keeps SP checks (sdel, 108 INVx1). false for DP 8T.
    bool generate_run_tcl(double width, double height,
                          const std::string& output_file,
                          int num_wlt, int num_wlb, int num_ysel,
                          int addr_width, int num_mux,
                          bool spice_only, double col_width,
                          const std::string& platform_path,
                          const std::string& tech_root,
                          bool single_port = true) const;

    bool run_openroad(const std::string& tcl_file,
                      const std::string& work_dir,
                      const std::string& log_file = "openroad.log",
                      const std::string& openroad_bin = "openroad") const;

    bool stream_def_to_gds(const std::string& def_file,
                           const std::string& tech_lef,
                           const std::string& cell_lef,
                           const std::string& macro_gds,
                           const std::string& output_gds,
                           const std::string& script_path = "") const;

    bool run_v2lvs(const std::string& work_dir,
                   const std::string& verilog_file = "netlist_for_lvs.v",
                   const std::string& spice_file = "netlist_for_lvs.sp",
                   const std::string& cdl_file = "") const;

    bool post_process_netlist(const std::string& work_dir,
                              const std::string& spice_file,
                              const std::string& cdl_file);

private:
    std::string design_name_ = "ctrl_decode";
    std::string site_name_ = "asap7sc7p5t";
    double site_height_ = 0.27;
    double max_utilization_ = 0.40;
    double bitcell_width_ = 0.108;
    int local_cpu_ = 8;
    QoRReport2 qor_;
    BandPlan band_;
    bool one_port_ = false;
    double min_height_ = 0.0;
    std::map<std::string, std::string> port_drivers_;

    // helpers
    bool file_exists(const std::string& p) const;
    std::vector<std::string> read_file(const std::string& p) const;
    bool write_file(const std::string& p, const std::vector<std::string>& lines) const;
    std::vector<std::string> merge_continuation(const std::vector<std::string>& lines) const;
    std::vector<std::string> add_power_to_subckt(const std::vector<std::string>& lines) const;
    bool parse_cdl(const std::string& cdl_path);
    void parse_subckt_from_lines(const std::vector<std::string>& lines);
    std::vector<std::string> expand_pins(const std::vector<std::string>& lines);
    std::vector<std::string> process_connect(const std::vector<std::string>& lines) const;

    std::map<std::string, std::vector<std::string>> subckt_dict_;
};

} // namespace OpenFinRAM

#endif // OPENFINRAM_OPENROAD_TCL_GENERATOR_HPP_
