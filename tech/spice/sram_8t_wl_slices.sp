* ASAP7 8T wordline driver slices, one per load class; WL<i> = SEL . B<i>

* 4 cells along the wordline: 0.7 fF
.SUBCKT wl_slice_c4 SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS
X_slice SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS wl_slice_nand4n2p_inv2n2p
.ENDS wl_slice_c4

* 8 cells along the wordline: 1.4 fF
.SUBCKT wl_slice_c8 SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS
X_slice SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS wl_slice_nand4n2p_inv2n2p
.ENDS wl_slice_c8

* 16 cells along the wordline: 2.9 fF
.SUBCKT wl_slice_c16 SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS
X_slice SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS wl_slice_nand4n2p_inv4n4p
.ENDS wl_slice_c16

* 32 cells along the wordline: 5.8 fF
.SUBCKT wl_slice_c32 SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS
X_slice SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS wl_slice_nand4n2p_inv9n9p
.ENDS wl_slice_c32

* 64 cells along the wordline: 11.6 fF
.SUBCKT wl_slice_c64 SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS
X_slice SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS wl_slice_nand8n4p_inv9n9p_8n8p
.ENDS wl_slice_c64

.SUBCKT inv_fin_2n2p_2f A Y VDD VSS
M0 Y A VSS VSS nmos_rvt nfin=2 l=20n nf=2 m=1
M1 Y A VDD VDD pmos_rvt nfin=2 l=20n nf=2 m=1
.ENDS inv_fin_2n2p_2f

.SUBCKT inv_fin_4n4p_2f A Y VDD VSS
M0 Y A VSS VSS nmos_rvt nfin=4 l=20n nf=2 m=1
M1 Y A VDD VDD pmos_rvt nfin=4 l=20n nf=2 m=1
.ENDS inv_fin_4n4p_2f

.SUBCKT inv_fin_9n9p_2f A Y VDD VSS
M0 Y A VSS VSS nmos_rvt nfin=9 l=20n nf=2 m=1
M1 Y A VDD VDD pmos_rvt nfin=9 l=20n nf=2 m=1
.ENDS inv_fin_9n9p_2f

.SUBCKT inv_fin_9n9p_8n8p_2f A Y VDD VSS
M0 Y A VSS VSS nmos_rvt nfin=9 l=20n nf=2 m=1
M1 Y A VDD VDD pmos_rvt nfin=9 l=20n nf=2 m=1
M2 Y A VDD VDD pmos_rvt nfin=8 l=20n nf=2 m=1
M3 Y A VSS VSS nmos_rvt nfin=8 l=20n nf=2 m=1
.ENDS inv_fin_9n9p_8n8p_2f

.SUBCKT nand2_fin_4n2p_2f A B Y VDD VSS
MNa0 Y A n0 VSS nmos_rvt nfin=4 l=20n nf=1 m=1
MNb0 n0 B VSS VSS nmos_rvt nfin=4 l=20n nf=1 m=1
MPa0 Y A VDD VDD pmos_rvt nfin=2 l=20n nf=1 m=1
MPb0 Y B VDD VDD pmos_rvt nfin=2 l=20n nf=1 m=1
MNa1 Y A n1 VSS nmos_rvt nfin=4 l=20n nf=1 m=1
MNb1 n1 B VSS VSS nmos_rvt nfin=4 l=20n nf=1 m=1
MPa1 Y A VDD VDD pmos_rvt nfin=2 l=20n nf=1 m=1
MPb1 Y B VDD VDD pmos_rvt nfin=2 l=20n nf=1 m=1
.ENDS nand2_fin_4n2p_2f

.SUBCKT nand2_fin_8n4p_2f A B Y VDD VSS
MNa0 Y A n0 VSS nmos_rvt nfin=8 l=20n nf=1 m=1
MNb0 n0 B VSS VSS nmos_rvt nfin=8 l=20n nf=1 m=1
MPa0 Y A VDD VDD pmos_rvt nfin=4 l=20n nf=1 m=1
MPb0 Y B VDD VDD pmos_rvt nfin=4 l=20n nf=1 m=1
MNa1 Y A n1 VSS nmos_rvt nfin=8 l=20n nf=1 m=1
MNb1 n1 B VSS VSS nmos_rvt nfin=8 l=20n nf=1 m=1
MPa1 Y A VDD VDD pmos_rvt nfin=4 l=20n nf=1 m=1
MPb1 Y B VDD VDD pmos_rvt nfin=4 l=20n nf=1 m=1
.ENDS nand2_fin_8n4p_2f

.SUBCKT wl_slice_nand4n2p_inv2n2p SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS
XN0 SEL B0 N0 VDD VSS nand2_fin_4n2p_2f
XD0 N0 WL0 VDD VSS inv_fin_2n2p_2f
XN1 SEL B1 N1 VDD VSS nand2_fin_4n2p_2f
XD1 N1 WL1 VDD VSS inv_fin_2n2p_2f
XN2 SEL B2 N2 VDD VSS nand2_fin_4n2p_2f
XD2 N2 WL2 VDD VSS inv_fin_2n2p_2f
XN3 SEL B3 N3 VDD VSS nand2_fin_4n2p_2f
XD3 N3 WL3 VDD VSS inv_fin_2n2p_2f
.ENDS wl_slice_nand4n2p_inv2n2p

.SUBCKT wl_slice_nand4n2p_inv4n4p SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS
XN0 SEL B0 N0 VDD VSS nand2_fin_4n2p_2f
XD0 N0 WL0 VDD VSS inv_fin_4n4p_2f
XN1 SEL B1 N1 VDD VSS nand2_fin_4n2p_2f
XD1 N1 WL1 VDD VSS inv_fin_4n4p_2f
XN2 SEL B2 N2 VDD VSS nand2_fin_4n2p_2f
XD2 N2 WL2 VDD VSS inv_fin_4n4p_2f
XN3 SEL B3 N3 VDD VSS nand2_fin_4n2p_2f
XD3 N3 WL3 VDD VSS inv_fin_4n4p_2f
.ENDS wl_slice_nand4n2p_inv4n4p

.SUBCKT wl_slice_nand4n2p_inv9n9p SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS
XN0 SEL B0 N0 VDD VSS nand2_fin_4n2p_2f
XD0 N0 WL0 VDD VSS inv_fin_9n9p_2f
XN1 SEL B1 N1 VDD VSS nand2_fin_4n2p_2f
XD1 N1 WL1 VDD VSS inv_fin_9n9p_2f
XN2 SEL B2 N2 VDD VSS nand2_fin_4n2p_2f
XD2 N2 WL2 VDD VSS inv_fin_9n9p_2f
XN3 SEL B3 N3 VDD VSS nand2_fin_4n2p_2f
XD3 N3 WL3 VDD VSS inv_fin_9n9p_2f
.ENDS wl_slice_nand4n2p_inv9n9p

.SUBCKT wl_slice_nand8n4p_inv9n9p_8n8p SEL B0 B1 B2 B3 WL0 WL1 WL2 WL3 VDD VSS
XN0 SEL B0 N0 VDD VSS nand2_fin_8n4p_2f
XD0 N0 WL0 VDD VSS inv_fin_9n9p_8n8p_2f
XN1 SEL B1 N1 VDD VSS nand2_fin_8n4p_2f
XD1 N1 WL1 VDD VSS inv_fin_9n9p_8n8p_2f
XN2 SEL B2 N2 VDD VSS nand2_fin_8n4p_2f
XD2 N2 WL2 VDD VSS inv_fin_9n9p_8n8p_2f
XN3 SEL B3 N3 VDD VSS nand2_fin_8n4p_2f
XD3 N3 WL3 VDD VSS inv_fin_9n9p_8n8p_2f
.ENDS wl_slice_nand8n4p_inv9n9p_8n8p
.END
