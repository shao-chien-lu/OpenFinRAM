// Two-port (2RW) controller.  Each port has its IO column at its own end of
// the bitlines -- port A at one end, port B at the other -- so a column is one
// unsplit array of 2*NUM_WL wordlines with one precharge and one column select
// per port.  NUM_WL stays the number of rows one row-select field addresses;
// the address bit above the column select (bank_sel) picks the upper or lower
// NUM_WL wordlines of that one array.
//
// The wordlines themselves are driven at the array by four-wordline slices
// (WL<i> = SEL . B<i>, chipforge_asap7's DriverSliceSpec), one strip per port
// on each side of this controller.  So the controller emits, per port, the
// one-hot of the wordline index's two low bits (sel_lo, static) and, per
// bank, the one-hot of its high bits gated by the wordline phase (sel_hi):
// wordline w = {bank_sel, row}, w[1:0] picks within a slice, w[WL_BITS-1:2]
// the slice.
//
// SHARED_B: banks 2k and 2k+1 are mirrored so their port-B ends face each
// other and share one IO block per bit (chipforge_asap7's two-sided
// IoColumnSpec).  Port B's precharge and column selects stay per bank, one
// set for each side of the block; its sense enable, write enable and output
// enable become one per bank pair (B_IO of them), firing for whichever bank
// of the pair the address picks.  Port A is unchanged.
module ctrl_decode #(
    parameter ADDR_WIDTH = 5,
    parameter NUM_WL     = 2,
    parameter NUM_BANK   = 1,
    parameter COLUMN_MUX = 4,
    parameter WL_BUF     = 5,
    parameter SAE_BUF    = 15,
    // The address bits of a group in the slice decode (sel_hi): 2, 3 or 4.
    parameter ROW_PREDECODE = 3,
    parameter SLICES     = (2 * NUM_WL) / 4,  // four wordlines per driver slice; not to be overridden
    parameter SHARED_B   = 0,
    parameter B_IO       = (SHARED_B != 0) ? NUM_BANK / 2 : NUM_BANK  // port-B IO blocks; not to be overridden
)(
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  ce_n_A,
    input  logic                  ce_n_B,
    input  logic                  we_n_A,
    input  logic                  we_n_B,
    input  logic                  oe_n_A,
    input  logic                  oe_n_B,
    input  logic [ADDR_WIDTH-1:0] A_A,
    input  logic [ADDR_WIDTH-1:0] A_B,

    output logic [NUM_BANK-1:0][SLICES-1:0]     sel_hi_A,
    output logic [3:0]                          sel_lo_A,
    output logic [NUM_BANK-1:0]                 blprechn_A,
    output logic [NUM_BANK-1:0][COLUMN_MUX-1:0] ysel_A,
    output logic [NUM_BANK-1:0][COLUMN_MUX-1:0] yseln_A,
    output logic [NUM_BANK-1:0]                 wrena_A,
    output logic [NUM_BANK-1:0]                 wrenan_A,
    output logic [NUM_BANK-1:0]                 oeb_out_A,
    output logic [NUM_BANK-1:0]                 oe_out_A,
    output logic [NUM_BANK-1:0]                 sae_A,

    output logic [NUM_BANK-1:0][SLICES-1:0]     sel_hi_B,
    output logic [3:0]                          sel_lo_B,
    output logic [NUM_BANK-1:0]                 blprechn_B,
    output logic [NUM_BANK-1:0][COLUMN_MUX-1:0] ysel_B,
    output logic [NUM_BANK-1:0][COLUMN_MUX-1:0] yseln_B,
    output logic [B_IO-1:0]                     wrena_B,
    output logic [B_IO-1:0]                     wrenan_B,
    output logic [B_IO-1:0]                     oeb_out_B,
    output logic [B_IO-1:0]                     oe_out_B,
    output logic [B_IO-1:0]                     sae_B
);

    function integer clog2;
        input integer value;
        begin
            value = value - 1;
            for (clog2 = 0; value > 0; clog2 = clog2 + 1)
                value = value >> 1;
        end
    endfunction

    localparam ROW_BITS      = clog2(NUM_WL);
    localparam WL_BITS       = ROW_BITS + 1;       // 2*NUM_WL wordlines: {bank_sel, row}
    localparam Y_BITS        = (COLUMN_MUX > 1) ? clog2(COLUMN_MUX) : 1;
    localparam SLICE_BITS    = (NUM_BANK > 1) ? clog2(NUM_BANK) : 1;
    localparam BANK_BIT_IDX  = ROW_BITS + Y_BITS;
    localparam SLICE_BIT_IDX = BANK_BIT_IDX + 1;
    localparam ADDR_USED_BITS = SLICE_BIT_IDX + SLICE_BITS;

    localparam [COLUMN_MUX-1:0] ONEHOT_BASE = {{(COLUMN_MUX - 1){1'b0}}, 1'b1};

    wire [ROW_BITS-1:0]   row_sel_d_A = A_A[ROW_BITS-1:0];
    wire [Y_BITS-1:0]     col_sel_d_A = A_A[BANK_BIT_IDX-1:ROW_BITS];
    wire                  bank_sel_d_A = A_A[BANK_BIT_IDX];
    wire [SLICE_BITS-1:0] slice_sel_d_A = (NUM_BANK > 1) ?
                                          A_A[SLICE_BIT_IDX + SLICE_BITS - 1:SLICE_BIT_IDX] :
                                          '0;

    wire [ROW_BITS-1:0]   row_sel_d_B = A_B[ROW_BITS-1:0];
    wire [Y_BITS-1:0]     col_sel_d_B = A_B[BANK_BIT_IDX-1:ROW_BITS];
    wire                  bank_sel_d_B = A_B[BANK_BIT_IDX];
    wire [SLICE_BITS-1:0] slice_sel_d_B = (NUM_BANK > 1) ?
                                          A_B[SLICE_BIT_IDX + SLICE_BITS - 1:SLICE_BIT_IDX] :
                                          '0;

    logic [ROW_BITS-1:0]   row_sel_r_A;
    logic [Y_BITS-1:0]     col_sel_r_A;
    logic                  bank_sel_r_A;
    logic [SLICE_BITS-1:0] slice_sel_r_A;

    logic [ROW_BITS-1:0]   row_sel_r_B;
    logic [Y_BITS-1:0]     col_sel_r_B;
    logic                  bank_sel_r_B;
    logic [SLICE_BITS-1:0] slice_sel_r_B;

    typedef enum logic [1:0] {
        IDLE  = 2'b00,
        READ  = 2'b01,
        WRITE = 2'b10
    } state_t;

    state_t state_A;
    state_t next_state_A;

    state_t state_B;
    state_t next_state_B;

    wire read_req_A  = (state_A == READ)  && !ce_n_A;
    wire write_req_A = (state_A == WRITE) && !ce_n_A;
    if (SHARED_B != 0 && (NUM_BANK < 2 || NUM_BANK % 2 != 0)) begin : g_bad_shared_b
        invalid_shared_port_b_needs_bank_pairs u_invalid ();   // two banks to a block
    end
    // The port-B IO block the addressed bank reads and writes through.
    wire [SLICE_BITS-1:0] io_sel_r_B = (SHARED_B != 0) ? (slice_sel_r_B >> 1) : slice_sel_r_B;

    wire read_req_B  = (state_B == READ)  && !ce_n_B;
    wire write_req_B = (state_B == WRITE) && !ce_n_B;

    // Precharge is released first, then WL is asserted after a short delay
    // to avoid VDD->BL->cell->VSS crowbar current.
    wire prech_off_A = (read_req_A || write_req_A) && clk;
    wire prech_off_B = (read_req_B || write_req_B) && clk;

    wire wl_any_fire_A;
    wire wl_any_fire_B;

    delay_cell #(.BUF_COUNT(WL_BUF)) u_delay_wl_A (
        .A(prech_off_A),
        .Y(wl_any_fire_A)
    );

    delay_cell #(.BUF_COUNT(WL_BUF)) u_delay_wl_B (
        .A(prech_off_B),
        .Y(wl_any_fire_B)
    );

    wire wl_read_fire_A  = wl_any_fire_A && read_req_A;

    wire wl_read_fire_B  = wl_any_fire_B && read_req_B;

    // Write enable follows the delayed clock, as the wordline does.  Gating
    // the raw clock with the registered state let it pulse for a clk-to-Q on
    // the edge after a write, while the state still read WRITE; the delayed
    // edge arrives after the state has settled, so that pulse cannot pass.
    wire wl_write_fire_A = wl_any_fire_A && write_req_A;
    wire wl_write_fire_B = wl_any_fire_B && write_req_B;

    // Predecode once per port.  The low two bits of the wordline index pick a
    // wordline within a slice: their one-hot is static and shared by every
    // slice and bank.  The high bits pick the slice: their one-hot is gated by
    // the wordline phase and the bank, and is the slice's SEL.
    wire [WL_BITS-1:0] wl_index_A = {bank_sel_r_A, row_sel_r_A};
    wire [WL_BITS-1:0] wl_index_B = {bank_sel_r_B, row_sel_r_B};
    wire [3:0] lo_A, lo_B;
    wire [SLICES-1:0] hi_A, hi_B;
    if (2 * NUM_WL < 4 || (2 * NUM_WL) % 4 != 0) begin : g_bad_wordlines
        invalid_sram_wordline_count u_invalid ();   // a strip is whole slices
    end
    sram_row_decode #(.NUM_WL(4), .ADDR_BITS(2)) u_lo_A
        (.A(wl_index_A[1:0]), .SEL(lo_A));
    sram_row_decode #(.NUM_WL(4), .ADDR_BITS(2)) u_lo_B
        (.A(wl_index_B[1:0]), .SEL(lo_B));
    if (SLICES > 1) begin : g_hi
        sram_row_decode #(.NUM_WL(SLICES), .ADDR_BITS(WL_BITS - 2),
                          .PREDECODE_BITS(ROW_PREDECODE)) u_hi_A
            (.A(wl_index_A[WL_BITS-1:2]), .SEL(hi_A));
        sram_row_decode #(.NUM_WL(SLICES), .ADDR_BITS(WL_BITS - 2),
                          .PREDECODE_BITS(ROW_PREDECODE)) u_hi_B
            (.A(wl_index_B[WL_BITS-1:2]), .SEL(hi_B));
    end else begin : g_one_slice
        assign hi_A = 1'b1;
        assign hi_B = 1'b1;
    end
    assign sel_lo_A = lo_A;
    assign sel_lo_B = lo_B;
    for (genvar bank = 0; bank < NUM_BANK; bank = bank + 1) begin : g_wordlines
        wire enable_A = rst_n && (read_req_A || write_req_A) && wl_any_fire_A
                        && (slice_sel_r_A == bank);
        wire enable_B = rst_n && (read_req_B || write_req_B) && wl_any_fire_B
                        && (slice_sel_r_B == bank);
        assign sel_hi_A[bank] = hi_A & {SLICES{enable_A}};
        assign sel_hi_B[bank] = hi_B & {SLICES{enable_B}};
    end

    // SAE is asserted after wl_read_fire with an additional SAE_BUF delay
    wire sae_raw_A;
    wire sae_raw_B;

    delay_cell #(.BUF_COUNT(SAE_BUF)) u_delay_sae_A (
        .A(wl_read_fire_A),
        .Y(sae_raw_A)
    );

    delay_cell #(.BUF_COUNT(SAE_BUF)) u_delay_sae_B (
        .A(wl_read_fire_B),
        .Y(sae_raw_B)
    );

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_A      <= IDLE;
            row_sel_r_A  <= '0;
            col_sel_r_A  <= '0;
            bank_sel_r_A <= 1'b0;
            slice_sel_r_A <= '0;
        end else begin
            state_A <= next_state_A;
            if (!ce_n_A) begin
                row_sel_r_A   <= row_sel_d_A;
                col_sel_r_A   <= col_sel_d_A;
                bank_sel_r_A  <= bank_sel_d_A;
                slice_sel_r_A <= slice_sel_d_A;
            end
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_B      <= IDLE;
            row_sel_r_B  <= '0;
            col_sel_r_B  <= '0;
            bank_sel_r_B <= 1'b0;
            slice_sel_r_B <= '0;
        end else begin
            state_B <= next_state_B;
            if (!ce_n_B) begin
                row_sel_r_B   <= row_sel_d_B;
                col_sel_r_B   <= col_sel_d_B;
                bank_sel_r_B  <= bank_sel_d_B;
                slice_sel_r_B <= slice_sel_d_B;
            end
        end
    end

    always_comb begin
        next_state_A = IDLE;
        if (!ce_n_A) begin
            if (!we_n_A && oe_n_A)      next_state_A = WRITE;
            else if (we_n_A && !oe_n_A) next_state_A = READ;
            else                        next_state_A = IDLE;
        end
    end

    always_comb begin
        next_state_B = IDLE;
        if (!ce_n_B) begin
            if (!we_n_B && oe_n_B)      next_state_B = WRITE;
            else if (we_n_B && !oe_n_B) next_state_B = READ;
            else                        next_state_B = IDLE;
        end
    end

    always_comb begin
        blprechn_A  = '0;
        oeb_out_A   = '1;
        sae_A       = '0;

        blprechn_B  = '0;
        oeb_out_B   = '1;
        sae_B       = '0;

        for (int i = 0; i < NUM_BANK; i = i + 1) begin
            ysel_A[i]   = '0;
            yseln_A[i]  = '1;
            wrena_A[i]  = 1'b0;
            wrenan_A[i] = 1'b1;

            ysel_B[i]   = '0;
            yseln_B[i]  = '1;
        end
        for (int i = 0; i < B_IO; i = i + 1) begin
            wrena_B[i]  = 1'b0;
            wrenan_B[i] = 1'b1;
        end

        if (read_req_A || write_req_A) begin
            blprechn_A[slice_sel_r_A] = prech_off_A;
            ysel_A[slice_sel_r_A]     = ONEHOT_BASE << col_sel_r_A;
            yseln_A[slice_sel_r_A]    = ~ysel_A[slice_sel_r_A];

            if (read_req_A) begin
                oeb_out_A[slice_sel_r_A] = oe_n_A;
                sae_A[slice_sel_r_A]     = sae_raw_A;
            end

            if (write_req_A) begin
                wrena_A[slice_sel_r_A]  = wl_write_fire_A;
                wrenan_A[slice_sel_r_A] = ~wl_write_fire_A;
            end
        end

        if (read_req_B || write_req_B) begin
            blprechn_B[slice_sel_r_B] = prech_off_B;
            ysel_B[slice_sel_r_B]     = ONEHOT_BASE << col_sel_r_B;
            yseln_B[slice_sel_r_B]    = ~ysel_B[slice_sel_r_B];

            if (read_req_B) begin
                oeb_out_B[io_sel_r_B] = oe_n_B;
                sae_B[io_sel_r_B]     = sae_raw_B;
            end

            if (write_req_B) begin
                wrena_B[io_sel_r_B]  = wl_write_fire_B;
                wrenan_B[io_sel_r_B] = ~wl_write_fire_B;
            end
        end

        oe_out_A = ~oeb_out_A;
        oe_out_B = ~oeb_out_B;
    end

endmodule
