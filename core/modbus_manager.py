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

import threading
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional

import minimalmodbus
import serial

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
            print(f"✅ Added Modbus device '{name}' (id={device_id}, "
                  f"port={port}, slave={slave_id})")
            return device_id

    def remove_device(self, device_id):
        with self._lock:
            if device_id in self.devices:
                self.disconnect(device_id)
                del self.devices[device_id]
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
            print(f"🔌 Connecting to '{device.name}' on {port_path} "
                  f"(slave={device.slave_id}, {device.baudrate}bps)...")

            instrument = minimalmodbus.Instrument(port_path, device.slave_id)
            instrument.serial.baudrate = device.baudrate
            instrument.serial.bytesize = 8
            instrument.serial.parity = _PARITY_MAP[device.parity]
            instrument.serial.stopbits = device.stopbits
            instrument.serial.timeout = device.timeout
            instrument.mode = minimalmodbus.MODE_RTU
            instrument.clear_buffers_before_each_transaction = True

            device.instrument = instrument
            device.connected = True
            device.last_connected = datetime.now().isoformat()
            device.last_error = None

            print(f"✅ Connected: {device.name}")
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
            print(f"🔌 Disconnected: {device.name}")

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

    # ----------------------------------------
    # Holding registers (FC3 read / FC6 write)
    # ----------------------------------------
    def read_holding_register(self, device_id, address) -> Optional[int]:
        device = self._require_connected(device_id)
        try:
            value = device.instrument.read_register(address, functioncode=3)
            print(f"[READ]  Holding register {address} = {value}")
            self._log_event(device, "read", f"Holding register {address} = {value}")
            return value
        except minimalmodbus.NoResponseError:
            device.last_error = f"No response reading holding register {address}"
            print(f"[READ]  No response for holding register {address}")
            self._log_event(device, "error", device.last_error)
            return None

    def write_holding_register(self, device_id, address, value) -> bool:
        device = self._require_connected(device_id)
        try:
            device.instrument.write_register(address, value, functioncode=6)
            print(f"[WRITE] Holding register {address} <- {value}")
            self._log_event(device, "write", f"Holding register {address} <- {value}")
            return True
        except minimalmodbus.NoResponseError:
            device.last_error = f"No response writing holding register {address}"
            print(f"[WRITE] No response writing holding register {address}")
            self._log_event(device, "error", device.last_error)
            return False

    # ----------------------------------------
    # Input registers (FC4 read-only)
    # ----------------------------------------
    def read_input_register(self, device_id, address) -> Optional[int]:
        device = self._require_connected(device_id)
        try:
            value = device.instrument.read_register(address, functioncode=4)
            print(f"[READ]  Input register {address} = {value}")
            self._log_event(device, "read", f"Input register {address} = {value}")
            return value
        except minimalmodbus.NoResponseError:
            device.last_error = f"No response reading input register {address}"
            print(f"[READ]  No response for input register {address}")
            self._log_event(device, "error", device.last_error)
            return None

    # ----------------------------------------
    # Coils (FC1 read / FC5 write)
    # ----------------------------------------
    def read_coil(self, device_id, address) -> Optional[int]:
        device = self._require_connected(device_id)
        try:
            value = device.instrument.read_bit(address, functioncode=1)
            print(f"[READ]  Coil {address} = {value}")
            self._log_event(device, "read", f"Coil {address} = {value}")
            return value
        except minimalmodbus.NoResponseError:
            device.last_error = f"No response reading coil {address}"
            print(f"[READ]  No response for coil {address}")
            self._log_event(device, "error", device.last_error)
            return None

    def write_coil(self, device_id, address, value) -> bool:
        device = self._require_connected(device_id)
        try:
            device.instrument.write_bit(address, value, functioncode=5)
            print(f"[WRITE] Coil {address} <- {value}")
            self._log_event(device, "write", f"Coil {address} <- {value}")
            return True
        except minimalmodbus.NoResponseError:
            device.last_error = f"No response writing coil {address}"
            print(f"[WRITE] No response writing coil {address}")
            self._log_event(device, "error", device.last_error)
            return False

    # ----------------------------------------
    # Discrete inputs (FC2 read-only)
    # ----------------------------------------
    def read_discrete_input(self, device_id, address) -> Optional[int]:
        device = self._require_connected(device_id)
        try:
            value = device.instrument.read_bit(address, functioncode=2)
            print(f"[READ]  Discrete input {address} = {value}")
            self._log_event(device, "read", f"Discrete input {address} = {value}")
            return value
        except minimalmodbus.NoResponseError:
            device.last_error = f"No response reading discrete input {address}"
            print(f"[READ]  No response for discrete input {address}")
            self._log_event(device, "error", device.last_error)
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
                print(f"  ✅ Found device at slave ID {slave_id}")
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