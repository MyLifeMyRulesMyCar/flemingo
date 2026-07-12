import { useState, useEffect, useRef } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { apiGet, apiPost } from "../api/client.js";
import { getSocket } from "../api/socket.js";
import StatusLed from "../components/StatusLed.jsx";
import { useToast } from "../components/Toast.jsx";

export default function MQTT() {
  const { role } = useAuth();
  const { showToast } = useToast();
  const [status, setStatus] = useState({});
  const storedHost = sessionStorage.getItem("mqtt_host") || "";
  const storedPort = sessionStorage.getItem("mqtt_port") || "1883";
  const storedUser = sessionStorage.getItem("mqtt_user") || "";
  const storedPass = sessionStorage.getItem("mqtt_pass") || "";
  const [broker, setBroker] = useState({
    host: storedHost,
    port: parseInt(storedPort) || 1883,
    username: storedUser,
    password: storedPass,
  });

  const [modRegs, setModRegs] = useState([]);
  const [modPoll, setModPoll] = useState(5);
  const [modNewDev, setModNewDev] = useState("");
  const prevModbusRunning = useRef(null);
  const prevCanRunning = useRef(null);
  const [modNewAddr, setModNewAddr] = useState(0);
  const [modNewFc, setModNewFc] = useState(3);
  const [modbusDevices, setModbusDevices] = useState([]);

  const [canPubTopic, setCanPubTopic] = useState("flemingo/edge-01/can/rx");
  const [canSubTopic, setCanSubTopic] = useState("flemingo/edge-01/can/tx");
  const [canQos, setCanQos] = useState(0);
  const [idFilter, setIdFilter] = useState("");

  const [ioPollMs, setIoPollMs] = useState(100);
  const [ioPublishOnChange, setIoPublishOnChange] = useState(false);
  const [canConnected, setCanConnected] = useState(false);

  const isOperator = role === "operator" || role === "admin";
  const isAdmin = role === "admin";

  const fetchStatus = async () => {
    try {
      const r = await apiGet("/api/mqtt/status");
      const d = await r.json();
      setStatus(d);
      const mb = d.bridges?.modbus;
      const wasRunning = prevModbusRunning.current;
      const isRunning = mb?.running;
      prevModbusRunning.current = isRunning;

      if (mb) {
        if (mb.running && mb.registers?.length) setModRegs(mb.registers);
        if (mb.poll_interval_s) setModPoll(mb.poll_interval_s);
      }

      if (wasRunning && !isRunning && mb?.stop_reason) {
        showToast(
          `Modbus bridge auto-stopped: ${mb.stop_reason}`,
          "error"
        );
      }
      const cb = d.bridges?.can;
      const wasCanRunning = prevCanRunning.current;
      const isCanRunning = cb?.running;
      prevCanRunning.current = isCanRunning;

      if (cb) {
        if (cb.publish_topic) setCanPubTopic(cb.publish_topic);
        if (cb.subscribe_topic) setCanSubTopic(cb.subscribe_topic);
        if (cb.qos !== undefined) setCanQos(cb.qos);
        if (cb.running && cb.id_filter && cb.id_filter.length > 0) setIdFilter(cb.id_filter.join(", "));
      }

      if (wasCanRunning && !isCanRunning && cb?.stop_reason) {
        showToast(
          `CAN bridge auto-stopped: ${cb.stop_reason}`,
          "error"
        );
      }
      const ib = d.bridges?.io;
      if (ib) {
        if (ib.poll_interval_ms) setIoPollMs(ib.poll_interval_ms);
        if (ib.publish_on_change !== undefined) setIoPublishOnChange(ib.publish_on_change);
      }
    } catch {}
  };

  useEffect(() => {
    fetchStatus();
    const t = setInterval(fetchStatus, 3000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const fetchDevices = () => {
      apiGet("/api/modbus/devices")
        .then((r) => r.json())
        .then((d) => {
          const connected = (d.devices || []).filter((dev) => dev.connected);
          setModbusDevices(connected);
        })
        .catch(() => {});
    };
    fetchDevices();
    const t = setInterval(fetchDevices, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    apiGet("/api/can/status")
      .then((r) => r.json())
      .then((d) => setCanConnected(d.connected === true))
      .catch(() => {});

    const sock = getSocket();
    if (!sock) return;
    const onStatus = (s) => setCanConnected(s.connected === true);
    sock.on("can_status", onStatus);
    return () => sock.off("can_status", onStatus);
  }, []);

  const handleConnect = async () => {
    const r = await apiPost("/api/mqtt/connect", {
      host: broker.host,
      port: Number(broker.port),
      username: broker.username || undefined,
      password: broker.password || undefined,
    });
    const d = await r.json();
    if (r.ok) showToast("Connecting to broker...", "success");
    else showToast(d.error || "Connect failed", "error");
    setTimeout(fetchStatus, 1000);
  };

  const handleDisconnect = async () => {
    const r = await apiPost("/api/mqtt/disconnect", {});
    if (r.ok) showToast("Disconnected", "success");
    fetchStatus();
  };

  const bridgeOp = async (path, body) => {
    const r = await apiPost(path, body || {});
    if (r.ok) showToast("OK", "success");
    else showToast((await r.json()).error || "Failed", "error");
    fetchStatus();
  };

  const addModbusRow = () => {
    if (!modNewDev.trim()) return;
    setModRegs([
      ...modRegs,
      { device_id: modNewDev.trim(), address: Number(modNewAddr), function_code: modNewFc },
    ]);
    setModNewDev("");
    setModNewAddr(0);
    setModNewFc(3);
  };

  const removeModbusRow = (idx) => {
    setModRegs(modRegs.filter((_, i) => i !== idx));
  };

  const startModbusBridge = async () => {
    if (modRegs.length === 0) {
      showToast("Add at least one register to start", "error");
      return;
    }
    const mb = status.bridges?.modbus;
    if (mb?.running) {
      const r = await apiPost("/api/mqtt/bridges/modbus/registers", {
        registers: modRegs,
      });
      if (r.ok) showToast("Register list updated", "success");
      else showToast((await r.json()).error || "Update failed", "error");
    } else {
      const r = await apiPost("/api/mqtt/bridges/modbus/start", {
        registers: modRegs,
        poll_interval_s: modPoll,
      });
      if (r.ok) showToast("Modbus bridge started", "success");
      else showToast((await r.json()).error || "Failed", "error");
    }
    fetchStatus();
  };

  const stopModbusBridge = () => bridgeOp("/api/mqtt/bridges/modbus/stop");

  const startIOBridge = async () => {
    const r = await apiPost("/api/mqtt/bridges/io/start", {
      poll_interval_ms: ioPollMs,
      publish_on_change: ioPublishOnChange,
    });
    if (r.ok) showToast("IO bridge started", "success");
    else showToast((await r.json()).error || "Failed", "error");
    fetchStatus();
  };

  const updateCANConfig = async () => {
    const ids = idFilter
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => parseInt(s, 0));
    const r = await apiPost("/api/mqtt/bridges/can/config", {
      publish_topic: canPubTopic,
      subscribe_topic: canSubTopic,
      qos: canQos,
      id_filter: ids,
    });
    if (r.ok) showToast("CAN config updated", "success");
    else showToast((await r.json()).error || "Failed", "error");
    fetchStatus();
  };

  const stats = status.stats || {};
  const bridges = status.bridges || {};

  return (
    <div>
      <div className="page-header">
        <h2>MQTT</h2>
        <p>Broker connection and CAN/Modbus/IO bridges</p>
      </div>

      <div className="card">
        <div className="card-header">
          Broker
          <StatusLed
            status={status.connected ? "ok" : "off"}
            label={status.connected ? "Connected" : "Disconnected"}
          />
        </div>
        <div className="form-inline">
          <div className="form-row">
            <label>Host</label>
            <input
              placeholder="192.168.1.x"
              value={broker.host}
              onChange={(e) => {
                sessionStorage.setItem("mqtt_host", e.target.value);
                setBroker({ ...broker, host: e.target.value });
              }}
              disabled={!isOperator}
            />
          </div>
          <div className="form-row">
            <label>Port</label>
            <input
              type="number"
              value={broker.port}
              onChange={(e) => {
                sessionStorage.setItem("mqtt_port", e.target.value);
                setBroker({ ...broker, port: e.target.value });
              }}
              style={{ width: 80 }}
              disabled={!isOperator}
            />
          </div>
          <div className="form-row">
            <label>Username</label>
            <input
              placeholder="(none)"
              value={broker.username}
              onChange={(e) => {
                sessionStorage.setItem("mqtt_user", e.target.value);
                setBroker({ ...broker, username: e.target.value });
              }}
              style={{ width: 120 }}
              disabled={!isOperator}
            />
          </div>
          <div className="form-row">
            <label>Password</label>
            <input
              type="password"
              placeholder="(none)"
              value={broker.password}
              onChange={(e) => {
                sessionStorage.setItem("mqtt_pass", e.target.value);
                setBroker({ ...broker, password: e.target.value });
              }}
              style={{ width: 120 }}
              disabled={!isOperator}
            />
          </div>
          {isOperator && (
            <>
              <button className="btn-primary" onClick={handleConnect} disabled={status.connected}>
                Connect
              </button>
              <button className="btn-default" onClick={handleDisconnect} disabled={!status.connected}>
                Disconnect
              </button>
            </>
          )}
        </div>
        <div style={{ display: "flex", gap: "20px", fontSize: "13px", marginTop: "8px" }}>
          <span>
            Published: <strong className="mono">{stats.messages_published ?? 0}</strong>
          </span>
          <span>
            Received: <strong className="mono">{stats.messages_received ?? 0}</strong>
          </span>
          <span>
            Reconnects: <strong className="mono">{stats.reconnects ?? 0}</strong>
          </span>
        </div>
      </div>

      <div className="bridge-grid">
        <CANBridgeCard
          status={bridges.can}
          brokerConnected={status.connected}
          pubTopic={canPubTopic}
          subTopic={canSubTopic}
          qos={canQos}
          idFilter={idFilter}
          canConnected={canConnected}
          onPubTopicChange={setCanPubTopic}
          onSubTopicChange={setCanSubTopic}
          onQosChange={setCanQos}
          onIdFilterChange={setIdFilter}
          onStart={() => bridgeOp("/api/mqtt/bridges/can/start")}
          onStop={() => bridgeOp("/api/mqtt/bridges/can/stop")}
          onConfig={updateCANConfig}
          isOperator={isOperator}
          isAdmin={isAdmin}
        />

        <ModbusBridgeCard
          status={bridges.modbus}
          brokerConnected={status.connected}
          registers={modRegs}
          modbusDevices={modbusDevices}
          onAddRow={addModbusRow}
          onRemoveRow={removeModbusRow}
          onStart={startModbusBridge}
          onStop={stopModbusBridge}
          isOperator={isOperator}
          newDev={modNewDev}
          onNewDevChange={setModNewDev}
          newAddr={modNewAddr}
          onNewAddrChange={setModNewAddr}
          newFc={modNewFc}
          onNewFcChange={setModNewFc}
          pollInterval={modPoll}
          onPollChange={setModPoll}
        />

        <IOBridgeCard
          status={bridges.io}
          brokerConnected={status.connected}
          pollMs={ioPollMs}
          publishOnChange={ioPublishOnChange}
          onPollMsChange={setIoPollMs}
          onPublishOnChangeChange={setIoPublishOnChange}
          onStart={startIOBridge}
          onStop={() => bridgeOp("/api/mqtt/bridges/io/stop")}
          isOperator={isOperator}
        />
      </div>
    </div>
  );
}

function CANBridgeCard({
  status, pubTopic, subTopic, qos, canConnected, brokerConnected, idFilter,
  onPubTopicChange, onSubTopicChange, onQosChange, onIdFilterChange,
  onStart, onStop, onConfig,
  isOperator, isAdmin,
}) {
  const s = status || {};
  const st = s.stats || {};
  return (
    <div className="card">
      <div className="card-header">
        CAN Bridge
        <StatusLed status={s.running ? "ok" : "off"} label={s.running ? "Running" : "Stopped"} />
      </div>
      {isOperator && (
        <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
          <button className="btn-primary" style={{ padding: "4px 12px", fontSize: "11px" }}
            onClick={onStart} disabled={s.running || !canConnected || !brokerConnected}>Start</button>
          <button className="btn-default" style={{ padding: "4px 12px", fontSize: "11px" }}
            onClick={onStop} disabled={!s.running}>Stop</button>
        </div>
      )}
      <div className="form-row">
        <label>Publish Topic</label>
        <input value={pubTopic} onChange={(e) => onPubTopicChange(e.target.value)}
          disabled={!isAdmin || s.running} style={{ fontFamily: "var(--font-mono)", fontSize: "11px" }} />
      </div>
      <div className="form-row">
        <label>Subscribe Topic</label>
        <input value={subTopic} onChange={(e) => onSubTopicChange(e.target.value)}
          disabled={!isAdmin || s.running} style={{ fontFamily: "var(--font-mono)", fontSize: "11px" }} />
      </div>
      <div className="form-row">
        <label>QoS</label>
        <select value={qos} onChange={(e) => onQosChange(Number(e.target.value))} disabled={!isAdmin || s.running}>
          <option value={0}>0 — At most once</option>
          <option value={1}>1 — At least once</option>
          <option value={2}>2 — Exactly once</option>
        </select>
      </div>
      <div className="form-row">
        <label>ID Filter (comma-separated hex, empty = all)</label>
        <input value={idFilter} onChange={(e) => onIdFilterChange(e.target.value)}
          placeholder="e.g. 0x100, 0x200"
          disabled={!isAdmin || s.running}
          style={{ fontFamily: "var(--font-mono)", fontSize: "11px" }} />
      </div>
      {isAdmin && (
        <button className="btn-default" style={{ padding: "4px 12px", fontSize: "11px", marginBottom: "12px" }}
          onClick={onConfig} disabled={s.running}>Apply Config</button>
      )}
      {!s.running && s.stop_reason && (
        <div style={{
          background: "#3a2a0a", border: "1px solid var(--status-warn)",
          color: "var(--status-warn)", padding: "8px 12px", borderRadius: "var(--radius)",
          fontSize: "12px", marginBottom: "12px",
        }}>
          Auto-stopped: {s.stop_reason}
        </div>
      )}
      <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
        <div>Published: {st.published ?? 0}</div>
        <div>Received: {st.received ?? 0}</div>
        <div>Errors: {st.errors ?? 0}</div>
      </div>
    </div>
  );
}

function IOBridgeCard({
  status, pollMs, publishOnChange, brokerConnected,
  onPollMsChange, onPublishOnChangeChange,
  onStart, onStop, isOperator,
}) {
  const s = status || {};
  const st = s.stats || {};
  return (
    <div className="card">
      <div className="card-header">
        IO Bridge
        <StatusLed status={s.running ? "ok" : "off"} label={s.running ? "Running" : "Stopped"} />
      </div>
      {isOperator && (
        <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
          <button className="btn-primary" style={{ padding: "4px 12px", fontSize: "11px" }}
            onClick={onStart} disabled={s.running || !brokerConnected}>Start</button>
          <button className="btn-default" style={{ padding: "4px 12px", fontSize: "11px" }}
            onClick={onStop} disabled={!s.running}>Stop</button>
        </div>
      )}
      <div className="form-row">
        <label>Poll interval (ms)</label>
        <input type="number" value={pollMs} onChange={(e) => onPollMsChange(Number(e.target.value))}
          style={{ width: 80 }} min={10} max={10000} disabled={!isOperator || s.running} />
      </div>
      <div className="form-row" style={{ display: "flex", alignItems: "center", gap: "10px" }}>
        <label style={{ marginBottom: 0 }}>Publish on change</label>
        <div className="toggle-switch">
          <input type="checkbox" checked={publishOnChange}
            onChange={(e) => onPublishOnChangeChange(e.target.checked)}
            disabled={!isOperator || s.running} />
          <span className="slider" />
        </div>
      </div>
      <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
        <div>Published: {(st.di_published ?? 0) + (st.do_published ?? 0)}</div>
        <div>Received: {st.do_received ?? 0}</div>
        <div>Errors: {st.errors ?? 0}</div>
      </div>
    </div>
  );
}

function ModbusBridgeCard({
  status, registers, pollInterval, modbusDevices, brokerConnected,
  onAddRow, onRemoveRow, onStart, onStop, isOperator,
  newDev, onNewDevChange, newAddr, onNewAddrChange, newFc, onNewFcChange, onPollChange,
}) {
  const s = status || {};
  const st = s.stats || {};
  const isRunning = s.running;
  return (
    <div className="card">
      <div className="card-header">
        Modbus Bridge
        <StatusLed status={isRunning ? "ok" : "off"} label={isRunning ? "Running" : "Stopped"} />
      </div>
      {isOperator && (
        <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
          <button className="btn-primary" style={{ padding: "4px 12px", fontSize: "11px" }}
            onClick={onStart} disabled={!brokerConnected || registers.length === 0}>{isRunning ? "Apply" : "Start"}</button>
          <button className="btn-default" style={{ padding: "4px 12px", fontSize: "11px" }}
            onClick={onStop} disabled={!isRunning}>Stop</button>
        </div>
      )}
      {!isRunning && s.stop_reason && (
        <div style={{
          background: "#3a2a0a", border: "1px solid var(--status-warn)",
          color: "var(--status-warn)", padding: "8px 12px", borderRadius: "var(--radius)",
          fontSize: "12px", marginBottom: "12px",
        }}>
          Auto-stopped: {s.stop_reason}
        </div>
      )}
      {brokerConnected && registers.length === 0 && (
        <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "12px" }}>
          Add at least one register below to enable Start
        </div>
      )}
      <div className="form-row">
        <label>Poll interval (seconds)</label>
        <input type="number" value={pollInterval} onChange={(e) => onPollChange(Number(e.target.value))}
          style={{ width: 80 }} min={1} max={3600} disabled={!isOperator || isRunning} />
      </div>
      <div className="form-row"><label>Registers ({registers.length})</label></div>

      {modbusDevices && modbusDevices.length > 0 && (
        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "8px" }}>
          {modbusDevices.map((d) => (
            <span
              key={d.id}
              onClick={() => isOperator && onNewDevChange(d.id)}
              title={`${d.name} (${d.port}, slave ${d.slave_id})`}
              style={{
                background: isOperator ? "#1a2733" : "#1c2128",
                color: "var(--accent)",
                padding: "2px 10px",
                borderRadius: "10px",
                fontSize: "11px",
                cursor: isOperator ? "pointer" : "default",
              }}
            >
              {d.name}
            </span>
          ))}
        </div>
      )}

      <table className="data-table" style={{ marginBottom: "10px" }}>
        <thead><tr><th>Device ID</th><th>Address</th><th>FC</th><th></th></tr></thead>
        <tbody>
          {registers.map((reg, idx) => (
            <tr key={idx}>
              <td style={{ fontFamily: "var(--font-sans)" }}>{reg.device_id}</td>
              <td>{reg.address}</td><td>FC{reg.function_code}</td>
              <td>{isOperator && <button className="btn-danger" style={{ padding: "2px 8px", fontSize: "11px" }} onClick={() => onRemoveRow(idx)} disabled={isRunning || false}>Del</button>}</td>
            </tr>
          ))}
          {registers.length === 0 && <tr><td colSpan={4} style={{ textAlign: "center", color: "var(--text-muted)" }}>No registers configured</td></tr>}
        </tbody>
      </table>
      {isOperator && (
        <div className="form-inline" style={{ marginBottom: "8px" }}>
          <div className="form-row">
            <label>Device</label>
            <select value={newDev} onChange={(e) => onNewDevChange(e.target.value)} style={{ width: 150 }}>
              <option value="">Select device...</option>
              {(modbusDevices || []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.id})
                </option>
              ))}
            </select>
          </div>
          <div className="form-row"><label>Addr</label><input type="number" value={newAddr} onChange={(e) => onNewAddrChange(Number(e.target.value))} style={{ width: 70 }} min={0} max={65535} disabled={isRunning || false} /></div>
          <div className="form-row"><label>FC</label><select value={newFc} onChange={(e) => onNewFcChange(Number(e.target.value))}><option value={1}>FC1</option><option value={2}>FC2</option><option value={3}>FC3</option><option value={4}>FC4</option></select></div>
          <button className="btn-primary" style={{ padding: "8px 14px", fontSize: "12px" }} onClick={onAddRow} disabled={isRunning || false}>Add</button>
        </div>
      )}
      <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
        <div>Published: {st.published ?? 0}</div>
        <div>Received: {st.received ?? 0}</div>
        <div>Errors: {st.errors ?? 0}</div>
      </div>
    </div>
  );
}
