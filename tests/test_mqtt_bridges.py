#!/usr/bin/env python3
# tests/test_mqtt_bridges.py
# Phase 8 — pure-logic tests for MQTT bridges and MQTTManager.
# No real broker, no hardware, no Flask context.
# Run with: pytest tests/test_mqtt_bridges.py

import sys
import os
import json
import unittest.mock as mock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bridges.can_bridge import CANBridge
from core.bridges.modbus_bridge import ModbusBridge
from core.bridges.io_bridge import IOBridge
from core.mqtt_manager import MQTTManager
from api.validators import (
    ValidationError,
    validate_mqtt_host,
    validate_mqtt_port,
    validate_mqtt_topic,
    validate_mqtt_qos,
    validate_poll_interval_s,
    validate_poll_interval_ms,
)


# ─── Helper: expose _topic_matches as a module-level function ─────────
def _topic_matches(pattern, topic):
    return MQTTManager._topic_matches(pattern, topic)


# ────────────────────────────────────────────────────────────────────
# Section 1: MQTT topic matching
# ────────────────────────────────────────────────────────────────────


def test_topic_matching():
    tm = _topic_matches
    assert tm("a/b/c", "a/b/c")
    assert not tm("a/b/c", "a/b/d")
    assert not tm("a/b/c", "a/b")
    assert tm("a/+/c", "a/b/c")
    assert tm("a/+/c", "a/anything/c")
    assert not tm("a/+/c", "a/b/d")
    assert not tm("a/+/c", "a/b/c/d")
    assert tm("a/#", "a/b")
    assert tm("a/#", "a/b/c/d/e")
    assert tm("flemingo/+/modbus/+/set", "flemingo/edge-01/modbus/dev1/set")
    assert not tm("flemingo/+/modbus/+/set", "flemingo/edge-01/other/dev1/set")
    assert tm("flemingo/edge-01/io/do/+/set", "flemingo/edge-01/io/do/0/set")
    assert tm("flemingo/edge-01/io/do/+/set", "flemingo/edge-01/io/do/3/set")
    assert not tm("flemingo/edge-01/io/do/+/set", "flemingo/edge-01/io/do/0/get")
    print("✅ topic matching (exact, +, #)")


# ────────────────────────────────────────────────────────────────────
# Section 2: MQTTManager — publish and routing
# ────────────────────────────────────────────────────────────────────


def make_mock_mqtt():
    mgr = MQTTManager()
    mgr._connected = True
    mgr._client = mock.MagicMock()
    mgr._client.publish.return_value = mock.MagicMock(rc=0)
    return mgr


def test_mqtt_manager_publish():
    mgr = make_mock_mqtt()
    ok_flag = mgr.publish("test/topic", {"value": 42}, qos=1)
    assert ok_flag
    assert mgr.stats["messages_published"] == 1
    payload_str = mgr._client.publish.call_args[0][1]
    assert json.loads(payload_str) == {"value": 42}
    print("✅ MQTTManager.publish serialises to JSON, increments counter")


def test_mqtt_manager_publish_disconnected():
    mgr = MQTTManager()
    assert mgr.publish("topic", {"x": 1}) is False
    print("✅ MQTTManager.publish returns False when disconnected")


def test_subscription_routing():
    mgr = make_mock_mqtt()
    received = []
    mgr.register_subscription(
        "flemingo/edge-01/can/tx", lambda p, t: received.append(("can", t))
    )
    mgr.register_subscription(
        "flemingo/edge-01/modbus/+/set", lambda p, t: received.append(("modbus", t))
    )
    msg = mock.MagicMock()
    msg.topic = "flemingo/edge-01/can/tx"
    msg.payload = b'{"can_id":291}'
    mgr._on_message(None, None, msg)
    assert len(received) == 1 and received[0][0] == "can"
    print("✅ exact topic routes to correct callback only")


def test_wildcard_routing():
    mgr = make_mock_mqtt()
    received = []
    mgr.register_subscription(
        "flemingo/edge-01/modbus/+/set", lambda p, t: received.append(("modbus", t))
    )
    mgr.register_subscription(
        "flemingo/edge-01/io/do/+/set", lambda p, t: received.append(("io", t))
    )
    msg = mock.MagicMock()
    msg.topic = "flemingo/edge-01/modbus/dev1/set"
    msg.payload = b'{"address":0,"value":42,"function_code":6}'
    mgr._on_message(None, None, msg)
    assert len(received) == 1 and received[0][0] == "modbus"
    print("✅ wildcard routes modbus write correctly, does not trigger io handler")


# ────────────────────────────────────────────────────────────────────
# Section 3: CANBridge
# ────────────────────────────────────────────────────────────────────


def make_can_bridge():
    mqtt = mock.MagicMock()
    mqtt.publish.return_value = True
    can = mock.MagicMock()
    cfg = {
        "publish_topic": "flemingo/edge-01/can/rx",
        "subscribe_topic": "flemingo/edge-01/can/tx",
        "qos": 0,
    }
    return CANBridge(mqtt, can, cfg), mqtt, can


def test_can_bridge_start_stop():
    bridge, mqtt, can = make_can_bridge()
    bridge.start()
    assert bridge.running
    can.subscribe.assert_called_once_with(bridge._on_can_rx)
    mqtt.register_subscription.assert_called_once()
    bridge.stop()
    assert not bridge.running
    can.unsubscribe.assert_called_once_with(bridge._on_can_rx)
    mqtt.deregister_subscription.assert_called_once()
    print("✅ CANBridge start/stop hooks into can_manager and mqtt_manager")


def test_can_bridge_double_start():
    bridge, _, _ = make_can_bridge()
    bridge.start()
    with pytest.raises(RuntimeError):
        bridge.start()
    bridge.stop()
    print("✅ CANBridge double-start raises RuntimeError")


def test_can_bridge_rx_publishes():
    bridge, mqtt, _ = make_can_bridge()
    bridge.start()
    entry = {
        "can_id": 291,
        "data": [1, 2, 3],
        "dlc": 3,
        "extended": False,
        "timestamp": "2026-07-03T00:00:00",
    }
    bridge._on_can_rx(entry)
    mqtt.publish.assert_called_once()
    topic, payload = mqtt.publish.call_args[0][0], mqtt.publish.call_args[0][1]
    assert topic == "flemingo/edge-01/can/rx"
    assert payload["can_id"] == 291
    assert payload["data"] == [1, 2, 3]
    assert bridge.stats["published"] == 1
    bridge.stop()
    print("✅ CANBridge publishes CAN RX frame to correct MQTT topic")


def test_can_bridge_mqtt_tx_valid():
    bridge, _, can = make_can_bridge()
    bridge.start()
    bridge._on_mqtt_tx(
        json.dumps({"can_id": 100, "data": [0xDE, 0xAD]}), "flemingo/edge-01/can/tx"
    )
    can.send_message.assert_called_once_with(100, [0xDE, 0xAD], extended=False)
    assert bridge.stats["received"] == 1
    bridge.stop()
    print("✅ CANBridge routes MQTT TX command to can_manager.send_message")


def test_can_bridge_tx_hex_can_id():
    bridge, _, can = make_can_bridge()
    bridge.start()
    bridge._on_mqtt_tx(json.dumps({"can_id": "0x123", "data": [1, 2]}), "t")
    can.send_message.assert_called_once_with(0x123, [1, 2], extended=False)
    bridge.stop()
    print("✅ CANBridge TX: hex string CAN ID accepted")


def test_can_bridge_tx_invalid_json():
    bridge, _, can = make_can_bridge()
    bridge.start()
    bridge._on_mqtt_tx("not json", "topic")
    can.send_message.assert_not_called()
    assert bridge.stats["errors"] == 1
    bridge.stop()
    print("✅ CANBridge TX: invalid JSON increments error, no crash")


def test_can_bridge_tx_bad_can_id():
    bridge, _, can = make_can_bridge()
    bridge.start()
    bridge._on_mqtt_tx(json.dumps({"can_id": 0x800, "data": [1]}), "t")
    can.send_message.assert_not_called()
    assert bridge.stats["errors"] == 1
    bridge.stop()
    print("✅ CANBridge TX: std CAN ID > 0x7FF rejected")


# ────────────────────────────────────────────────────────────────────
# Section 4: ModbusBridge
# Poll-logic tests call _do_poll() directly WITHOUT starting the
# thread, to avoid race conditions and 99s join timeouts.
# ────────────────────────────────────────────────────────────────────


def make_modbus_bridge():
    mqtt = mock.MagicMock()
    mqtt.publish.return_value = True
    modbus = mock.MagicMock()
    modbus.read_holding_register.return_value = 42
    modbus.get_device.return_value = mock.MagicMock(name="TestDevice")
    cfg = {
        "poll_interval_s": 0.05,  # tiny so stop() join is fast
        "publish_topic_template": "flemingo/edge-01/modbus/{dev_id}/r{address}",
        "subscribe_topic_template": "flemingo/edge-01/modbus/+/set",
        "qos": 1,
    }
    return ModbusBridge(mqtt, modbus, cfg), mqtt, modbus


def test_modbus_bridge_start_stop():
    bridge, mqtt, _ = make_modbus_bridge()
    bridge.start(
        register_list=[{"device_id": "dev1", "address": 0, "function_code": 3}]
    )
    assert bridge.running
    mqtt.register_subscription.assert_called_once()
    bridge.stop()
    assert not bridge.running
    mqtt.deregister_subscription.assert_called_once()
    print("✅ ModbusBridge start/stop registers wildcard subscription")


def test_modbus_bridge_poll_publishes():
    bridge, mqtt, modbus = make_modbus_bridge()
    bridge.register_list = [{"device_id": "dev1", "address": 0, "function_code": 3}]
    bridge.running = True  # guard check: no thread, just direct _do_poll()
    bridge._do_poll()
    modbus.read_holding_register.assert_called_once_with("dev1", 0)
    mqtt.publish.assert_called_once()
    topic = mqtt.publish.call_args[0][0]
    payload = mqtt.publish.call_args[0][1]
    assert topic == "flemingo/edge-01/modbus/dev1/r0"
    assert payload["value"] == 42
    assert payload["device_id"] == "dev1"
    assert payload["address"] == 0
    print("✅ ModbusBridge _do_poll publishes register value to correct topic")


def test_modbus_bridge_poll_skips_none():
    bridge, mqtt, modbus = make_modbus_bridge()
    modbus.read_holding_register.return_value = None
    bridge.register_list = [{"device_id": "dev1", "address": 0, "function_code": 3}]
    bridge.running = True
    bridge._do_poll()
    mqtt.publish.assert_not_called()
    print("✅ ModbusBridge poll skips None (device disconnected)")


def test_modbus_bridge_poll_multiple_registers():
    bridge, mqtt, modbus = make_modbus_bridge()
    modbus.read_holding_register.side_effect = [10, 20, 30]
    bridge.register_list = [
        {"device_id": "dev1", "address": 0, "function_code": 3},
        {"device_id": "dev1", "address": 1, "function_code": 3},
        {"device_id": "dev1", "address": 2, "function_code": 3},
    ]
    bridge.running = True
    bridge._do_poll()
    assert mqtt.publish.call_count == 3
    topics = [c[0][0] for c in mqtt.publish.call_args_list]
    assert "flemingo/edge-01/modbus/dev1/r0" in topics
    assert "flemingo/edge-01/modbus/dev1/r2" in topics
    print("✅ ModbusBridge polls all registers in list per cycle")


def test_modbus_bridge_mqtt_write_fc6():
    bridge, _, modbus = make_modbus_bridge()
    bridge._on_mqtt_write(
        json.dumps({"address": 5, "value": 1234, "function_code": 6}),
        "flemingo/edge-01/modbus/dev1/set",
    )
    modbus.write_holding_register.assert_called_once_with("dev1", 5, 1234)
    print("✅ ModbusBridge MQTT write FC6 → write_holding_register")


def test_modbus_bridge_mqtt_write_fc5():
    bridge, _, modbus = make_modbus_bridge()
    bridge._on_mqtt_write(
        json.dumps({"address": 0, "value": 1, "function_code": 5}),
        "flemingo/edge-01/modbus/dev1/set",
    )
    modbus.write_coil.assert_called_once_with("dev1", 0, 1)
    print("✅ ModbusBridge MQTT write FC5 → write_coil")


def test_modbus_bridge_mqtt_write_bad_value():
    bridge, _, modbus = make_modbus_bridge()
    bridge._on_mqtt_write(
        json.dumps({"address": 0, "value": 99999, "function_code": 6}),
        "flemingo/edge-01/modbus/dev1/set",
    )
    modbus.write_holding_register.assert_not_called()
    assert bridge.stats["errors"] == 1
    print("✅ ModbusBridge MQTT write: FC6 value > 65535 rejected")


def test_modbus_bridge_mqtt_write_extracts_device_id():
    bridge, _, modbus = make_modbus_bridge()
    bridge._on_mqtt_write(
        json.dumps({"address": 2, "value": 7, "function_code": 6}),
        "flemingo/edge-01/modbus/sensor-02/set",
    )
    modbus.write_holding_register.assert_called_once_with("sensor-02", 2, 7)
    print("✅ ModbusBridge extracts device_id from wildcard topic")


def test_modbus_bridge_hot_update_registers():
    bridge, _, _ = make_modbus_bridge()
    bridge.start(
        register_list=[{"device_id": "dev1", "address": 0, "function_code": 3}]
    )
    bridge.update_register_list(
        [
            {"device_id": "dev1", "address": 0, "function_code": 3},
            {"device_id": "dev1", "address": 1, "function_code": 3},
        ]
    )
    assert len(bridge.register_list) == 2
    bridge.stop()
    print("✅ ModbusBridge hot-update register list while running")


def test_modbus_bridge_double_start():
    bridge, _, _ = make_modbus_bridge()
    bridge.start(
        register_list=[{"device_id": "dev1", "address": 0, "function_code": 3}]
    )
    with pytest.raises(RuntimeError):
        bridge.start(register_list=[])
    bridge.stop()
    print("✅ ModbusBridge double-start raises RuntimeError")


# ────────────────────────────────────────────────────────────────────
# Section 5: IOBridge
# Poll tests call _do_poll() directly without starting the thread.
# ────────────────────────────────────────────────────────────────────


def make_io_bridge(di_values=None):
    mqtt = mock.MagicMock()
    mqtt.publish.return_value = True
    io = mock.MagicMock()
    state = mock.MagicMock()
    state.get_di.return_value = list(di_values or [0, 0, 0, 0])
    cfg = {
        "poll_interval_ms": 50,  # tiny so stop() join is fast
        "publish_on_change": True,
        "publish_topic_di": "flemingo/edge-01/io/di/{channel}",
        "subscribe_topic_do": "flemingo/edge-01/io/do/+/set",
        "qos": 1,
    }
    return IOBridge(mqtt, io, state, cfg), mqtt, io, state


def test_io_bridge_start_stop():
    bridge, mqtt, _, _ = make_io_bridge()
    bridge.start()
    assert bridge.running
    mqtt.register_subscription.assert_called_once()
    bridge.stop()
    assert not bridge.running
    print("✅ IOBridge start/stop registers DO subscription")


def test_io_bridge_first_read_publishes():
    bridge, mqtt, _, _ = make_io_bridge(di_values=[1, 0, 0, 0])
    bridge._do_poll()
    assert mqtt.publish.call_count == 4
    print("✅ IOBridge: first DI reading publishes all 4 channels (no debounce)")


def test_io_bridge_unchanged_not_republished():
    bridge, mqtt, _, state = make_io_bridge(di_values=[1, 0, 0, 0])
    bridge._do_poll()  # first read — publishes all 4 (publish_on_change=True, last_di was None)
    count_after = mqtt.publish.call_count
    bridge._do_poll()  # same values — no publish
    assert mqtt.publish.call_count == count_after
    print("✅ IOBridge: stable DI value not re-published (publish_on_change=True)")


def test_io_bridge_publish_always():
    bridge, mqtt, _, _ = make_io_bridge(di_values=[0, 0, 0, 0])
    bridge.publish_on_change = False
    bridge._do_poll()
    assert mqtt.publish.call_count == 4
    mqtt.publish.reset_mock()
    bridge._do_poll()
    assert mqtt.publish.call_count == 4
    print(
        "✅ IOBridge: all channels published every cycle when publish_on_change=False"
    )


def test_io_bridge_do_set():
    bridge, _, io, _ = make_io_bridge()
    bridge._on_mqtt_do_set(json.dumps({"value": 1}), "flemingo/edge-01/io/do/2/set")
    io.write_output.assert_called_once_with(2, 1)
    assert bridge.stats["do_received"] == 1
    print("✅ IOBridge MQTT DO set → io_manager.write_output(channel, value)")


def test_io_bridge_do_bad_channel():
    bridge, _, io, _ = make_io_bridge()
    bridge._on_mqtt_do_set(json.dumps({"value": 1}), "flemingo/edge-01/io/do/9/set")
    io.write_output.assert_not_called()
    assert bridge.stats["errors"] == 1
    print("✅ IOBridge MQTT DO set: channel 9 out of range rejected")


def test_io_bridge_do_bad_value():
    bridge, _, io, _ = make_io_bridge()
    bridge._on_mqtt_do_set(json.dumps({"value": 5}), "flemingo/edge-01/io/do/0/set")
    io.write_output.assert_not_called()
    assert bridge.stats["errors"] == 1
    print("✅ IOBridge MQTT DO set: value 5 (not 0 or 1) rejected")


def test_io_bridge_do_invalid_json():
    bridge, _, io, _ = make_io_bridge()
    bridge._on_mqtt_do_set("not-json", "flemingo/edge-01/io/do/0/set")
    io.write_output.assert_not_called()
    assert bridge.stats["errors"] == 1
    print("✅ IOBridge MQTT DO set: invalid JSON increments error, no crash")


# ────────────────────────────────────────────────────────────────────
# Section 6: MQTT validators
# ────────────────────────────────────────────────────────────────────


def test_mqtt_validators():
    # host
    assert validate_mqtt_host("192.168.1.1") == "192.168.1.1"
    assert validate_mqtt_host("  broker.local  ") == "broker.local"
    with pytest.raises(ValidationError):
        validate_mqtt_host("")
    with pytest.raises(ValidationError):
        validate_mqtt_host("a b")
    with pytest.raises(ValidationError):
        validate_mqtt_host(12345)
    print("✅ validate_mqtt_host")

    # port
    assert validate_mqtt_port(1883) == 1883
    assert validate_mqtt_port("8883") == 8883
    with pytest.raises(ValidationError):
        validate_mqtt_port(0)
    with pytest.raises(ValidationError):
        validate_mqtt_port(65536)
    print("✅ validate_mqtt_port")

    # topic
    assert validate_mqtt_topic("flemingo/edge-01/can/rx") == "flemingo/edge-01/can/rx"
    assert validate_mqtt_topic("a/+/c") == "a/+/c"
    assert validate_mqtt_topic("a/#") == "a/#"
    with pytest.raises(ValidationError):
        validate_mqtt_topic("")
    with pytest.raises(ValidationError):
        validate_mqtt_topic("a\x00b")
    with pytest.raises(ValidationError):
        validate_mqtt_topic("x" * 129)
    print("✅ validate_mqtt_topic")

    # qos
    for q in (0, 1, 2):
        assert validate_mqtt_qos(q) == q
    with pytest.raises(ValidationError):
        validate_mqtt_qos(3)
    with pytest.raises(ValidationError):
        validate_mqtt_qos(-1)
    print("✅ validate_mqtt_qos")

    # poll_interval_s
    assert validate_poll_interval_s(5) == 5.0
    assert validate_poll_interval_s(0.5) == 0.5
    assert validate_poll_interval_s(3600) == 3600.0
    with pytest.raises(ValidationError):
        validate_poll_interval_s(0)
    with pytest.raises(ValidationError):
        validate_poll_interval_s(3601)
    print("✅ validate_poll_interval_s")

    # poll_interval_ms
    assert validate_poll_interval_ms(100) == 100
    assert validate_poll_interval_ms(10) == 10
    assert validate_poll_interval_ms(10000) == 10000
    with pytest.raises(ValidationError):
        validate_poll_interval_ms(9)
    with pytest.raises(ValidationError):
        validate_poll_interval_ms(10001)
    print("✅ validate_poll_interval_ms")


# ────────────────────────────────────────────────────────────────────
# Section 7: load_mqtt_config + topic resolution
# ────────────────────────────────────────────────────────────────────


def test_load_mqtt_config():
    from core.config import load_mqtt_config

    cfg = load_mqtt_config(force_reload=True)

    assert "broker" in cfg
    assert "bridges" in cfg

    prefix = cfg["bridges"]["prefix"]
    device_id = cfg["bridges"]["device_id"]

    can_pub = cfg["bridges"]["can"]["publish_topic"]
    modbus_sub = cfg["bridges"]["modbus"]["subscribe_topic_template"]

    assert prefix in can_pub, f"prefix not in CAN topic: {can_pub}"
    assert device_id in can_pub, f"device_id not in CAN topic: {can_pub}"
    assert prefix in modbus_sub, f"prefix not in Modbus sub: {modbus_sub}"
    assert device_id in modbus_sub, f"device_id not in Modbus sub: {modbus_sub}"

    # Raw template strings should NOT appear in resolved output
    assert "{prefix}" not in can_pub
    assert "{device_id}" not in can_pub
    print("✅ load_mqtt_config: topics resolved with prefix and device_id")
