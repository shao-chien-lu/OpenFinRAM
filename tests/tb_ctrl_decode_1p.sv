// Exhaustive functional check for the single-port ctrl_decode
// (tech/verilog_dp/sram_control_1p.v, --bitcell 6t): every address read and
// written, each checked against the decode the two-port controller's port A
// performs, and an idle cycle checked to drive nothing.
`timescale 1ns/1ps
module tb_ctrl_decode_1p #(
    parameter int NUM_WL     = 8,
    parameter int NUM_BANK   = 1,
    parameter int COLUMN_MUX = 4,
    parameter int ROW_PREDECODE = 3   // address bits per sel_hi predecode group
);
    localparam int ROW_BITS   = $clog2(NUM_WL);
    localparam int COL_BITS   = (COLUMN_MUX > 1) ? $clog2(COLUMN_MUX) : 1;
    localparam int BANK_BITS  = (NUM_BANK > 1) ? $clog2(NUM_BANK) : 0;
    localparam int ADDR_WIDTH = ROW_BITS + BANK_BITS + 1 + COL_BITS;
    localparam longint unsigned NUM_ADDR = 64'd1 << ADDR_WIDTH;

    logic clk = 0, rst_n = 0;
    logic ce_n_A = 1, we_n_A = 1, oe_n_A = 1;
    logic [ADDR_WIDTH-1:0] A_A = 0;

    localparam SLICES = (2 * NUM_WL) / 4;
    logic [NUM_BANK-1:0][SLICES-1:0] sel_hi_A;
    logic [3:0] sel_lo_A;
    logic [NUM_BANK-1:0][COLUMN_MUX-1:0] ysel_A, yseln_A;
    logic [NUM_BANK-1:0] blprechn_A, wrena_A, wrenan_A, oeb_out_A, oe_out_A, sae_A;

    ctrl_decode #(
        .ADDR_WIDTH(ADDR_WIDTH), .NUM_WL(NUM_WL), .NUM_BANK(NUM_BANK),
        .COLUMN_MUX(COLUMN_MUX), .WL_BUF(2), .SAE_BUF(2),
        .ROW_PREDECODE(ROW_PREDECODE)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .ce_n_A(ce_n_A), .we_n_A(we_n_A), .oe_n_A(oe_n_A), .A_A(A_A),
        .sel_hi_A(sel_hi_A), .sel_lo_A(sel_lo_A),
        .blprechn_A(blprechn_A),
        .ysel_A(ysel_A), .yseln_A(yseln_A),
        .wrena_A(wrena_A), .wrenan_A(wrenan_A),
        .oeb_out_A(oeb_out_A), .oe_out_A(oe_out_A), .sae_A(sae_A)
    );

    always #5 clk = ~clk;
    int errors = 0;

    function automatic bit is_power_of_two(input int value);
        return value > 0 && (value & (value - 1)) == 0;
    endfunction

    task automatic check_access(input logic [ADDR_WIDTH-1:0] address, input bit write_access);
        int unsigned row, col, bank;
        logic upper;
        logic [NUM_BANK*SLICES-1:0] expected_hi;
        logic [3:0] expected_lo;
        logic [NUM_BANK*COLUMN_MUX-1:0] expected_ysel, expected_yseln;
        logic [NUM_BANK-1:0] expected_prech, expected_wrena, expected_wrenan;
        logic [NUM_BANK-1:0] expected_oeb, expected_oe, expected_sae;

        row   = address & (NUM_WL - 1);
        col   = (address >> ROW_BITS) & (COLUMN_MUX - 1);
        upper = address[ROW_BITS+COL_BITS];
        bank  = address >> (ROW_BITS + COL_BITS + 1);

        expected_hi = '0; expected_lo = '0;
        expected_ysel = '0; expected_yseln = '1;
        expected_prech = '0;
        expected_wrena = '0; expected_wrenan = '1;
        expected_oeb = '1; expected_oe = '0; expected_sae = '0;
        // The bit above the column select is the top bit of the wordline
        // index w; the slices make WL<w> = sel_hi[w >> 2] . sel_lo[w & 3].
        expected_hi[bank*SLICES + ((upper*NUM_WL + row) >> 2)] = 1'b1;
        expected_lo[(upper*NUM_WL + row) & 3] = 1'b1;
        expected_ysel[bank*COLUMN_MUX + col] = 1'b1;
        expected_yseln[bank*COLUMN_MUX + col] = 1'b0;
        expected_prech[bank] = 1'b1;
        if (write_access) begin
            expected_wrena[bank] = 1'b1;
            expected_wrenan[bank] = 1'b0;
        end else begin
            expected_oeb[bank] = 1'b0;
            expected_oe[bank] = 1'b1;
            expected_sae[bank] = 1'b1;
        end

        ce_n_A = 0; we_n_A = !write_access; oe_n_A = write_access; A_A = address;
        @(posedge clk); #2;
        if (sel_hi_A !== expected_hi || sel_lo_A !== expected_lo ||
            ysel_A !== expected_ysel || yseln_A !== expected_yseln ||
            blprechn_A !== expected_prech ||
            wrena_A !== expected_wrena || wrenan_A !== expected_wrenan ||
            oeb_out_A !== expected_oeb || oe_out_A !== expected_oe ||
            sae_A !== expected_sae) begin
            errors++;
            $display("FAIL %s address=%0d", write_access ? "write" : "read", address);
        end
    endtask

    initial begin
        if (!is_power_of_two(NUM_WL) || NUM_WL < 2 || !is_power_of_two(NUM_BANK) ||
            !is_power_of_two(COLUMN_MUX) || COLUMN_MUX < 2)
            $fatal(1, "unsupported test geometry");

        repeat (2) @(posedge clk);
        rst_n = 1;
        for (longint unsigned address = 0; address < NUM_ADDR; address++) begin
            check_access(address[ADDR_WIDTH-1:0], 0);
            check_access(address[ADDR_WIDTH-1:0], 1);
        end

        // Deselected: nothing fires.
        ce_n_A = 1; we_n_A = 1; oe_n_A = 1;
        @(posedge clk); @(posedge clk); #2;
        if (sel_hi_A !== '0 || ysel_A !== '0 || blprechn_A !== '0 ||
            wrena_A !== '0 || wrenan_A !== '1 || sae_A !== '0 || oeb_out_A !== '1) begin
            errors++;
            $display("FAIL deselected cycle drives the array");
        end

        if (errors == 0)
            $display("PASS: 1RW decode correct over all %0d addresses (NUM_WL=%0d, BANKS=%0d, MUX=%0d)",
                     NUM_ADDR, NUM_WL, NUM_BANK, COLUMN_MUX);
        else
            $fatal(1, "FAILED: %0d error(s)", errors);
        $finish;
    end
endmodule
