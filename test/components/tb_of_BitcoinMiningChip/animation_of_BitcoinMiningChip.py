import colorsys
import itertools
from collections import deque

import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.animation import FuncAnimation
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Rectangle
import py4hw

from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip import BitcoinMiningChip
from minuscul_crypto_miner.architecture.bitcoinMining.bitcoinminingchip import protocol as P
from bitcoin_mining_chip_test_doubles import HostUart, make_header, CMD_NAMES, EASY_NBITS, HARD_NBITS


# Live floorplan of BitcoinMiningChip: the die with its UART pins, the real
# blocks (UsartRx/UsartTx, ChipControl with its job FSM, parser and registers,
# the shared Sha256Engine, NBitsDecoder, every core's two Sha256Compressor
# stages (message schedule computed on the fly) and its own difficulty validator, the hit-only arbiter tree) and
# the buses between them, lit when active. Each nonce attempt is a coloured token moving through the stages;
# a hit that meets the target gets a gold outline all the way back to Control.

# --- Simulation speed -------------------------------------------------------
# Frames drawn per real second. While a job is loaded one simulated clock edge
# is advanced per frame; while only the UART is busy (frames crawling over the
# line) UART_CLOCKS_PER_FRAME edges are advanced per frame instead.
CLOCKS_PER_SECOND = 1000
UART_CLOCKS_PER_FRAME = 40

NUM_CORES = 4              # 1..8 fit comfortably on the floorplan
CORES_PER_ARBITER = 2      # cores grouped under each first-level (hits-only) arbiter
CLKS_PER_BIT = 4
ROUNDS_PER_CYCLE = 4       # Sha256Compressor rounds per cycle: 1 2 4 8
# Bitcoin shortcuts are on (chip default): rounds 0-2 of the second header
# block are precomputed once per job, finisher blocks whose top word already
# fails the target are dropped at round 61 (shown as "✗61"), and the target
# is compared little-endian, as Bitcoin does.
ABORT_AFTER_CYCLES = 300   # how long the hard (never-passing) job runs before the host aborts it
WAVE_CYCLES = 900          # cycles of UART line history shown in the waveform strip

PARTIAL_MASK = 0b0101 if NUM_CORES >= 3 else 0b01   # job 2 runs on a subset of the cores
PARTIAL_BASE = 1000


def _reg_write(addr, value):
    return P.build_frame(P.CMD_WRITE_REG, [addr] + list(value.to_bytes(4, 'big')))


# Host session played over the UART, one step at a time:
#   ('send', frame, label) | ('wait_frames', n) | ('wait_cycles', n) | ('wait_idle',)
HOST_SCRIPT = [
    ('send', P.build_frame(P.CMD_PING), "PING"),
    ('wait_frames', 1),
    ('send', P.build_frame(P.CMD_NEW_JOB, make_header(7, EASY_NBITS)[:76]), "NEW_JOB #1 (easy)"),
    ('wait_frames', 2),
    ('send', _reg_write(P.REG_ACTIVE_CORES_MASK, PARTIAL_MASK), f"WRITE MASK={PARTIAL_MASK:#06b}"),
    ('wait_frames', 1),
    ('send', _reg_write(P.REG_CORE_BASE_OFFSET, PARTIAL_BASE), f"WRITE BASE={PARTIAL_BASE}"),
    ('wait_frames', 1),
    ('send', P.build_frame(P.CMD_NEW_JOB, make_header(11, EASY_NBITS)[:76]), "NEW_JOB #2 (easy)"),
    ('wait_frames', 2),
    ('send', _reg_write(P.REG_ACTIVE_CORES_MASK, (1 << NUM_CORES) - 1), "WRITE MASK=all"),
    ('wait_frames', 1),
    ('send', P.build_frame(P.CMD_NEW_JOB, make_header(13, HARD_NBITS)[:76]), "NEW_JOB #3 (hard)"),
    ('wait_frames', 1),
    ('wait_cycles', ABORT_AFTER_CYCLES),
    ('send', P.build_frame(P.CMD_ABORT_JOB), "ABORT_JOB"),
    ('wait_idle',),
    ('send', P.build_frame(P.CMD_READ_REG, [P.REG_STATUS]), "READ_REG STATUS"),
    ('wait_frames', 1),
]


# Stages inside one core (pipeline='fused'), the way the hardware is built:
# BitcoinMiningCore = nonce iterator -> SHA-256 #1 (Sha256Compressor, H0 = midstate)
#                     -> SHA-256 #2 (Sha256Compressor, fixed 32-byte padding)
#                     -> DifficultyValidator (only hits leave the core)
# Each compressor has two slots: the block in its round loop, and the
# finished digest waiting in its output register.
CORE_STAGE_SHORT = ["iter", "rounds", "H out", "rounds", "H out"]
NUM_CORE_STAGES = len(CORE_STAGE_SHORT)
S_ITER, S_R1, S_OUT1, S_R2, S_OUT2 = range(NUM_CORE_STAGES)

CONTROL_STATES = [(0, "IDLE"), (1, "LOAD"), (2, "RUN"), (3, "DRAIN")]
PARSER_PILLS = [("SOF", (0,)), ("CMD", (1,)), ("LEN", (2, 3)), ("DATA", (4,)), ("CHK", (5,))]


# --- Palette (dark "silicon" theme) -----------------------------------------
BG = '#0b1220'
DIE = '#131b2a'
DIE_EDGE = '#4a5a78'
BLOCK = '#1b2536'
BLOCK_EDGE = '#56688a'
SHA1_TINT = '#17304a'
SHA2_TINT = '#2a2142'
CELL_EMPTY = '#0f1622'
CELL_EDGE = '#34425c'
TEXT = '#dbe4f0'
DIM = '#71809b'
FAINT = '#2b3850'
C_JOB = '#f39c12'       # job broadcast bus
C_MID = '#2ecc71'       # midstate bus
C_RES = '#f1c40f'       # winning result path
C_UART = '#4fc3f7'      # UART / byte streams
C_CORE = '#3498db'      # core mining
STATE_COLORS = {0: '#7f8c8d', 1: C_JOB, 2: C_CORE, 3: '#9b59b6'}


def _generate_token_colors(n):
    """n well-spread colors readable on a dark background: hues stepped by
    the golden ratio, alternating two lightness levels."""
    colors = []
    hue = 0.0
    for i in range(n):
        hue = (hue + 0.6180339887) % 1.0
        lightness = 0.58 if i % 2 == 0 else 0.70
        r, g, b = colorsys.hls_to_rgb(hue, lightness, 0.70)
        colors.append('#{:02x}{:02x}{:02x}'.format(int(r * 255), int(g * 255), int(b * 255)))
    return colors


_TOKEN_COLORS = itertools.cycle(_generate_token_colors(100))


class Token:
    """One nonce attempt's calculation as it rides through the chip."""

    def __init__(self, nonce):
        self.nonce = nonce
        self.color = next(_TOKEN_COLORS)
        self.found = False  # set True once a DifficultyValidator confirms it passed the target
        self.early_rejected = False  # finisher dropped it at round 61

    @property
    def label(self):
        if self.early_rejected:
            return "✗61"
        return getattr(self, 'label_override', f"{self.nonce:#x}")


# --- Build the real simulation ----------------------------------------------
sys_ = py4hw.HWSystem()

reset = sys_.wire('reset', 1)
py4hw.Constant(sys_, 'reset', 0, reset)
host_to_chip = sys_.wire('host_to_chip', 1)
chip_to_host = sys_.wire('chip_to_host', 1)

host = HostUart(sys_, 'host', host_to_chip, chip_to_host, CLKS_PER_BIT)
chip = BitcoinMiningChip(sys_, 'chip', reset, host_to_chip, chip_to_host,
                         num_cores=NUM_CORES, cores_per_arbiter=CORES_PER_ARBITER,
                         clks_per_bit=CLKS_PER_BIT, pipeline='fused',
                         l2_rounds_per_cycle=ROUNDS_PER_CYCLE, debug=False)

sim = sys_.getSimulator()

control = chip.children['control']
usart_rx = chip.children['usart_rx']
usart_tx = chip.children['usart_tx']
midstate_controller = chip.children['midstate_engine'].children['controller']
precompute = chip.children['precompute']
nbits_decoder = chip.children['nbits_decoder']
cores = [chip.children[f'core_{i}'] for i in range(NUM_CORES)]
NUM_GROUPS = (NUM_CORES + CORES_PER_ARBITER - 1) // CORES_PER_ARBITER
arbiters = [chip.children[f'arbiter_{g}'] for g in range(NUM_GROUPS)]
core_validators = [core.children['validator'] for core in cores]
top_arbiter = chip.children['top_arbiter']


# --- Host session --------------------------------------------------------------
host_log = []   # (direction, text) of the last few frames each way
script_idx = 0
wait_until = None
frames_seen = 0
frames_at_send = 0   # host.frames length when the last 'send' step ran


def _log(direction, text):
    host_log.append((direction, text))
    del host_log[:-16]


def run_host_script():
    """Advance HOST_SCRIPT as far as it can go this cycle."""
    global script_idx, wait_until, frames_seen, frames_at_send

    while len(host.frames) > frames_seen:
        cmd, payload, _ = host.frames[frames_seen]
        detail = ""
        if cmd == P.RSP_RESULT:
            detail = f" nonce={int.from_bytes(payload[0:4], 'big'):#x}"
        elif cmd == P.RSP_REG_VALUE:
            detail = f" value={int.from_bytes(payload[1:5], 'big')}"
        elif cmd in (P.RSP_JOB_NACK, P.RSP_WRITE_ACK, P.RSP_REG_NACK):
            detail = f" {payload.hex()}"
        _log('in', f"{CMD_NAMES.get(cmd, hex(cmd))}{detail}")
        frames_seen += 1

    while script_idx < len(HOST_SCRIPT):
        step = HOST_SCRIPT[script_idx]
        match step[0]:
            case 'send':
                host.send(step[1])
                _log('out', step[2])
                frames_at_send = len(host.frames)
            case 'wait_frames':
                if len(host.frames) < frames_at_send + step[1]:
                    return
            case 'wait_cycles':
                if wait_until is None:
                    wait_until = cycle_count + step[1]
                if cycle_count < wait_until:
                    return
                wait_until = None
            case 'wait_idle':
                if host.sending() or control.state != 0:
                    return
        script_idx += 1


def host_script_finished():
    return script_idx >= len(HOST_SCRIPT)


# --- Token tracking ----------------------------------------------------------
# One slot per real component instance: a token moves into a slot the instant
# that component's own state shows it just started working on something new
# (a rising edge of "busy"), read straight off each component's .state --
# never a re-implementation of the handshake logic itself.
midstate_slot = None
core_slots = [[None] * NUM_CORE_STAGES for _ in range(NUM_CORES)]
validator_slots = [None] * NUM_CORES      # each core's own DifficultyValidator
validator_expire = [False] * NUM_CORES
arbiter_slots = [None] * NUM_GROUPS
top_slot = None
control_slot = None     # the hit Control fetched (gold) or a late hit being discarded
control_expire = False

_prev_midstate_busy = False
_prev_out_full = [(False, False) for _ in range(NUM_CORES)]
_prev_core_nonce = [None] * NUM_CORES
_prev_core_active = [False] * NUM_CORES
_prev_arbiter_busy = [False] * NUM_GROUPS
_prev_top_busy = False

digests_examined = 0
results_found = 0
cycle_count = 0
running_cycles = 0
wave_rx = deque([1] * WAVE_CYCLES, maxlen=WAVE_CYCLES)
wave_tx = deque([1] * WAVE_CYCLES, maxlen=WAVE_CYCLES)


def advance_one_clock():
    global midstate_slot, top_slot, control_slot, control_expire
    global _prev_midstate_busy, _prev_top_busy
    global digests_examined, results_found, cycle_count, running_cycles

    sim.clk(1)
    cycle_count += 1
    if control.state == 2:
        running_cycles += 1
    wave_rx.append(host_to_chip.get())
    wave_tx.append(chip_to_host.get())
    run_host_script()

    # --- Control: a hit it fetched stays (gold) until the job is back to
    # idle; a late hit discarded while draining flashes for one frame.
    if control_expire:
        control_slot = None
        control_expire = False
    if control_slot is not None and control_slot.found and control.state == 0:
        control_slot = None
    if top_arbiter.Digest_Fetched.get() == 1:
        control_slot = top_slot
        top_slot = None
        # Control only keeps the hit if it just queued it as the job's RESULT;
        # anything fetched while draining is a discarded late hit.
        pending = control.pending_frame
        if control_slot is not None and pending is not None and pending[0] == P.RSP_RESULT \
                and control_slot.nonce == int.from_bytes(bytes(pending[1][0:4]), 'big'):
            results_found += 1
        else:
            if control_slot is not None:
                control_slot.found = False
            control_expire = True

    # --- Top arbiter: hits from every group arbiter -> Control
    top_busy = top_arbiter.state == 1
    if top_busy and not _prev_top_busy:
        top_slot = arbiter_slots[top_arbiter.selected]
        arbiter_slots[top_arbiter.selected] = None
    _prev_top_busy = top_busy

    # --- Per-group arbiter: its cores' validators (hits only) -> 1 stream
    for g in range(NUM_GROUPS):
        busy = arbiters[g].state == 1
        if busy and not _prev_arbiter_busy[g]:
            idx = g * CORES_PER_ARBITER + arbiters[g].selected
            arbiter_slots[g] = validator_slots[idx]
            validator_slots[idx] = None
        _prev_arbiter_busy[g] = busy

    # --- Per-core DifficultyValidator: a rejected digest flashes for one
    # frame, a passing one (validator state 1) stays until its arbiter takes it.
    for i, validator in enumerate(core_validators):
        if validator_expire[i]:
            validator_slots[i] = None
            validator_expire[i] = False
        if validator.Digest_Fetched.get() == 1:
            digests_examined += 1
            validator_slots[i] = core_slots[i][NUM_CORE_STAGES - 1]
            core_slots[i][NUM_CORE_STAGES - 1] = None
            if validator_slots[i] is not None and validator.state == 1:
                validator_slots[i].found = True
            else:
                validator_expire[i] = True

    # --- Each core's compressors, high-to-low so a token moves at most one
    # slot per cycle: a block moves into a round loop the cycle the compressor
    # latches it (block_consumed pulse), and into the output register the
    # cycle that register fills (a compressor can do both in one cycle).
    for core_idx, core in enumerate(cores):
        slots = core_slots[core_idx]
        sha1, sha2 = core.children['sha1'], core.children['sha2']
        nonce_iter = core.children['nonce_iter']
        prev1, prev2 = _prev_out_full[core_idx]

        if sha2.out_full and not prev2:
            slots[S_OUT2], slots[S_R2] = slots[S_R2], None
            if slots[S_OUT2] is not None and sha2.last_early_reject:
                slots[S_OUT2].early_rejected = True
        if sha2.block_consumed.get() == 1:
            slots[S_R2], slots[S_OUT1] = slots[S_OUT1], None
        if sha1.out_full and not prev1:
            slots[S_OUT1], slots[S_R1] = slots[S_R1], None
        if sha1.block_consumed.get() == 1:
            slots[S_R1], slots[S_ITER] = slots[S_ITER], None
        _prev_out_full[core_idx] = (sha1.out_full, sha2.out_full)

        # A new token appears whenever the presented nonce changes, or the
        # iterator (re)starts a job; it disappears when Control stops the core.
        active = nonce_iter.state == 1
        if active and (nonce_iter.nonce != _prev_core_nonce[core_idx] or not _prev_core_active[core_idx]):
            slots[S_ITER] = Token(nonce_iter.nonce)
        if not active:
            slots[S_ITER] = None

        _prev_core_nonce[core_idx] = nonce_iter.nonce
        _prev_core_active[core_idx] = active

    # --- Sha256Engine: computes the shared midstate once per job, then
    # stays "cached" (green) until the next NEW_JOB reloads it.
    midstate_busy = midstate_controller.state not in (0, 4)
    if midstate_busy and not _prev_midstate_busy:
        midstate_slot = Token(0)
        midstate_slot.label_override = "computing"
    if midstate_controller.state == 4 and midstate_slot is not None and not midstate_slot.found:
        midstate_slot.found = True
        midstate_slot.label_override = "cached"
    _prev_midstate_busy = midstate_busy


def job_loaded():
    return control.state in (1, 2, 3)


def animation_finished():
    return host_script_finished() and not host.sending() and control.state == 0 and not control.tx_queue


# --- Floorplan geometry (data units, equal aspect) ---------------------------
W, H = 120, 60
DIE_X0, DIE_X1, DIE_Y0, DIE_Y1 = 17.0, 119.5, 1.0, 57.0
CORE_Y0, CORE_Y1 = 3.2, 52.5          # vertical band shared by cores / buses
RX_BOX = (19.0, 35.5, 11.0, 11.0)     # x, y, w, h
TX_BOX = (19.0, 6.5, 11.0, 11.0)
CTRL_BOX = (32.0, 4.0, 13.0, 50.5)
JOB_BUS_X = 47.0
ENGINE_BOX = (49.0, 41.0, 9.5, 11.5)
PRECOMP_BOX = (49.0, 30.0, 9.5, 8.8)
NBITS_BOX = (49.0, 4.0, 9.5, 8.5)
LEGEND_BOX = (49.0, 13.8, 9.5, 14.4)
MID_BUS_X = 60.0
CORE_X0, CORE_X1 = 62.0, 107.6
ARB_X, TOP_X, TREE_W = 108.8, 114.2, 4.8
RX_PIN_Y = RX_BOX[1] + RX_BOX[3] / 2
TX_PIN_Y = TX_BOX[1] + TX_BOX[3] / 2
RESULT_PATH_Y = 1.9

ROW_H = (CORE_Y1 - CORE_Y0) / NUM_CORES


def core_row(i):
    """(y0, y1) of core i's outline, core 0 at the top."""
    y1 = CORE_Y1 - i * ROW_H
    return y1 - ROW_H + 0.35, y1 - 0.35


fig = plt.figure(figsize=(18, 10.4), facecolor=BG)
ax = fig.add_axes([0.005, 0.155, 0.99, 0.79])
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.set_aspect('equal')
ax.axis('off')
ax.set_facecolor(BG)

fig.text(0.5, 0.975, "Bitcoin mining chip — live floorplan", ha='center', va='top',
         fontsize=17, fontweight='bold', color=TEXT)
fig.text(0.5, 0.947, f"{NUM_CORES} cores · {ROUNDS_PER_CYCLE} rounds/cycle · rounds 0–2 precomputed · "
                     f"early reject at round 61 · Bitcoin byte order",
         ha='center', va='top', fontsize=10, color=DIM)

GLOW = lambda color: [pe.withStroke(linewidth=7, foreground=color, alpha=0.25), pe.Normal()]


def rbox(x, y, w, h, fc=BLOCK, ec=BLOCK_EDGE, lw=1.2, r=0.6, z=2, alpha=1.0):
    patch = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                           facecolor=fc, edgecolor=ec, linewidth=lw, zorder=z, alpha=alpha)
    ax.add_patch(patch)
    return patch


def label(x, y, text, size=8, color=TEXT, weight='normal', ha='left', va='center', family=None, z=6):
    return ax.text(x, y, text, fontsize=size, color=color, fontweight=weight, ha=ha, va=va,
                   family=family, zorder=z)


def wire(xs, ys, color=FAINT, lw=1.6, z=1):
    line, = ax.plot(xs, ys, color=color, linewidth=lw, solid_capstyle='round', zorder=z)
    return line


def arrow(x0, y0, x1, y1, color=FAINT, lw=1.6, z=1):
    a = FancyArrowPatch((x0, y0), (x1, y1), arrowstyle='-|>', mutation_scale=9,
                        color=color, linewidth=lw, zorder=z)
    ax.add_patch(a)
    return a


def set_lit(artist, on, color, base=FAINT, lw_on=2.4, lw_off=1.6):
    artist.set_color(color if on else base)
    artist.set_linewidth(lw_on if on else lw_off)
    artist.set_path_effects(GLOW(color) if on else [])


class Cell:
    """A pipeline-stage cell: shows its stage name when empty, the token's nonce when busy."""

    def __init__(self, x, y, w, h, stage, size=5.6, progress=False):
        self.patch = rbox(x, y, w, h, fc=CELL_EMPTY, ec=CELL_EDGE, lw=0.9, r=0.35, z=4)
        self.text = label(x + w / 2, y + h / 2, stage, size=size, color=DIM, ha='center', family='monospace')
        self.stage = stage
        self.bar = None
        if progress:
            # round-loop progress, 0..64 rounds, along the bottom of the cell
            self.bar_x, self.bar_w = x + 0.35, w - 0.7
            ax.add_patch(Rectangle((self.bar_x, y + 0.3), self.bar_w, 0.45, facecolor=BG, alpha=0.35, zorder=5))
            self.bar = Rectangle((self.bar_x, y + 0.3), 0, 0.45, facecolor='#0b1220', zorder=5.5)
            ax.add_patch(self.bar)

    def set_progress(self, frac):
        if self.bar is not None:
            self.bar.set_width(self.bar_w * frac)

    def paint(self, token, dim=False):
        if token is None:
            self.patch.set_facecolor(CELL_EMPTY)
            self.patch.set_edgecolor(CELL_EDGE)
            self.patch.set_linewidth(0.9)
            self.patch.set_path_effects([])
            self.text.set_text(self.stage)
            self.text.set_color(FAINT if dim else DIM)
            self.text.set_fontweight('normal')
        else:
            self.patch.set_facecolor(token.color)
            self.patch.set_edgecolor(C_RES if token.found else BG)
            self.patch.set_linewidth(2.2 if token.found else 0.9)
            self.patch.set_path_effects(GLOW(C_RES) if token.found else [])
            self.text.set_text(token.label)
            self.text.set_color('#0b1220')
            self.text.set_fontweight('bold')


# --- Die, host and pins ------------------------------------------------------
rbox(DIE_X0, DIE_Y0, DIE_X1 - DIE_X0, DIE_Y1 - DIE_Y0, fc=DIE, ec=DIE_EDGE, lw=2.2, r=1.4, z=0)
label(DIE_X0 + 1.2, DIE_Y1 - 1.3, "BitcoinMiningChip", size=10, weight='bold')

rbox(0.3, 3.0, 14.4, 51.5, fc='#101a2b', ec='#3a4a66', lw=1.4, r=0.9, z=1)
label(7.5, 52.6, "HOST", size=10, weight='bold', ha='center')
label(7.5, 50.9, "UART frames", size=7, color=DIM, ha='center')
host_log_texts = [label(1.1, 48.9 - 2.6 * k, '', size=6.2, family='monospace', va='top') for k in range(16)]

for y, name in [(RX_PIN_Y, 'uart_rx'), (TX_PIN_Y, 'uart_tx')]:
    rbox(DIE_X0 - 0.7, y - 0.7, 1.4, 1.4, fc='#c9a227', ec='#806414', lw=1.0, r=0.15, z=5)
    label(DIE_X0 - 1.0, y + 1.6, name, size=7, color=DIM, ha='right')
uart_rx_pin_wire = arrow(14.7, RX_PIN_Y, DIE_X0 - 0.7, RX_PIN_Y)
uart_tx_pin_wire = arrow(DIE_X0 - 0.7, TX_PIN_Y, 14.7, TX_PIN_Y)
uart_rx_in_wire = arrow(DIE_X0 + 0.7, RX_PIN_Y, RX_BOX[0], RX_PIN_Y)
uart_tx_in_wire = arrow(TX_BOX[0], TX_PIN_Y, DIE_X0 + 0.7, TX_PIN_Y)

# --- USART -------------------------------------------------------------------
rx_patch = rbox(*RX_BOX)
label(RX_BOX[0] + RX_BOX[2] / 2, RX_BOX[1] + RX_BOX[3] - 1.3, "UsartRx", size=8.5, weight='bold', ha='center')
label(RX_BOX[0] + RX_BOX[2] / 2, RX_BOX[1] + RX_BOX[3] - 2.9, "8N1 deserialiser", size=6.2, color=DIM, ha='center')
rx_byte_text = label(RX_BOX[0] + RX_BOX[2] / 2, RX_BOX[1] + 4.2, '', size=12, weight='bold', ha='center', family='monospace')
rx_state_text = label(RX_BOX[0] + RX_BOX[2] / 2, RX_BOX[1] + 1.5, '', size=6.5, color=DIM, ha='center')

tx_patch = rbox(*TX_BOX)
label(TX_BOX[0] + TX_BOX[2] / 2, TX_BOX[1] + TX_BOX[3] - 1.3, "UsartTx", size=8.5, weight='bold', ha='center')
label(TX_BOX[0] + TX_BOX[2] / 2, TX_BOX[1] + TX_BOX[3] - 2.9, "8N1 serialiser", size=6.2, color=DIM, ha='center')
tx_byte_text = label(TX_BOX[0] + TX_BOX[2] / 2, TX_BOX[1] + 4.2, '', size=12, weight='bold', ha='center', family='monospace')
tx_state_text = label(TX_BOX[0] + TX_BOX[2] / 2, TX_BOX[1] + 1.5, '', size=6.5, color=DIM, ha='center')

rx_stream = arrow(RX_BOX[0] + RX_BOX[2], RX_PIN_Y, CTRL_BOX[0], RX_PIN_Y)
tx_stream = arrow(CTRL_BOX[0], TX_PIN_Y, TX_BOX[0] + TX_BOX[2], TX_PIN_Y)
label((RX_BOX[0] + RX_BOX[2] + CTRL_BOX[0]) / 2, RX_PIN_Y + 1.0, "bytes", size=5.8, color=DIM, ha='center')
label((TX_BOX[0] + TX_BOX[2] + CTRL_BOX[0]) / 2, TX_PIN_Y + 1.0, "bytes", size=5.8, color=DIM, ha='center')

# --- ChipControl -------------------------------------------------------------
cx0, cy0, cw, ch = CTRL_BOX
ctrl_patch = rbox(cx0, cy0, cw, ch, lw=1.6)
label(cx0 + cw / 2, cy0 + ch - 1.4, "ChipControl", size=9.5, weight='bold', ha='center')
label(cx0 + cw / 2, cy0 + ch - 3.0, "job state machine", size=6.2, color=DIM, ha='center')

fsm_cx, fsm_cy, fsm_r, node_r = cx0 + cw / 2, cy0 + ch - 11.2, 4.0, 1.75
fsm_pos = {0: (fsm_cx, fsm_cy + fsm_r), 1: (fsm_cx + fsm_r, fsm_cy),
           2: (fsm_cx, fsm_cy - fsm_r), 3: (fsm_cx - fsm_r, fsm_cy)}
for s in range(4):
    (x0, y0), (x1, y1) = fsm_pos[s], fsm_pos[(s + 1) % 4]
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle='-|>', mutation_scale=8,
                                 connectionstyle='arc3,rad=-0.28', color=DIM, linewidth=1.0,
                                 shrinkA=16, shrinkB=16, zorder=3))
fsm_nodes = {}
for s, name in CONTROL_STATES:
    x, y = fsm_pos[s]
    circ = Circle((x, y), node_r, facecolor=BLOCK, edgecolor=STATE_COLORS[s], linewidth=1.4, zorder=4)
    ax.add_patch(circ)
    txt = label(x, y, name, size=6.0, weight='bold', ha='center', color=STATE_COLORS[s])
    fsm_nodes[s] = (circ, txt)

label(cx0 + cw / 2, cy0 + ch - 19.0, "frame parser", size=6.2, color=DIM, ha='center')
pill_w = (cw - 1.2 - 0.25 * (len(PARSER_PILLS) - 1)) / len(PARSER_PILLS)
parser_pills = []
for k, (name, states) in enumerate(PARSER_PILLS):
    px = cx0 + 0.6 + k * (pill_w + 0.25)
    patch = rbox(px, cy0 + ch - 21.8, pill_w, 1.8, fc=CELL_EMPTY, ec=CELL_EDGE, lw=0.8, r=0.3, z=4)
    txt = label(px + pill_w / 2, cy0 + ch - 20.9, name, size=5.0, color=DIM, ha='center')
    parser_pills.append((patch, txt, states))

label(cx0 + cw / 2, cy0 + ch - 24.2, "registers", size=6.2, color=DIM, ha='center')
reg_texts = [label(cx0 + 0.9, cy0 + ch - 26.2 - 1.75 * k, '', size=6.3, family='monospace') for k in range(6)]

label(cx0 + cw / 2, cy0 + 10.6, "result latch", size=6.2, color=DIM, ha='center')
result_cell = Cell(cx0 + 1.2, cy0 + 4.3, cw - 2.4, 5.0, "—", size=8)
result_note = label(cx0 + cw / 2, cy0 + 2.3, '', size=6.3, ha='center', color=DIM)

# --- Job broadcast bus, Sha256Engine, NBitsDecoder ---------------------------
job_stub = wire([cx0 + cw, JOB_BUS_X], [cy0 + ch - 11.2] * 2)
job_bus = wire([JOB_BUS_X, JOB_BUS_X], [CORE_Y0, CORE_Y1])
label(JOB_BUS_X - 0.4, CORE_Y1 + 1.4, "job bus", size=6.5, color=C_JOB, ha='center')

ex, ey, ew, eh = ENGINE_BOX
engine_patch = rbox(ex, ey, ew, eh)
label(ex + ew / 2, ey + eh - 1.3, "Sha256Engine", size=7.5, weight='bold', ha='center')
label(ex + ew / 2, ey + eh - 2.8, "shared midstate", size=6.0, color=DIM, ha='center')
engine_cell = Cell(ex + 0.9, ey + 1.2, ew - 1.8, 4.6, "idle", size=6.5)
engine_in = wire([JOB_BUS_X, ex], [ey + eh - 5.5] * 2)
label(JOB_BUS_X + 0.4, ey + eh - 4.6, "header[0:64]", size=5.2, color=DIM)

nx, ny, nw, nh = NBITS_BOX
nbits_patch = rbox(nx, ny, nw, nh)
label(nx + nw / 2, ny + nh - 1.3, "NBitsDecoder", size=7.0, weight='bold', ha='center')
nbits_text = label(nx + nw / 2, ny + 3.6, '', size=7.5, ha='center', family='monospace')
label(nx + nw / 2, ny + 1.4, "target → validators", size=5.5, color=DIM, ha='center')
nbits_in = wire([JOB_BUS_X, nx], [ny + nh / 2] * 2)

mid_stub = wire([ex + ew, MID_BUS_X], [ey + 3.5] * 2)
mid_bus = wire([MID_BUS_X, MID_BUS_X], [CORE_Y0, ey + 3.5])
label(MID_BUS_X + 0.2, CORE_Y1 + 1.4, "midstate\n+ state₃", size=6.5, color=C_MID, ha='center', va='bottom')

px, py_, pw, ph = PRECOMP_BOX
precomp_patch = rbox(px, py_, pw, ph)
label(px + pw / 2, py_ + ph - 1.3, "Precompute", size=7.5, weight='bold', ha='center')
label(px + pw / 2, py_ + ph - 2.8, "rounds 0–2, once/job", size=5.6, color=DIM, ha='center')
precomp_text = label(px + pw / 2, py_ + 2.6, '', size=7.0, ha='center', family='monospace')
precomp_in = wire([ex + ew / 2, ex + ew / 2], [ey, py_ + ph])             # midstate from the engine
precomp_job = wire([JOB_BUS_X, px], [py_ + ph - 4.4] * 2)                  # merkle_tail / time / bits
precomp_out = wire([px + pw, MID_BUS_X], [py_ + 3.0] * 2)                   # state3 onto the bus

lx, ly, lw_, lh = LEGEND_BOX
rbox(lx, ly, lw_, lh, fc='#101827', ec=FAINT, lw=1.0, r=0.6, z=2)  # above the job-bus taps
label(lx + lw_ / 2, ly + lh - 1.3, "legend", size=7, weight='bold', ha='center', color=DIM)
legend_rows = [
    (C_JOB, "job fields, nonce\nranges, enables"),
    (C_MID, "midstate, state₃"),
    (C_CORE, "core mining"),
    (C_RES, "hit ≤ target"),
    (C_UART, "UART traffic"),
]
for k, (color, text) in enumerate(legend_rows):
    yy = ly + lh - 3.6 - 2.35 * k
    wire([lx + 0.7, lx + 2.2], [yy, yy], color=color, lw=2.6, z=3)
    label(lx + 2.6, yy, text, size=5.4, color=TEXT, va='center')

# --- Cores ---------------------------------------------------------------------
VAL_W, ITER_W = 4.0, 4.0
ITER_X0, ITER_X1 = CORE_X0 + 0.6, CORE_X0 + 0.6 + ITER_W
CVAL_X0, CVAL_X1 = CORE_X1 - 0.6 - VAL_W, CORE_X1 - 0.6
SHA_W = (CVAL_X0 - 0.5 - (ITER_X1 + 0.5) - 0.5) / 2
SHA1_X0, SHA1_X1 = ITER_X1 + 0.5, ITER_X1 + 0.5 + SHA_W
SHA2_X0, SHA2_X1 = SHA1_X1 + 0.5, SHA1_X1 + 0.5 + SHA_W
CELL_GAP = 0.3

core_views = []
for i in range(NUM_CORES):
    y0, y1 = core_row(i)
    outline = rbox(CORE_X0, y0, CORE_X1 - CORE_X0, y1 - y0, fc='#151f30', ec=BLOCK_EDGE, lw=1.4, r=0.7, z=2)
    title = label(CORE_X0 + 0.8, y1 - 1.25, f"core {i}", size=8, weight='bold')
    info = label(CORE_X0 + 6.2, y1 - 1.25, '', size=6.4, color=DIM, family='monospace')

    sy0, sy1 = y0 + 0.6, y1 - 2.6
    cell_y0, cell_h = sy0 + 0.5, (sy1 - sy0) - 2.4
    rbox(SHA1_X0, sy0, SHA1_X1 - SHA1_X0, sy1 - sy0, fc=SHA1_TINT, ec='#2d557f', lw=1.0, r=0.5, z=3)
    label(SHA1_X0 + 0.5, sy1 - 0.95, "SHA-256 #1 · Sha256Compressor · H₀ = midstate", size=5.6, color='#8fb6e0')
    rbox(SHA2_X0, sy0, SHA2_X1 - SHA2_X0, sy1 - sy0, fc=SHA2_TINT, ec='#5a4690', lw=1.0, r=0.5, z=3)
    label(SHA2_X0 + 0.5, sy1 - 0.95, "SHA-256 #2 · Sha256Compressor · fixed pad", size=5.6, color='#b9a6ea')
    rbox(ITER_X0, sy0, ITER_X1 - ITER_X0, sy1 - sy0, fc='#1a2a24', ec='#2f6b52', lw=1.0, r=0.5, z=3)
    label((ITER_X0 + ITER_X1) / 2, sy1 - 0.95, "nonce", size=5.6, color='#7fd1a8', ha='center')
    rbox(CVAL_X0, sy0, CVAL_X1 - CVAL_X0, sy1 - sy0, fc='#2e2a14', ec='#7d6a1c', lw=1.0, r=0.5, z=3)
    label((CVAL_X0 + CVAL_X1) / 2, sy1 - 0.95, "validator", size=5.6, color='#e3c75a', ha='center')

    cells = [Cell(ITER_X0 + 0.4, cell_y0, ITER_W - 0.8, cell_h, "iter")]
    for x_from in (SHA1_X0, SHA2_X0):
        round_w = (SHA_W - 0.8 - CELL_GAP) * 0.62
        out_w = (SHA_W - 0.8 - CELL_GAP) - round_w
        cells.append(Cell(x_from + 0.4, cell_y0, round_w, cell_h, "rounds", progress=True))
        cells.append(Cell(x_from + 0.4 + round_w + CELL_GAP, cell_y0, out_w, cell_h, "H out"))
    val_cell = Cell(CVAL_X0 + 0.4, cell_y0, CVAL_X1 - CVAL_X0 - 0.8, cell_h, "≤ T")

    mid_y = (y0 + y1) / 2
    job_tap = wire([JOB_BUS_X, CORE_X0], [y1 - 1.25] * 2)
    mid_tap = wire([MID_BUS_X, CORE_X0], [mid_y - 1.0] * 2)
    out_wire = wire([CORE_X1, ARB_X], [mid_y] * 2)
    core_views.append(dict(outline=outline, title=title, info=info, cells=cells, val_cell=val_cell,
                           job_tap=job_tap, mid_tap=mid_tap, out_wire=out_wire))

# --- Arbiter tree ----------------------------------------------------------------
group_views = []
for g in range(NUM_GROUPS):
    first = g * CORES_PER_ARBITER
    last = min(first + CORES_PER_ARBITER, NUM_CORES) - 1
    gy0, gy1 = core_row(last)[0], core_row(first)[1]
    gmid = (gy0 + gy1) / 2
    views = {}
    patch = rbox(ARB_X, gy0, TREE_W, gy1 - gy0, lw=1.2)
    label(ARB_X + TREE_W / 2, gy1 - 1.2, f"arbiter {g}", size=5.8, weight='bold', ha='center')
    label(ARB_X + TREE_W / 2, gy1 - 2.5, "hits only", size=5.2, color=DIM, ha='center')
    views['arb'] = (patch, Cell(ARB_X + 0.45, gmid - 2.4, TREE_W - 0.9, 4.8, "—"))
    views['up'] = wire([ARB_X + TREE_W, TOP_X], [gmid] * 2)
    group_views.append(views)

top_patch = rbox(TOP_X, CORE_Y0 + 0.35, TREE_W, CORE_Y1 - CORE_Y0 - 0.7, lw=1.2)
label(TOP_X + TREE_W / 2, CORE_Y1 - 1.6, "top\narbiter", size=5.8, weight='bold', ha='center')
top_cell = Cell(TOP_X + 0.45, (CORE_Y0 + CORE_Y1) / 2 - 2.4, TREE_W - 0.9, 4.8, "—")
result_path = wire([TOP_X + TREE_W / 2, TOP_X + TREE_W / 2, cx0 + cw / 2, cx0 + cw / 2],
                   [CORE_Y0 + 0.35, RESULT_PATH_Y, RESULT_PATH_Y, cy0])
label((TOP_X + cx0 + cw) / 2, RESULT_PATH_Y + 0.75, "winning nonce + digest → Control", size=5.8, color=DIM, ha='center')

# --- Header status line and UART waveform strip --------------------------------
status_text = fig.text(0.995, 0.947, '', ha='right', va='top', fontsize=9, color=TEXT, family='monospace')
cycle_text = fig.text(0.005, 0.947, '', ha='left', va='top', fontsize=9, color=TEXT, family='monospace')

wax = fig.add_axes([0.06, 0.025, 0.925, 0.115])
wax.set_facecolor('#0d1524')
for spine in wax.spines.values():
    spine.set_color(FAINT)
wax.tick_params(colors=DIM, labelsize=7)
wax.set_xlim(-WAVE_CYCLES, 0)
wax.set_ylim(-0.4, 3.4)
wax.set_yticks([0.5, 2.5])
wax.set_yticklabels(["uart_tx\nchip→host", "uart_rx\nhost→chip"], color=TEXT, fontsize=7)
wax.set_xlabel("clock cycles ago", color=DIM, fontsize=7, labelpad=1)
wave_x = list(range(-WAVE_CYCLES + 1, 1))
wave_rx_line, = wax.step(wave_x, [2 + v for v in wave_rx], where='post', color=C_UART, linewidth=1.1)
wave_tx_line, = wax.step(wave_x, list(wave_tx), where='post', color=C_RES, linewidth=1.1)


# --- Per-frame repaint -----------------------------------------------------------------
def paint():
    st = control.state
    mining = st == 2
    job_on = st in (1, 2, 3)
    midstate_ok = midstate_controller.state == 4 and job_on
    state3_ok = precompute.state == 2 and job_on

    # UART lines and endpoints
    rx_busy, tx_busy = usart_rx.state != 0, usart_tx.state != 0
    for a in (uart_rx_pin_wire, uart_rx_in_wire):
        set_lit(a, rx_busy, C_UART)
    for a in (uart_tx_pin_wire, uart_tx_in_wire):
        set_lit(a, tx_busy, C_UART)
    set_lit(rx_stream, control.rx.Valid.get() == 1 or control.p_state != 0, C_UART)
    set_lit(tx_stream, bool(control.tx_queue), C_UART)
    rx_patch.set_edgecolor(C_UART if rx_busy else BLOCK_EDGE)
    tx_patch.set_edgecolor(C_UART if tx_busy else BLOCK_EDGE)
    rx_byte_text.set_text(f"{usart_rx.data:#04x}")
    rx_state_text.set_text(["idle", "start bit", f"bit {usart_rx.bit}", "stop bit"][usart_rx.state])
    tx_byte_text.set_text(f"{usart_tx.data:#04x}")
    tx_state_text.set_text(f"{['idle', 'start bit', f'bit {usart_tx.bit}', 'stop bit'][usart_tx.state]} · "
                           f"{len(control.tx_queue)} B queued")

    # Control
    ctrl_patch.set_edgecolor(STATE_COLORS[st])
    ctrl_patch.set_path_effects(GLOW(STATE_COLORS[st]) if job_on else [])
    for s, (circ, txt) in fsm_nodes.items():
        active = s == st
        circ.set_facecolor(STATE_COLORS[s] if active else BLOCK)
        circ.set_path_effects(GLOW(STATE_COLORS[s]) if active else [])
        txt.set_color(BG if active else STATE_COLORS[s])
    for patch, txt, states in parser_pills:
        active = control.p_state in states
        patch.set_facecolor(C_UART if active else CELL_EMPTY)
        txt.set_color(BG if active else DIM)
    mask = control.regs[P.REG_ACTIVE_CORES_MASK] & ((1 << NUM_CORES) - 1)
    status = control._status()
    regs = [
        f"STATUS  {['idle', 'busy', 'result', 'error'][status]}",
        f"MASK    {mask:#0{NUM_CORES + 2}b}",
        f"BASE    {control.regs[P.REG_CORE_BASE_OFFSET]}",
        f"TIMEOUT {control.regs[P.REG_JOB_TIMEOUT]}",
        f"STRIDE  {control.stride if job_on else '—'}",
        f"CORES   {NUM_CORES}",
    ]
    for txt, value in zip(reg_texts, regs):
        txt.set_text(value)
    result_cell.paint(control_slot)
    if control_slot is None:
        result_note.set_text("")
    elif control_slot.found:
        result_note.set_text("RESULT frame queued")
        result_note.set_color(C_RES)
    else:
        result_note.set_text("late hit, discarded")
        result_note.set_color(DIM)

    # Job bus, engine, decoder, midstate bus
    for w_ in (job_stub, job_bus, engine_in, nbits_in):
        set_lit(w_, job_on, C_JOB)
    engine_cell.paint(midstate_slot if job_on or midstate_slot is not None else None)
    engine_patch.set_edgecolor(C_MID if midstate_ok else (C_JOB if st == 1 else BLOCK_EDGE))
    for w_ in (mid_stub, mid_bus, precomp_in):
        set_lit(w_, midstate_ok, C_MID)
    set_lit(precomp_job, job_on, C_JOB)
    set_lit(precomp_out, state3_ok, C_MID)
    precomp_patch.set_edgecolor(C_MID if state3_ok else (C_JOB if precompute.state == 1 else BLOCK_EDGE))
    precomp_text.set_text({0: "—", 1: f"round {precompute.t}/3", 2: "state₃ ✓"}[precompute.state])
    target = nbits_decoder.target.get()   # the decoder's own output (byte order handled there)
    if control.job_bits:
        nbits_text.set_text(f"T≈2^{target.bit_length() - 1}" if target else "T=0")
    else:
        nbits_text.set_text("—")

    # Cores
    for i, view in enumerate(core_views):
        in_job = job_on and i in control.active
        enabled = control.enables[i] == 1
        in_flight = len(cores[i].children['nonce_tracker'].nonce_fifo)
        if enabled:
            color = C_CORE
        elif in_flight:
            color = STATE_COLORS[3]
        else:
            color = BLOCK_EDGE
        view['outline'].set_edgecolor(color)
        view['outline'].set_path_effects(GLOW(color) if enabled else [])
        excluded = job_on and not in_job
        view['outline'].set_alpha(0.45 if excluded else 1.0)
        view['title'].set_color(FAINT if excluded else TEXT)
        if excluded:
            view['info'].set_text("not in ACTIVE_CORES_MASK")
        elif in_job:
            view['info'].set_text(f"nonce {control.starts[i]} + k·{control.stride}   in flight {in_flight}")
        else:
            view['info'].set_text(f"idle   in flight {in_flight}")
        for cell, token in zip(view['cells'], core_slots[i]):
            cell.paint(token, dim=excluded)
        for cell, sha in ((view['cells'][S_R1], cores[i].children['sha1']), (view['cells'][S_R2], cores[i].children['sha2'])):
            cell.set_progress(sha.t / 64 if sha.state != 0 else 0)
        view['val_cell'].paint(validator_slots[i], dim=excluded)
        set_lit(view['job_tap'], in_job, C_JOB)
        set_lit(view['mid_tap'], in_job and state3_ok, C_MID)

    # Arbiter tree (hits only)
    for g, views in enumerate(group_views):
        arb_patch, arb_cell = views['arb']
        arb_cell.paint(arbiter_slots[g])
        holding = arbiter_slots[g] is not None
        arb_patch.set_edgecolor(C_RES if holding else BLOCK_EDGE)
        set_lit(views['up'], holding, C_RES)
        for k in range(CORES_PER_ARBITER):
            i = g * CORES_PER_ARBITER + k
            if i < NUM_CORES:
                hit = validator_slots[i] is not None and validator_slots[i].found
                set_lit(core_views[i]['out_wire'], hit, C_RES)
    top_cell.paint(top_slot)
    top_patch.set_edgecolor(C_RES if top_slot is not None else BLOCK_EDGE)
    set_lit(result_path, top_slot is not None or (control_slot is not None and control_slot.found), C_RES)

    # Host log
    for k, txt in enumerate(host_log_texts):
        if k < len(host_log):
            direction, text = host_log[k]
            txt.set_text(("→ " if direction == 'out' else "← ") + text)
            txt.set_color(C_UART if direction == 'out' else C_RES)
        else:
            txt.set_text("")

    # Waveforms
    wave_rx_line.set_ydata([2 + v for v in wave_rx])
    wave_tx_line.set_ydata(list(wave_tx))

    # Header
    speed = "1 clk/frame" if job_on else f"{UART_CLOCKS_PER_FRAME} clk/frame (UART only)"
    cycle_text.set_text(f"cycle {cycle_count:>6}   [{speed}]")
    rate = f"{running_cycles / digests_examined:.1f} cyc/hash" if digests_examined else "—"
    done = "   finished" if animation_finished() else ""
    rejects = sum(core.children['sha2'].early_rejects for core in cores)
    status_text.set_text(f"hashes {digests_examined} ({rate})   early rejects {rejects}   results {results_found}{done}")


def update(_frame):
    if animation_finished():
        return []

    n = 1 if job_loaded() else UART_CLOCKS_PER_FRAME
    for _ in range(n):
        advance_one_clock()
        if animation_finished():
            break
    paint()
    return []


run_host_script()
paint()

ani = FuncAnimation(fig, update, interval=1000 / CLOCKS_PER_SECOND, blit=False,
                    cache_frame_data=False)

plt.show()
