// Behavioral stand-ins for the ASAP7 structural cells instantiated in
// sram_control.v / delay_cell.v, so the decoder RTL can be simulated.
// The delay chains become logically transparent (even inverter count -> Y==A),
// which is exactly what we want for a *functional* decode-correctness check.
module INVx1_ASAP7_75t_R (input A, output Y);
    assign Y = ~A;
endmodule

module BUFx4_ASAP7_75t_R (input A, output Y);
    assign Y = A;
endmodule

module BUFx2_ASAP7_75t_R (input A, output Y);
    assign Y = A;
endmodule

module BUFx8_ASAP7_75t_R (input A, output Y);
    assign Y = A;
endmodule

module AND2x2_ASAP7_75t_R (input A, B, output Y);
    assign Y = A & B;
endmodule

module AND3x1_ASAP7_75t_R (input A, B, C, output Y);
    assign Y = A & B & C;
endmodule

module AND4x1_ASAP7_75t_R (input A, B, C, D, output Y);
    assign Y = A & B & C & D;
endmodule
