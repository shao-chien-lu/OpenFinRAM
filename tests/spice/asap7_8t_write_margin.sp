* ASAP7 8T bitcell write-margin regression (title replaced by the gate).
.param VDDVAL=0.7
VDD VDD 0 {VDDVAL}

* Bitline write margin, measured quasi-statically.  The cell starts holding
* Q=VDD, and the bitline on the Q side is ramped down while its complement is
* held at the precharge rail.  The trip point is the bitline voltage at which
* the latch gives up, so the margin is VDDVAL minus that: a cell that flips
* while the bitline is still high has plenty of margin, one that needs the
* bitline near ground has almost none.
*
* The ramp is 2 ns for a 20 ps simulation step -- slow enough that the latch
* sees a sequence of settled operating points rather than a transient edge, so
* the number is a static margin and not a slew-dependent one.
VRAMP RAMP 0 PWL(0 {VDDVAL} 0.20n {VDDVAL} 2.20n 0 3n 0)

* Both wordlines rise before the ramp starts, so the access devices are fully
* on for the whole sweep and the measured trip is not gated by WL slew.
VWL WL 0 PWL(0 0 0.05n 0 0.07n {VDDVAL} 3n {VDDVAL})
VOFF OFF 0 0
VHI  HI  0 {VDDVAL}

* Two identical cells, written through opposite ports from the SAME ramp.
* Port A and port B are claimed symmetric by the 2RW macro contract, so their
* trip points must agree; driving both from one source removes any stimulus
* difference as an explanation if they do not.
*
* Port order: WLA WLB BLA BLAN BLB BLBN VDD VSS Q QB
* The idle port's bitlines sit at the precharge rail with its wordline low,
* which is the real idle bias rather than a floating one.
XCA WL  OFF RAMP HI   HI   HI  VDD 0 QA  QAN  sram_cell_8t_probe
XCB OFF WL  HI   HI   RAMP HI  VDD 0 QBS QBSN sram_cell_8t_probe

.ic V(QA)={VDDVAL} V(QAN)=0 V(QBS)={VDDVAL} V(QBSN)=0

.tran 1p 3n uic

* The bitline voltage at which each cell flips.  CROSS=1 takes the first
* crossing, which is the write; without it a cell that rings would report a
* later recrossing.
.measure tran WM_A_TRIP FIND v(RAMP) WHEN v(QA)=v(QAN) CROSS=1
.measure tran WM_B_TRIP FIND v(RAMP) WHEN v(QBS)=v(QBSN) CROSS=1

* Final state, as a guard: a cell that never flipped reports no trip above,
* but a cell that flipped and flipped back would still report one.
.measure tran QA_FINAL  find V(QA)  at=2.90n
.measure tran QAN_FINAL find V(QAN) at=2.90n
.measure tran QB_FINAL  find V(QBS) at=2.90n
.measure tran QBN_FINAL find V(QBSN) at=2.90n
.end
