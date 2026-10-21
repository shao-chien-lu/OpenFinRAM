// Exhaustive functional check for the true-dual-port ctrl_decode.  Each port
// is independently exercised for reads and writes over the complete address
// space, followed by a simultaneous write to different addresses.
`timescale 1ns/1ps
module tb_ctrl_decode_dp #(
    parameter int NUM_WL     = 8,
    parameter int NUM_BANK   = 1,
    parameter int COLUMN_MUX = 4,
    parameter int ROW_PREDECODE = 3,  // address bits per sel_hi predecode group
    parameter int SHARED_B   = 0   // banks share port B's IO block in pairs
);
    localparam int B_IO = SHARED_B ? NUM_BANK / 2 : NUM_BANK;
    localparam int ROW_BITS   = $clog2(NUM_WL);
    localparam int COL_BITS   = (COLUMN_MUX > 1) ? $clog2(COLUMN_MUX) : 1;
    localparam int BANK_BITS  = (NUM_BANK > 1) ? $clog2(NUM_BANK) : 0;
    localparam int ADDR_WIDTH = ROW_BITS + BANK_BITS + 1 + COL_BITS;
    localparam longint unsigned NUM_ADDR = 64'd1 << ADDR_WIDTH;

    logic clk = 0, rst_n = 0;
    logic ce_n_A = 1, we_n_A = 1, oe_n_A = 1;
    logic ce_n_B = 1, we_n_B = 1, oe_n_B = 1;
    logic [ADDR_WIDTH-1:0] A_A = 0, A_B = 0;

    localparam SLICES = (2 * NUM_WL) / 4;
    logic [NUM_BANK-1:0][SLICES-1:0] sel_hi_A, sel_hi_B;
    logic [3:0] sel_lo_A, sel_lo_B;
    logic [NUM_BANK-1:0][COLUMN_MUX-1:0]
        ysel_A, yseln_A, ysel_B, yseln_B;
    logic [NUM_BANK-1:0]
        blprechn_A, wrena_A, wrenan_A,
        oeb_out_A, oe_out_A, sae_A,
        blprechn_B;
    // Port B's sense, write and output enables: one per IO block.
    logic [B_IO-1:0] wrena_B, wrenan_B, oeb_out_B, oe_out_B, sae_B;

    ctrl_decode #(
        .ADDR_WIDTH(ADDR_WIDTH), .NUM_WL(NUM_WL), .NUM_BANK(NUM_BANK),
        .COLUMN_MUX(COLUMN_MUX), .WL_BUF(2), .SAE_BUF(2), .SHARED_B(SHARED_B),
        .ROW_PREDECODE(ROW_PREDECODE)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .ce_n_A(ce_n_A), .ce_n_B(ce_n_B),
        .we_n_A(we_n_A), .we_n_B(we_n_B),
        .oe_n_A(oe_n_A), .oe_n_B(oe_n_B), .A_A(A_A), .A_B(A_B),
        .sel_hi_A(sel_hi_A), .sel_lo_A(sel_lo_A),
        .blprechn_A(blprechn_A),
        .ysel_A(ysel_A), .yseln_A(yseln_A),
        .wrena_A(wrena_A), .wrenan_A(wrenan_A),
        .oeb_out_A(oeb_out_A), .oe_out_A(oe_out_A), .sae_A(sae_A),
        .sel_hi_B(sel_hi_B), .sel_lo_B(sel_lo_B),
        .blprechn_B(blprechn_B),
        .ysel_B(ysel_B), .yseln_B(yseln_B),
        .wrena_B(wrena_B), .wrenan_B(wrenan_B),
        .oeb_out_B(oeb_out_B), .oe_out_B(oe_out_B), .sae_B(sae_B)
    );

    always #5 clk = ~clk;
    int errors = 0;

    function automatic bit is_power_of_two(input int value);
        return value > 0 && (value & (value - 1)) == 0;
    endfunction

    task automatic check_selected_port(
        input bit port_b,
        input logic [ADDR_WIDTH-1:0] address,
        input bit write_access
    );
        int unsigned row, col, bank;
        logic upper;
        logic [NUM_BANK*SLICES-1:0] expected_hi;
        logic [3:0] expected_lo;
        logic [NUM_BANK*COLUMN_MUX-1:0] expected_ysel, expected_yseln;
        logic [NUM_BANK-1:0] expected_prech;
        logic [NUM_BANK-1:0] expected_wrena, expected_wrenan;
        logic [NUM_BANK-1:0] expected_oeb, expected_oe, expected_sae;
        // Port B's enables, by IO block: a shared block is the bank's pair.
        int unsigned io;
        logic [B_IO-1:0] io_wrena, io_wrenan, io_oeb, io_oe, io_sae;

        row    = address & (NUM_WL - 1);
        col    = (address >> ROW_BITS) & (COLUMN_MUX - 1);
        upper  = address[ROW_BITS+COL_BITS];
        bank   = address >> (ROW_BITS + COL_BITS + 1);

        expected_hi = '0; expected_lo = '0;
        expected_ysel = '0; expected_yseln = '1;
        expected_prech = '0;
        expected_wrena = '0; expected_wrenan = '1;
        expected_oeb = '1; expected_oe = '0; expected_sae = '0;
        // One array per column: the bit above the column select is the top
        // bit of the wordline index w.  The slices at the array make
        // WL<w> = sel_hi[w >> 2] . sel_lo[w & 3]; only the bank's sel_hi fires.
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
        io = SHARED_B ? bank / 2 : bank;
        io_wrena = '0; io_wrenan = '1; io_oeb = '1; io_oe = '0; io_sae = '0;
        if (write_access) begin
            io_wrena[io] = 1'b1;
            io_wrenan[io] = 1'b0;
        end else begin
            io_oeb[io] = 1'b0;
            io_oe[io] = 1'b1;
            io_sae[io] = 1'b1;
        end

        ce_n_A = 1; we_n_A = 1; oe_n_A = 1;
        ce_n_B = 1; we_n_B = 1; oe_n_B = 1;
        if (port_b) begin
            ce_n_B = 0; we_n_B = !write_access; oe_n_B = write_access;
            A_B = address;
        end else begin
            ce_n_A = 0; we_n_A = !write_access; oe_n_A = write_access;
            A_A = address;
        end
        @(posedge clk); #2;

        if (!port_b) begin
            if (sel_hi_A !== expected_hi || sel_lo_A !== expected_lo ||
                ysel_A !== expected_ysel || yseln_A !== expected_yseln ||
                blprechn_A !== expected_prech ||
                wrena_A !== expected_wrena || wrenan_A !== expected_wrenan ||
                oeb_out_A !== expected_oeb || oe_out_A !== expected_oe ||
                sae_A !== expected_sae) begin
                errors++;
                $display("FAIL A %s address=%0d", write_access ? "write" : "read", address);
            end
            if (sel_hi_B !== '0 || ysel_B !== '0 || blprechn_B !== '0 ||
                wrena_B !== '0 || wrenan_B !== '1 || sae_B !== '0) begin
                errors++;
                $display("FAIL A access activated idle port B at address=%0d", address);
            end
        end else begin
            if (sel_hi_B !== expected_hi || sel_lo_B !== expected_lo ||
                ysel_B !== expected_ysel || yseln_B !== expected_yseln ||
                blprechn_B !== expected_prech ||
                wrena_B !== io_wrena || wrenan_B !== io_wrenan ||
                oeb_out_B !== io_oeb || oe_out_B !== io_oe ||
                sae_B !== io_sae) begin
                errors++;
                $display("FAIL B %s address=%0d", write_access ? "write" : "read", address);
            end
            if (sel_hi_A !== '0 || ysel_A !== '0 || blprechn_A !== '0 ||
                wrena_A !== '0 || wrenan_A !== '1 || sae_A !== '0) begin
                errors++;
                $display("FAIL B access activated idle port A at address=%0d", address);
            end
        end
    endtask

    initial begin
        if (!is_power_of_two(NUM_WL) || NUM_WL < 2 ||
            !is_power_of_two(NUM_BANK) || (SHARED_B && NUM_BANK < 2) ||
            !is_power_of_two(COLUMN_MUX) || COLUMN_MUX < 2)
            $fatal(1, "unsupported test geometry");

        repeat (2) @(posedge clk);
        rst_n = 1;
        for (longint unsigned address = 0; address < NUM_ADDR; address++) begin
            check_selected_port(0, address[ADDR_WIDTH-1:0], 0);
            check_selected_port(0, address[ADDR_WIDTH-1:0], 1);
            check_selected_port(1, address[ADDR_WIDTH-1:0], 0);
            check_selected_port(1, address[ADDR_WIDTH-1:0], 1);
        end

        // Both write-state machines and write-enable buses must be live in the
        // same cycle; different addresses avoid an intentional cell collision.
        ce_n_A = 0; we_n_A = 0; oe_n_A = 1; A_A = '0;
        ce_n_B = 0; we_n_B = 0; oe_n_B = 1; A_B = NUM_ADDR - 1;
        @(posedge clk); #2;
        if ($countones(wrena_A) != 1 || $countones(wrena_B) != 1 ||
            $countones(sel_hi_A) != 1 || $countones(sel_hi_B) != 1 ||
            $countones(sel_lo_A) != 1 || $countones(sel_lo_B) != 1) begin
            errors++;
            $display("FAIL simultaneous A/B write activation");
        end

        if (errors == 0)
            $display("PASS: 2RW decode correct over all %0d addresses (NUM_WL=%0d, BANKS=%0d, MUX=%0d, SHARED_B=%0d)",
                     NUM_ADDR, NUM_WL, NUM_BANK, COLUMN_MUX, SHARED_B);
        else
            $fatal(1, "FAILED: %0d error(s)", errors);
        $finish;
    end
endmodule
