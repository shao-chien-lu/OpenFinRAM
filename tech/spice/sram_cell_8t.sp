* OpenFinRAM ASAP7 true-dual-port 8T bitcell reference netlist.
*
* Logical topology and port convention follow the Apache-2.0 OpenRAM cell:
* https://github.com/VLSIDA/sky130_fd_bd_sram/blob/fc63b12883b4bf458ee8c756ba64c37063e1ffb9/cells/openram_dp_cell/sky130_fd_bd_sram__openram_dp_cell.base.spice
*
* This is an independent ASAP7 implementation.  The parallel Sky130 latch
* fingers are represented by the two-fin ASAP7 devices used by OpenFinRAM.
* Pin map: WL0/BL0/BR0 -> WLA/BLA/BLAN; WL1/BL1/BR1 -> WLB/BLB/BLBN.

.SUBCKT sram_cell_8t WLA WLB BLA BLAN BLB BLBN VDD VSS
M0 Q  QB VSS VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M1 QB Q  VSS VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M2 Q  QB VDD VDD pmos_sram L=2e-08 W=2.7e-08 nfin=1
M3 QB Q  VDD VDD pmos_sram L=2e-08 W=2.7e-08 nfin=1
M4 Q  WLA BLA  VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M5 QB WLA BLAN VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M6 Q  WLB BLB  VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
M7 QB WLB BLBN VSS nmos_sram L=2e-08 W=5.4e-08 nfin=2
.ENDS sram_cell_8t
