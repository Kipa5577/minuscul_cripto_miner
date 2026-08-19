import colorsys
import itertools

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.patches import Rectangle
import py4hw

from minuscul_crypto_miner.architecture.bitcoinMining import BitcoinMinerEngine


# --- Simulation speed -------------------------------------------------------
# One simulated clock edge is advanced per animation frame; this controls
# how many of those frames are drawn per real second.
CLOCKS_PER_SECOND = 60

NUM_LANES = 9              # number of nonce-search lanes -- change freely (each lane draws 12 stage columns)
NUM_DIGESTS_TO_SHOW = 40   # stop once this many double-hashes have reached DifficultyValidator
NBITS = 0x20100000         # lenient compact difficulty (~2**252 target) so a few "found blocks" show up

# Fixed 80-byte test header -- not a real Bitcoin header, arbitrary but
# deterministic, since this only demonstrates the pipeline's own timing.
HEADER_TEMPLATE = bytes((i * 7 + 3) % 256 for i in range(80))
HEADER_FIRST64 = HEADER_TEMPLATE[:64]
MERKLE_TAIL = HEADER_TEMPLATE[64:68]
TIME_FIELD = HEADER_TEMPLATE[68:72]
BITS_FIELD = HEADER_TEMPLATE[72:76]

LANE_STAGE_NAMES = [
    "NonceIterator",
    "L1_Handler",
    "L1_res_buffer",
    "L2_Handler",
    "Stage1_OutBuf",
    "DigestBridge",
    "Fin_InputBuffer",
    "Fin_InputFormat",
    "Fin_L1_Handler",
    "Fin_L1_res_buf",
    "Fin_L2_Handler",
    "Fin_OutputBuf",
]
NUM_LANE_STAGES = len(LANE_STAGE_NAMES)

def _generate_token_colors(n):
    """n well-spread, readable colors: hues stepped by the golden ratio
    (avoids near-repeats even for consecutive tokens), alternating between
    two lightness levels so adjacent hues stay visually distinguishable."""
    colors = []
    hue = 0.0
    for i in range(n):
        hue = (hue + 0.6180339887) % 1.0
        lightness = 0.42 if i % 2 == 0 else 0.58
        r, g, b = colorsys.hls_to_rgb(hue, lightness, 0.65)
        colors.append('#{:02x}{:02x}{:02x}'.format(int(r * 255), int(g * 255), int(b * 255)))
    return colors


_TOKEN_COLORS = itertools.cycle(_generate_token_colors(100))


class Token:
    """One nonce attempt's calculation as it rides through both hash stages."""

    def __init__(self, nonce):
        self.nonce = nonce
        self.color = next(_TOKEN_COLORS)
        self.found = False  # set True once DifficultyValidator confirms it passed the target

    @property
    def label(self):
        return f"{self.nonce:#x}"


# --- Build the real simulation ----------------------------------------------
sys_ = py4hw.HWSystem()

reset = sys_.wire('reset', 1)
py4hw.Constant(sys_, 'reset', 0, reset)

header_first64_wire = sys_.wire('header_first64bytes', 512)
py4hw.Constant(sys_, 'header_first64bytes_const', int.from_bytes(HEADER_FIRST64, 'big'), header_first64_wire)

new_job = sys_.wire('new_job', 1)


class OneShotPulse(py4hw.Logic):
    # Drives out=1 for exactly the first clock cycle, 0 thereafter --
    # Sha256Engine expects new_job as a pulse, not a held-high level.
    def __init__(self, parent, name, out):
        super().__init__(parent, name)
        self.out = self.addOut('out', out)
        self.fired = False

    def clock(self):
        self.out.prepare(0 if self.fired else 1)
        self.fired = True


OneShotPulse(sys_, 'new_job_pulse', new_job)

merkle_tail_wire = sys_.wire('merkle_tail', 32)
py4hw.Constant(sys_, 'merkle_tail_const', int.from_bytes(MERKLE_TAIL, 'big'), merkle_tail_wire)
time_wire = sys_.wire('time_field', 32)
py4hw.Constant(sys_, 'time_field_const', int.from_bytes(TIME_FIELD, 'big'), time_wire)
bits_wire = sys_.wire('bits', 32)
py4hw.Constant(sys_, 'bits_const', int.from_bytes(BITS_FIELD, 'big'), bits_wire)

nBits_wire = sys_.wire('nBits', 32)
py4hw.Constant(sys_, 'nBits_const', NBITS, nBits_wire)

result_ready = sys_.wire('result_ready', 1)
found_nonce_out = sys_.wire('found_nonce_out', 512)
found_nbbytes_out = sys_.wire('found_nbbytes_out', 8)
found_digest_out = sys_.wire('found_digest_out', 256)
result_fetched = sys_.wire('result_fetched', 1)

miner = BitcoinMinerEngine(sys_, 'miner', reset, header_first64_wire, new_job,
                            merkle_tail_wire, time_wire, bits_wire, nBits_wire,
                            result_ready, found_nonce_out, found_nbbytes_out, found_digest_out, result_fetched,
                            num_lanes=NUM_LANES, debug=False)

result_bus = miner.children['result_bus']
validator = miner.children['validator']
midstate_controller = miner.children['midstate_engine'].children['controller']


class FoundBlockDrain(py4hw.Logic):
    """Immediately fetches whatever DifficultyValidator reports found, so it never stalls."""
    def __init__(self, parent, name, result_ready, result_fetched):
        super().__init__(parent, name)
        self.result_ready = self.addIn('result_ready', result_ready)
        self.result_fetched = self.addOut('result_fetched', result_fetched)
        self.pending_ack = False
        self.found_count = 0

    def clock(self):
        fetched = 0
        if self.result_ready.get() == 1 and not self.pending_ack:
            fetched = 1
            self.pending_ack = True
            self.found_count += 1
        elif self.result_ready.get() == 0:
            self.pending_ack = False
        self.result_fetched.prepare(fetched)


drain = FoundBlockDrain(sys_, 'drain', result_ready, result_fetched)

sim = sys_.getSimulator()

digests_examined = 0


# --- Token tracking ----------------------------------------------------------
# One slot per real component instance, tracked the same way as the
# multi-engine animation: a token moves into a slot the instant that
# component's own state shows it just started working on something new
# (a rising edge of "busy"), read straight off each component's own .state
# -- never a re-implementation of the handshake logic itself.
midstate_slot = None
lane_slots = [[None] * NUM_LANE_STAGES for _ in range(NUM_LANES)]
resultbus_slot = None
dv_slot = None
dv_expire_pending = False

_prev_midstate_busy = False
_prev_lane_busy = [[False] * NUM_LANE_STAGES for _ in range(NUM_LANES)]
_prev_lane_nonce = [None] * NUM_LANES
_prev_resultbus_busy = False


def _lane_stage_busy(i):
    nonce_iter = miner.children[f'nonce_iter_{i}']
    l1 = miner.children[f'L1_{i}']
    l1buf = miner.children[f'L1buf_{i}']
    l2 = miner.children[f'L2_{i}']
    outbuf1 = miner.children[f'stage1_outbuf_{i}']
    bridge = miner.children[f'digest_bridge_{i}']
    finisher = miner.children[f'finisher_{i}']
    fin_inbuf = finisher.children['inbuf']
    fin_inp = finisher.children['inp']
    fin_l1 = finisher.children['L1']
    fin_l1buf = finisher.children['L1buf']
    fin_l2 = finisher.children['L2']
    fin_outbuf = finisher.children['outbuf']
    return [
        nonce_iter.state == 1,
        l1.state != 0,
        l1buf.pending,
        l2.state != 0,
        outbuf1.state == 1,
        bridge.state == 1,
        fin_inbuf.state == 1,
        fin_inp.state == 1,
        fin_l1.state != 0,
        fin_l1buf.pending,
        fin_l2.state != 0,
        fin_outbuf.state == 1,
    ]


def advance_one_clock():
    global midstate_slot, resultbus_slot, dv_slot, dv_expire_pending
    global _prev_midstate_busy, _prev_resultbus_busy
    global digests_examined

    sim.clk(1)

    # --- DifficultyValidator: resolve whatever it was holding from a
    # previous tick first (a rejected token's one-frame display expires; a
    # found token that just got fetched by the drain is counted), *then*
    # check for a newly examined digest.
    if dv_expire_pending:
        dv_slot = None
        dv_expire_pending = False
    if dv_slot is not None and not dv_slot.found and validator.state == 2:
        dv_slot.found = True  # just confirmed a pass; stop treating it as a one-frame flash
    if dv_slot is not None and dv_slot.found and validator.state == 0:
        dv_slot = None  # the drain fetched it

    if result_bus.Digest_Fetched.get() == 1:
        digests_examined += 1
        dv_slot = resultbus_slot
        resultbus_slot = None
        if validator.state != 2:
            dv_expire_pending = True  # rejected: show for exactly one frame

    # --- ResultBus: N lanes' finishers -> 1 stream
    rb_busy = result_bus.state == 1
    if rb_busy and not _prev_resultbus_busy:
        idx = result_bus.selected
        resultbus_slot = lane_slots[idx][NUM_LANE_STAGES - 1]
        lane_slots[idx][NUM_LANE_STAGES - 1] = None
    _prev_resultbus_busy = rb_busy

    # --- Each lane's own 12-stage pipeline, high-to-low per lane.
    for lane_idx in range(NUM_LANES):
        busy_now = _lane_stage_busy(lane_idx)
        prev_busy = _prev_lane_busy[lane_idx]
        slots = lane_slots[lane_idx]

        nonce_iter = miner.children[f'nonce_iter_{lane_idx}']
        cur_nonce = nonce_iter.nonce

        for i in reversed(range(NUM_LANE_STAGES)):
            if i == 0:
                continue
            if busy_now[i] and not prev_busy[i]:
                slots[i] = slots[i - 1]
                slots[i - 1] = None

        # NonceIterator has no separate "idle" state to rise from
        # (block_ready is held continuously once active) -- unlike every
        # other stage here, a rising busy_now[0]-vs-prev_busy[0] edge only
        # ever happens once, on its very first activation, so it cannot be
        # used to detect each subsequent nonce attempt. A new token appears
        # whenever the presented nonce actually changed instead, same idiom
        # as seedGenerator's Seq flip. This must run *after* the loop above
        # (which may have just pulled the previous token out of slots[0]
        # into slots[1] this same cycle) so a fresh token doesn't get
        # overwritten before it's had a chance to move on.
        if cur_nonce != _prev_lane_nonce[lane_idx]:
            slots[0] = Token(cur_nonce)

        _prev_lane_nonce[lane_idx] = cur_nonce
        _prev_lane_busy[lane_idx] = busy_now

    # --- Sha256Engine: computes the shared midstate once, then stays
    # permanently "cached" (green, stationary) for the rest of the run.
    midstate_busy = midstate_controller.state != 0 and midstate_controller.state != 4
    if midstate_busy and not _prev_midstate_busy and midstate_slot is None:
        midstate_slot = Token(0)
        midstate_slot.label_override = "midstate"
    if midstate_controller.state == 4 and midstate_slot is not None:
        midstate_slot.found = True  # reuse the "found" (green) styling as "cached"
    _prev_midstate_busy = midstate_busy


def pipeline_finished():
    return digests_examined >= NUM_DIGESTS_TO_SHOW


# --- Drawing ------------------------------------------------------------------
COL_MIDSTATE = 0
COL_LANE_START = 1
COL_RESULTBUS = COL_LANE_START + NUM_LANE_STAGES
COL_DV = COL_RESULTBUS + 1
NUM_COLS = COL_DV + 1

ROW_H = 1.6
ROWS_TOP_MARGIN = 0.9
TOTAL_ROWS_H = NUM_LANES * ROW_H
CANVAS_H = TOTAL_ROWS_H + ROWS_TOP_MARGIN

BOX_MARGIN_X = 0.10
BOX_MARGIN_Y = 0.12

fig, ax = plt.subplots(figsize=(1.5 * NUM_COLS, max(4.5, 1.6 * NUM_LANES + 1.6)))
ax.set_xlim(0, NUM_COLS)
ax.set_ylim(0, CANVAS_H + 0.35)
ax.axis('off')
fig.suptitle(f"Bitcoin mining pipeline ({NUM_LANES} lanes, cached midstate)", fontsize=14, fontweight='bold')

wide_boxes = {}   # col -> (stage_box, token_box, token_label)
lane_boxes = {}   # (row, col_offset) -> (stage_box, token_box, token_label)


def _make_box(x, y, w, h, label=None, label_y=None):
    stage_box = Rectangle((x + BOX_MARGIN_X, y + BOX_MARGIN_Y), w - 2 * BOX_MARGIN_X, h - 2 * BOX_MARGIN_Y,
                           facecolor='white', edgecolor='black', linewidth=1.6, zorder=2)
    ax.add_patch(stage_box)
    token_label = ax.text(x + w / 2, y + h / 2, '', ha='center', va='center',
                           fontsize=7, fontweight='bold', zorder=3)
    if label is not None:
        ax.text(x + w / 2, label_y, label, ha='center', va='bottom', fontsize=8, rotation=60)
    return stage_box, token_label


# Wide (single-instance) boxes spanning the full row height: Sha256Engine, ResultBus, DifficultyValidator.
for col, name in [(COL_MIDSTATE, "Sha256Engine\n(midstate)"), (COL_RESULTBUS, "ResultBus"), (COL_DV, "DifficultyValidator")]:
    stage_box, token_label = _make_box(col, 0, 1, TOTAL_ROWS_H, label=name, label_y=TOTAL_ROWS_H + 0.15)
    wide_boxes[col] = (stage_box, token_label)

# Per-lane stage boxes, one row per lane.
for row in range(NUM_LANES):
    y = row * ROW_H
    ax.text(COL_LANE_START - 0.05, y + ROW_H / 2, f"lane {row}",
             ha='right', va='center', fontsize=9, rotation=90)
    for j, name in enumerate(LANE_STAGE_NAMES):
        col = COL_LANE_START + j
        label = name if row == NUM_LANES - 1 else None
        stage_box, token_label = _make_box(col, y, 1, ROW_H,
                                            label=label, label_y=TOTAL_ROWS_H + 0.15)
        lane_boxes[(row, j)] = (stage_box, token_label)
    # Dotted delimiter between lane rows.
    if row > 0:
        ax.plot([COL_LANE_START, COL_RESULTBUS], [y, y], linestyle=':', color='black', linewidth=1.0, zorder=1)

# Dotted vertical delimiters between every component column.
for col in range(NUM_COLS + 1):
    ax.plot([col, col], [0, CANVAS_H], linestyle=':', color='black', linewidth=1.2, zorder=1)

# A single line tracing the overall order of operations -- not the real wiring.
order_y = CANVAS_H + 0.15
ax.annotate('', xy=(NUM_COLS - 0.05, order_y), xytext=(0.05, order_y),
            arrowprops=dict(arrowstyle='-|>', color='#555555', linewidth=1.5))

cycle_text = ax.text(0.0, CANVAS_H + 0.32, '', fontsize=10, ha='left', va='top', family='monospace')
status_text = ax.text(NUM_COLS, CANVAS_H + 0.32, '', fontsize=10, ha='right', va='top', family='monospace')

cycle_count = 0


def _paint(box, label, token):
    if token is None:
        box.set_facecolor('none')
        box.set_edgecolor('black')
        label.set_text('')
    else:
        box.set_facecolor(token.color)
        box.set_edgecolor('#2ecc71' if token.found else 'black')
        box.set_linewidth(3 if token.found else 1.6)
        text = getattr(token, 'label_override', token.label)
        label.set_text(('*' if token.found else '') + text)


def update(_frame):
    global cycle_count

    if pipeline_finished():
        return []

    advance_one_clock()
    cycle_count += 1

    _paint(*wide_boxes[COL_MIDSTATE], midstate_slot)
    _paint(*wide_boxes[COL_RESULTBUS], resultbus_slot)
    _paint(*wide_boxes[COL_DV], dv_slot)

    for row in range(NUM_LANES):
        for j in range(NUM_LANE_STAGES):
            box, label = lane_boxes[(row, j)]
            _paint(box, label, lane_slots[row][j])

    cycle_text.set_text(f"clock cycle: {cycle_count}")
    status = f"digests examined: {digests_examined}/{NUM_DIGESTS_TO_SHOW}  found: {drain.found_count}"
    if pipeline_finished():
        status += "  (finished)"
    status_text.set_text(status)

    return []


ani = FuncAnimation(fig, update, interval=1000 / CLOCKS_PER_SECOND, blit=False,
                     cache_frame_data=False)

plt.tight_layout()
plt.show()
