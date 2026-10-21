#include "spice_templates.hpp"

namespace OpenFinRAM {

std::string SpiceTemplates::get_cell_6t() {
    return R"(.SUBCKT sram_cell_6t_122 WL BLN BL VDD VSS
M0 QB WL BLN VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M1 Q QB VSS VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M2 VSS Q QB VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M3 BL WL Q VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M4 Q QB VDD VDD pmos_sram L=2e-08 W=2.7e-08 nfin=1
M5 VDD Q QB VDD pmos_sram L=2e-08 W=2.7e-08 nfin=1
.ENDS)";
}

std::string SpiceTemplates::get_cell_8t() {
    return R"(.SUBCKT sram_cell_8t WLA WLB BLA BLAN BLB BLBN VDD VSS
M0 Q  QB VSS VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M1 QB Q  VSS VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M2 Q  QB VDD VDD pmos_sram L=2e-08 W=2.7e-08 nfin=1
M3 QB Q  VDD VDD pmos_sram L=2e-08 W=2.7e-08 nfin=1
M4 Q  WLA BLA  VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M5 QB WLA BLAN VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M6 Q  WLB BLB  VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M7 QB WLB BLBN VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
.ENDS)";
}

std::string SpiceTemplates::get_dummy_cell() {
    return R"(.SUBCKT dummy_sram_6t122 BLN VDD VSS
M0 QB VSS BLN VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2
M1 Q VDD VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2
M2 Q VDD VDD VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1
.ENDS)";
}

std::string SpiceTemplates::get_dummy_cell_8t() {
    return R"(.SUBCKT dummy_cell_8t BLA BLAN BLB BLBN VDD VSS
M0 VSS VDD VSS VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M1 VDD VSS VSS VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M2 VSS VDD VDD VDD pmos_sram L=2e-08 W=2.7e-08 nfin=1
M3 VDD VSS VDD VDD pmos_sram L=2e-08 W=2.7e-08 nfin=1
M4 VSS VSS BLA  VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M5 VDD VSS BLAN VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M6 VSS VSS BLB  VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M7 VDD VSS BLBN VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
.ENDS)";
}

std::string SpiceTemplates::get_dummy_topbot_v1() {
    return R"(.SUBCKT dummy_topbot_v1 BLN VDD VSS
M0 6 VSS BLN VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2
M1 VSS VDD VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2
M2 VDD VDD VDD VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1
.ENDS)";
}

std::string SpiceTemplates::get_dummy_topbot_v2() {
    return R"(.SUBCKT dummy_topbot_v2 BLN VDD VSS
M0 VSS VDD VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2
M1 6 VSS BLN VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2
M2 VDD VDD VDD VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1
.ENDS)";
}

std::string SpiceTemplates::get_prech_v1() {
    return R"(.SUBCKT sram_prech_ymux_6t112_v1 blprechn yseln ysel san sa bln bl VDD VSS
M0 bln ysel san VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M1 bl ysel sa VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M2 bln blprechn VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3
M3 bl blprechn VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3
M4 san yseln bln VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3
M5 sa yseln bl VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3
.ENDS)";
}

std::string SpiceTemplates::get_prech_v2() {
    return R"(.SUBCKT sram_prech_ymux_6t112_v2 blprechn yseln ysel san sa bln bl VDD VSS
M0 bl ysel sa VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M1 bln ysel san VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M2 bl blprechn VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3
M3 bln blprechn VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3
M4 sa yseln bl VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3
M5 san yseln bln VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3
.ENDS)";
}

std::string SpiceTemplates::get_prech_ymux() {
    return R"(.SUBCKT wrasst_prech_ymux_x4_sram_6t122_v2 bln[0] bln[1] bln[2] bln[3] bl[0] bl[1] bl[2] bl[3] yseln[0] yseln[1]
+ yseln[2] yseln[3] ysel[0] ysel[1] ysel[2] ysel[3] san sa blprechn VDD 
+ VSS
X0 blprechn yseln[0] ysel[0] san sa bln[0] bl[0] VDD VSS sram_prech_ymux_6t112_v1
X1 blprechn yseln[1] ysel[1] san sa bln[1] bl[1] VDD VSS sram_prech_ymux_6t112_v1
X2 blprechn yseln[2] ysel[2] san sa bln[2] bl[2] VDD VSS sram_prech_ymux_6t112_v2
X3 blprechn yseln[3] ysel[3] san sa bln[3] bl[3] VDD VSS sram_prech_ymux_6t112_v2
.ENDS)";
}

std::string SpiceTemplates::get_write_driver() {
    return R"(.SUBCKT write_driver_sram D wrena wrenan wdo wdon vdd vss
M0 vdd D  53  vdd pmos_rvt L=2e-08 W=8.1e-08 nfin=3
M1 vss D  53  vss nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M2 50  51  vdd vdd pmos_rvt L=2e-08 W=2.7e-08 nfin=1
M3 vdd 50  51  vdd pmos_rvt L=2e-08 W=2.7e-08 nfin=1
M4  50  51  vss vss nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M5  vss 50  51  vss nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M6 D   wrenan 50  vss nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M7 53  wrenan 51  vss nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M8  wdo  wrena  50   vss nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M9  51   wrena  wdon vss nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M10 wdo  wrenan 50   vdd pmos_rvt L=2e-08 W=8.1e-08 nfin=3
M11 51   wrenan wdon vdd pmos_rvt L=2e-08 W=8.1e-08 nfin=3
.ENDS)";
}

std::string SpiceTemplates::get_sense_amp() {
    return R"(.SUBCKT sense_amp_sram sa san SAE SAPRECHN qa qan vdd vss
M0 qan SAPRECHN vdd vdd pmos_rvt L=2e-08 W=1.08e-07 nfin=4
M1 59  SAPRECHN qan vdd pmos_rvt L=2e-08 W=1.08e-07 nfin=4
M2 qa  SAPRECHN 59  vdd pmos_rvt L=2e-08 W=1.08e-07 nfin=4
M3 vdd SAPRECHN qa  vdd pmos_rvt L=2e-08 W=1.08e-07 nfin=4
M4 vdd qa  qan vdd pmos_rvt L=2e-08 W=1.08e-07 nfin=4
M5 qa  qan vdd vdd pmos_rvt L=2e-08 W=1.08e-07 nfin=4
M6  52  sa  57  vss nmos_rvt L=2e-08 W=3.24e-07 nfin=12
M7  58  san 52  vss nmos_rvt L=2e-08 W=3.24e-07 nfin=12
M8  57  qa  qan vss nmos_rvt L=2e-08 W=3.24e-07 nfin=12
M9 qa  qan 58  vss nmos_rvt L=2e-08 W=3.24e-07 nfin=12
M10  vss SAE 52  vss nmos_rvt L=2e-08 W=3.24e-07 nfin=12
M11  52  SAE vss vss nmos_rvt L=2e-08 W=3.24e-07 nfin=12
M12  qan vss vss vss nmos_rvt L=2e-08 W=3.24e-07 nfin=12
M13 vss vss qa  vss nmos_rvt L=2e-08 W=3.24e-07 nfin=12
M14 qan vdd vdd vdd pmos_rvt L=2e-08 W=1.08e-07 nfin=4
M15 vdd vdd qa  vdd pmos_rvt L=2e-08 W=1.08e-07 nfin=4
.ENDS)";
}

std::string SpiceTemplates::get_or2() {
    return R"(.SUBCKT or2_sram A B VDD VSS Y
MM5 VSS net7 Y VSS nmos_rvt w=162.00n l=20n nfin=6
MM1 VSS B net7 VSS nmos_rvt w=54.0n l=20n nfin=2
MM2 VSS A net7 VSS nmos_rvt w=54.0n l=20n nfin=2
MM0 VDD net7 Y VDD pmos_rvt w=162.00n l=20n nfin=6
MM4 net15 B net7 VDD pmos_rvt w=81.0n l=20n nfin=3
MM3 VDD A net15 VDD pmos_rvt w=81.0n l=20n nfin=3
.ENDS)";
}

std::string SpiceTemplates::get_io_nand() {
    return R"(.SUBCKT io_nand_3f_6f A B Y VDD VSS
M0 VSS A 6 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M1 6 A VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M2 Y B 6 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M3 6 B Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M4 VDD A Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3
M5 Y B VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3
.ENDS)";
}

std::string SpiceTemplates::get_tbuf() {
    return R"(.SUBCKT TBUF_INV A ENB EN VDD VSS Y
MP1 n1 A VDD   VDD pmos_rvt L=2e-08 W=1296.00n nfin=48
MP2 Y   ENB  n1 VDD pmos_rvt L=2e-08 W=972.00n nfin=36
MN1 n2 A VSS   VSS nmos_rvt L=2e-08 W=1296.00n nfin=48
MN2 Y   EN  n2 VSS nmos_rvt L=2e-08 W=972.00n nfin=36
.ENDS)";
}

std::string SpiceTemplates::get_iocolgrp() {
    return R"(.SUBCKT iocolgrp_sram_6t122_v2 wrenan wrena SAE SAPRECHN oeb_out oe_out D Q bltn[0] bltn[1] bltn[2]
+ bltn[3] blt[0] blt[1] blt[2] blt[3] blbn[0] blbn[1] blbn[2] blbn[3] blb[0] 
+ blb[1] blb[2] blb[3] BLPRECHTN BLPRECHBN yseltn[0] yseltn[1] yseltn[2] yseltn[3] yselt[0]
+ yselt[1] yselt[2] yselt[3] yselbn[0] yselbn[1] yselbn[2] yselbn[3] yselb[0] yselb[1] yselb[2] 
+ yselb[3] vdd vss
XWD D wrena wrenan sa san vdd vss write_driver_sram
XSA sa san SAE SAPRECHN qa qan vdd vss sense_amp_sram
X42 bltn[0] bltn[1] bltn[2] bltn[3] blt[0] blt[1] blt[2] blt[3] yseltn[0] yseltn[1]
+ yseltn[2] yseltn[3] yselt[0] yselt[1] yselt[2] yselt[3] san sa BLPRECHTN vdd 
+ vss wrasst_prech_ymux_x4_sram_6t122_v2
X43 blbn[0] blbn[1] blbn[2] blbn[3] blb[0] blb[1] blb[2] blb[3] yselbn[0] yselbn[1]
+ yselbn[2] yselbn[3] yselb[0] yselb[1] yselb[2] yselb[3] san sa BLPRECHBN vdd 
+ vss wrasst_prech_ymux_x4_sram_6t122_v2
M19 vss 49 48 vss nmos_rvt L=2e-08 W=8.1e-08 nfin=3
M39 vdd 49 48 vdd pmos_rvt L=2e-08 W=8.1e-08 nfin=3
X44 49 qa 54 vdd vss io_nand_3f_6f
X45 54 qan 49 vdd vss io_nand_3f_6f
X46 48 oeb_out oe_out vdd vss Q TBUF_INV
.ENDS)";
}

std::string SpiceTemplates::get_ioprech_8t_a() {
    return R"(.SUBCKT ioprech_sram_8t_a
+ WRENAN_A WRENA_A SAE_A SAPRECHN_A OEB_OUT_A OE_OUT_A D_A Q_A
+ BLTN_A[0] BLTN_A[1] BLTN_A[2] BLTN_A[3]
+ BLT_A[0] BLT_A[1] BLT_A[2] BLT_A[3]
+ BLBN_A[0] BLBN_A[1] BLBN_A[2] BLBN_A[3]
+ BLB_A[0] BLB_A[1] BLB_A[2] BLB_A[3]
+ BLPRECHTN_A BLPRECHBN_A
+ YSELTN_A[0] YSELTN_A[1] YSELTN_A[2] YSELTN_A[3]
+ YSELT_A[0] YSELT_A[1] YSELT_A[2] YSELT_A[3]
+ YSELBN_A[0] YSELBN_A[1] YSELBN_A[2] YSELBN_A[3]
+ YSELB_A[0] YSELB_A[1] YSELB_A[2] YSELB_A[3]
+ VDD VSS
XIO_A WRENAN_A WRENA_A SAE_A SAPRECHN_A OEB_OUT_A OE_OUT_A D_A Q_A
+ BLTN_A[0] BLTN_A[1] BLTN_A[2] BLTN_A[3]
+ BLT_A[0] BLT_A[1] BLT_A[2] BLT_A[3]
+ BLBN_A[0] BLBN_A[1] BLBN_A[2] BLBN_A[3]
+ BLB_A[0] BLB_A[1] BLB_A[2] BLB_A[3]
+ BLPRECHTN_A BLPRECHBN_A
+ YSELTN_A[0] YSELTN_A[1] YSELTN_A[2] YSELTN_A[3]
+ YSELT_A[0] YSELT_A[1] YSELT_A[2] YSELT_A[3]
+ YSELBN_A[0] YSELBN_A[1] YSELBN_A[2] YSELBN_A[3]
+ YSELB_A[0] YSELB_A[1] YSELB_A[2] YSELB_A[3]
+ VDD VSS iocolgrp_sram_6t122_v2
.ENDS)";
}

std::string SpiceTemplates::get_ioprech_8t_b() {
    return R"(.SUBCKT ioprech_sram_8t_b
+ WRENAN_B WRENA_B SAE_B SAPRECHN_B OEB_OUT_B OE_OUT_B D_B Q_B
+ BLTN_B[0] BLTN_B[1] BLTN_B[2] BLTN_B[3]
+ BLT_B[0] BLT_B[1] BLT_B[2] BLT_B[3]
+ BLBN_B[0] BLBN_B[1] BLBN_B[2] BLBN_B[3]
+ BLB_B[0] BLB_B[1] BLB_B[2] BLB_B[3]
+ BLPRECHTN_B BLPRECHBN_B
+ YSELTN_B[0] YSELTN_B[1] YSELTN_B[2] YSELTN_B[3]
+ YSELT_B[0] YSELT_B[1] YSELT_B[2] YSELT_B[3]
+ YSELBN_B[0] YSELBN_B[1] YSELBN_B[2] YSELBN_B[3]
+ YSELB_B[0] YSELB_B[1] YSELB_B[2] YSELB_B[3]
+ VDD VSS
XIO_B WRENAN_B WRENA_B SAE_B SAPRECHN_B OEB_OUT_B OE_OUT_B D_B Q_B
+ BLTN_B[0] BLTN_B[1] BLTN_B[2] BLTN_B[3]
+ BLT_B[0] BLT_B[1] BLT_B[2] BLT_B[3]
+ BLBN_B[0] BLBN_B[1] BLBN_B[2] BLBN_B[3]
+ BLB_B[0] BLB_B[1] BLB_B[2] BLB_B[3]
+ BLPRECHTN_B BLPRECHBN_B
+ YSELTN_B[0] YSELTN_B[1] YSELTN_B[2] YSELTN_B[3]
+ YSELT_B[0] YSELT_B[1] YSELT_B[2] YSELT_B[3]
+ YSELBN_B[0] YSELBN_B[1] YSELBN_B[2] YSELBN_B[3]
+ YSELB_B[0] YSELB_B[1] YSELB_B[2] YSELB_B[3]
+ VDD VSS iocolgrp_sram_6t122_v2
.ENDS)";
}

// Both wrappers side by side with both faces wired out.  The emitted macro no
// longer contains this: each port's IO stands at its own end of the bitlines
// and SpiceGenerator::generate_colgrp_8t instantiates the wrappers directly,
// idling the face turned away from the array.  It stays as the fixture the
// device-level IO regressions drive (tests/spice/asap7_8t_ioprech_write.sp,
// _isolation.sp), which exercise both faces of both wrappers.
std::string SpiceTemplates::get_iocolgrp_8t() {
    // The wrappers expose SAE and SAPRECHN separately.  The existing dual-port
    // controller has one sense phase per port, whose low/high levels already
    // implement precharge/evaluate, so the composite intentionally maps that
    // phase to both pins.  Keep the split wrapper contract for a future
    // independently timed sense-precharge signal.
    return R"(.SUBCKT iocolgrp_sram_8t
+ wrena_A wrenan_A wrena_B wrenan_B
+ oeb_out_A oe_out_A DA QA
+ oeb_out_B oe_out_B DB QB
+ blt_A[0]  blt_A[1]  blt_A[2]  blt_A[3]
+ bltn_A[0] bltn_A[1] bltn_A[2] bltn_A[3]
+ blb_A[0]  blb_A[1]  blb_A[2]  blb_A[3]
+ blbn_A[0] blbn_A[1] blbn_A[2] blbn_A[3]
+ blt_B[0]  blt_B[1]  blt_B[2]  blt_B[3]
+ bltn_B[0] bltn_B[1] bltn_B[2] bltn_B[3]
+ blb_B[0]  blb_B[1]  blb_B[2]  blb_B[3]
+ blbn_B[0] blbn_B[1] blbn_B[2] blbn_B[3]
+ blprechtn_A blprechbn_A blprechtn_B blprechbn_B
+ yseltn_A[0] yseltn_A[1] yseltn_A[2] yseltn_A[3]
+ yselt_A[0]  yselt_A[1]  yselt_A[2]  yselt_A[3]
+ yselbn_A[0] yselbn_A[1] yselbn_A[2] yselbn_A[3]
+ yselb_A[0]  yselb_A[1]  yselb_A[2]  yselb_A[3]
+ yseltn_B[0] yseltn_B[1] yseltn_B[2] yseltn_B[3]
+ yselt_B[0]  yselt_B[1]  yselt_B[2]  yselt_B[3]
+ yselbn_B[0] yselbn_B[1] yselbn_B[2] yselbn_B[3]
+ yselb_B[0]  yselb_B[1]  yselb_B[2]  yselb_B[3]
+ sae_A sae_B
+ vdd vss
XIO_A wrenan_A wrena_A sae_A sae_A oeb_out_A oe_out_A DA QA
+ bltn_A[0] bltn_A[1] bltn_A[2] bltn_A[3]
+ blt_A[0]  blt_A[1]  blt_A[2]  blt_A[3]
+ blbn_A[0] blbn_A[1] blbn_A[2] blbn_A[3]
+ blb_A[0]  blb_A[1]  blb_A[2]  blb_A[3]
+ blprechtn_A blprechbn_A
+ yseltn_A[0] yseltn_A[1] yseltn_A[2] yseltn_A[3]
+ yselt_A[0]  yselt_A[1]  yselt_A[2]  yselt_A[3]
+ yselbn_A[0] yselbn_A[1] yselbn_A[2] yselbn_A[3]
+ yselb_A[0]  yselb_A[1]  yselb_A[2]  yselb_A[3]
+ vdd vss ioprech_sram_8t_a
XIO_B wrenan_B wrena_B sae_B sae_B oeb_out_B oe_out_B DB QB
+ bltn_B[0] bltn_B[1] bltn_B[2] bltn_B[3]
+ blt_B[0]  blt_B[1]  blt_B[2]  blt_B[3]
+ blbn_B[0] blbn_B[1] blbn_B[2] blbn_B[3]
+ blb_B[0]  blb_B[1]  blb_B[2]  blb_B[3]
+ blprechtn_B blprechbn_B
+ yseltn_B[0] yseltn_B[1] yseltn_B[2] yseltn_B[3]
+ yselt_B[0]  yselt_B[1]  yselt_B[2]  yselt_B[3]
+ yselbn_B[0] yselbn_B[1] yselbn_B[2] yselbn_B[3]
+ yselb_B[0]  yselb_B[1]  yselb_B[2]  yselb_B[3]
+ vdd vss ioprech_sram_8t_b
.ENDS)";
}

} // namespace OpenFinRAM
