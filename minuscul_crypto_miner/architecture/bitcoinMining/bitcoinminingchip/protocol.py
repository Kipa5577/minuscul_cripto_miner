# USART framing constants of BitcoinMiningChip (see README.md for the full
# protocol). Shared by ChipControl (hardware side) and the testbenches (host
# side) so both always agree on the encoding.
#
# Frame: SOF(1) | CMD(1) | LEN(2, big-endian) | PAYLOAD(LEN) | CHK(1)
#        CHK = XOR over CMD, LEN and PAYLOAD bytes.

SOF = 0xAA

# Host -> Chip
CMD_NEW_JOB = 0x01
CMD_ABORT_JOB = 0x02
CMD_PING = 0x03
CMD_READ_REG = 0x04
CMD_WRITE_REG = 0x05

# Chip -> Host
RSP_JOB_ACK = 0x81
RSP_JOB_NACK = 0x82
RSP_RESULT = 0x83
RSP_PONG = 0x84
RSP_REG_VALUE = 0x85
RSP_WRITE_ACK = 0x86
RSP_REG_NACK = 0x87

NEW_JOB_LEN = 76  # header_first64bytes(64) | merkle_tail(4) | time(4) | bits(4)
MAX_PAYLOAD_LEN = NEW_JOB_LEN  # longest host->chip payload

# JOB_NACK reason codes
NACK_MALFORMED = 0x01  # wrong LEN for the command
NACK_BUSY = 0x02       # a job is still loading/running/draining
NACK_TIMEOUT = 0x03    # JOB_TIMEOUT expired before a result was found
NACK_NO_CORES = 0x04   # ACTIVE_CORES_MASK selects no instantiated core

# Register map
REG_CHIP_ID = 0x00
REG_STATUS = 0x01
REG_NUM_CORES = 0x02
REG_ACTIVE_CORES_MASK = 0x03
REG_CORE_BASE_OFFSET = 0x04
REG_JOB_TIMEOUT = 0x05
NUM_REGS = 6
RW_REGS = (REG_ACTIVE_CORES_MASK, REG_CORE_BASE_OFFSET, REG_JOB_TIMEOUT)

# STATUS register values
STATUS_IDLE = 0
STATUS_BUSY = 1
STATUS_RESULT_PENDING = 2
STATUS_ERROR = 3

CHIP_ID = 0xB7C0_0001  # "BitCOin" chip, version 1


def checksum(cmd, payload):
    chk = cmd ^ ((len(payload) >> 8) & 0xFF) ^ (len(payload) & 0xFF)
    for b in payload:
        chk ^= b
    return chk


def build_frame(cmd, payload=b''):
    payload = bytes(payload)
    return bytes([SOF, cmd, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF]) + payload + bytes([checksum(cmd, payload)])
