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

    def __init__(self, io_manager, state, can_manager):
        self._io = io_manager
        self._state = state
        self._can = can_manager
        self._lock = threading.Lock()

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
                        addr, val = (
                            struct.unpack(">HH", pdu[1:5]) if len(pdu) >= 5 else (0, 0)
                        )
                        _write_coil(srv, addr, 1 if val == 0xFF00 else 0)
                        response = header + pdu  # echo
                    elif fc == 15:  # Write Multiple Coils
                        addr, cnt = (
                            struct.unpack(">HH", pdu[1:5]) if len(pdu) >= 5 else (0, 0)
                        )
                        for i in range(cnt):
                            byte_offset = i // 8
                            bit_offset = i % 8
                            val = 0
                            data_start = 6
                            byte_idx = data_start + byte_offset
                            if len(pdu) > byte_idx:
                                val = (pdu[byte_idx] >> bit_offset) & 1
                            _write_coil(srv, addr + i, val)
                        response_pdu = pdu[:5]  # fc + addr + count (not full write PDU)
                        resp_len = 1 + len(response_pdu)
                        resp_header = struct.pack(">HHHB", tid, 0, resp_len, uid)
                        response = resp_header + response_pdu
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


def _write_coil(server, channel, value):
    if 0 <= channel < 4:
        server._io.write_output(channel, value)
        server._state.set_do(channel, value)
