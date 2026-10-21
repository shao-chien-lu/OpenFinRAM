* OpenFinRAM ASAP7 8T dummy and edge-cell reference netlists.
*
* Cell roles follow the Apache-2.0 OpenRAM dual-port cell family at:
* https://github.com/VLSIDA/sky130_fd_bd_sram/tree/fc63b12883b4bf458ee8c756ba64c37063e1ffb9/cells
*
* The cap cells are electrically empty physical boundary cells.  Their ports
* name pass-through/terminating conductors in GDS, matching OpenRAM's cap-row
* and cap-column convention.

.SUBCKT dummy_cell_8t BLA BLAN BLB BLBN VDD VSS
M0 VSS VDD VSS VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M1 VDD VSS VSS VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M2 VSS VDD VDD VDD pmos_sram L=2e-08 W=2.7e-08 nfin=1
M3 VDD VSS VDD VDD pmos_sram L=2e-08 W=2.7e-08 nfin=1
M4 VSS VSS BLA  VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M5 VDD VSS BLAN VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M6 VSS VSS BLB  VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M7 VDD VSS BLBN VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
.ENDS dummy_cell_8t

.SUBCKT sram_cell_8t_col_cap BLA BLAN BLB BLBN VDD VSS
.ENDS sram_cell_8t_col_cap

.SUBCKT sram_cell_8t_row_cap WLA WLB VSS
.ENDS sram_cell_8t_row_cap

.SUBCKT sram_cell_8t_corner VDD VSS
.ENDS sram_cell_8t_corner

* Explicit physical orientations: _lr reverses X, _v2 reverses Y.
* Geometry and pins are materialized about the 8T placement boundary.
.SUBCKT sram_cell_8t_corner_lr VDD VSS
.ENDS sram_cell_8t_corner_lr
.SUBCKT sram_cell_8t_corner_v2 VDD VSS
.ENDS sram_cell_8t_corner_v2
.SUBCKT sram_cell_8t_corner_v2_lr VDD VSS
.ENDS sram_cell_8t_corner_v2_lr

.SUBCKT dummy_vertical_8t WLA WLB VSS
.ENDS dummy_vertical_8t
.SUBCKT dummy_vertical_8t_lr WLA WLB VSS
.ENDS dummy_vertical_8t_lr
.SUBCKT dummy_vertical_8t_v2 WLA WLB VSS
.ENDS dummy_vertical_8t_v2
.SUBCKT dummy_vertical_8t_v2_lr WLA WLB VSS
.ENDS dummy_vertical_8t_v2_lr

.SUBCKT dummy_topbot_8t_v1 BLA BLAN BLB BLBN VDD VSS
.ENDS dummy_topbot_8t_v1
.SUBCKT dummy_topbot_8t_v1_lr BLA BLAN BLB BLBN VDD VSS
.ENDS dummy_topbot_8t_v1_lr
.SUBCKT dummy_topbot_8t_v2 BLA BLAN BLB BLBN VDD VSS
.ENDS dummy_topbot_8t_v2
.SUBCKT dummy_topbot_8t_v2_lr BLA BLAN BLB BLBN VDD VSS
.ENDS dummy_topbot_8t_v2_lr

* Blank fillers contain only FIN/poly and the placement boundary.
.SUBCKT FILLER_BLANK_8t
.ENDS FILLER_BLANK_8t
.SUBCKT FILLER_cgedge_8t
.ENDS FILLER_cgedge_8t
