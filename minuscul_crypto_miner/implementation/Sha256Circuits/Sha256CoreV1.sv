




module Sha256CoreV1 (
    input logic clk, reset;
    input logic digest_fetched;
    // Bus Connection


    //
    output logic seed_out; 
    output logic nbbytes_out;
    output logic digest_out;
    output logic digest_ready;
);
    // InputBuffer <-> InputFormatter
    logic input_buffer_to_input_formatter_message;
    logic input_buffer_to_input_fornatter_message_length_bytes
    logic input_buffer_to_input_formatter_message_ready
    logic input_buffer_to_output_buffer_message_fetched

    // InputBuffer <-> OutputBuffer
    logic input_buffer_to_output_buffer_seed_push;
    logic input_buffer_to_output_buffer_nbbytes_push;
    logic input_buffer_to_output_buffer_push_valid;

    // inputFormatter <-> L1_Handler
    logic input_formatter_to_l1_handler_padded_block_ready;
    logic input_formatter_to_l1_handler_padded_block;
    logic l1_handler_to_input_formatter_padded_block_consumed;


    //L1_Handler <-> L1_res_Buffer







endmodule 