import itertools

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.patches import Rectangle
import py4hw

from minuscul_crypto_miner.architecture.Sha256Crcuits import *
from minuscul_crypto_miner.architecture.bus.bus import Bus, BusInterface, ResultBus
from minuscul_crypto_miner.architecture.seedGenerator.seedGenerator import seedGenerator
from minuscul_crypto_miner.architecture.difficultyValidator.DifficultyValidator import DifficultyValidator


# --- Simulation speed -------------------------------------------------------
# One simulated clock edge is advanced per animation frame; this controls
# how many of those frames are drawn per real second.
CLOCKS_PER_SECOND = 12

NUM_ENGINES = 10          # number of Sha256CoreV1 instances -- change freely
NUM_DIGESTS_TO_SHOW = 40  # stop once this many digests have reached DifficultyValidator
TARGET = (1 << 256) // 16  # lenient threshold so a few "found blocks" show up during the run

ENGINE_STAGE_NAMES = [
    "InputBuffer",
    "InputFormatter",
    "L1_Handler",
    "L1_res_buffer",
    "L2_Handler",
    "OutputBuffer",
]
NUM_ENGINE_STAGES = len(ENGINE_STAGE_NAMES)

_TOKEN_COLORS = itertools.cycle([
    "#2ecc71", "#7b1e2b", "#f6a8c9", "#3498db",
    "#e67e22", "#9b59b6", "#16a085", "#f1c40f",
])


class Token:
    """One seed's calculation as it rides through the full pipeline."""

    def __init__(self, seed, nbbytes):
        self.seed = seed
        self.nbbytes = nbbytes
        self.color = next(_TOKEN_COLORS)
        self.found = False  # set True once DifficultyValidator confirms it passed Target

    @property
    def label(self):
        return f"{self.seed:#x}"


# --- Build the real simulation ----------------------------------------------
sys_ = py4hw.HWSystem()

reset = sys_.wire('reset', 1)
py4hw.Constant(sys_, 'reset', 0, reset)

master_if = BusInterface(sys_, 'master_if')
gen = seedGenerator(sys_, 'gen', reset, master_if, debug=False)

slave_ifs = [BusInterface(sys_, f'slave_if_{i}') for i in range(NUM_ENGINES)]
bus = Bus(sys_, 'bus', master_if, slave_ifs, debug=False)

engines = []
engine_outputs = []
for i in range(NUM_ENGINES):
    seed_out = sys_.wire(f'engine_{i}_seed_out', 512)
    nbbytes_out = sys_.wire(f'engine_{i}_nbbytes_out', 8)
    digest_out = sys_.wire(f'engine_{i}_digest_out', 256)
    digest_ready = sys_.wire(f'engine_{i}_digest_ready', 1)
    digest_fetched = sys_.wire(f'engine_{i}_digest_fetched', 1)

    engine = Sha256CoreV1(sys_, f'sha256_engine_{i}', reset, slave_ifs[i],
                           seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched,
                           debug=False)
    engines.append(engine)
    engine_outputs.append((seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched))

merged_seed_out = sys_.wire('merged_seed_out', 512)
merged_nbbytes_out = sys_.wire('merged_nbbytes_out', 8)
merged_digest_out = sys_.wire('merged_digest_out', 256)
merged_digest_ready = sys_.wire('merged_digest_ready', 1)
merged_digest_fetched = sys_.wire('merged_digest_fetched', 1)

result_bus = ResultBus(sys_, 'result_bus', engine_outputs,
                        merged_seed_out, merged_nbbytes_out, merged_digest_out,
                        merged_digest_ready, merged_digest_fetched,
                        debug=False)

target_wire = sys_.wire('target', 256)
py4hw.Constant(sys_, 'target', TARGET, target_wire)

result_ready = sys_.wire('result_ready', 1)
found_seed_out = sys_.wire('found_seed_out', 512)
found_nbbytes_out = sys_.wire('found_nbbytes_out', 8)
found_digest_out = sys_.wire('found_digest_out', 256)
result_fetched = sys_.wire('result_fetched', 1)

validator = DifficultyValidator(sys_, 'validator', reset, target_wire,
                                 merged_digest_out, merged_digest_ready, merged_digest_fetched,
                                 merged_seed_out, merged_nbbytes_out,
                                 result_ready, found_seed_out, found_nbbytes_out, found_digest_out, result_fetched,
                                 debug=False)


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
# single-engine animation: a token moves into a slot the instant that
# component's own state shows it just started working on something new
# (a rising edge of "busy"), read straight off each component's own .state
# -- never a re-implementation of the handshake logic itself.
gen_slot = None
bus_slot = None
engine_slots = [[None] * NUM_ENGINE_STAGES for _ in range(NUM_ENGINES)]
resultbus_slot = None
dv_slot = None
dv_expire_pending = False

_prev_gen_seq = None
_prev_bus_busy = False
_prev_engine_busy = [[False] * NUM_ENGINE_STAGES for _ in range(NUM_ENGINES)]
_prev_resultbus_busy = False


def _engine_stage_busy(engine):
    inbuf = engine.children['inbuf']
    inp = engine.children['inp']
    l1 = engine.children['L1']
    l1buf = engine.children['L1buf']
    l2 = engine.children['L2']
    outbuf = engine.children['outbuf']
    return [
        inbuf.state == 1,
        inp.state == 1,
        l1.state != 0,
        l1buf.pending,
        l2.state != 0,
        outbuf.state == 1,
    ]


def advance_one_clock():
    global gen_slot, bus_slot, resultbus_slot, dv_slot, dv_expire_pending
    global _prev_gen_seq, _prev_bus_busy, _prev_resultbus_busy
    global digests_examined

    sim.clk(1)

    # --- DifficultyValidator: resolve whatever it was holding from a
    # previous tick first (a rejected token's one-frame display expires; a
    # found token that just got fetched by the drain is counted), *then*
    # check for a newly examined digest -- same "resolve old before
    # accepting new" ordering used throughout, just spelled out explicitly
    # here because DV's pass/reject branches aren't symmetric.
    if dv_expire_pending:
        dv_slot = None
        dv_expire_pending = False
    if dv_slot is not None and not dv_slot.found and validator.state == 2:
        dv_slot.found = True  # just confirmed a pass; stop treating it as a one-frame flash
    if dv_slot is not None and dv_slot.found and validator.state == 0:
        dv_slot = None  # the drain fetched it

    if merged_digest_fetched.get() == 1:
        digests_examined += 1
        dv_slot = resultbus_slot
        resultbus_slot = None
        if validator.state != 2:
            dv_expire_pending = True  # rejected: show for exactly one frame

    # --- ResultBus: N engines -> 1 stream
    rb_busy = result_bus.state == 1
    if rb_busy and not _prev_resultbus_busy:
        idx = result_bus.selected
        resultbus_slot = engine_slots[idx][NUM_ENGINE_STAGES - 1]
        engine_slots[idx][NUM_ENGINE_STAGES - 1] = None
    _prev_resultbus_busy = rb_busy

    # --- Each engine's own 6-stage pipeline, high-to-low per engine.
    for eng_idx, engine in enumerate(engines):
        busy_now = _engine_stage_busy(engine)
        prev_busy = _prev_engine_busy[eng_idx]
        slots = engine_slots[eng_idx]

        for i in reversed(range(NUM_ENGINE_STAGES)):
            if busy_now[i] and not prev_busy[i]:
                if i == 0:
                    if bus.selected_slave == eng_idx and bus_slot is not None:
                        slots[0] = bus_slot
                        bus_slot = None
                else:
                    slots[i] = slots[i - 1]
                    slots[i - 1] = None

        _prev_engine_busy[eng_idx] = busy_now

    # --- Bus: 1 stream -> N engines
    bus_busy = bus.state == 1
    if bus_busy and not _prev_bus_busy:
        bus_slot = gen_slot
        gen_slot = None
    _prev_bus_busy = bus_busy

    # --- seedGenerator: creates a token once it has a genuinely new value
    # ready. It no longer cycles through a separate "holding" state (that
    # gap was removed to cut delivery latency), so state alone can't signal
    # "a new value" -- Seq flips exactly once per new value instead.
    if gen.state == 1 and gen.seq != _prev_gen_seq:
        gen_slot = Token(gen.seedVal, gen.NBbytes)
    _prev_gen_seq = gen.seq


def pipeline_finished():
    return digests_examined >= NUM_DIGESTS_TO_SHOW


# --- Drawing ------------------------------------------------------------------
COL_GEN = 0
COL_BUS = 1
COL_ENGINE_START = 2
COL_RESULTBUS = COL_ENGINE_START + NUM_ENGINE_STAGES
COL_DV = COL_RESULTBUS + 1
NUM_COLS = COL_DV + 1

ROW_H = 1.6
ROWS_TOP_MARGIN = 0.9
TOTAL_ROWS_H = NUM_ENGINES * ROW_H
CANVAS_H = TOTAL_ROWS_H + ROWS_TOP_MARGIN

BOX_MARGIN_X = 0.10
BOX_MARGIN_Y = 0.12

fig, ax = plt.subplots(figsize=(1.9 * NUM_COLS, max(4.5, 1.6 * NUM_ENGINES + 1.6)))
ax.set_xlim(0, NUM_COLS)
ax.set_ylim(0, CANVAS_H + 0.35)
ax.axis('off')
fig.suptitle(f"Full mining pipeline ({NUM_ENGINES} x Sha256CoreV1)", fontsize=14, fontweight='bold')

wide_boxes = {}     # col -> (stage_box, token_box, token_label)
engine_boxes = {}   # (row, col_offset) -> (stage_box, token_box, token_label)


def _make_box(x, y, w, h, label=None, label_y=None):
    stage_box = Rectangle((x + BOX_MARGIN_X, y + BOX_MARGIN_Y), w - 2 * BOX_MARGIN_X, h - 2 * BOX_MARGIN_Y,
                           facecolor='white', edgecolor='black', linewidth=1.6, zorder=2)
    ax.add_patch(stage_box)
    token_label = ax.text(x + w / 2, y + h / 2, '', ha='center', va='center',
                           fontsize=8, fontweight='bold', zorder=3)
    if label is not None:
        ax.text(x + w / 2, label_y, label, ha='center', va='bottom', fontsize=9, rotation=0)
    return stage_box, token_label


# Wide (single-instance) boxes spanning the full row height: seedGenerator, Bus, ResultBus, DifficultyValidator.
for col, name in [(COL_GEN, "seedGenerator"), (COL_BUS, "Bus"),
                   (COL_RESULTBUS, "ResultBus"), (COL_DV, "DifficultyValidator")]:
    stage_box, token_label = _make_box(col, 0, 1, TOTAL_ROWS_H, label=name, label_y=TOTAL_ROWS_H + 0.15)
    wide_boxes[col] = (stage_box, token_label)

# Per-engine stage boxes, one row per engine.
for row in range(NUM_ENGINES):
    y = row * ROW_H
    ax.text(COL_ENGINE_START - 0.05, y + ROW_H / 2, f"engine {row}",
             ha='right', va='center', fontsize=9, rotation=90)
    for j, name in enumerate(ENGINE_STAGE_NAMES):
        col = COL_ENGINE_START + j
        label = name if row == NUM_ENGINES - 1 else None
        stage_box, token_label = _make_box(col, y, 1, ROW_H,
                                            label=label, label_y=TOTAL_ROWS_H + 0.15)
        engine_boxes[(row, j)] = (stage_box, token_label)
    # Dotted delimiter between engine rows.
    if row > 0:
        ax.plot([COL_ENGINE_START, COL_RESULTBUS], [y, y], linestyle=':', color='black', linewidth=1.0, zorder=1)

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
        label.set_text(('*' if token.found else '') + token.label)


def update(_frame):
    global cycle_count

    if pipeline_finished():
        return []

    advance_one_clock()
    cycle_count += 1

    _paint(*wide_boxes[COL_GEN], gen_slot)
    _paint(*wide_boxes[COL_BUS], bus_slot)
    _paint(*wide_boxes[COL_RESULTBUS], resultbus_slot)
    _paint(*wide_boxes[COL_DV], dv_slot)

    for row in range(NUM_ENGINES):
        for j in range(NUM_ENGINE_STAGES):
            box, label = engine_boxes[(row, j)]
            _paint(box, label, engine_slots[row][j])

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
