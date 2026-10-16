# Wire naming convention

Wires that connect two components should be named after what they carry and
who they connect, not after an abbreviation:

```
<producer_stage>_to_<consumer_stage>_<what_the_payload_is>
```

- `producer_stage` — the component driving the wire (the one calling `.prepare()` on it).
- `consumer_stage` — the component reading it (the one calling `.get()` on it).
- `what_the_payload_is` — a short description of the data itself, not its width or type.

Stage names match the component's class name, lowercased
(`input_formatter`, `l1_handler`, `l1_res_buffer`, `l2_handler`), or
`input_bus`/`output_bus` for the engine's external ports.

## Examples (Sha256CoreV1 pipeline)

| Current name | Convention name |
|---|---|
| `output_inp_L1` | `input_formatter_to_l1_handler_padded_block` |
| `output_address` | `l2_handler_to_l1_res_buffer_schedule_address` |
| `output_val` | `l1_res_buffer_to_l2_handler_schedule_word` |
| `input_wire` / `Input` | `input_bus_to_input_formatter_message` |
| `NBbytes` | `input_bus_to_input_formatter_message_length_bytes` |
