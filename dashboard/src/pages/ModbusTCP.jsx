import { useState, useEffect } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { apiGet, apiPost, apiDelete } from "../api/client.js";
import StatusLed from "../components/StatusLed.jsx";
import { useToast } from "../components/Toast.jsx";

const FC_OPTIONS = [
  { value: 3, label: "FC3 — Read Holding Register" },
  { value: 4, label: "FC4 — Read Input Register" },
  { value: 1, label: "FC1 — Read Coils" },
  { value: 2, label: "FC2 — Read Discrete Inputs" },
  { value: 5, label: "FC5 — Write Single Coil" },
  { value: 6, label: "FC6 — Write Single Register" },
  { value: 15, label: "FC15 — Write Multiple Coils" },
  { value: 16, label: "FC16 — Write Multiple Registers" },
];

export default function ModbusTCP() {
  const { role } = useAuth();
  const { showToast } = useToast();
  const isAdmin = role === "admin";
  const isOperator = role === "operator" || isAdmin;

  const [status, setStatus] = useState({});
  const [entries, setEntries] = useState([]);
  const [config, setConfig] = useState({ host: "0.0.0.0", port: 502 });
  const [newEntry, setNewEntry] = useState({
    function_code: 3, address: 0, source_key: "", label: "",
  });
  const [showAdd, setShowAdd] = useState(false);
  const [validationErrors, setValidationErrors] = useState([]);

  const fetchStatus = async () => {
    try {
      const r = await apiGet("/api/modbus-tcp/status");
      setStatus(await r.json());
    } catch {}
  };

  const fetchEntries = async () => {
    try {
      const r = await apiGet("/api/modbus-tcp/register-map");
      setEntries((await r.json()).entries || []);
    } catch {}
  };

  const fetchConfig = async () => {
    try {
      const r = await apiGet("/api/modbus-tcp/config");
      setConfig(await r.json());
    } catch {}
  };

  useEffect(() => { fetchStatus(); fetchEntries(); fetchConfig(); }, []);

  const handleStart = async () => {
    const r = await apiPost("/api/modbus-tcp/start", { port: config.port });
    if (r.ok) showToast("Server started", "success");
    else showToast((await r.json()).error || "Failed", "error");
    fetchStatus();
  };

  const handleStop = async () => {
    await apiPost("/api/modbus-tcp/stop", {});
    showToast("Server stopped", "success");
    fetchStatus();
  };

  const handleSaveConfig = async () => {
    const r = await apiPost("/api/modbus-tcp/config", { port: config.port });
    if (r.ok) showToast("Config updated", "success");
    else showToast((await r.json()).error || "Failed", "error");
    fetchStatus();
  };

  const handleValidate = async () => {
    const r = await apiPost("/api/modbus-tcp/register-map/validate", { entries });
    const d = await r.json();
    setValidationErrors(d.errors || []);
    if (d.valid) showToast("Valid", "success");
    else showToast(`${d.errors.length} error(s)`, "error");
  };

  const handleSaveEntries = async () => {
    const r = await apiPost("/api/modbus-tcp/register-map", { entries });
    if (r.ok) {
      showToast(`Saved (${entries.length} entries)`, "success");
      fetchEntries();
    } else showToast((await r.json()).error || "Save failed", "error");
  };

  const addEntry = () => {
    if (!newEntry.source_key.trim()) return;
    setEntries([...entries, { ...newEntry, source_key: newEntry.source_key.trim() }]);
    setNewEntry({ function_code: 3, address: 0, source_key: "", label: "" });
    setShowAdd(false);
  };

  const removeEntry = (idx) => {
    setEntries(entries.filter((_, i) => i !== idx));
  };

  return (
    <div>
      <div className="page-header">
        <h2>Modbus TCP Server</h2>
        <p>Expose DI/DO/CAN state to external SCADA/HMI over Modbus TCP</p>
      </div>

      <div className="card">
        <div className="card-header">
          Status
          <StatusLed
            status={status.running ? "ok" : "off"}
            label={status.running ? "Running" : "Stopped"}
          />
        </div>
        <div style={{ display: "flex", gap: "20px", fontSize: "13px", marginBottom: "12px" }}>
          <span>Bind: <strong className="mono">{config.host}:{config.port}</strong></span>
          <span>Entries: <strong className="mono">{status.entries ?? entries.length}</strong></span>
          <span>Clients: <strong className="mono">{status.client_count ?? 0}</strong></span>
        </div>
        {isOperator && (
          <div style={{ display: "flex", gap: "8px" }}>
            <button className="btn-primary" onClick={handleStart} disabled={status.running}>
              Start
            </button>
            <button className="btn-default" onClick={handleStop} disabled={!status.running}>
              Stop
            </button>
          </div>
        )}
      </div>

      {isAdmin && (
        <div className="card">
          <div className="card-header">Bind Settings</div>
          <div className="form-inline">
            <div className="form-row">
              <label>Port</label>
              <input type="number" value={config.port}
                onChange={(e) => setConfig({ ...config, port: Number(e.target.value) })}
                style={{ width: 80 }} min={1024} max={65535} />
            </div>
            <button className="btn-primary" onClick={handleSaveConfig}>Apply</button>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-header">
          Register Map ({entries.length})
          {isAdmin && (
            <button className="btn-primary" style={{ padding: "4px 12px", fontSize: "11px" }}
              onClick={() => setShowAdd(!showAdd)}>
              {showAdd ? "Cancel" : "Add Entry"}
            </button>
          )}
        </div>

        {showAdd && (
          <div className="form-inline" style={{ marginBottom: "12px" }}>
            <div className="form-row">
              <label>Function Code</label>
              <select value={newEntry.function_code}
                onChange={(e) => setNewEntry({ ...newEntry, function_code: Number(e.target.value) })}>
                {FC_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div className="form-row">
              <label>Address</label>
              <input type="number" value={newEntry.address}
                onChange={(e) => setNewEntry({ ...newEntry, address: Number(e.target.value) })}
                style={{ width: 80 }} min={0} max={65535} />
            </div>
            <div className="form-row">
              <label>Source Key</label>
              <input placeholder="e.g. di:0, can:status.rx_total" value={newEntry.source_key}
                onChange={(e) => setNewEntry({ ...newEntry, source_key: e.target.value })}
                style={{ width: 200 }} />
            </div>
            <div className="form-row">
              <label>Label</label>
              <input placeholder="Optional" value={newEntry.label}
                onChange={(e) => setNewEntry({ ...newEntry, label: e.target.value })}
                style={{ width: 120 }} />
            </div>
            <button className="btn-primary" onClick={addEntry}>Add</button>
          </div>
        )}

        <table className="data-table" style={{ marginBottom: "10px" }}>
          <thead>
            <tr><th>FC</th><th>Address</th><th>Source</th><th>Label</th><th></th></tr>
          </thead>
          <tbody>
            {entries.map((e, idx) => (
              <tr key={idx}>
                <td>FC{e.function_code}</td>
                <td>{e.address}</td>
                <td style={{ fontFamily: "var(--font-mono)" }}>{e.source_key}</td>
                <td style={{ fontFamily: "var(--font-sans)" }}>{e.label || "—"}</td>
                <td>{isAdmin && <button className="btn-danger"
                  style={{ padding: "2px 8px", fontSize: "11px" }}
                  onClick={() => removeEntry(idx)}>Del</button>}</td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>
                No entries. Add at least one to define the register map.
              </td></tr>
            )}
          </tbody>
        </table>

        {isAdmin && (
          <div style={{ display: "flex", gap: "8px" }}>
            <button className="btn-default" onClick={handleValidate}>Validate</button>
            <button className="btn-primary" onClick={handleSaveEntries}>Save</button>
          </div>
        )}

        {validationErrors.length > 0 && (
          <div style={{ marginTop: "12px", padding: "8px 12px", background: "#3a1a1a",
            border: "1px solid var(--status-err)", borderRadius: "var(--radius)",
            fontSize: "12px", color: "var(--status-err)" }}>
            {validationErrors.map((e, i) => <div key={i}>• {e}</div>)}
          </div>
        )}
      </div>
    </div>
  );
}
