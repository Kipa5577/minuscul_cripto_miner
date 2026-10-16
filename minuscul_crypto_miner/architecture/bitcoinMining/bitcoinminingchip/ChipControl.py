import py4hw
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip.UsartInterface import ByteStreamInterface
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip import protocol as P


'''
Two state machines run side by side in this leaf component, plus a TX queue:

Frame parser (one byte per cycle from UsartRx):
0 = sof      # hunting for SOF (0xAA); any other byte is discarded
1 = cmd
2 = len_hi
3 = len_lo
4 = payload  # LEN bytes, only the first MAX_PAYLOAD_LEN are stored
5 = chk      # good checksum -> command executed; bad checksum -> frame dropped silently
A frame stalled mid-way for rx_timeout_cycles returns the parser to sof[0],
so a truncated frame can't swallow the next one.

Job sequencing:
0 = idle      # cores stopped, registers writable, accepts NEW_JOB
1 = loading   # new_job pulsed; waiting for the shared Sha256Engine's
              # midstate_ready to be seen low (reload started) then high
2 = running   # cores enabled; waiting for a validated result / timeout / ABORT_JOB
3 = draining  # cores stopped; discarding late results until every lane's
              # NonceTracker is empty and every arbiter/validator is idle for
              # DRAIN_SETTLE_CYCLES in a row, then sends the pending RESULT
              # (or timeout JOB_NACK) and goes back to idle[0]

Why drain instead of reset: the pipeline stages only use reset to block a
*new* start, not to clear work already in flight, so the only safe way to
reuse the cores for the next job is to stop feeding them and let what's in
flight run out. The RESULT frame is only queued once the drain is over, so
when the host receives RESULT the chip is already idle and can take the next
NEW_JOB straight away.

Nonce partitioning (done at NEW_JOB, per README): the k-th *active* core
(ACTIVE_CORES_MASK, bit i = core i) starts at CORE_BASE_OFFSET + k and every
active core steps by the number of active cores.
'''

DRAIN_SETTLE_CYCLES = 8


class ChipControl(py4hw.Logic):
    def __init__(self,parent,name,reset,
                 RxStream:ByteStreamInterface,TxStream:ByteStreamInterface,
                 # shared midstate engine
                 new_job,midstate_ready,
                 # job fields broadcast to the midstate engine and every core
                 header_first64bytes,merkle_tail,time_field,bits,
                 # per-core control/status (lists, one entry per core)
                 core_enables,core_nonce_starts,nonce_stride,core_idles,
                 # result arbiter tree
                 arbiter_busys,result_ready,result_nonce,result_digest,result_fetched,
                 rx_timeout_cycles,debug=False):
        super().__init__(parent,name)

        self.reset = self.addIn("reset",reset)
        self.rx = self.addInterfaceSink("rx",RxStream)
        self.tx = self.addInterfaceSource("tx",TxStream)

        self.new_job = self.addOut("new_job",new_job)
        self.midstate_ready = self.addIn("midstate_ready",midstate_ready)

        self.header_first64bytes = self.addOut("header_first64bytes",header_first64bytes)
        self.merkle_tail = self.addOut("merkle_tail",merkle_tail)
        self.time_field = self.addOut("time_field",time_field)
        self.bits = self.addOut("bits",bits)

        self.num_cores = len(core_enables)
        self.core_enables = [self.addOut(f"core_{i}_enable",w) for i,w in enumerate(core_enables)]
        self.core_nonce_starts = [self.addOut(f"core_{i}_nonce_start",w) for i,w in enumerate(core_nonce_starts)]
        self.nonce_stride = self.addOut("nonce_stride",nonce_stride)
        self.core_idles = [self.addIn(f"core_{i}_idle",w) for i,w in enumerate(core_idles)]

        self.arbiter_busys = [self.addIn(f"arbiter_{i}_busy",w) for i,w in enumerate(arbiter_busys)]
        self.result_ready = self.addIn("result_ready",result_ready)
        self.result_nonce = self.addIn("result_nonce",result_nonce)
        self.result_digest = self.addIn("result_digest",result_digest)
        self.result_fetched = self.addOut("result_fetched",result_fetched)

        self.rx_timeout_cycles = rx_timeout_cycles
        self.debug = debug

        # Register file
        self.regs = {
            P.REG_ACTIVE_CORES_MASK: (1 << self.num_cores) - 1,
            P.REG_CORE_BASE_OFFSET: 0,
            P.REG_JOB_TIMEOUT: 0,
        }
        self.error = False

        # Frame parser
        self.p_state = 0
        self.p_cmd = 0
        self.p_len = 0
        self.p_count = 0
        self.p_payload = []
        self.p_chk = 0
        self.p_idle_cycles = 0
        self.rx_pending_ack = False

        # Job sequencing
        self.state = 0
        self.midstate_seen_low = False
        self.engine_loading = False  # Sha256Engine reload in progress (must finish before going idle)
        self.new_job_pulse = False
        self.job_cycles = 0
        self.settle = 0
        self.pending_frame = None    # frame to send once draining[3] completes
        self.result_pending_ack = False

        # Latched job outputs
        self.job_header = 0
        self.job_merkle_tail = 0
        self.job_time = 0
        self.job_bits = 0
        self.enables = [0] * self.num_cores
        self.starts = [0] * self.num_cores
        self.stride = 1
        self.active = []

        self.tx_queue = []

    # ------------------------------------------------------------------ TX
    def _send(self,cmd,payload=b''):
        self.tx_queue.extend(P.build_frame(cmd,payload))
        if self.debug:
            print(f"ChipControl:queued frame cmd={cmd:#04x} len={len(payload)}")

    # ---------------------------------------------------------- registers
    def _status(self):
        match self.state:
            case 0:
                return P.STATUS_ERROR if self.error else P.STATUS_IDLE
            case 3:
                if self.pending_frame is not None and self.pending_frame[0] == P.RSP_RESULT:
                    return P.STATUS_RESULT_PENDING
                return P.STATUS_BUSY
            case _:
                return P.STATUS_BUSY

    def _read_reg(self,addr):
        match addr:
            case P.REG_CHIP_ID:
                return P.CHIP_ID
            case P.REG_STATUS:
                return self._status()
            case P.REG_NUM_CORES:
                return self.num_cores
            case _:
                return self.regs[addr]

    # ------------------------------------------------------------ commands
    def _execute(self,cmd,payload):
        n = len(payload)
        match cmd:
            case P.CMD_PING:
                if n == 0:
                    self._send(P.RSP_PONG)
            case P.CMD_READ_REG:
                addr = payload[0] if n >= 1 else 0xFF
                if n != 1 or addr >= P.NUM_REGS:
                    self._send(P.RSP_REG_NACK,[addr])
                else:
                    self._send(P.RSP_REG_VALUE,[addr] + list(self._read_reg(addr).to_bytes(4,'big')))
            case P.CMD_WRITE_REG:
                addr = payload[0] if n >= 1 else 0xFF
                if n != 5 or addr not in P.RW_REGS or self.state != 0:
                    self._send(P.RSP_REG_NACK,[addr])
                else:
                    self.regs[addr] = int.from_bytes(bytes(payload[1:5]),'big')
                    self._send(P.RSP_WRITE_ACK,[addr])
            case P.CMD_NEW_JOB:
                self._new_job(payload)
            case P.CMD_ABORT_JOB:
                if n == 0 and self.state in (1,2,3):
                    if self.debug:
                        print("ChipControl:ABORT_JOB")
                    self._start_drain(None)
            case _:
                if self.debug:
                    print(f"ChipControl:unknown cmd={cmd:#04x}, dropped")

    def _new_job(self,payload):
        if len(payload) != P.NEW_JOB_LEN:
            self._send(P.RSP_JOB_NACK,[P.NACK_MALFORMED])
            return
        if self.state != 0:
            self._send(P.RSP_JOB_NACK,[P.NACK_BUSY])
            return

        mask = self.regs[P.REG_ACTIVE_CORES_MASK] & ((1 << self.num_cores) - 1)
        active = [i for i in range(self.num_cores) if (mask >> i) & 1]
        if not active:
            self._send(P.RSP_JOB_NACK,[P.NACK_NO_CORES])
            return

        raw = bytes(payload)
        self.job_header = int.from_bytes(raw[0:64],'big')
        self.job_merkle_tail = int.from_bytes(raw[64:68],'big')
        self.job_time = int.from_bytes(raw[68:72],'big')
        self.job_bits = int.from_bytes(raw[72:76],'big')

        base = self.regs[P.REG_CORE_BASE_OFFSET]
        self.stride = len(active)
        self.starts = [0] * self.num_cores
        for rank, core in enumerate(active):
            self.starts[core] = (base + rank) & 0xFFFFFFFF
        self.active = active

        self.error = False
        self.midstate_seen_low = False
        self.engine_loading = True
        self.new_job_pulse = True
        self.state = 1
        self._send(P.RSP_JOB_ACK)
        if self.debug:
            print(f"ChipControl:idle[0]->loading[1] active={active} stride={self.stride}")

    def _start_drain(self,pending_frame):
        self.enables = [0] * self.num_cores
        self.pending_frame = pending_frame
        self.settle = 0
        self.state = 3

    # -------------------------------------------------------------- parser
    def _parse_byte(self,b):
        self.p_idle_cycles = 0
        match self.p_state:
            case 0:
                if b == P.SOF:
                    self.p_state = 1
            case 1:
                self.p_cmd = b
                self.p_chk = b
                self.p_state = 2
            case 2:
                self.p_len = b << 8
                self.p_chk ^= b
                self.p_state = 3
            case 3:
                self.p_len |= b
                self.p_chk ^= b
                self.p_payload = []
                self.p_count = 0
                self.p_state = 4 if self.p_len > 0 else 5
            case 4:
                if len(self.p_payload) < P.MAX_PAYLOAD_LEN:
                    self.p_payload.append(b)
                self.p_chk ^= b
                self.p_count += 1
                if self.p_count == self.p_len:
                    self.p_state = 5
            case 5:
                self.p_state = 0
                if b == self.p_chk:
                    # An over-long payload was truncated on capture; pad the
                    # length back so command handlers reject it as malformed.
                    payload = self.p_payload + [0] * (self.p_len - len(self.p_payload)) \
                        if self.p_len > P.MAX_PAYLOAD_LEN else self.p_payload
                    self._execute(self.p_cmd,payload)
                elif self.debug:
                    print(f"ChipControl:bad checksum on cmd={self.p_cmd:#04x}, frame dropped")

    # --------------------------------------------------------------- clock
    def clock(self):

        new_job = 0
        rx_ack = 0
        result_fetched = 0

        if self.reset.get() == 1:
            self.state = 0
            self.p_state = 0
            self.enables = [0] * self.num_cores
            self.tx_queue = []
        else:
            # --- RX: one byte per cycle -------------------------------------
            if self.rx.Valid.get() == 1 and not self.rx_pending_ack:
                rx_ack = 1
                self.rx_pending_ack = True
                self._parse_byte(self.rx.Data.get() & 0xFF)
            else:
                if self.rx.Valid.get() == 0:
                    self.rx_pending_ack = False
                if self.p_state != 0:
                    self.p_idle_cycles += 1
                    if self.p_idle_cycles >= self.rx_timeout_cycles:
                        self.p_state = 0
                        if self.debug:
                            print("ChipControl:frame timeout, parser resynced")

            # NEW_JOB accepted this very cycle -> one-cycle pulse to the midstate engine
            if self.new_job_pulse:
                new_job = 1
                self.new_job_pulse = False

            # --- Result handshake guard (same idiom as DifficultyValidator) --
            result_now = self.result_ready.get()
            if result_now == 0:
                self.result_pending_ack = False
            take_result = result_now == 1 and not self.result_pending_ack

            # --- Job sequencing --------------------------------------------
            match self.state:
                case 1:
                    ready = self.midstate_ready.get()
                    if ready == 0:
                        self.midstate_seen_low = True
                    elif self.midstate_seen_low:
                        self.engine_loading = False
                        self.enables = [1 if i in self.active else 0 for i in range(self.num_cores)]
                        self.job_cycles = 0
                        self.state = 2
                        if self.debug:
                            print("ChipControl:loading[1]->running[2]")
                case 2:
                    self.job_cycles += 1
                    timeout = self.regs[P.REG_JOB_TIMEOUT]
                    if take_result:
                        result_fetched = 1
                        self.result_pending_ack = True
                        nonce = self.result_nonce.get() & 0xFFFFFFFF
                        digest = self.result_digest.get()
                        self._start_drain((P.RSP_RESULT,list(nonce.to_bytes(4,'big')) + list(digest.to_bytes(32,'big'))))
                        if self.debug:
                            print(f"ChipControl:running[2]->draining[3] result nonce={nonce:#010x}")
                    elif timeout != 0 and self.job_cycles >= timeout:
                        self.error = True
                        self._start_drain((P.RSP_JOB_NACK,[P.NACK_TIMEOUT]))
                        if self.debug:
                            print("ChipControl:running[2]->draining[3] timeout")
                case 3:
                    if take_result:
                        # late hit from the job being drained: discard
                        result_fetched = 1
                        self.result_pending_ack = True
                    if self.engine_loading and self.midstate_ready.get() == 0:
                        self.midstate_seen_low = True
                    elif self.engine_loading and self.midstate_seen_low:
                        self.engine_loading = False

                    busy = (self.engine_loading or result_now == 1
                            or any(w.get() == 0 for w in self.core_idles)
                            or any(w.get() == 1 for w in self.arbiter_busys))
                    self.settle = 0 if busy else self.settle + 1
                    if self.settle >= DRAIN_SETTLE_CYCLES:
                        if self.pending_frame is not None:
                            self._send(*self.pending_frame)
                        self.pending_frame = None
                        self.state = 0
                        if self.debug:
                            print("ChipControl:draining[3]->idle[0]")

        # --- TX: pop the head byte once UsartTx has taken it ----------------
        if self.tx.Ack.get() == 1 and self.tx_queue:
            self.tx_queue.pop(0)

        self.tx.Data.prepare(self.tx_queue[0] if self.tx_queue else 0)
        self.tx.Valid.prepare(1 if self.tx_queue else 0)
        self.rx.Ack.prepare(rx_ack)

        self.new_job.prepare(new_job)
        self.header_first64bytes.prepare(self.job_header)
        self.merkle_tail.prepare(self.job_merkle_tail)
        self.time_field.prepare(self.job_time)
        self.bits.prepare(self.job_bits)
        for port, value in zip(self.core_enables, self.enables):
            port.prepare(value)
        for port, value in zip(self.core_nonce_starts, self.starts):
            port.prepare(value)
        self.nonce_stride.prepare(self.stride)
        self.result_fetched.prepare(result_fetched)
