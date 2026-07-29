#!/usr/bin/env python3
# core/modbus_tcp_server.py
# Modbus TCP server — exposes DI/DO/CAN state to external SCADA/HMI
# clients over Modbus TCP. Reads live from core/state.py and
# core/can_manager.py on every request — no cached copy.
#
# Uses raw asyncio + struct for Modbus TCP framing (6-byte MBAP header
# + PDU). Avoids pymodbus's deprecated SimDevice/SimData datastore API
# which has no callback support for live reads.

import asyncio
import logging
import struct
import threading

logger = logging.getLogger(__name__)


def _resolve_source(source_key: str, can_status: dict) -> int:
    """Resolve a source key to an integer register value."""
    if source_key is None:
        return 0
    if source_key == "can:status.rx_total":
        return int(can_status.get("rx_total", 0))
    elif source_key == "can:status.tx_total":
        return int(can_status.get("tx_total", 0))
    elif source_key == "can:status.errors":
        return int(can_status.get("errors", 0))
    elif source_key == "can:status.connected":
        return 1 if can_status.get("connected") else 0
    elif source_key == "can:status.uptime":
        return int(can_status.get("uptime", 0) or 0)
    return 0


class ModbusTCPServer:
    """Live-reading Modbus TCP server. Runs on its own thread using
    asyncio, non-blocking to the daemon's other threads.

    Lifecycle:
        server = ModbusTCPServer(io_manager, state, can_manager)
        server.load_register_map()
        server.start(host="0.0.0.0", port=5020)
        server.get_status()
        server.reload_register_map()
        server.stop()
    """

    def __init__(self, io_manager, state, can_manager, modbus_manager=None,
                 can_send_channels=None):
        self._io = io_manager
        self._state = state
        self._can = can_manager
        self._modbus = modbus_manager
        self._lock = threading.Lock()
        self._can_send_channels = can_send_channels or []
        self._can_send_lock = asyncio.Lock()
        self._can_stage = {}

        self.host = "0.0.0.0"
        self.port = 5020  # non-privileged (>1024), Modbus TCP convention
        self.running = False
        self._thread = None
        self._register_map = None
        self._loop = None
        self._server = None

        self.stats = {
            "client_count": 0,
            "exceptions": 0,
        }

    # ----------------------------------------------------------------
    # Register map
    # ----------------------------------------------------------------
    def load_register_map(self):
        from core.modbus_tcp_register_map import load_register_map as _load

        self._register_map = _load()
        logger.info(
            f"Modbus TCP: register map loaded ({len(self._register_map)} entries)"
        )

    def reload_register_map(self):
        """Reload the register map without dropping the TCP listener.
        Takes effect on the next client request."""
        with self._lock:
            self.load_register_map()

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------
    def start(self, host: str = "0.0.0.0", port: int = 5020):
        if self.running:
            raise RuntimeError("Modbus TCP server already running")

        self.host = host
        self.port = port
        self.running = True
        self._thread = threading.Thread(
            target=self._serve_loop, name="Modbus-TCP", daemon=True
        )
        self._thread.start()
        logger.info(f"Modbus TCP server started on {host}:{port}")

    def stop(self):
        with self._lock:
            if not self.running:
                return
            self.running = False

        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.close)

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        self._loop = None
        self._server = None

        logger.info("Modbus TCP server stopped")

    # ----------------------------------------------------------------
    # Internal — asyncio TCP server with manual Modbus framing
    # ----------------------------------------------------------------
    def _serve_loop(self):
        asyncio.run(self._serve_async())

    async def _serve_async(self):
        srv = self  # closure for handler
        self._loop = asyncio.get_running_loop()

        async def handle(reader, writer):
            addr = writer.get_extra_info("peername")
            logger.info(f"Modbus TCP: client connected {addr}")
            peername = addr[0] if addr else "?"

            srv.stats["client_count"] += 1
            try:
                while srv.running:
                    header = await asyncio.wait_for(reader.readexactly(7), timeout=60.0)
                    tid, pid, length, uid = struct.unpack(">HHHB", header)
                    pdu_len = length - 1
                    if pdu_len <= 0:
                        break
                    pdu = await asyncio.wait_for(
                        reader.readexactly(pdu_len), timeout=10.0
                    )
                    fc = pdu[0]

                    if fc == 1:  # Read Coils → DO state
                        addr, cnt = (
                            struct.unpack(">HH", pdu[1:5]) if len(pdu) >= 5 else (0, 1)
                        )
                        response = _build_bit_response(
                            tid, uid, fc, srv._state.get_do(), addr, cnt
                        )
                    elif fc == 2:  # Read Discrete Inputs → DI state
                        addr, cnt = (
                            struct.unpack(">HH", pdu[1:5]) if len(pdu) >= 5 else (0, 1)
                        )
                        response = _build_bit_response(
                            tid, uid, fc, srv._state.get_di(), addr, cnt
                        )
                    elif fc in (3, 4):  # Read Registers → CAN via map
                        addr, cnt = (
                            struct.unpack(">HH", pdu[1:5]) if len(pdu) >= 5 else (0, 1)
                        )
                        response = _build_register_response(
                            tid, uid, fc, srv, addr, cnt
                        )
                    elif fc == 5:  # Write Single Coil
                        response = await _handle_fc5_coil(pdu, tid, uid, srv)
                    elif fc == 6:  # Write Single Register
                        response = await _handle_fc6_write(pdu, tid, uid, srv)
                    elif fc == 15:  # Write Multiple Coils
                        response = _parse_fc15_write(pdu, tid, uid, srv)
                    elif fc == 16:  # Write Multiple Registers
                        response = await _handle_fc16_write(pdu, tid, uid, srv)
                    else:
                        # Unknown / unsupported function code
                        response = _build_exception(tid, uid, fc, 1)  # illegal function

                    writer.write(response)
                    await writer.drain()
            except (
                asyncio.IncompleteReadError,
                asyncio.TimeoutError,
                ConnectionResetError,
            ):
                pass  # client disconnected
            except Exception as e:
                srv.stats["exceptions"] += 1
                logger.warning(f"Modbus TCP handler error ({peername}): {e}")
            finally:
                srv.stats["client_count"] = max(0, srv.stats["client_count"] - 1)
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                logger.info(f"Modbus TCP: client disconnected {peername}")

        try:
            server = await asyncio.start_server(handle, self.host, self.port)
            self._server = server
            async with server:
                await server.serve_forever()
        except asyncio.CancelledError:
            pass  # stop() closed the server, expected
        except OSError as e:
            logger.error(f"Modbus TCP server bind failed: {e}")
            self.running = False

    # ----------------------------------------------------------------
    # Status
    # ----------------------------------------------------------------
    def get_status(self) -> dict:
        return {
            "running": self.running,
            "host": self.host,
            "port": self.port,
            "entries": len(self._register_map) if self._register_map else 0,
            "client_count": self.stats["client_count"],
            "exceptions": self.stats["exceptions"],
        }

    def get_register_map(self) -> list:
        if self._register_map is None:
            return []
        return [e.to_dict() for e in self._register_map]


# ═══════════════════════════════════════════════════════════════════
# Modbus response builders (FC 1/2/3/4)
# ═══════════════════════════════════════════════════════════════════


def _build_bit_response(tid, uid, fc, values, addr, count):
    """Build a read-coils or read-discrete-inputs response."""
    bits = []
    for i in range(count):
        ch = addr + i
        bits.append(values[ch] if 0 <= ch < len(values) else 0)

    byte_count = (len(bits) + 7) // 8
    data = bytearray(byte_count)
    for i, b in enumerate(bits):
        if b:
            data[i // 8] |= 1 << (i % 8)

    header = struct.pack(">HHHB", tid, 0, 3 + byte_count, uid)
    return header + struct.pack(">BB", fc, byte_count) + bytes(data)


def _build_register_response(tid, uid, fc, server, addr, count):
    """Build a read-registers response using the register map."""
    reg_map = server._register_map or []
    lookup = {e.address: e.source_key for e in reg_map if e.function_code in (3, 4)}
    try:
        can = server._can.get_status()
    except Exception:
        server.stats["exceptions"] += 1
        can = {}

    reg_data = bytearray()
    for i in range(count):
        source = lookup.get(addr + i)
        val = _resolve_source(source, can) & 0xFFFF
        reg_data.extend(struct.pack(">H", val))

    header = struct.pack(">HHHB", tid, 0, 3 + len(reg_data), uid)
    return header + struct.pack(">BB", fc, len(reg_data)) + bytes(reg_data)


def _build_exception(tid, uid, fc, code):
    """Build an exception response."""
    header = struct.pack(">HHHB", tid, 0, 3, uid)
    return header + struct.pack(">BB", fc | 0x80, code)


# ═══════════════════════════════════════════════════════════════════
# Write helpers (FC 5/15)
# ═══════════════════════════════════════════════════════════════════


def _parse_fc15_write(pdu: bytes, tid: int, uid: int, server) -> bytes:
    """Parse and execute an FC 15 (Write Multiple Coils) request.
    Returns the framed response bytes ready to send on the wire."""
    addr, cnt = struct.unpack(">HH", pdu[1:5]) if len(pdu) >= 5 else (0, 0)
    for i in range(cnt):
        byte_offset = i // 8
        bit_offset = i % 8
        val = 0
        data_start = 6
        byte_idx = data_start + byte_offset
        if len(pdu) > byte_idx:
            val = (pdu[byte_idx] >> bit_offset) & 1
        _write_coil(server, addr + i, val)
    response_pdu = pdu[:5]  # fc + addr + count
    resp_len = 1 + len(response_pdu)
    resp_header = struct.pack(">HHHB", tid, 0, resp_len, uid)
    return resp_header + response_pdu


def _write_coil(server, channel, value):
    if 0 <= channel < 4:
        server._io.write_output(channel, value)
        server._state.set_do(channel, value)


# ═══════════════════════════════════════════════════════════════════
# CAN send channel helpers
# ═══════════════════════════════════════════════════════════════════


def _can_channel_for_trigger(server, coil_address):
    """Return the CANSendChannel whose trigger_coil_address matches, or None."""
    for ch in (server._can_send_channels or []):
        if ch.trigger_coil_address == coil_address:
            return ch
    return None


def _can_channel_for_staging(server, addr, cnt):
    """Return the CANSendChannel whose 6-register staging block starts
    at *addr* and spans *cnt* registers, or None."""
    for ch in (server._can_send_channels or []):
        staging = ch.staging_addresses()
        if len(staging) == cnt and staging[0] == addr:
            return ch
    return None


# ═══════════════════════════════════════════════════════════════════
# FC 5 — coil write (DO + CAN trigger)
# ═══════════════════════════════════════════════════════════════════


async def _handle_fc5_coil(pdu: bytes, tid: int, uid: int, server) -> bytes:
    """Handle FC 5 — Write Single Coil.
    If the coil address matches a CAN send channel trigger, fire the
    staged CAN frame. Otherwise, write to DO output as before."""
    fc = pdu[0]
    header = struct.pack(">HHHB", tid, 0, 3, uid)
    resp_len = 1 + len(pdu)

    if len(pdu) < 5:
        return _build_exception(tid, uid, fc, 0x02)

    addr, val = struct.unpack(">HH", pdu[1:5])
    coil_val = 1 if val == 0xFF00 else 0

    channel = _can_channel_for_trigger(server, addr)
    if channel is not None and coil_val == 1:
        async with server._can_send_lock:
            stage = server._can_stage.get(channel.name)
            if stage is None:
                return _build_exception(tid, uid, fc, 0x02)

            loop = asyncio.get_running_loop()
            try:
                ok = await loop.run_in_executor(
                    None,
                    server._can.send_message,
                    stage["id"],
                    stage["data"][: stage["dlc"]],
                    False,
                )
            except (ValueError, RuntimeError):
                ok = False

            server._can_stage.pop(channel.name, None)

            if ok:
                resp_header = struct.pack(">HHHB", tid, 0, resp_len, uid)
                return resp_header + pdu
            return _build_exception(tid, uid, fc, 0x0A)

    _write_coil(server, addr, coil_val)
    resp_header = struct.pack(">HHHB", tid, 0, resp_len, uid)
    return resp_header + pdu


# ═══════════════════════════════════════════════════════════════════
# Write helpers (FC 6/16) — holding register writes to RTU devices
# ═══════════════════════════════════════════════════════════════════


def _rtu_entry_for_address(server, address):
    """Return the RTU-writable RegisterMapEntry for *address*, or None."""
    for e in (server._register_map or []):
        if e.address == address and e.function_code in (3, 4):
            if e.source_type == "modbus_rtu" and e.writable:
                return e
    return None


async def _handle_fc6_write(pdu: bytes, tid: int, uid: int, server) -> bytes:
    """Parse and execute an FC 6 (Write Single Register) request."""
    header = struct.pack(">HHHB", tid, 0, 3, uid)
    fc = pdu[0]

    if len(pdu) < 5:
        return _build_exception(tid, uid, fc, 0x02)

    addr, value = struct.unpack(">HH", pdu[1:5])

    entry = _rtu_entry_for_address(server, addr)
    if entry is None:
        return _build_exception(tid, uid, fc, 0x02)

    loop = asyncio.get_running_loop()
    try:
        ok = await loop.run_in_executor(
            None,
            server._modbus.write_holding_register,
            entry.rtu_device_id,
            entry.rtu_address,
            value,
        )
    except Exception:
        return _build_exception(tid, uid, fc, 0x0A)

    if ok:
        resp_len = 1 + len(pdu)
        resp_header = struct.pack(">HHHB", tid, 0, resp_len, uid)
        return resp_header + pdu
    return _build_exception(tid, uid, fc, 0x0A)


async def _handle_fc16_write(pdu: bytes, tid: int, uid: int, server) -> bytes:
    """Parse and execute an FC 16 (Write Multiple Registers) request.
    Checks CAN send channel staging blocks first; falls through to
    RTU holding register writes if no channel matches."""
    fc = pdu[0]

    if len(pdu) < 6:
        return _build_exception(tid, uid, fc, 0x02)

    addr, cnt, byte_count = struct.unpack(">HHB", pdu[1:6])

    expected_bytes = cnt * 2
    if byte_count != expected_bytes or len(pdu) < 6 + byte_count:
        return _build_exception(tid, uid, fc, 0x03)

    data_start = 6
    reg_values = []
    for i in range(cnt):
        offset = data_start + i * 2
        val = struct.unpack(">H", pdu[offset : offset + 2])[0]
        reg_values.append(val)

    channel = _can_channel_for_staging(server, addr, cnt)
    if channel is not None:
        server._can_stage[channel.name] = {
            "id": reg_values[0] & 0x7FF,
            "data": reg_values[1:5],
            "dlc": min(reg_values[5] & 0xFF, 8),
        }
        resp_pdu = pdu[:5]
        resp_len = 1 + len(resp_pdu)
        resp_header = struct.pack(">HHHB", tid, 0, resp_len, uid)
        return resp_header + resp_pdu

    for i in range(cnt):
        entry = _rtu_entry_for_address(server, addr + i)
        if entry is None:
            return _build_exception(tid, uid, fc, 0x02)

    loop = asyncio.get_running_loop()
    for i in range(cnt):
        entry = _rtu_entry_for_address(server, addr + i)
        try:
            ok = await loop.run_in_executor(
                None,
                server._modbus.write_holding_register,
                entry.rtu_device_id,
                entry.rtu_address,
                reg_values[i],
            )
        except Exception:
            return _build_exception(tid, uid, fc, 0x0A)
        if not ok:
            return _build_exception(tid, uid, fc, 0x0A)

    resp_pdu = pdu[:5]
    resp_len = 1 + len(resp_pdu)
    resp_header = struct.pack(">HHHB", tid, 0, resp_len, uid)
    return resp_header + resp_pdu
