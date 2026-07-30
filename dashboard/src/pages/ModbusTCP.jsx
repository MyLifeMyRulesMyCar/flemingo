import { useState, useEffect, useRef } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { apiGet, apiPost } from "../api/client.js";
import StatusLed from "../components/StatusLed.jsx";
import ConfirmModal from "../components/ConfirmModal.jsx";
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

const ADDR_COLORS = {
  local: "#2a7a2a",
  modbus_rtu: "#2a4a7a",
  can_send_channel: "#7a6a2a",
  overlap: "#7a2a2a",
};

function AddressMapStrip({ entries, channels }) {
  const occupied = [];

  for (const e of entries) {
    const kind = [1, 5, 15].includes(e.function_code) ? "coil" : "holding";
    occupied.push({
      kind,
      address: e.address,
      source: e.source_type || "local",
      label: e.label || e.source_key || `FC${e.function_code}`,
    });
  }

  for (const ch of channels) {
    const addrs = [
      { kind: "holding", address: ch.id_address, label: `${ch.name} ID` },
      { kind: "coil", address: ch.trigger_coil_address, label: `${ch.name} trigger` },
      { kind: "holding", address: ch.dlc_address, label: `${ch.name} DLC` },
    ];
    for (let i = 0; i < 4; i++) {
      addrs.push({ kind: "holding", address: ch.data_start_address + i, label: `${ch.name} data${i}` });
    }
    for (const a of addrs) {
      const existing = occupied.find(
        (o) => o.kind === a.kind && o.address === a.address
      );
      if (existing) {
        existing.source = "overlap";
        existing.label += " / " + a.label;
      } else {
        occupied.push({ ...a, source: "can_send_channel" });
      }
    }
  }

  occupied.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind < b.kind ? -1 : 1;
    return a.address - b.address;
  });

  if (occupied.length === 0) return null;

  const total = occupied.length;
  const pct = 100 / total;

  return (
    <div style={{ marginTop: "12px", marginBottom: "12px" }}>
      <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "4px" }}>
        Address Map
      </div>
      <div style={{ display: "flex", height: "24px", borderRadius: "var(--radius)", overflow: "hidden" }}>
        {occupied.map((o, i) => (
          <div
            key={i}
            title={`${o.kind} ${o.address}: ${o.label}`}
            style={{
              width: `${pct}%`,
              backgroundColor: ADDR_COLORS[o.source] || "#555",
              borderRight: i < total - 1 ? "1px solid #111" : "none",
            }}
          />
        ))}
      </div>
      <div style={{ display: "flex", gap: "12px", marginTop: "4px", fontSize: "11px", color: "var(--text-muted)" }}>
        <span><span style={{ color: ADDR_COLORS.local, fontWeight: "bold" }}>■</span> Local</span>
        <span><span style={{ color: ADDR_COLORS.modbus_rtu, fontWeight: "bold" }}>■</span> RTU</span>
        <span><span style={{ color: ADDR_COLORS.can_send_channel, fontWeight: "bold" }}>■</span> CAN Send</span>
        <span><span style={{ color: ADDR_COLORS.overlap, fontWeight: "bold" }}>■</span> Overlap</span>
      </div>
    </div>
  );
}

function ExceptionToastFeed({ serverRunning, lastSeenRef }) {
  const { showToast } = useToast();

  useEffect(() => {
    if (!serverRunning) return;
    let active = true;
    const poll = async () => {
      try {
        const r = await apiGet("/api/modbus-tcp/recent-exceptions");
        const d = await r.json();
        const exs = d.exceptions || [];
        for (const ex of exs) {
          if (!active) break;
          if (!lastSeenRef.current || ex.timestamp > lastSeenRef.current) {
            lastSeenRef.current = ex.timestamp;
            showToast(`FC${ex.function_code} addr ${ex.address}: ${ex.message}`, "error");
          }
        }
      } catch {}
    };
    poll();
    const t = setInterval(poll, 3000);
    return () => { active = false; clearInterval(t); };
  }, [serverRunning]);

  return null;
}

function ServerCard({
  status, config, isOperator, isAdmin,
  onStart, onStop, onSaveConfig, setConfig, isolationWarning,
}) {
  return (
    <div className="card">
      <div className="card-header">
        Server
        <StatusLed
          status={status.running ? "ok" : "off"}
          label={status.running ? "Running" : "Stopped"}
        />
      </div>
      <div style={{ display: "flex", gap: "20px", fontSize: "13px", marginBottom: "12px" }}>
        <span>Bind: <strong className="mono">{config.host}:{config.port}</strong></span>
        <span>Entries: <strong className="mono">{status.entries ?? 0}</strong></span>
        <span>Clients: <strong className="mono">{status.client_count ?? 0}</strong></span>
        <span>Exceptions: <strong className="mono">{status.exceptions ?? 0}</strong></span>
      </div>
      {isOperator && (
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="btn-primary" onClick={onStart} disabled={status.running}>
            Start
          </button>
          <button className="btn-default" onClick={onStop} disabled={!status.running}>
            Stop
          </button>
        </div>
      )}
      {isAdmin && (
        <div className="form-inline" style={{ marginTop: "12px" }}>
          <div className="form-row">
            <label>Port</label>
            <input type="number" value={config.port}
              onChange={(e) => setConfig({ ...config, port: Number(e.target.value) })}
              style={{ width: 80 }} min={1024} max={65535} />
          </div>
          <button className="btn-primary" onClick={onSaveConfig} disabled={status.running}>
            Apply
          </button>
        </div>
      )}
      {isolationWarning && (
        <div style={{
          background: "#3a2a0a", border: "1px solid var(--status-warn)",
          color: "var(--status-warn)", padding: "8px 12px",
          borderRadius: "var(--radius)", fontSize: "12px", marginTop: "10px",
        }}>
          {isolationWarning}
        </div>
      )}
    </div>
  );
}

function NetworkCard({
  network, candidate, setCandidate, revertStatus,
  isAdmin, showNetworkConfirm, setShowNetworkConfirm,
  onApply, onConfirm,
}) {
  return (
    <div className="card">
      <div className="card-header">Network (eth1)</div>
      <div style={{ fontSize: "13px", marginBottom: "12px", display: "flex", gap: "20px" }}>
        <span>IP: <strong className="mono">{network.ip === "unknown" ? "—" : network.ip || "—"}</strong></span>
        <span>Subnet: <strong className="mono">/{network.prefix_len || "—"}</strong></span>
        <span>Gateway: <strong className="mono">{network.gateway || "—"}</strong></span>
      </div>

      {revertStatus.pending && (
        <div style={{
          background: "#3a2a0a", border: "1px solid var(--status-warn)",
          color: "var(--status-warn)", padding: "10px 14px", borderRadius: "var(--radius)",
          fontSize: "13px", marginBottom: "12px",
        }}>
          Config pending — reverts in {revertStatus.revert_at
            ? Math.max(0, Math.ceil(revertStatus.revert_at - Date.now() / 1000))
            : "?"}s unless confirmed.
          <button className="btn-primary" style={{ marginLeft: "12px", padding: "4px 12px", fontSize: "11px" }}
            onClick={onConfirm}>Confirm</button>
        </div>
      )}

      {isAdmin && (
        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "flex-end" }}>
          <div className="form-row">
            <label>Static IP</label>
            <input placeholder="e.g. 192.168.2.100" value={candidate.ip}
              onChange={(e) => setCandidate({ ...candidate, ip: e.target.value })}
              style={{ width: 150 }} />
          </div>
          <div className="form-row">
            <label>Prefix</label>
            <input type="number" value={candidate.prefix_len}
              onChange={(e) => setCandidate({ ...candidate, prefix_len: Number(e.target.value) })}
              style={{ width: 60 }} min={1} max={32} />
          </div>
          <div className="form-row">
            <label>Gateway</label>
            <input placeholder="e.g. 192.168.2.1" value={candidate.gateway}
              onChange={(e) => setCandidate({ ...candidate, gateway: e.target.value })}
              style={{ width: 150 }} />
          </div>
          <button className="btn-primary"
            onClick={() => setShowNetworkConfirm(true)}
            disabled={!candidate.ip || !candidate.gateway}>
            Apply
          </button>
        </div>
      )}

      <ConfirmModal
        isOpen={showNetworkConfirm}
        title="Change Network Address?"
        message={`This will change eth1 to ${candidate.ip}/${candidate.prefix_len}. The page will redirect to the new address. The change reverts automatically in 60s unless you confirm.`}
        confirmLabel="Change IP"
        danger
        onConfirm={onApply}
        onCancel={() => setShowNetworkConfirm(false)}
      />
    </div>
  );
}

function RegisterMapCard({
  entries, isAdmin, isOperator,
  showAdd, setShowAdd,
  newEntry, setNewEntry,
  onAddEntry, onRemoveEntry,
  onValidate, onSave,
  validationErrors,
  testWriteEntry, setTestWriteEntry,
  testWriteValue, setTestWriteValue,
  testWriteResult, onSubmitTestWrite,
}) {
  const [showWritableConfirm, setShowWritableConfirm] = useState(false);

  const handleWritableChange = (e) => {
    if (e.target.checked) {
      setShowWritableConfirm(true);
    } else {
      setNewEntry({ ...newEntry, writable: false });
    }
  };

  const confirmWritable = () => {
    setNewEntry({ ...newEntry, writable: true });
    setShowWritableConfirm(false);
  };

  return (
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
            <label>Type</label>
            <select value={newEntry.source_type}
              onChange={(e) => setNewEntry({ ...newEntry, source_type: e.target.value })}>
              <option value="local">Local (DI/DO/CAN)</option>
              <option value="modbus_rtu">Modbus RTU</option>
            </select>
          </div>
          {newEntry.source_type === "local" ? (
            <>
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
            </>
          ) : (
            <>
              <div className="form-row">
                <label>TCP Address</label>
                <input type="number" value={newEntry.address}
                  onChange={(e) => setNewEntry({ ...newEntry, address: Number(e.target.value) })}
                  style={{ width: 80 }} min={0} max={65535} />
              </div>
              <div className="form-row">
                <label>RTU Device ID</label>
                <input placeholder="e.g. dev1" value={newEntry.rtu_device_id}
                  onChange={(e) => setNewEntry({ ...newEntry, rtu_device_id: e.target.value })}
                  style={{ width: 80 }} />
              </div>
              <div className="form-row">
                <label>RTU Address</label>
                <input type="number" value={newEntry.rtu_address}
                  onChange={(e) => setNewEntry({ ...newEntry, rtu_address: Number(e.target.value) })}
                  style={{ width: 80 }} min={0} max={65535} />
              </div>
              <div className="form-row">
                <label>Writable</label>
                <input type="checkbox" checked={newEntry.writable}
                  onChange={handleWritableChange} />
              </div>
            </>
          )}
          <div className="form-row">
            <label>Label</label>
            <input placeholder="Optional" value={newEntry.label}
              onChange={(e) => setNewEntry({ ...newEntry, label: e.target.value })}
              style={{ width: 120 }} />
          </div>
          <button className="btn-primary" onClick={onAddEntry}>Add</button>
        </div>
      )}

      <table className="data-table" style={{ marginBottom: "10px" }}>
        <thead>
          <tr><th>FC</th><th>Addr</th><th>Type</th><th>Source</th><th>W</th><th>Label</th><th></th></tr>
        </thead>
        <tbody>
          {entries.map((e, idx) => (
            <tr key={idx}>
              <td>FC{e.function_code}</td>
              <td>{e.address}</td>
              <td>{e.source_type === "modbus_rtu" ? (
                <span title={`RTU device=${e.rtu_device_id} addr=${e.rtu_address}`}>RTU</span>
              ) : "Local"}</td>
              <td style={{ fontFamily: "var(--font-mono)" }}>{e.source_key}</td>
              <td>{e.writable ? "✓" : "—"}</td>
              <td style={{ fontFamily: "var(--font-sans)" }}>{e.label || "—"}</td>
              <td>
                {e.source_type === "modbus_rtu" && e.writable && isOperator && (
                  <button className="btn-default"
                    style={{ padding: "2px 8px", fontSize: "11px", marginRight: "4px" }}
                    onClick={() => setTestWriteEntry(e)}>Test Write</button>
                )}
                {isAdmin && <button className="btn-danger"
                  style={{ padding: "2px 8px", fontSize: "11px" }}
                  onClick={() => onRemoveEntry(idx)}>Del</button>}
              </td>
            </tr>
          ))}
          {entries.length === 0 && (
            <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text-muted)" }}>
              No entries. Add at least one to define the register map.
            </td></tr>
          )}
        </tbody>
      </table>

      {isAdmin && (
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="btn-default" onClick={onValidate}>Validate</button>
          <button className="btn-primary" onClick={onSave}>Save</button>
        </div>
      )}

      {validationErrors.length > 0 && (
        <div style={{ marginTop: "12px", padding: "8px 12px", background: "#3a1a1a",
          border: "1px solid var(--status-err)", borderRadius: "var(--radius)",
          fontSize: "12px", color: "var(--status-err)" }}>
          {validationErrors.map((e, i) => <div key={i}>• {e}</div>)}
        </div>
      )}

      {testWriteEntry && (
        <div style={{ marginTop: "12px", padding: "12px", background: "#1a1a2a",
          border: "1px solid var(--border)", borderRadius: "var(--radius)", fontSize: "13px" }}>
          <strong>Test Write</strong> — FC{testWriteEntry.function_code} addr {testWriteEntry.address}
          {" → "}RTU device {testWriteEntry.rtu_device_id} addr {testWriteEntry.rtu_address}
          <div style={{ display: "flex", gap: "8px", marginTop: "8px", alignItems: "center" }}>
            <input type="number" placeholder="Value (0–65535)" value={testWriteValue}
              onChange={(e) => setTestWriteValue(e.target.value)}
              style={{ width: 140 }} min={0} max={65535} />
            <button className="btn-primary" onClick={onSubmitTestWrite}>Send</button>
            <button className="btn-default" onClick={() => setTestWriteEntry(null)}>Close</button>
          </div>
          {testWriteResult && (
            <div style={{ marginTop: "8px", color: testWriteResult.ok ? "var(--status-ok)" : "var(--status-err)" }}>
              {testWriteResult.message}
            </div>
          )}
        </div>
      )}

      <ConfirmModal
        isOpen={showWritableConfirm}
        title="Make Register Writable?"
        message="This lets any connected SCADA client write to this device's register. Continue?"
        confirmLabel="Yes, make writable"
        danger
        onConfirm={confirmWritable}
        onCancel={() => setShowWritableConfirm(false)}
      />
    </div>
  );
}

function CANSendChannelsCard({
  channels, isAdmin,
  showAdd, setShowAdd,
  newChannel, setNewChannel,
  onAdd, onRemove, onSave,
}) {
  const formatLastTrigger = (t) => {
    if (!t) return "—";
    const id = "0x" + t.id.toString(16).toUpperCase();
    const data = "[" + (t.data || []).join(", ") + "]";
    const ts = t.timestamp ? t.timestamp.slice(11, 19) : "?";
    if (t.success) return `${id} ${data} dlc=${t.dlc} @ ${ts}`;
    return `${t.error} @ ${ts}`;
  };

  return (
    <div className="card">
      <div className="card-header">
        CAN Send Channels ({channels.length})
        {isAdmin && (
          <button className="btn-primary" style={{ padding: "4px 12px", fontSize: "11px" }}
            onClick={() => setShowAdd(!showAdd)}>
            {showAdd ? "Cancel" : "Add Send Channel"}
          </button>
        )}
      </div>

      {showAdd && (
        <div className="form-inline" style={{ marginBottom: "12px" }}>
          <div className="form-row">
            <label>Name</label>
            <input placeholder="Channel name" value={newChannel.name}
              onChange={(e) => setNewChannel({ ...newChannel, name: e.target.value })}
              style={{ width: 120 }} />
          </div>
          <div className="form-row">
            <label>ID Reg</label>
            <input type="number" value={newChannel.id_address}
              onChange={(e) => setNewChannel({ ...newChannel, id_address: Number(e.target.value) })}
              style={{ width: 80 }} min={0} max={65535} />
          </div>
          <div className="form-row">
            <label>Data Start</label>
            <input type="number" value={newChannel.data_start_address}
              onChange={(e) => setNewChannel({ ...newChannel, data_start_address: Number(e.target.value) })}
              style={{ width: 80 }} min={0} max={65535} />
          </div>
          <div className="form-row">
            <label>DLC Reg</label>
            <input type="number" value={newChannel.dlc_address}
              onChange={(e) => setNewChannel({ ...newChannel, dlc_address: Number(e.target.value) })}
              style={{ width: 80 }} min={0} max={65535} />
          </div>
          <div className="form-row">
            <label>Trigger Coil</label>
            <input type="number" value={newChannel.trigger_coil_address}
              onChange={(e) => setNewChannel({ ...newChannel, trigger_coil_address: Number(e.target.value) })}
              style={{ width: 80 }} min={0} max={65535} />
          </div>
          <button className="btn-primary" onClick={onAdd}>Add</button>
        </div>
      )}

      {channels.length > 0 && (
        <table className="data-table" style={{ marginBottom: "10px" }}>
          <thead>
            <tr>
              <th>Name</th>
              <th>ID (Reg)</th>
              <th>Data (Regs 4)</th>
              <th>DLC (Reg)</th>
              <th>Trigger (Coil)</th>
              <th>Last Trigger</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {channels.map((ch, idx) => (
              <tr key={idx}>
                <td style={{ fontFamily: "var(--font-mono)" }}>{ch.name}</td>
                <td>{ch.id_address}</td>
                <td>{ch.data_start_address}–{ch.data_start_address + 3}</td>
                <td>{ch.dlc_address}</td>
                <td>{ch.trigger_coil_address}</td>
                <td style={{
                  fontSize: "11px",
                  color: ch.last_trigger && !ch.last_trigger.success
                    ? "var(--status-err)" : "var(--text-muted)",
                }}>
                  {formatLastTrigger(ch.last_trigger)}
                </td>
                <td>{isAdmin && (
                  <button className="btn-danger" style={{ padding: "2px 8px", fontSize: "11px" }}
                    onClick={() => onRemove(idx)}>Del</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {channels.length === 0 && (
        <div style={{ textAlign: "center", color: "var(--text-muted)", fontSize: "13px", marginBottom: "8px" }}>
          No CAN send channels defined.
        </div>
      )}

      <div style={{
        background: "#1a1a2a", border: "1px solid #334",
        color: "var(--text-muted)", padding: "8px 12px",
        borderRadius: "var(--radius)", fontSize: "12px", marginTop: "8px",
      }}>
        Write all 6 registers in a single multi-register write (FC 16),
        then write the trigger coil separately. Writing them individually
        does not guarantee the frame sent matches what you intended.
      </div>

      {isAdmin && channels.length > 0 && (
        <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
          <button className="btn-primary" onClick={onSave}>Save Channels</button>
        </div>
      )}
    </div>
  );
}

export default function ModbusTCP() {
  const { role } = useAuth();
  const { showToast } = useToast();
  const isAdmin = role === "admin";
  const isOperator = role === "operator" || isAdmin;
  const lastSeenRef = useRef(null);

  const [status, setStatus] = useState({});
  const [entries, setEntries] = useState([]);
  const [config, setConfig] = useState({ host: "0.0.0.0", port: 502 });
  const [newEntry, setNewEntry] = useState({
    function_code: 3, address: 0, source_key: "", label: "",
    source_type: "local", rtu_device_id: "", rtu_address: 0, writable: false,
  });
  const [showAdd, setShowAdd] = useState(false);
  const [validationErrors, setValidationErrors] = useState([]);
  const [network, setNetwork] = useState({ ip: "", prefix_len: 24, gateway: "" });
  const [candidate, setCandidate] = useState({ ip: "", prefix_len: 24, gateway: "" });
  const [revertStatus, setRevertStatus] = useState({});
  const [showNetworkConfirm, setShowNetworkConfirm] = useState(false);
  const [isolationWarning, setIsolationWarning] = useState("");
  const [testWriteEntry, setTestWriteEntry] = useState(null);
  const [testWriteValue, setTestWriteValue] = useState("");
  const [testWriteResult, setTestWriteResult] = useState(null);
  const [canChannels, setCanChannels] = useState([]);
  const [showAddChannel, setShowAddChannel] = useState(false);
  const [newChannel, setNewChannel] = useState({
    name: "", id_address: 0, data_start_address: 0,
    dlc_address: 0, trigger_coil_address: 0,
  });

  const fetchStatus = async () => {
    try { const r = await apiGet("/api/modbus-tcp/status"); setStatus(await r.json()); } catch {}
  };
  const fetchEntries = async () => {
    try { const r = await apiGet("/api/modbus-tcp/register-map"); setEntries((await r.json()).entries || []); } catch {}
  };
  const fetchConfig = async () => {
    try { const r = await apiGet("/api/modbus-tcp/config"); setConfig(await r.json()); } catch {}
  };
  const fetchNetwork = async () => {
    try { const r = await apiGet("/api/network/config"); setNetwork(await r.json()); } catch {}
  };
  const fetchRevertStatus = async () => {
    try { const r = await apiGet("/api/network/status"); setRevertStatus(await r.json()); } catch {}
  };
  const fetchChannels = async () => {
    try { const r = await apiGet("/api/modbus-tcp/can-send-channels"); setCanChannels((await r.json()).channels || []); } catch {}
  };

  useEffect(() => {
    if (!status.running) { setIsolationWarning(""); return; }
    if (status.host === "0.0.0.0") {
      setIsolationWarning(
        "eth1 has no active IP or cable — listening on all interfaces " +
        "(0.0.0.0), not isolated from the management network. " +
        "Configure eth1's static IP and plug in the cable first."
      );
    } else if (network.carrier === false) {
      setIsolationWarning(
        "Cable disconnected on eth1 — server is still running on " +
        status.host + " but clients can no longer reach it."
      );
    } else { setIsolationWarning(""); }
  }, [status, network]);

  useEffect(() => { fetchStatus(); fetchEntries(); fetchConfig(); fetchChannels(); }, []);
  useEffect(() => {
    fetchNetwork(); fetchRevertStatus();
    const t = setInterval(() => {
      fetchRevertStatus(); fetchNetwork(); fetchConfig(); fetchStatus(); fetchChannels();
    }, 3000);
    return () => clearInterval(t);
  }, []);

  const handleStart = async () => {
    const r = await apiPost("/api/modbus-tcp/start", { port: config.port });
    const d = await r.json();
    if (r.ok) showToast("Server started", "success");
    else showToast(d.error || "Failed", "error");
    fetchStatus();
  };
  const handleStop = async () => {
    await apiPost("/api/modbus-tcp/stop", {});
    showToast("Server stopped", "success");
    setIsolationWarning("");
    fetchStatus();
  };
  const handleSaveConfig = async () => {
    const r = await apiPost("/api/modbus-tcp/config", { port: config.port });
    if (r.ok) showToast("Config updated", "success");
    else showToast((await r.json()).error || "Failed", "error");
    fetchStatus();
  };
  const handleApplyNetwork = async () => {
    const r = await apiPost("/api/network/apply", candidate);
    const d = await r.json();
    if (r.ok) { setShowNetworkConfirm(false); showToast("IP applied — confirm within 60s", "success"); fetchRevertStatus(); }
    else showToast(d.error || "Apply failed", "error");
  };
  const handleConfirmNetwork = async () => {
    const r = await apiPost("/api/network/confirm", {});
    if (r.ok) showToast("Network config confirmed — change is permanent", "success");
    else showToast((await r.json()).error || "Confirm failed", "error");
    fetchRevertStatus();
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
    if (r.ok) { showToast(`Saved (${entries.length} entries)`, "success"); fetchEntries(); }
    else showToast((await r.json()).error || "Save failed", "error");
  };
  const addEntry = () => {
    if (!newEntry.source_key.trim() && newEntry.source_type === "local") return;
    if (!newEntry.rtu_device_id.trim() && newEntry.source_type === "modbus_rtu") return;
    setEntries([...entries, { ...newEntry, source_key: newEntry.source_key.trim() }]);
    setNewEntry({ function_code: 3, address: 0, source_key: "", label: "",
      source_type: "local", rtu_device_id: "", rtu_address: 0, writable: false });
    setShowAdd(false);
  };
  const removeEntry = (idx) => setEntries(entries.filter((_, i) => i !== idx));

  const submitTestWrite = async () => {
    if (!testWriteEntry || !testWriteValue.trim()) return;
    const val = parseInt(testWriteValue, 10);
    if (isNaN(val) || val < 0 || val > 65535) {
      setTestWriteResult({ ok: false, message: "Value must be 0–65535" }); return;
    }
    try {
      const r = await apiPost("/api/modbus-tcp/register-map/test-write", {
        device_id: testWriteEntry.rtu_device_id,
        address: testWriteEntry.rtu_address, value: val,
      });
      const d = await r.json();
      if (r.ok) setTestWriteResult({ ok: true, message: d.message });
      else setTestWriteResult({ ok: false, message: d.error || "Write failed" });
    } catch (e) { setTestWriteResult({ ok: false, message: "Network error" }); }
  };

  const handleAddChannel = () => {
    if (!newChannel.name.trim()) return;
    setCanChannels([...canChannels, { ...newChannel, name: newChannel.name.trim() }]);
    setNewChannel({ name: "", id_address: 0, data_start_address: 0, dlc_address: 0, trigger_coil_address: 0 });
    setShowAddChannel(false);
  };
  const handleRemoveChannel = (idx) => setCanChannels(canChannels.filter((_, i) => i !== idx));
  const handleSaveChannels = async () => {
    const r = await apiPost("/api/modbus-tcp/can-send-channels", { channels: canChannels });
    if (r.ok) { showToast(`Saved (${canChannels.length} channels)`, "success"); fetchChannels(); }
    else showToast((await r.json()).error || "Save failed", "error");
  };

  return (
    <div>
      <div className="page-header">
        <h2>Modbus TCP</h2>
        <p>Expose IO, CAN, and RTU registers to SCADA/HMI over Modbus TCP</p>
      </div>

      <ExceptionToastFeed serverRunning={status.running} lastSeenRef={lastSeenRef} />

      <ServerCard
        status={status} config={config}
        isOperator={isOperator} isAdmin={isAdmin}
        onStart={handleStart} onStop={handleStop}
        onSaveConfig={handleSaveConfig} setConfig={setConfig}
        isolationWarning={isolationWarning}
      />

      <NetworkCard
        network={network} candidate={candidate} setCandidate={setCandidate}
        revertStatus={revertStatus} isAdmin={isAdmin}
        showNetworkConfirm={showNetworkConfirm} setShowNetworkConfirm={setShowNetworkConfirm}
        onApply={handleApplyNetwork} onConfirm={handleConfirmNetwork}
      />

      <RegisterMapCard
        entries={entries} isAdmin={isAdmin} isOperator={isOperator}
        showAdd={showAdd} setShowAdd={setShowAdd}
        newEntry={newEntry} setNewEntry={setNewEntry}
        onAddEntry={addEntry} onRemoveEntry={removeEntry}
        onValidate={handleValidate} onSave={handleSaveEntries}
        validationErrors={validationErrors}
        testWriteEntry={testWriteEntry} setTestWriteEntry={setTestWriteEntry}
        testWriteValue={testWriteValue} setTestWriteValue={setTestWriteValue}
        testWriteResult={testWriteResult} onSubmitTestWrite={submitTestWrite}
      />

      <AddressMapStrip entries={entries} channels={canChannels} />

      <CANSendChannelsCard
        channels={canChannels} isAdmin={isAdmin}
        showAdd={showAddChannel} setShowAdd={setShowAddChannel}
        newChannel={newChannel} setNewChannel={setNewChannel}
        onAdd={handleAddChannel} onRemove={handleRemoveChannel} onSave={handleSaveChannels}
      />
    </div>
  );
}
