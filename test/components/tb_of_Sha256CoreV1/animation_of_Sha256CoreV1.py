import itertools

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.patches import FancyBboxPatch
import py4hw

from minuscul_crypto_miner.architecture.Sha256Crcuits import *
from minuscul_crypto_miner.architecture.bus.bus import BusInterface
from sha256_core_v1_test_doubles import Sender, Collector


# --- Simulation speed -----------------------------------------------------
# One simulated clock edge is advanced per animation frame
CLOCKS_PER_SECOND = 30

L1_WORDS_PER_CYCLE = 2 # 1 2
l2_ROUNDS_PER_CYCLE = 4 # 1 2 4

NUM_SEEDS = 30

STAGE_NAMES = [
    "InputBuffer",
    "InputFormatter",
    "L1_Handler",
    "L1_res_buffer",
    "l2_Handler",
    "Output Buffer",
]
NUM_STAGES = len(STAGE_NAMES)

_TOKEN_COLORS = itertools.cycle([
    "#2ecc71",  # green
    "#7b1e2b",  # dark red
    "#f6a8c9",  # pink
    "#3498db",  # blue
    "#e67e22",  # orange
    "#9b59b6",  # purple
    "#16a085",  # teal
    "#f1c40f",  # gold
])


class Token:
    """One seed's calculation as it rides through the pipeline."""

    def __init__(self, seed, nbbytes):
        self.seed = seed
        self.nbbytes = nbbytes
        self.color = next(_TOKEN_COLORS)

    @property
    def label(self):
        return f"{self.seed:#x}"

    @property
    def text_color(self):
        """Black or white label text, whichever contrasts better against
        this token's fill color (simple relative-luminance threshold)."""
        r, g, b = (int(self.color[i:i + 2], 16) for i in (1, 3, 5))
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        return '#1b1b1b' if luminance > 150 else 'white'


# --- Build the real Sha256CoreV1 simulation --------------------------------
sys = py4hw.HWSystem()

reset = sys.wire('reset', 1)
py4hw.Constant(sys, 'reset', 0, reset)

bus_if = BusInterface(sys, 'bus_if')

seed_out = sys.wire('seed_out', 512)
nbbytes_out = sys.wire('nbbytes_out', 8)
digest_out = sys.wire('digest_out', 256)
digest_ready = sys.wire('digest_ready', 1)
digest_fetched = sys.wire('digest_fetched', 1)

engine = Sha256CoreV1(sys, 'sha256_engine', reset, bus_if,seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched,
                       debug=False,l2_rounds_per_cycle=l2_ROUNDS_PER_CYCLE,l1_words_per_cycle=L1_WORDS_PER_CYCLE)

seeds = [(i, max(1, (i.bit_length() + 7) // 8)) for i in range(1, NUM_SEEDS + 1)]

snd = Sender(sys, 'snd', bus_if, seeds, debug=False)
col = Collector(sys, 'col', seed_out, nbbytes_out, digest_out, digest_ready, digest_fetched)

sim = sys.getSimulator()

# One Token (or None) per pipeline stage.
slots = [None] * NUM_STAGES

inbuf = engine.children['inbuf']
inp = engine.children['inp']
l1 = engine.children['L1']
l1buf = engine.children['L1buf']
l2 = engine.children['L2']
outbuf = engine.children['outbuf']

prev_busy = [False] * NUM_STAGES


def _stage_busy():
    """Whether each real component currently has a token assigned to it,
    read straight off its own state -- the same state each component's
    clock() method itself branches on."""
    return [
        inbuf.state == 1,   # holding a seed captured from the bus
        inp.state == 1,     # presenting a padded block to L1_Handler
        l1.state != 0,      # expanding the message schedule
        l1buf.pending,      # holding a schedule l2_Handler hasn't fully drained yet
        l2.state != 0,      # running compression rounds
        outbuf.state == 1,  # holding a finished digest, waiting to be fetched
    ]


def advance_one_clock():
    """Steps the real simulation by one clock and slides Tokens between
    stages on the rising edge of each stage's own busy state -- i.e. the
    exact cycle each real component starts working on something new."""

    pending_seed_idx = snd.idx  # snapshot before clk(): Sender may advance idx during this tick
    sim.clk(1)

    busy_now = _stage_busy()

    # Process stages from the *end* of the pipeline backwards:
    for i in reversed(range(NUM_STAGES)):
        if busy_now[i] and not prev_busy[i]:
            if i == 0:
                if pending_seed_idx < len(seeds):
                    seed_val, nbbytes = seeds[pending_seed_idx]
                    slots[0] = Token(seed_val, nbbytes)
            else:
                slots[i] = slots[i - 1]
                slots[i - 1] = None
        elif not busy_now[i] and prev_busy[i] and i == NUM_STAGES - 1:
            # Output Buffer just went idle: its digest was fetched, leaving the pipeline.
            slots[i] = None

    prev_busy[:] = busy_now


def pipeline_finished():
    return snd.idx >= len(seeds) and all(s is None for s in slots) and len(col.captured) >= NUM_SEEDS


# --- Drawing ----------------------------------------------------------------
BG_COLOR = "#f4f6f8"
STAGE_FILL = "#e8edf2"
STAGE_EDGE = "#2c3e50"
SEPARATOR_COLOR = "#b0b8c1"
IDLE_TOKEN_EDGE = "#c7ced6"

fig, ax = plt.subplots(figsize=(2.1 * NUM_STAGES, 4.2))
fig.patch.set_facecolor(BG_COLOR)
ax.set_facecolor(BG_COLOR)
ax.set_xlim(0, NUM_STAGES)
ax.set_ylim(0, 2.6)
ax.axis('off')
fig.suptitle("Sha256CoreV1 pipeline", fontsize=16, fontweight='bold',
             color=STAGE_EDGE, family='sans-serif')

STAGE_BOX_Y = 1.35
STAGE_BOX_H = 1.05
TOKEN_BOX_Y = 0.15
TOKEN_BOX_H = 1.05
BOX_MARGIN = 0.12
ROUNDING = 0.06

stage_boxes = []
token_boxes = []
token_labels = []

for i, name in enumerate(STAGE_NAMES):
    x = i + BOX_MARGIN
    w = 1 - 2 * BOX_MARGIN

    ax.text(i + 0.5, STAGE_BOX_Y + STAGE_BOX_H + 0.12, name,
            ha='center', va='bottom', fontsize=10.5, fontweight='bold', color=STAGE_EDGE)

    stage_box = FancyBboxPatch((x, STAGE_BOX_Y), w, STAGE_BOX_H,
                                boxstyle=f"round,pad=0,rounding_size={ROUNDING}",
                                facecolor=STAGE_FILL, edgecolor=STAGE_EDGE,
                                linewidth=1.8, zorder=2)
    ax.add_patch(stage_box)
    stage_boxes.append(stage_box)

    token_box = FancyBboxPatch((x, TOKEN_BOX_Y), w, TOKEN_BOX_H,
                                boxstyle=f"round,pad=0,rounding_size={ROUNDING}",
                                facecolor='white', edgecolor=IDLE_TOKEN_EDGE,
                                linewidth=1.4, linestyle=(0, (4, 3)), zorder=2)
    ax.add_patch(token_box)
    token_boxes.append(token_box)

    label = ax.text(i + 0.5, TOKEN_BOX_Y + TOKEN_BOX_H / 2, '', ha='center', va='center',
                     fontsize=10, fontweight='bold', color='white', zorder=3)
    token_labels.append(label)

    if i > 0:
        # Dashed separator between this stage and the previous one.
        ax.plot([i, i], [0.05, STAGE_BOX_Y + STAGE_BOX_H + 0.28],
                linestyle='--', color=SEPARATOR_COLOR, linewidth=1, zorder=1)

# A single line tracing the *order* of operations through the stages
order_y = STAGE_BOX_Y + STAGE_BOX_H + 0.3
ax.annotate('', xy=(NUM_STAGES - 0.05, order_y + 0.15), xytext=(0.05, order_y + 0.15),
            arrowprops=dict(arrowstyle='-|>', color='#7f8c9a', linewidth=2))

cycle_text = ax.text(0.0, 2.55, '', fontsize=10.5, ha='left', va='top',
                      family='monospace', color=STAGE_EDGE, fontweight='bold')
done_text = ax.text(NUM_STAGES, 2.55, '', fontsize=10.5, ha='right', va='top',
                     family='monospace', color=STAGE_EDGE, fontweight='bold')

cycle_count = 0


def update(_frame):
    global cycle_count

    if pipeline_finished():
        return stage_boxes + token_boxes + token_labels + [cycle_text, done_text]

    advance_one_clock()
    cycle_count += 1

    for i in range(NUM_STAGES):
        token = slots[i]
        box = token_boxes[i]
        label = token_labels[i]
        if token is None:
            box.set_facecolor('white')
            box.set_edgecolor(IDLE_TOKEN_EDGE)
            box.set_linestyle((0, (4, 3)))
            label.set_text('')
        else:
            box.set_facecolor(token.color)
            box.set_edgecolor(STAGE_EDGE)
            box.set_linestyle('solid')
            label.set_color(token.text_color)
            label.set_text(token.label)

    cycle_text.set_text(f"clock cycle: {cycle_count}")
    done_text.set_text(f"digests done: {len(col.captured)}/{NUM_SEEDS}")

    if pipeline_finished():
        done_text.set_text(f"digests done: {len(col.captured)}/{NUM_SEEDS}  (finished)")

    return stage_boxes + token_boxes + token_labels + [cycle_text, done_text]


ani = FuncAnimation(fig, update, interval=1000 / CLOCKS_PER_SECOND, blit=False,
                     cache_frame_data=False)

plt.tight_layout()
plt.show()
