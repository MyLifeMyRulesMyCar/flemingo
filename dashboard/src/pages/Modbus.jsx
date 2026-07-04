import { useState, useEffect } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { apiGet, apiPost, apiDelete } from "../api/client.js";
import StatusLed from "../components/StatusLed.jsx";
import ConfirmModal from "../components/ConfirmModal.jsx";
import { useToast } from "../components/Toast.jsx";

const BAUDRATES = [9600, 19200, 38400, 57600, 115200, 230400];

export default function Modbus() {
  const { role } = useAuth();
  const { showToast } = useToast();
  const [devices, setDevices] = useState([]);
  const [ports, setPorts] = useState({});

  const [showAdd, setShowAdd] = useState(false);
  const [newDev, setNewDev] = useState({
    name: "", port: "", slave_id: 1, baudrate: 115200, parity: "N", stopbits: 1,
  });

  const [readDev, setReadDev] = useState("");
  const [readAddr, setReadAddr] = useState(0);
  const [readFc, setReadFc] = useState(3);
  const [readResult, setReadResult] = useState(null);

  const [writeDev, setWriteDev] = useState("");
  const [writeAddr, setWriteAddr] = useState(0);
  const [writeVal, setWriteVal] = useState(0);
  const [writeFc, setWriteFc] = useState(6);

  const [delTarget, setDelTarget] = useState(null);
  const isAdmin = role === "admin";
  const isOperator = role === "operator" || isAdmin;

  const fetchDevices = async () => {
    try {
      const r = await apiGet("/api/modbus/devices");
      setDevices((await r.json()).devices || []);
    } catch {}
  };

  useEffect(() => {
    fetchDevices();
    apiGet("/api/modbus/ports")
      .then((r) => r.json())
      .then((d) => setPorts(d.ports || {}))
      .catch(() => {});
  }, []);

  const handleAdd = async () => {
    const r = await apiPost("/api/modbus/devices", newDev);
    const d = await r.json();
    if (r.ok) {
      showToast("Device added", "success");
      setShowAdd(false);
      fetchDevices();
    } else {
      showToast(d.error || "Add failed", "error");
    }
  };

  const handleDelete = async () => {
    const r = await apiDelete(`/api/modbus/devices/${delTarget}`);
    if (r.ok) {
      showToast("Device deleted", "success");
      fetchDevices();
    } else {
      showToast((await r.json()).error || "Delete failed", "error");
    }
    setDelTarget(null);
  };

  const handleConnect = async (id) => {
    const r = await apiPost(`/api/modbus/devices/${id}/connect`, {});
    if (r.ok) showToast("Connected", "success");
    else showToast((await r.json()).error || "Connect failed", "error");
    fetchDevices();
  };

  const handleDisconnect = async (id) => {
    const r = await apiPost(`/api/modbus/devices/${id}/disconnect`, {});
    showToast("Disconnected", "success");
    fetchDevices();
  };

  const handleRead = async () => {
    const r = await apiPost(`/api/modbus/devices/${readDev}/read`, {
      address: Number(readAddr),
      function_code: readFc,
    });
    const d = await r.json();
    if (r.ok) {
      setReadResult(d.value);
      showToast(`Read: ${d.value}`, "success");
    } else {
      showToast(d.error || "Read failed", "error");
    }
  };

  const handleWrite = async () => {
    const r = await apiPost(`/api/modbus/devices/${writeDev}/write`, {
      address: Number(writeAddr),
      value: Number(writeVal),
      function_code: writeFc,
    });
    const d = await r.json();
    if (r.ok) showToast(`Wrote: ${writeVal}`, "success");
    else showToast(d.error || "Write failed", "error");
  };

  return (
    <div>
      <div className="page-header">
        <h2>Modbus RTU</h2>
        <p>RS485 device management</p>
      </div>

      <div className="card">
        <div className="card-header">
          Devices ({devices.length})
          {isAdmin && (
            <button className="btn-primary" onClick={() => setShowAdd(!showAdd)}>
              {showAdd ? "Cancel" : "Add Device"}
            </button>
          )}
        </div>

        {showAdd && (
          <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", marginBottom: "16px" }}>
            <input placeholder="Name" value={newDev.name}
              onChange={(e) => setNewDev({ ...newDev, name: e.target.value })}
              style={{ width: 130 }} />
            <select value={newDev.port}
              onChange={(e) => setNewDev({ ...newDev, port: e.target.value })}>
              <option value="">Port...</option>
              {Object.keys(ports).map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <input type="number" placeholder="Slave ID" value={newDev.slave_id}
              onChange={(e) => setNewDev({ ...newDev, slave_id: Number(e.target.value) })}
              style={{ width: 80 }} min={1} max={247} />
            <select value={newDev.baudrate}
              onChange={(e) => setNewDev({ ...newDev, baudrate: Number(e.target.value) })}>
              {BAUDRATES.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
            <button className="btn-primary" onClick={handleAdd}>Save</button>
          </div>
        )}

        <table className="data-table device-table">
          <thead>
            <tr>
              <th>Name</th><th>Port</th><th>Slave</th><th>Baud</th>
              <th>Connected</th><th>Breaker</th><th></th>
            </tr>
          </thead>
          <tbody>
            {devices.map((d) => (
              <tr key={d.id}>
                <td style={{ fontFamily: "var(--font-sans)" }}>{d.name}</td>
                <td>{d.port}</td>
                <td>{d.slave_id}</td>
                <td>{d.baudrate}</td>
                <td><StatusLed status={d.connected ? "ok" : "off"} /></td>
                <td>{d.circuit_breaker?.state || "closed"}</td>
                <td>
                  {isOperator && !d.connected && (
                    <button className="btn-primary" style={{ padding: "4px 10px", fontSize: "11px" }}
                      onClick={() => handleConnect(d.id)}>Connect</button>
                  )}
                  {isOperator && d.connected && (
                    <button className="btn-default" style={{ padding: "4px 10px", fontSize: "11px" }}
                      onClick={() => handleDisconnect(d.id)}>Disconnect</button>
                  )}
                  {isAdmin && (
                    <button className="btn-danger" style={{ padding: "4px 10px", fontSize: "11px", marginLeft: 6 }}
                      onClick={() => setDelTarget(d.id)}>Del</button>
                  )}
                </td>
              </tr>
            ))}
            {devices.length === 0 && (
              <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text-muted)" }}>
                No devices configured
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-header">Read Register</div>
        <div className="form-inline">
          <div className="form-row">
            <label>Device</label>
            <select value={readDev} onChange={(e) => setReadDev(e.target.value)}>
              <option value="">Select...</option>
              {devices.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </div>
          <div className="form-row">
            <label>Address</label>
            <input type="number" value={readAddr} onChange={(e) => setReadAddr(e.target.value)}
              style={{ width: 80 }} min={0} max={65535} />
          </div>
          <div className="form-row">
            <label>FC</label>
            <select value={readFc} onChange={(e) => setReadFc(Number(e.target.value))}>
              <option value={1}>FC1 (Coil)</option>
              <option value={2}>FC2 (Discrete)</option>
              <option value={3}>FC3 (Holding)</option>
              <option value={4}>FC4 (Input)</option>
            </select>
          </div>
          <button className="btn-primary" onClick={handleRead} disabled={!readDev}>
            Read
          </button>
        </div>
        {readResult !== null && (
          <div className="read-result">{readResult}</div>
        )}
      </div>

      <div className="card">
        <div className="card-header">Write Register</div>
        <div className="form-inline">
          <div className="form-row">
            <label>Device</label>
            <select value={writeDev} onChange={(e) => setWriteDev(e.target.value)}>
              <option value="">Select...</option>
              {devices.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </div>
          <div className="form-row">
            <label>Address</label>
            <input type="number" value={writeAddr} onChange={(e) => setWriteAddr(e.target.value)}
              style={{ width: 80 }} min={0} max={65535} />
          </div>
          <div className="form-row">
            <label>Value</label>
            <input type="number" value={writeVal} onChange={(e) => setWriteVal(e.target.value)}
              style={{ width: 80 }} min={0} max={65535} />
          </div>
          <div className="form-row">
            <label>FC</label>
            <select value={writeFc} onChange={(e) => setWriteFc(Number(e.target.value))}>
              <option value={6}>FC6 (Holding)</option>
              <option value={5}>FC5 (Coil)</option>
            </select>
          </div>
          <button className="btn-primary" onClick={handleWrite}
            disabled={!writeDev || !isOperator}>
            Write
          </button>
        </div>
      </div>

      <ConfirmModal isOpen={!!delTarget}
        title="Delete Device"
        message={`Permanently remove device '${delTarget}'?`}
        confirmLabel="Delete"
        danger
        onConfirm={handleDelete}
        onCancel={() => setDelTarget(null)} />
    </div>
  );
}
