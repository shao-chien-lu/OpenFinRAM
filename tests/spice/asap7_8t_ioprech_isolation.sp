* ASAP7 8T shared-cell, two-port isolation regression.
* The first operation writes different cells concurrently.  The following reads
* prove independent-address operation, then legal same-address read/read access.
.param VDDVAL=0.7
VDD VDD 0 {VDDVAL}
VDA DA 0 {VDDVAL}
VDB DB 0 0
VOEB OEB 0 0
VOE OE 0 {VDDVAL}

* Precharge is active low.  Release it during each write/read window.
VPRECH PRECH 0 PWL(0 0
+ 0.50n 0 0.52n {VDDVAL} 1.42n {VDDVAL} 1.44n 0
+ 1.90n 0 1.92n {VDDVAL} 3.12n {VDDVAL} 3.14n 0
+ 3.60n 0 3.62n {VDDVAL} 5.12n {VDDVAL} 5.14n 0
+ 5.60n 0 5.62n {VDDVAL} 7.12n {VDDVAL} 7.14n 0 7.40n 0)

* Both write ports are enabled only for the initial, different-address write.
VWRENA_A WRENA_A 0 PWL(0 0 0.68n 0 0.70n {VDDVAL}
+ 1.25n {VDDVAL} 1.27n 0 7.40n 0)
VWRENAN_A WRENAN_A 0 PWL(0 {VDDVAL} 0.68n {VDDVAL} 0.70n 0
+ 1.25n 0 1.27n {VDDVAL} 7.40n {VDDVAL})
VWRENA_B WRENA_B 0 PWL(0 0 0.68n 0 0.70n {VDDVAL}
+ 1.25n {VDDVAL} 1.27n 0 7.40n 0)
VWRENAN_B WRENAN_B 0 PWL(0 {VDDVAL} 0.68n {VDDVAL} 0.70n 0
+ 1.25n 0 1.27n {VDDVAL} 7.40n {VDDVAL})

* A selects column 0 for the first three operations and column 1 for the last.
VAY0 AY0 0 PWL(0 0 0.58n 0 0.60n {VDDVAL} 1.34n {VDDVAL} 1.36n 0
+ 1.98n 0 2.00n {VDDVAL} 3.04n {VDDVAL} 3.06n 0
+ 3.68n 0 3.70n {VDDVAL} 5.04n {VDDVAL} 5.06n 0 7.40n 0)
VAY0N AY0N 0 PWL(0 {VDDVAL} 0.58n {VDDVAL} 0.60n 0 1.34n 0
+ 1.36n {VDDVAL} 1.98n {VDDVAL} 2.00n 0 3.04n 0
+ 3.06n {VDDVAL} 3.68n {VDDVAL} 3.70n 0 5.04n 0
+ 5.06n {VDDVAL} 7.40n {VDDVAL})
VAY1 AY1 0 PWL(0 0 5.68n 0 5.70n {VDDVAL} 7.04n {VDDVAL}
+ 7.06n 0 7.40n 0)
VAY1N AY1N 0 PWL(0 {VDDVAL} 5.68n {VDDVAL} 5.70n 0
+ 7.04n 0 7.06n {VDDVAL} 7.40n {VDDVAL})

* B selects column 1 for the independent-address operations, column 0 for the
* same-address-one read, and column 1 again for the same-address-zero read.
VBY1 BY1 0 PWL(0 0 0.58n 0 0.60n {VDDVAL} 1.34n {VDDVAL} 1.36n 0
+ 1.98n 0 2.00n {VDDVAL} 3.04n {VDDVAL} 3.06n 0
+ 5.68n 0 5.70n {VDDVAL} 7.04n {VDDVAL} 7.06n 0 7.40n 0)
VBY1N BY1N 0 PWL(0 {VDDVAL} 0.58n {VDDVAL} 0.60n 0 1.34n 0
+ 1.36n {VDDVAL} 1.98n {VDDVAL} 2.00n 0 3.04n 0
+ 3.06n {VDDVAL} 5.68n {VDDVAL} 5.70n 0 7.04n 0
+ 7.06n {VDDVAL} 7.40n {VDDVAL})
VBY0 BY0 0 PWL(0 0 3.68n 0 3.70n {VDDVAL} 5.04n {VDDVAL}
+ 5.06n 0 7.40n 0)
VBY0N BY0N 0 PWL(0 {VDDVAL} 3.68n {VDDVAL} 3.70n 0
+ 5.04n 0 5.06n {VDDVAL} 7.40n {VDDVAL})

* Real cell wordlines.  The write establishes cell 0 = 1 and cell 1 = 0.
VWLA0 WLA0 0 PWL(0 0 0.76n 0 0.78n {VDDVAL} 1.30n {VDDVAL} 1.32n 0
+ 2.08n 0 2.10n {VDDVAL} 2.98n {VDDVAL} 3.00n 0
+ 3.78n 0 3.80n {VDDVAL} 4.98n {VDDVAL} 5.00n 0 7.40n 0)
VWLB1 WLB1 0 PWL(0 0 0.76n 0 0.78n {VDDVAL} 1.30n {VDDVAL} 1.32n 0
+ 2.08n 0 2.10n {VDDVAL} 2.98n {VDDVAL} 3.00n 0
+ 5.78n 0 5.80n {VDDVAL} 6.98n {VDDVAL} 7.00n 0 7.40n 0)
VWLB0 WLB0 0 PWL(0 0 3.78n 0 3.80n {VDDVAL} 4.98n {VDDVAL}
+ 5.00n 0 7.40n 0)
VWLA1 WLA1 0 PWL(0 0 5.78n 0 5.80n {VDDVAL} 6.98n {VDDVAL}
+ 7.00n 0 7.40n 0)

* The StrongARMs evaluate only after the bitlines have developed differential.
VSAE SAE 0 PWL(0 0 2.42n 0 2.44n {VDDVAL} 2.90n {VDDVAL} 2.92n 0
+ 4.42n 0 4.44n {VDDVAL} 4.90n {VDDVAL} 4.92n 0
+ 6.42n 0 6.44n {VDDVAL} 6.90n {VDDVAL} 6.92n 0 7.40n 0)
VOFFN OFFN 0 {VDDVAL}
VOFF OFF 0 0

.SUBCKT bitload4 N0 N1 N2 N3
C0 N0 0 10f
C1 N1 0 10f
C2 N2 0 10f
C3 N3 0 10f
R0 N0 0 1T
R1 N1 0 1T
R2 N2 0 1T
R3 N3 0 1T
.ENDS bitload4

XIO WRENA_A WRENAN_A WRENA_B WRENAN_B
+ OEB OE DA QA OEB OE DB QB
+ AT0 AT1 AT2 AT3 ATN0 ATN1 ATN2 ATN3
+ AB0 AB1 AB2 AB3 ABN0 ABN1 ABN2 ABN3
+ BT0 BT1 BT2 BT3 BTN0 BTN1 BTN2 BTN3
+ BB0 BB1 BB2 BB3 BBN0 BBN1 BBN2 BBN3
+ PRECH PRECH PRECH PRECH
+ AY0N AY1N OFFN OFFN AY0 AY1 OFF OFF
+ OFFN OFFN OFFN OFFN OFF OFF OFF OFF
+ BY0N BY1N OFFN OFFN BY0 BY1 OFF OFF
+ OFFN OFFN OFFN OFFN OFF OFF OFF OFF
+ SAE SAE VDD 0 iocolgrp_sram_8t

* Two actual 8T cells share the composite's top bitlines.  The ports first
* access separate cells, then jointly read cell 0 and cell 1 in turn.
XCELL0 WLA0 WLB0 AT0 ATN0 BT0 BTN0 VDD 0 sram_cell_8t
XCELL1 WLA1 WLB1 AT1 ATN1 BT1 BTN1 VDD 0 sram_cell_8t

XATN ATN0 ATN1 ATN2 ATN3 bitload4
XAT AT0 AT1 AT2 AT3 bitload4
XABN ABN0 ABN1 ABN2 ABN3 bitload4
XAB AB0 AB1 AB2 AB3 bitload4
XBTN BTN0 BTN1 BTN2 BTN3 bitload4
XBT BT0 BT1 BT2 BT3 bitload4
XBBN BBN0 BBN1 BBN2 BBN3 bitload4
XBB BB0 BB1 BB2 BB3 bitload4

.ic V(ATN0)=0 V(AT0)=0 V(ATN1)=0 V(AT1)=0
+ V(BTN0)=0 V(BT0)=0 V(BTN1)=0 V(BT1)=0
.tran 1p 7.40n uic

* Different-address simultaneous read: A reads cell 0 = 1; B reads cell 1 = 0.
.measure tran A_DIFF_NEG find V(ATN0) at=2.38n
.measure tran A_DIFF_POS find V(AT0) at=2.38n
.measure tran B_DIFF_NEG find V(BTN1) at=2.38n
.measure tran B_DIFF_POS find V(BT1) at=2.38n
.measure tran QA_DIFF find V(QA) at=2.80n
.measure tran QB_DIFF find V(QB) at=2.80n

* Legal same-address read/read: both access pairs read cell 0 = 1.
.measure tran A_SAME_NEG find V(ATN0) at=4.38n
.measure tran A_SAME_POS find V(AT0) at=4.38n
.measure tran B_SAME_NEG find V(BTN0) at=4.38n
.measure tran B_SAME_POS find V(BT0) at=4.38n
.measure tran QA_SAME find V(QA) at=4.80n
.measure tran QB_SAME find V(QB) at=4.80n

* The complementary polarity: both ports read cell 1 = 0.
.measure tran A_SAME_ZERO_NEG find V(ATN1) at=6.38n
.measure tran A_SAME_ZERO_POS find V(AT1) at=6.38n
.measure tran B_SAME_ZERO_NEG find V(BTN1) at=6.38n
.measure tran B_SAME_ZERO_POS find V(BT1) at=6.38n
.measure tran QA_SAME_ZERO find V(QA) at=6.80n
.measure tran QB_SAME_ZERO find V(QB) at=6.80n
.end
