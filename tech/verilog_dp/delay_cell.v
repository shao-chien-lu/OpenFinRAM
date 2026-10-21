module delay_cell #(
    parameter BUF_COUNT = 10
) (
    input  A,
    output Y
);

    // Create a wire array from [0] to [BUF_COUNT] (total BUF_COUNT + 1 wires)
    wire [BUF_COUNT:0] chain_wires;

    // Connect "A" to the first wire of the delay chain
    assign chain_wires[0] = A;

    // Use a generate loop to instantiate BUF_COUNT inverters
    genvar i;
    generate
        for (i = 0; i < BUF_COUNT; i = i + 1) begin: inv_chain_loop
            // The controller's pulse sequencing depends on every physical
            // stage in this chain.  Tag the instances so synthesis can verify
            // and name exactly this network, and P&R can protect it from
            // resizing or removal.
            (* keep, dont_touch, physical_dp_delay *)
            BUFx2_ASAP7_75t_R u_inv (
                .A(chain_wires[i]),   // input
                .Y(chain_wires[i+1])  // output
            );
        end
    endgenerate

    // Connect the last wire of the delay chain to "Y"
    assign Y = chain_wires[BUF_COUNT];

endmodule
