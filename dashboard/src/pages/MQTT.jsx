import { useState, useEffect } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { apiGet, apiPost } from "../api/client.js";
import StatusLed from "../components/StatusLed.jsx";
import { useToast } from "../components/Toast.jsx";

export default function MQTT() {
  const { role } = useAuth();
  const { showToast } = useToast();
  const [status, setStatus] = useState({});
  const [broker, setBroker] = useState({ host: "", port: 1883, username: "", password: "" });

  const [modRegs, setModRegs] = useState([]);
  const [modPoll, setModPoll] = useState(5);
  const [modNewDev, setModNewDev] = useState("");
  const [modNewAddr, setModNewAddr] = useState(0);
  const [modNewFc, setModNewFc] = useState(3);

  const isOperator = role === "operator" || role === "admin";
  const isAdmin = role === "admin";

  const fetchStatus = async () => {
    try {
      const r = await apiGet("/api/mqtt/status");
      const d = await r.json();
      setStatus(d);
      const mb = d.bridges?.modbus;
      if (mb) {
        if (mb.registers?.length) setModRegs(mb.registers);
        if (mb.poll_interval_s) setModPoll(mb.poll_interval_s);
      }
    } catch {}
  };

  useEffect(() => {
    fetchStatus();
    const t = setInterval(fetchStatus, 3000);
    return () => clearInterval(t);
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
      publish_on_change: false,
    });
    if (r.ok) showToast("IO bridge started", "success");
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
              onChange={(e) => setBroker({ ...broker, host: e.target.value })}
              disabled={!isOperator}
            />
          </div>
          <div className="form-row">
            <label>Port</label>
            <input
              type="number"
              value={broker.port}
              onChange={(e) => setBroker({ ...broker, port: e.target.value })}
              style={{ width: 80 }}
              disabled={!isOperator}
            />
          </div>
          {isOperator && (
            <>
              <button className="btn-primary" onClick={handleConnect}>
                Connect
              </button>
              <button className="btn-default" onClick={handleDisconnect}>
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
        <BridgeCard
          title="CAN Bridge"
          status={bridges.can}
          onStart={() => bridgeOp("/api/mqtt/bridges/can/start")}
          onStop={() => bridgeOp("/api/mqtt/bridges/can/stop")}
          isOperator={isOperator}
        />

        <ModbusBridgeCard
          status={bridges.modbus}
          registers={modRegs}
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

        <BridgeCard
          title="IO Bridge"
          status={bridges.io}
          onStart={startIOBridge}
          onStop={() => bridgeOp("/api/mqtt/bridges/io/stop")}
          isOperator={isOperator}
        />
      </div>
    </div>
  );
}

function BridgeCard({ title, status, onStart, onStop, isOperator }) {
  const s = status || {};
  const st = s.stats || {};
  return (
    <div className="card">
      <div className="card-header">
        {title}
        <StatusLed
          status={s.running ? "ok" : "off"}
          label={s.running ? "Running" : "Stopped"}
        />
      </div>
      {isOperator && (
        <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
          <button
            className="btn-primary"
            style={{ padding: "4px 12px", fontSize: "11px" }}
            onClick={onStart}
            disabled={s.running}
          >
            Start
          </button>
          <button
            className="btn-default"
            style={{ padding: "4px 12px", fontSize: "11px" }}
            onClick={onStop}
            disabled={!s.running}
          >
            Stop
          </button>
        </div>
      )}
      <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
        <div>Published: {st.published ?? st.di_published ?? 0}</div>
        <div>Received: {st.received ?? st.do_received ?? 0}</div>
        <div>Errors: {st.errors ?? 0}</div>
      </div>
    </div>
  );
}

function ModbusBridgeCard({
  status,
  registers,
  pollInterval,
  onAddRow,
  onRemoveRow,
  onStart,
  onStop,
  isOperator,
  newDev,
  onNewDevChange,
  newAddr,
  onNewAddrChange,
  newFc,
  onNewFcChange,
  onPollChange,
}) {
  const s = status || {};
  const st = s.stats || {};
  const isRunning = s.running;

  return (
    <div className="card">
      <div className="card-header">
        Modbus Bridge
        <StatusLed
          status={isRunning ? "ok" : "off"}
          label={isRunning ? "Running" : "Stopped"}
        />
      </div>

      {isOperator && (
        <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
          <button
            className="btn-primary"
            style={{ padding: "4px 12px", fontSize: "11px" }}
            onClick={onStart}
            disabled={false}
          >
            {isRunning ? "Apply" : "Start"}
          </button>
          <button
            className="btn-default"
            style={{ padding: "4px 12px", fontSize: "11px" }}
            onClick={onStop}
            disabled={!isRunning}
          >
            Stop
          </button>
        </div>
      )}

      <div className="form-row">
        <label>Poll interval (seconds)</label>
        <input
          type="number"
          value={pollInterval}
          onChange={(e) => onPollChange(Number(e.target.value))}
          style={{ width: 80 }}
          min={1}
          max={3600}
          disabled={!isOperator || isRunning}
        />
      </div>

      <div className="form-row">
        <label>Registers ({registers.length})</label>
      </div>

      <table className="data-table" style={{ marginBottom: "10px" }}>
        <thead>
          <tr>
            <th>Device ID</th>
            <th>Address</th>
            <th>FC</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {registers.map((reg, idx) => (
            <tr key={idx}>
              <td style={{ fontFamily: "var(--font-sans)" }}>{reg.device_id}</td>
              <td>{reg.address}</td>
              <td>FC{reg.function_code}</td>
              <td>
                {isOperator && (
                  <button
                    className="btn-danger"
                    style={{ padding: "2px 8px", fontSize: "11px" }}
                    onClick={() => onRemoveRow(idx)}
                  >
                    Del
                  </button>
                )}
              </td>
            </tr>
          ))}
          {registers.length === 0 && (
            <tr>
              <td colSpan={4} style={{ textAlign: "center", color: "var(--text-muted)" }}>
                No registers configured
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {isOperator && (
        <div className="form-inline" style={{ marginBottom: "8px" }}>
          <div className="form-row">
            <label>Device</label>
            <input
              placeholder="dev1"
              value={newDev}
              onChange={(e) => onNewDevChange(e.target.value)}
              style={{ width: 100 }}
            />
          </div>
          <div className="form-row">
            <label>Addr</label>
            <input
              type="number"
              value={newAddr}
              onChange={(e) => onNewAddrChange(Number(e.target.value))}
              style={{ width: 70 }}
              min={0}
              max={65535}
            />
          </div>
          <div className="form-row">
            <label>FC</label>
            <select value={newFc} onChange={(e) => onNewFcChange(Number(e.target.value))}>
              <option value={1}>FC1</option>
              <option value={2}>FC2</option>
              <option value={3}>FC3</option>
              <option value={4}>FC4</option>
            </select>
          </div>
          <button className="btn-primary" style={{ padding: "8px 14px", fontSize: "12px" }} onClick={onAddRow}>
            Add
          </button>
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
