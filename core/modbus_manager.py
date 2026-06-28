#!/usr/bin/env python3
# core/modbus_manager.py
# RS485/Modbus RTU backend manager for Purple Pi OH2.
#
# Talks to slaves over a USB-to-RS485 adapter (NOT the onboard UART
# pins) - confirmed working at /dev/ttyUSB0, 115200 8N1, slave ID 1.
# A second adapter slot (/dev/ttyUSB1) is pre-defined below for when
# you add a second bus/device.
#
# Same role as core/io_manager.py and core/can_manager.py: this is
# the layer a Flask API will sit on top of in Phase 4.
#
# Phase 5 reliability additions: each device gets its OWN CircuitBreaker
# (a dead slave on one port shouldn't degrade any other configured
# device), connect() retries with backoff since opening a serial port
# can fail transiently, and aggregate health is mirrored into
# core.resilience.health_status.

import logging
import threading
import time
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional

import minimalmodbus
import serial

from core.resilience import CircuitBreaker, CircuitOpenError, retry_with_backoff, health_status
from core.config import load_reliability_config

logger = logging.getLogger(__name__)

# ============================================
# Known ports (USB-to-RS485 adapters)
# ============================================
MODBUS_PORTS = {
    "ttyUSB0": {
        "device": "/dev/ttyUSB0",
        "name": "RS485 Adapter 1",
    },
    "ttyUSB1": {
        "device": "/dev/ttyUSB1",
        "name": "RS485 Adapter 2",
    },
}

# Matches your validated test scripts
DEFAULT_BAUDRATE = 115200
DEFAULT_PARITY = "N"
DEFAULT_STOPBITS = 1
DEFAULT_TIMEOUT = 1.0

_PARITY_MAP = {
    "N": serial.PARITY_NONE,
    "E": serial.PARITY_EVEN,
    "O": serial.PARITY_ODD,
}


class ModbusDevice:
    """One configured Modbus RTU slave: a port + slave ID + serial params."""

    def __init__(self, device_id, name, port, slave_id,
                 baudrate=DEFAULT_BAUDRATE, parity=DEFAULT_PARITY,
                 stopbits=DEFAULT_STOPBITS, timeout=DEFAULT_TIMEOUT):
        self.id = device_id
        self.name = name
        self.port = port            # key into MODBUS_PORTS, e.g. "ttyUSB0"
        self.slave_id = slave_id
        self.baudrate = baudrate
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout

        self.instrument: Optional[minimalmodbus.Instrument] = None
        self.connected = False
        self.last_connected = None
        self.last_error = None

        cb_config = load_reliability_config()["circuit_breaker"]["modbus"]
        self.breaker = CircuitBreaker(
            failure_threshold=cb_config["failure_threshold"],
            timeout=cb_config["timeout"],
            expected_exception=Exception,
            name=f"Modbus-{device_id}",
        )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "port": self.port,
            "slave_id": self.slave_id,
            "baudrate": self.baudrate,
            "parity": self.parity,
            "stopbits": self.stopbits,
            "connected": self.connected,
            "last_connected": self.last_connected,
            "last_error": self.last_error,
            "circuit_breaker": self.breaker.get_state(),
        }


class ModbusManager:
    """
    Backend manager for RS485/Modbus RTU devices over USB adapters.

    Usage:
        mgr = ModbusManager()
        dev_id = mgr.add_device("Sensor 1", port="ttyUSB0", slave_id=1)
        mgr.connect(dev_id)
        mgr.read_holding_register(dev_id, 0)
        mgr.write_holding_register(dev_id, 0, 42)
        mgr.disconnect(dev_id)
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.devices: Dict[str, ModbusDevice] = {}
        self.log = deque(maxlen=500)
        self._next_id = 1
        self._retry_config = load_reliability_config()["retry"]

        # Health-check thread: periodically pings each connected device
        # so physical disconnection is detected without waiting for an
        # API call. After 5 failed checks the device's breaker opens and
        # device.connected flips to False.
        self._health_running = False
        self._health_thread = None
        self._health_interval = 5       # seconds between checks
        self._health_register = 0        # holding register to read as liveness probe

        health_status.update("modbus", "unknown", "No devices configured")
        self._start_health_check()

    # ----------------------------------------
    # Aggregate health
    # ----------------------------------------
    def _refresh_health(self):
        """Mirror per-device state into the process-wide health_status
        registry. Called after connect/disconnect - not after every
        single read/write, to avoid lock contention on a hot path."""
        devices = list(self.devices.values())
        if not devices:
            health_status.update("modbus", "unknown", "No devices configured")
            return

        connected = sum(1 for d in devices if d.connected)
        breakers_open = sum(1 for d in devices if d.breaker.state.value == "open")

        if breakers_open:
            health_status.update(
                "modbus", "degraded",
                f"{breakers_open}/{len(devices)} device(s) circuit-open"
            )
        elif connected == len(devices):
            health_status.update("modbus", "healthy", f"All {len(devices)} device(s) connected")
        elif connected == 0:
            health_status.update("modbus", "unhealthy", "No devices connected")
        else:
            health_status.update(
                "modbus", "degraded",
                f"{connected}/{len(devices)} device(s) connected"
            )

    # ----------------------------------------
    # Device registry
    # ----------------------------------------
    def add_device(self, name, port, slave_id, baudrate=DEFAULT_BAUDRATE,
                    parity=DEFAULT_PARITY, stopbits=DEFAULT_STOPBITS,
                    timeout=DEFAULT_TIMEOUT) -> str:
        if port not in MODBUS_PORTS:
            raise ValueError(f"Unknown port '{port}', known: {list(MODBUS_PORTS)}")

        with self._lock:
            device_id = f"dev{self._next_id}"
            self._next_id += 1
            device = ModbusDevice(device_id, name, port, slave_id,
                                   baudrate, parity, stopbits, timeout)
            self.devices[device_id] = device
            logger.info(f"Added Modbus device '{name}' (id={device_id}, "
                        f"port={port}, slave={slave_id})")
            self._refresh_health()
            return device_id

    def remove_device(self, device_id):
        with self._lock:
            if device_id in self.devices:
                self.disconnect(device_id)
                del self.devices[device_id]
                self._refresh_health()
                return True
            return False

    def get_device(self, device_id) -> Optional[ModbusDevice]:
        return self.devices.get(device_id)

    def get_all_devices(self) -> List[Dict]:
        return [d.to_dict() for d in self.devices.values()]

    # ----------------------------------------
    # Connection
    # ----------------------------------------
    def connect(self, device_id) -> bool:
        device = self.devices.get(device_id)
        if not device:
            raise ValueError(f"Unknown device id '{device_id}'")

        with self._lock:
            port_path = MODBUS_PORTS[device.port]["device"]
            logger.info(f"Connecting to '{device.name}' on {port_path} "
                        f"(slave={device.slave_id}, {device.baudrate}bps)...")

            @retry_with_backoff(
                max_retries=self._retry_config["max_retries"],
                initial_delay=self._retry_config["initial_delay"],
                max_delay=self._retry_config["max_delay"],
                expected_exception=Exception,
            )
            def _open_instrument():
                instrument = minimalmodbus.Instrument(port_path, device.slave_id)
                instrument.serial.baudrate = device.baudrate
                instrument.serial.bytesize = 8
                instrument.serial.parity = _PARITY_MAP[device.parity]
                instrument.serial.stopbits = device.stopbits
                instrument.serial.timeout = device.timeout
                instrument.mode = minimalmodbus.MODE_RTU
                instrument.clear_buffers_before_each_transaction = True
                return instrument

            try:
                device.instrument = _open_instrument()
            except Exception as e:
                device.last_error = f"Failed to open {port_path}: {e}"
                logger.error(f"Connect failed for '{device.name}': {device.last_error}")
                self._refresh_health()
                raise RuntimeError(device.last_error) from e

            device.connected = True
            device.last_connected = datetime.now().isoformat()
            device.last_error = None
            device.breaker.reset()

            logger.info(f"Connected: {device.name}")
            self._refresh_health()
            return True

    def disconnect(self, device_id):
        device = self.devices.get(device_id)
        if not device:
            return
        with self._lock:
            if device.instrument:
                try:
                    device.instrument.serial.close()
                except Exception:
                    pass
            device.instrument = None
            device.connected = False
            logger.info(f"Disconnected: {device.name}")
        self._refresh_health()

    # ----------------------------------------
    # Health-check thread (detects physical disconnection)
    # ----------------------------------------
    def _mark_disconnected(self, device: ModbusDevice, reason: str = ""):
        """Called when repeated failures indicate the device is gone."""
        if not device.connected:
            return
        device.connected = False
        if device.instrument:
            try:
                device.instrument.serial.close()
            except Exception:
                pass
            device.instrument = None
        device.last_error = reason
        logger.warning(f"Modbus device '{device.name}' disconnected: {reason}")
        self._log_event(device, "disconnect", reason)
        self._refresh_health()

    def _start_health_check(self):
        if self._health_thread and self._health_thread.is_alive():
            return
        self._health_running = True
        self._health_thread = threading.Thread(
            target=self._health_check_loop, name="Modbus-Health", daemon=True
        )
        self._health_thread.start()
        logger.info("Modbus health-check thread started")

    def _stop_health_check(self):
        self._health_running = False
        if self._health_thread:
            self._health_thread.join(timeout=2)
        logger.info("Modbus health-check thread stopped")

    def _health_check_loop(self):
        logger.info("Modbus health-check loop running")
        while self._health_running:
            try:
                devices = list(self.devices.values())
                for device in devices:
                    if not device.connected or not device.instrument:
                        continue
                    ok, _ = self._call_through_breaker(
                        device, "health_check",
                        lambda: device.instrument.read_register(
                            self._health_register, functioncode=3
                        ),
                    )
                    if ok and device.breaker.state.value != "open":
                        # Healthy — nothing to do
                        pass
                self._refresh_health()
            except Exception as e:
                logger.warning(f"Modbus health-check loop error: {e}")
            time.sleep(self._health_interval)
        logger.info("Modbus health-check loop stopped")

    # ----------------------------------------
    # Internal helpers
    # ----------------------------------------
    def _require_connected(self, device_id) -> ModbusDevice:
        device = self.devices.get(device_id)
        if not device:
            raise ValueError(f"Unknown device id '{device_id}'")
        if not device.connected or not device.instrument:
            raise RuntimeError(f"Device '{device.name}' not connected")
        return device

    def _log_event(self, device, event_type, message, data=None):
        self.log.append({
            "timestamp": datetime.now().isoformat(),
            "device_id": device.id,
            "device_name": device.name,
            "type": event_type,
            "message": message,
            "data": data,
        })

    def _call_through_breaker(self, device: ModbusDevice, op_name: str, func):
        """
        Runs `func` (a zero-arg callable doing the actual minimalmodbus
        call) through `device.breaker`. Repeated NoResponseError on THIS
        device opens THIS device's breaker only - other devices on other
        ports (or other slave IDs on the same port) are unaffected.

        Returns (ok, result) instead of raising, so callers keep the
        existing None/False-on-failure return shape rather than needing
        a try/except at every call site.
        """
        @device.breaker.call
        def _attempt():
            return func()

        try:
            result = _attempt()
            device.last_error = None
            return True, result
        except CircuitOpenError as e:
            device.last_error = str(e)
            self._log_event(device, "error", device.last_error)
            self._mark_disconnected(device, f"Circuit breaker open: {e}")
            return False, None
        except minimalmodbus.NoResponseError:
            device.last_error = f"No response: {op_name}"
            logger.warning(f"[{op_name}] no response")
            self._log_event(device, "error", device.last_error)
            return False, None

    # ----------------------------------------
    # Holding registers (FC3 read / FC6 write)
    # ----------------------------------------
    def read_holding_register(self, device_id, address) -> Optional[int]:
        device = self._require_connected(device_id)
        ok, value = self._call_through_breaker(
            device, f"holding register {address}",
            lambda: device.instrument.read_register(address, functioncode=3),
        )
        if ok:
            logger.info(f"[READ]  Holding register {address} = {value}")
            self._log_event(device, "read", f"Holding register {address} = {value}")
            return value
        return None

    def write_holding_register(self, device_id, address, value) -> bool:
        device = self._require_connected(device_id)
        ok, _ = self._call_through_breaker(
            device, f"write holding register {address}",
            lambda: device.instrument.write_register(address, value, functioncode=6),
        )
        if ok:
            logger.info(f"[WRITE] Holding register {address} <- {value}")
            self._log_event(device, "write", f"Holding register {address} <- {value}")
            return True
        return False

    # ----------------------------------------
    # Input registers (FC4 read-only)
    # ----------------------------------------
    def read_input_register(self, device_id, address) -> Optional[int]:
        device = self._require_connected(device_id)
        ok, value = self._call_through_breaker(
            device, f"input register {address}",
            lambda: device.instrument.read_register(address, functioncode=4),
        )
        if ok:
            logger.info(f"[READ]  Input register {address} = {value}")
            self._log_event(device, "read", f"Input register {address} = {value}")
            return value
        return None

    # ----------------------------------------
    # Coils (FC1 read / FC5 write)
    # ----------------------------------------
    def read_coil(self, device_id, address) -> Optional[int]:
        device = self._require_connected(device_id)
        ok, value = self._call_through_breaker(
            device, f"coil {address}",
            lambda: device.instrument.read_bit(address, functioncode=1),
        )
        if ok:
            logger.info(f"[READ]  Coil {address} = {value}")
            self._log_event(device, "read", f"Coil {address} = {value}")
            return value
        return None

    def write_coil(self, device_id, address, value) -> bool:
        device = self._require_connected(device_id)
        ok, _ = self._call_through_breaker(
            device, f"write coil {address}",
            lambda: device.instrument.write_bit(address, value, functioncode=5),
        )
        if ok:
            logger.info(f"[WRITE] Coil {address} <- {value}")
            self._log_event(device, "write", f"Coil {address} <- {value}")
            return True
        return False

    # ----------------------------------------
    # Discrete inputs (FC2 read-only)
    # ----------------------------------------
    def read_discrete_input(self, device_id, address) -> Optional[int]:
        device = self._require_connected(device_id)
        ok, value = self._call_through_breaker(
            device, f"discrete input {address}",
            lambda: device.instrument.read_bit(address, functioncode=2),
        )
        if ok:
            logger.info(f"[READ]  Discrete input {address} = {value}")
            self._log_event(device, "read", f"Discrete input {address} = {value}")
            return value
        return None

    # ----------------------------------------
    # Slave scan
    # ----------------------------------------
    def scan_port(self, port, start_id=1, end_id=10, baudrate=DEFAULT_BAUDRATE,
                  register=0, functioncode=3) -> List[int]:
        """Probe a range of slave IDs on a port by attempting a register read on each."""
        if port not in MODBUS_PORTS:
            raise ValueError(f"Unknown port '{port}'")
        port_path = MODBUS_PORTS[port]["device"]

        found = []
        for slave_id in range(start_id, end_id + 1):
            inst = None
            try:
                inst = minimalmodbus.Instrument(port_path, slave_id)
                inst.serial.baudrate = baudrate
                inst.serial.bytesize = 8
                inst.serial.parity = serial.PARITY_NONE
                inst.serial.stopbits = 1
                inst.serial.timeout = 0.3
                inst.mode = minimalmodbus.MODE_RTU
                inst.clear_buffers_before_each_transaction = True

                inst.read_register(register, functioncode=functioncode)
                found.append(slave_id)
                logger.info(f"Slave scan: found device at slave ID {slave_id}")
            except Exception:
                pass
            finally:
                if inst is not None:
                    try:
                        inst.serial.close()
                    except Exception:
                        pass

        return found

    # ----------------------------------------
    # Logs
    # ----------------------------------------
    def get_logs(self, count=100) -> List[Dict]:
        return list(self.log)[-count:]


# Module-level singleton, same pattern as io_manager/can_manager
modbus_manager = ModbusManager()