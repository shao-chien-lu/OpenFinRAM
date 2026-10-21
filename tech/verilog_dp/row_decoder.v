// Hierarchical SRAM row decode. Address must be stable before EN rises and
// remain stable until EN falls; the controller registers the address and
// supplies the delayed wordline phase. This block is combinational.
module sram_row_decode #(
    parameter integer NUM_WL = 32,
    parameter integer ADDR_BITS = (NUM_WL > 1) ? $clog2(NUM_WL) : 1,
    parameter integer PREDECODE_BITS = 3
)(
    input  wire [ADDR_BITS-1:0] A,
    output wire [NUM_WL-1:0] SEL
);
    // Balance groups: 5 address bits -> 3+2, 7 -> 3+2+2, 8 -> 3+3+2; with
    // 4-bit groups 7 -> 4+3, 9 -> 3+3+3, 12 -> 4+4+4 (three groups, AND3).
    localparam integer GROUPS = (ADDR_BITS + PREDECODE_BITS - 1) / PREDECODE_BITS;
    localparam integer BASE_BITS = ADDR_BITS / GROUPS;
    localparam integer EXTRA = ADDR_BITS % GROUPS;
    localparam integer STRIDE = 1 << PREDECODE_BITS;
    (* keep = "true" *) wire [GROUPS*STRIDE-1:0] predecoded;
    wire [ADDR_BITS-1:0] inverted;
    if (NUM_WL < 1 || ADDR_BITS < 1 || ADDR_BITS < $clog2(NUM_WL)
        || PREDECODE_BITS < 2 || PREDECODE_BITS > 4) begin : g_bad_parameters
        invalid_sram_row_decoder_parameters u_invalid ();
    end
    for (genvar bit_index = 0; bit_index < ADDR_BITS; bit_index = bit_index + 1) begin : g_invert
        INVx1_ASAP7_75t_R u_invert (.A(A[bit_index]), .Y(inverted[bit_index]));
    end

    for (genvar g = 0; g < GROUPS; g = g + 1) begin : g_predecode
        localparam integer BITS = BASE_BITS + (g < EXTRA);
        localparam integer LSB = g * BASE_BITS + ((g < EXTRA) ? g : EXTRA);
        for (genvar term = 0; term < STRIDE; term = term + 1) begin : g_term
            if (term < (1 << BITS)) begin : g_used
                wire [BITS-1:0] literals;
                for (genvar b = 0; b < BITS; b = b + 1) begin : g_literal
                    assign literals[b] = (term & (1 << b)) ? A[LSB+b] : inverted[LSB+b];
                end
                // Hard gates make the predecode boundary survive ABC;
                // keeping only wires permits it to rebuild flat comparators.
                if (BITS == 4) begin : g_four
                    (* keep = "true", physical_predecode = 1 *)
                    AND4x1_ASAP7_75t_R u_term (.A(literals[0]), .B(literals[1]), .C(literals[2]),
                                             .D(literals[3]), .Y(predecoded[g*STRIDE+term]));
                end else if (BITS == 3) begin : g_three
                    (* keep = "true", physical_predecode = 1 *)
                    AND3x1_ASAP7_75t_R u_term (.A(literals[0]), .B(literals[1]), .C(literals[2]),
                                             .Y(predecoded[g*STRIDE+term]));
                end else if (BITS == 2) begin : g_two
                    (* keep = "true", physical_predecode = 1 *)
                    AND2x2_ASAP7_75t_R u_term (.A(literals[0]), .B(literals[1]),
                                             .Y(predecoded[g*STRIDE+term]));
                end else begin : g_one
                    (* keep = "true", physical_predecode = 1 *)
                    BUFx2_ASAP7_75t_R u_term (.A(literals[0]), .Y(predecoded[g*STRIDE+term]));
                end
            end else begin : g_unused
                assign predecoded[g*STRIDE+term] = 1'b0;
            end
        end
    end

    for (genvar row = 0; row < NUM_WL; row = row + 1) begin : g_row
        wire [GROUPS-1:0] group_match;
        for (genvar g = 0; g < GROUPS; g = g + 1) begin : g_match
            localparam integer BITS = BASE_BITS + (g < EXTRA);
            localparam integer LSB = g * BASE_BITS + ((g < EXTRA) ? g : EXTRA);
            localparam integer TERM = (row >> LSB) & ((1 << BITS) - 1);
            assign group_match[g] = predecoded[g*STRIDE+TERM];
        end
        assign SEL[row] = &group_match;
    end
endmodule

// Separate final phase gating and load-driving stage. Share SEL between the
// half-arrays/banks of one port; each half has its own EN and final drivers.
module sram_wordline_driver_array #(
    parameter integer NUM_WL = 32,
    parameter integer DRIVE = 4
)(
    input  wire [NUM_WL-1:0] SEL,
    input  wire EN,
    output wire [NUM_WL-1:0] WL
);
    for (genvar row = 0; row < NUM_WL; row = row + 1) begin : g_driver
        wire enabled;
        (* keep = "true", dont_touch = "true", physical_wl_gate = 1 *)
        AND2x2_ASAP7_75t_R u_enable (.A(SEL[row]), .B(EN), .Y(enabled));
        if (DRIVE == 2) begin : g_x2
            (* keep = "true", dont_touch = "true", physical_wl_driver = 1 *)
            BUFx2_ASAP7_75t_R u_driver (.A(enabled), .Y(WL[row]));
        end else if (DRIVE == 4) begin : g_x4
            (* keep = "true", dont_touch = "true", physical_wl_driver = 1 *)
            BUFx4_ASAP7_75t_R u_driver (.A(enabled), .Y(WL[row]));
        end else if (DRIVE == 8) begin : g_x8
            (* keep = "true", dont_touch = "true", physical_wl_driver = 1 *)
            BUFx8_ASAP7_75t_R u_driver (.A(enabled), .Y(WL[row]));
        end else begin : g_invalid_drive
            // An unsupported physical drive must fail elaboration/synthesis.
            unsupported_sram_wordline_drive u_invalid ();
        end
    end
endmodule

// Standalone decoder for one half-array, with independent 2RW address paths.
module sram_decoder_2rw #(
    parameter integer NUM_WL = 32,
    parameter integer ADDR_BITS = (NUM_WL > 1) ? $clog2(NUM_WL) : 1,
    parameter integer PREDECODE_BITS = 3,
    parameter integer DRIVE = 4
)(
    input wire [ADDR_BITS-1:0] A_A, A_B,
    input wire EN_A, EN_B,
    output wire [NUM_WL-1:0] WLA, WLB
);
    wire [NUM_WL-1:0] select_A, select_B;
    sram_row_decode #(.NUM_WL(NUM_WL), .ADDR_BITS(ADDR_BITS), .PREDECODE_BITS(PREDECODE_BITS))
        u_decode_A (.A(A_A), .SEL(select_A));
    sram_row_decode #(.NUM_WL(NUM_WL), .ADDR_BITS(ADDR_BITS), .PREDECODE_BITS(PREDECODE_BITS))
        u_decode_B (.A(A_B), .SEL(select_B));
    sram_wordline_driver_array #(.NUM_WL(NUM_WL), .DRIVE(DRIVE))
        u_drivers_A (.SEL(select_A), .EN(EN_A), .WL(WLA));
    sram_wordline_driver_array #(.NUM_WL(NUM_WL), .DRIVE(DRIVE))
        u_drivers_B (.SEL(select_B), .EN(EN_B), .WL(WLB));
endmodule
