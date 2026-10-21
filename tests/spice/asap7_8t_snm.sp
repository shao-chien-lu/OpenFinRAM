* ASAP7 8T bitcell SNM butterfly (title replaced by the gate).
.param VDDVAL=0.7
.param WLAV=0.7
.param WLBV=0.0
.param SWEEPSTEP=0.005
VDD VDD 0 {VDDVAL}

* Wordline bias selects the measurement.  The gate rewrites these two .param
* lines per configuration:
*   WLAV=0     WLBV=0      hold  -- retention, no access device conducting
*   WLAV=VDD   WLBV=0      read A only
*   WLAV=0     WLBV=VDD    read B only
*   WLAV=VDD   WLBV=VDD    read, BOTH ports selected
* The last is the case docs/asap7_8t_bitcell.md declares legal, and the one a
* row half-selected on both ports also sees.
VWLA WLA 0 {WLAV}
VWLB WLB 0 {WLBV}

* Every bitline sits at the precharge rail.  During a read that is what the
* storage-low node is pulled towards through its access device, so this is the
* bias that sets read stability.
VHI HI 0 {VDDVAL}

* The sweep forces one storage node of each cell.  Forcing overrides the
* latch's feedback, which is what breaks the loop and lets a static transfer
* curve be measured at all.
VSW SW 0 0

* Two cells, forced on opposite nodes, sharing one sweep.
*
*   cell A: Q forced, QB observed  ->  QB = f(Q), first butterfly curve
*   cell B: QB forced, Q observed  ->  Q  = g(QB), reflected butterfly curve
*
* Both cells are otherwise identical and see identical bias, so the two curves
* are the same inverter measured in opposite directions -- which is exactly
* what a butterfly plot is.  Taking them from two instances rather than one
* avoids having to break and restore the loop within a single cell.
*
* Port order: WLA WLB BLA BLAN BLB BLBN VDD VSS Q QB
XCA WLA WLB HI HI HI HI VDD 0 SW    QAOUT sram_cell_8t_probe
XCB WLA WLB HI HI HI HI VDD 0 QBOUT SW    sram_cell_8t_probe

* The gate compares 5 mV and 2.5 mV sweeps at every bias/corner to check
* interpolation convergence; the finer sweep supplies the reported margin.
* V(SW) is printed explicitly: Xyce's .prn carries an Index column but not the
* sweep source's value, and the butterfly is plotted against it.
*
* QAOUT and QBOUT are expected to be identical -- the cell is symmetric, so
* forcing Q and forcing QB give the same response, and the two lobes are
* mirror images about y = x.  The post-processor asserts that rather than
* assuming it, which makes this a cell-symmetry check as well as an SNM one.
.dc VSW 0 {VDDVAL} {SWEEPSTEP}
.print dc V(SW) V(QAOUT) V(QBOUT)
.end
