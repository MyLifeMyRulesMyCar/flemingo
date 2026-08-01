#!/usr/bin/env python3
# core/io_manager.py
# GPIO DI/DO manager for the Purple Pi OH2 HAT project.
# Pin map is loaded from core/config.py's load_hardware_config(),
# which reads config/hardware.yaml if present or falls back to defaults.

import gpiod
from gpiod.line import Direction, Value, Bias
import threading

# Ordered channel lists so DI[0]/DO[0] map predictably to arrays/JSON
DO_CHANNELS = ["DO0", "DO1", "DO2", "DO3"]
DI_CHANNELS = ["DI0", "DI1", "DI2", "DI3"]


class IOManager:
    """
    GPIO manager for Purple Pi OH2 digital inputs/outputs.

    Groups pins by chip so each chip is only requested once
    (DO0-3 all live on gpiochip1; DI0/DI1 on gpiochip4; DI2/DI3 on gpiochip3).

    Falls back to simulation mode automatically if gpiod can't open
    the chips (e.g. permissions not set yet), so test scripts never
    crash outright - they just tell you it's simulated.
    """

    def __init__(self):
        self._hw_lock = threading.RLock()
        self.requests_in = {}
        self.requests_out = {}
        self.simulation = False
        self._sim_di = [0, 0, 0, 0]
        self._sim_do = [0, 0, 0, 0]

        from core.config import load_hardware_config

        hw = load_hardware_config()
        self.output_pins = {
            name: (p["chip"], p["line"]) for name, p in hw["gpio"]["outputs"].items()
        }
        self.input_pins = {
            name: (p["chip"], p["line"]) for name, p in hw["gpio"]["inputs"].items()
        }

        try:
            self._init_hardware()
        except Exception as e:
            print(f"⚠️  GPIO init failed: {e}")
            print("💾 Falling back to simulation mode")
            print("   Fix: sudo chmod 666 /dev/gpiochip1 /dev/gpiochip3 /dev/gpiochip4")
            self.simulation = True

    # ----------------------------------------
    # Hardware setup
    # ----------------------------------------
    def _setup_outputs(self):
        chips = {}
        for name in DO_CHANNELS:
            chip, line = self.output_pins[name]
            chips.setdefault(chip, []).append(line)

        for chip, lines in chips.items():
            config = {
                ln: gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    output_value=Value.INACTIVE,
                )
                for ln in lines
            }
            req = gpiod.request_lines(chip, consumer="purpleio_do", config=config)
            self.requests_out[chip] = req

    def _setup_inputs(self):
        chips = {}
        for name in DI_CHANNELS:
            chip, line = self.input_pins[name]
            chips.setdefault(chip, []).append(line)

        for chip, lines in chips.items():
            config = {
                ln: gpiod.LineSettings(
                    direction=Direction.INPUT,
                    bias=Bias.PULL_DOWN,
                )
                for ln in lines
            }
            req = gpiod.request_lines(chip, consumer="purpleio_di", config=config)
            self.requests_in[chip] = req

    def _cleanup_hardware(self):
        for req in list(self.requests_in.values()) + list(self.requests_out.values()):
            try:
                req.release()
            except Exception:
                pass
        self.requests_in.clear()
        self.requests_out.clear()

    def _init_hardware(self):
        with self._hw_lock:
            self._cleanup_hardware()
            self._setup_outputs()
            self._setup_inputs()
        print("✅ GPIO initialized (hardware mode)")

    # ----------------------------------------
    # Public API
    # ----------------------------------------
    def read_all_inputs(self):
        """Returns [DI0, DI1, DI2, DI3] as a 0/1 list."""
        if self.simulation:
            return list(self._sim_di)

        with self._hw_lock:
            values = []
            for name in DI_CHANNELS:
                chip, line = self.input_pins[name]
                req = self.requests_in.get(chip)
                if not req:
                    raise RuntimeError(f"GPIO chip {chip} not requested")
                val = req.get_value(line)
                values.append(1 if val == Value.ACTIVE else 0)
            return values

    def read_input(self, channel):
        """Read a single DI channel (0-3)."""
        return self.read_all_inputs()[channel]

    def write_output(self, channel, value):
        """Write a single DO channel (0-3), value 0/1."""
        name = DO_CHANNELS[channel]
        chip, line = self.output_pins[name]

        if self.simulation:
            self._sim_do[channel] = 1 if value else 0
            print(f"💾 Simulation: {name} = {value}")
            return

        with self._hw_lock:
            req = self.requests_out.get(chip)
            if not req:
                raise RuntimeError(f"GPIO chip {chip} not requested")
            req.set_value(line, Value.ACTIVE if value else Value.INACTIVE)

    def write_all_outputs(self, values):
        """values: list of 4 ints (0/1), one per DO channel."""
        for ch, v in enumerate(values):
            self.write_output(ch, v)

    def get_status(self):
        return {
            "simulation": self.simulation,
            "di_chips": list(self.requests_in.keys()),
            "do_chips": list(self.requests_out.keys()),
        }

    def close(self):
        self._cleanup_hardware()


if __name__ == "__main__":
    mgr = IOManager()
    print(mgr.get_status())
    mgr.close()
