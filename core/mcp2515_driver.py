#!/usr/bin/env python3
"""
MCP2515 CAN Controller Driver - Purple Pi OH2 Version
SPI0 Bus, CS1 (Pin 26) -> /dev/spidev0.1

This is the driver from the Elephantronics Purple Pi OH2 Industrial docs,
unchanged. Auto-probes /dev/spidev0.1 (CS1, your documented wiring) then
falls back to /dev/spidev0.0 if not found.
"""

import spidev
import time

# -- SPI Commands --------------------------------------------------------------
MCP2515_RESET       = 0xC0
MCP2515_READ        = 0x03
MCP2515_WRITE       = 0x02
MCP2515_RTS         = 0x80
MCP2515_BIT_MODIFY  = 0x05
MCP2515_READ_STATUS = 0xA0
MCP2515_RX_STATUS   = 0xB0

# -- Registers ------------------------------------------------------------------
CANCTRL  = 0x0F
CANSTAT  = 0x0E
TEC      = 0x1C          # Transmit Error Counter
REC      = 0x1D          # Receive Error Counter
CNF1     = 0x2A
CNF2     = 0x29
CNF3     = 0x28
CANINTE  = 0x2B
CANINTF  = 0x2C
EFLG     = 0x2D

TXB0CTRL = 0x30
TXB0SIDH = 0x31
TXB0SIDL = 0x32
TXB0EID8 = 0x33
TXB0EID0 = 0x34
TXB0DLC  = 0x35
TXB0DATA = 0x36

# TX buffer 2 (used exclusively for health-check transmits so it never
# conflicts with user-facing send_message() which defaults to buffer 0)
TXB2CTRL = 0x50
TXB2SIDH = 0x51
TXB2SIDL = 0x52
TXB2EID8 = 0x53
TXB2EID0 = 0x54
TXB2DLC  = 0x55
TXB2DATA = 0x56

RXB0CTRL = 0x60
RXB0SIDH = 0x61
RXB0SIDL = 0x62
RXB0EID8 = 0x63
RXB0EID0 = 0x64
RXB0DLC  = 0x65
RXB0DATA = 0x66

RXB1CTRL = 0x70
RXB1SIDH = 0x71
RXB1SIDL = 0x72
RXB1EID8 = 0x73
RXB1EID0 = 0x74
RXB1DLC  = 0x75
RXB1DATA = 0x76

# -- Operating Modes -------------------------------------------------------------
MODE_NORMAL     = 0x00
MODE_SLEEP      = 0x20
MODE_LOOPBACK   = 0x40
MODE_LISTENONLY = 0x60
MODE_CONFIG     = 0x80

# -- Bitrate Tables (exact values from Arduino mcp2515.h) ------------------------
CAN_SPEED_8MHZ = {
    1000000: [0x00, 0x80, 0x80],
    500000:  [0x00, 0x90, 0x82],
    250000:  [0x00, 0xB1, 0x85],
    200000:  [0x00, 0xB4, 0x86],
    125000:  [0x01, 0xB1, 0x85],
    100000:  [0x01, 0xB4, 0x86],
    80000:   [0x01, 0xBF, 0x87],
    50000:   [0x03, 0xB4, 0x86],
    40000:   [0x03, 0xBF, 0x87],
    33333:   [0x47, 0xE2, 0x85],
    31250:   [0x07, 0xA4, 0x84],
    20000:   [0x07, 0xBF, 0x87],
    10000:   [0x0F, 0xBF, 0x87],
    5000:    [0x1F, 0xBF, 0x87],
}

CAN_SPEED_16MHZ = {
    1000000: [0x00, 0xD0, 0x82],
    500000:  [0x00, 0xF0, 0x86],
    250000:  [0x41, 0xF1, 0x85],
    200000:  [0x01, 0xFA, 0x87],
    125000:  [0x03, 0xF0, 0x86],
    100000:  [0x03, 0xFA, 0x87],
    95000:   [0x03, 0xAD, 0x07],
    83333:   [0x03, 0xBE, 0x07],
    80000:   [0x03, 0xFF, 0x87],
    50000:   [0x07, 0xFA, 0x87],
    40000:   [0x07, 0xFF, 0x87],
    33333:   [0x4E, 0xF1, 0x85],
    20000:   [0x0F, 0xFF, 0x87],
    10000:   [0x1F, 0xFF, 0x87],
    5000:    [0x3F, 0xFF, 0x87],
}

CAN_SPEED_20MHZ = {
    1000000: [0x00, 0xD9, 0x82],
    500000:  [0x00, 0xFA, 0x87],
    250000:  [0x41, 0xFB, 0x86],
    200000:  [0x01, 0xFF, 0x87],
    125000:  [0x03, 0xFA, 0x87],
    100000:  [0x04, 0xFA, 0x87],
    83333:   [0x04, 0xFE, 0x87],
    80000:   [0x04, 0xFF, 0x87],
    50000:   [0x09, 0xFA, 0x87],
    40000:   [0x09, 0xFF, 0x87],
    33333:   [0x0B, 0xFF, 0x87],
}


class CANMessage:
    """CAN Message container."""

    def __init__(self, can_id=0, data=None, dlc=0, extended=False, rtr=False):
        self.can_id   = can_id
        self.data     = data if data else []
        self.dlc      = dlc if dlc else len(self.data)
        self.extended = extended
        self.rtr      = rtr

    def __repr__(self):
        if self.rtr:
            return f"ID: 0x{self.can_id:03X}  RTR  DLC: {self.dlc}"
        data_str = ' '.join(f'0x{b:02X}' for b in self.data[:self.dlc])
        ext = " [EXT]" if self.extended else ""
        return f"ID: 0x{self.can_id:03X}{ext}  DLC: {self.dlc}  Data: [{data_str}]"


class MCP2515:
    """
    MCP2515 CAN controller driver for Purple Pi OH2.
    Default: bus=0, device=None -> auto-probe /dev/spidev0.1 then /dev/spidev0.0
    """

    def __init__(self, spi_bus=0, spi_device=None, spi_speed=1_000_000, crystal=8_000_000):
        if crystal == 16_000_000:
            self.speed_table  = CAN_SPEED_16MHZ
            self.crystal_name = "16 MHz"
        elif crystal == 20_000_000:
            self.speed_table  = CAN_SPEED_20MHZ
            self.crystal_name = "20 MHz"
        else:
            self.speed_table  = CAN_SPEED_8MHZ
            self.crystal_name = "8 MHz"
            if crystal != 8_000_000:
                print(f"⚠️  Unknown crystal {crystal/1e6} MHz -> using 8 MHz table")

        if spi_device is None:
            spi_device = self._find_spi_device(spi_bus, spi_speed)

        self.spi = spidev.SpiDev()
        self.spi.open(spi_bus, spi_device)
        self.spi.max_speed_hz = spi_speed
        self.spi.mode = 0b00
        self.spi.lsbfirst = False

        print(f"🔧 MCP2515  /dev/spidev{spi_bus}.{spi_device}"
              f"  @{spi_speed//1000} kHz  crystal={self.crystal_name}")

    @staticmethod
    def _probe_spi_device(spi_bus, spi_device, spi_speed):
        probe = spidev.SpiDev()
        try:
            probe.open(spi_bus, spi_device)
            probe.max_speed_hz = spi_speed
            probe.mode = 0b00
            probe.lsbfirst = False
            probe.xfer2([MCP2515_RESET])
            time.sleep(0.01)
            status = probe.xfer2([MCP2515_READ, CANSTAT, 0x00])[2]
            ctrl = probe.xfer2([MCP2515_READ, CANCTRL, 0x00])[2]
            got = status & 0xE0
            print(f"  🔍 probe /dev/spidev{spi_bus}.{spi_device}: CANSTAT=0x{status:02X} (mode=0x{got:02X}), CANCTRL=0x{ctrl:02X}")
            return got == MODE_CONFIG
        except Exception as exc:
            print(f"  ⚠️  probe /dev/spidev{spi_bus}.{spi_device} failed: {exc}")
            return False
        finally:
            try:
                probe.close()
            except Exception:
                pass

    @staticmethod
    def _find_spi_device(spi_bus, spi_speed):
        for spi_device in (1, 0):
            if MCP2515._probe_spi_device(spi_bus, spi_device, spi_speed):
                print(f"  ✅ Found MCP2515 on /dev/spidev{spi_bus}.{spi_device}")
                return spi_device
        raise RuntimeError(f"No MCP2515 found on /dev/spidev{spi_bus}.0 or /dev/spidev{spi_bus}.1")

    def reset(self):
        self.spi.xfer2([MCP2515_RESET])
        time.sleep(0.01)

    def read_register(self, addr):
        return self.spi.xfer2([MCP2515_READ, addr, 0x00])[2]

    def read_registers(self, addr, count):
        return self.spi.xfer2([MCP2515_READ, addr] + [0x00] * count)[2:]

    def write_register(self, addr, value):
        self.spi.xfer2([MCP2515_WRITE, addr, value])

    def write_registers(self, addr, values):
        self.spi.xfer2([MCP2515_WRITE, addr] + list(values))

    def modify_register(self, addr, mask, value):
        self.spi.xfer2([MCP2515_BIT_MODIFY, addr, mask, value])

    def set_mode(self, mode):
        _NAMES = {
            MODE_NORMAL:     "NORMAL",
            MODE_SLEEP:      "SLEEP",
            MODE_LOOPBACK:   "LOOPBACK",
            MODE_LISTENONLY: "LISTEN-ONLY",
            MODE_CONFIG:     "CONFIG",
        }
        self.modify_register(CANCTRL, 0xE0, mode)
        time.sleep(0.01)
        got = self.read_register(CANSTAT) & 0xE0
        if got == mode:
            print(f"   ✅ Mode -> {_NAMES.get(mode, f'0x{mode:02X}')}")
            return True
        print(f"   ❌ Mode change FAILED  expected=0x{mode:02X}  got=0x{got:02X}")
        return False

    def set_bitrate(self, bitrate):
        if bitrate not in self.speed_table:
            closest = min(self.speed_table, key=lambda x: abs(x - bitrate))
            print(f"⚠️  {bitrate} bps not in table -> using closest {closest} bps")
            bitrate = closest

        cfg = self.speed_table[bitrate]

        if not self.set_mode(MODE_CONFIG):
            print("❌ Could not enter CONFIG mode")
            return False

        self.write_register(CNF1, cfg[0])
        self.write_register(CNF2, cfg[1])
        self.write_register(CNF3, cfg[2])

        c1, c2, c3 = (self.read_register(r) for r in (CNF1, CNF2, CNF3))
        if c1 == cfg[0] and c2 == cfg[1] and c3 == cfg[2]:
            print(f"   ✅ Bitrate {bitrate} bps  CNF=[0x{c1:02X}, 0x{c2:02X}, 0x{c3:02X}]")
            return True

        print(f"   ⚠️  CNF verify fail  expected=[0x{cfg[0]:02X},0x{cfg[1]:02X},0x{cfg[2]:02X}]"
              f"  got=[0x{c1:02X},0x{c2:02X},0x{c3:02X}]")
        return False

    def init(self, bitrate=125_000, mode=MODE_NORMAL, loopback=False):
        sep = "─" * 52
        print(f"\n{sep}")
        print("  MCP2515 init — Purple Pi OH2")
        print(sep)

        self.reset()
        print("✅ Reset")

        if not self.set_bitrate(bitrate):
            return False

        # RXM=11 on both buffers -> mask/filters off, accept ANY valid frame
        # (standard or extended). BUKT=1 on RXB0 enables rollover to RXB1
        # when RXB0 is full, so nothing gets silently dropped under load.
        self.write_register(RXB0CTRL, 0x64)
        self.write_register(RXB1CTRL, 0x60)

        self.write_register(CANINTE, 0x03)
        self.write_register(CANINTF, 0x00)

        final_mode = MODE_LOOPBACK if loopback else mode
        if loopback:
            print("🔄 Loopback mode (self-test)")

        ok = self.set_mode(final_mode)
        print(f"{sep}")
        print(f"  {'✅ Init complete' if ok else '❌ Init FAILED'}")
        print(f"{sep}\n")
        return ok

    def send_message(self, msg, txbuf=0):
        _TX = [
            (TXB0CTRL, TXB0SIDH, TXB0DLC, TXB0DATA),
            (0x40,     0x41,     0x45,    0x46),
            (0x50,     0x51,     0x55,    0x56),
        ]
        txbuf = max(0, min(txbuf, 2))
        ctrl, sidh, dlc_reg, data_reg = _TX[txbuf]

        if self.read_register(ctrl) & 0x08:
            return False

        if msg.extended:
            self.write_register(sidh,     (msg.can_id >> 21) & 0xFF)
            self.write_register(sidh + 1, ((msg.can_id >> 13) & 0xE0) | 0x08 |
                                           ((msg.can_id >> 16) & 0x03))
            self.write_register(sidh + 2, (msg.can_id >> 8)  & 0xFF)
            self.write_register(sidh + 3,  msg.can_id        & 0xFF)
        else:
            self.write_register(sidh,     (msg.can_id >> 3)  & 0xFF)
            self.write_register(sidh + 1, (msg.can_id << 5)  & 0xE0)

        dlc_val = (msg.dlc & 0x0F) | (0x40 if msg.rtr else 0x00)
        self.write_register(dlc_reg, dlc_val)

        if not msg.rtr:
            for i in range(min(msg.dlc, 8)):
                self.write_register(data_reg + i, msg.data[i])

        self.spi.xfer2([MCP2515_RTS | (1 << txbuf)])
        return True

    def available(self):
        intf = self.read_register(CANINTF)
        if intf & 0x01:
            return 1
        if intf & 0x02:
            return 2
        return 0

    def read_message(self, rxbuf=None):
        if rxbuf is None:
            rxbuf = self.available()
            if rxbuf == 0:
                return None

        if rxbuf == 1:
            sidh_r, dlc_r, data_r, flag_bit = RXB0SIDH, RXB0DLC, RXB0DATA, 0x01
        else:
            sidh_r, dlc_r, data_r, flag_bit = RXB1SIDH, RXB1DLC, RXB1DATA, 0x02

        sidh     = self.read_register(sidh_r)
        sidl     = self.read_register(sidh_r + 1)
        eid8     = self.read_register(sidh_r + 2)
        eid0     = self.read_register(sidh_r + 3)
        dlc_byte = self.read_register(dlc_r)

        extended = bool(sidl & 0x08)
        rtr      = bool(dlc_byte & 0x40)
        dlc      = dlc_byte & 0x0F

        if extended:
            can_id = (((sidh & 0xFF) << 21) |
                      ((sidl & 0xE0) << 13) |
                      ((sidl & 0x03) << 16) |
                       (eid8  << 8)         |
                        eid0)
        else:
            can_id = (sidh << 3) | (sidl >> 5)

        data = []
        if not rtr:
            for i in range(min(dlc, 8)):
                data.append(self.read_register(data_r + i))

        self.modify_register(CANINTF, flag_bit, 0x00)
        return CANMessage(can_id, data, dlc, extended, rtr)

    def get_error_flags(self):
        return self.read_register(EFLG)

    def get_tec(self):
        """Read Transmit Error Counter."""
        return self.read_register(TEC)

    def get_rec(self):
        """Read Receive Error Counter."""
        return self.read_register(REC)

    def check_tx_result(self, txbuf=0):
        """Returns 'pending', 'success', or 'error' for a TX buffer.
        After send_message()+RTS the MCP2515 handles transmission
        autonomously — poll this until it stops returning 'pending'."""
        addrs = [TXB0CTRL, 0x40, TXB2CTRL]
        status = self.read_register(addrs[max(0, min(txbuf, 2))])
        if status & 0x08:    # TXREQ — still transmitting
            return 'pending'
        if status & 0x10:    # TXERR — transmission failed (no ACK)
            return 'error'
        return 'success'

    def abort_tx(self, txbuf=0):
        """Abort a pending transmission by clearing the TXREQ bit.
        Safe to call even if the buffer is already free."""
        addrs = [TXB0CTRL, 0x40, TXB2CTRL]
        ctrl = addrs[max(0, min(txbuf, 2))]
        self.spi.xfer2([MCP2515_BIT_MODIFY, ctrl, 0x08, 0x00])

    def clear_rx_overflow(self):
        self.modify_register(EFLG, 0xC0, 0x00)

    def get_status(self):
        return self.spi.xfer2([MCP2515_READ_STATUS, 0x00])[1]

    def close(self):
        self.spi.close()
        print("🔌 SPI closed")