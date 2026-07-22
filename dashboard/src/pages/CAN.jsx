import { useState, useEffect } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { apiGet, apiPost } from "../api/client.js";
import { getSocket } from "../api/socket.js";
import StatusLed from "../components/StatusLed.jsx";
import { useToast } from "../components/Toast.jsx";

const BITRATES = [125000, 250000, 500000, 1000000];

export default function CAN() {
  const { role } = useAuth();
  const { showToast } = useToast();
  const [status, setStatus] = useState({});
  const [messages, setMessages] = useState([]);
  const [bitrate, setBitrate] = useState(125000);
  const [canId, setCanId] = useState("");
  const [dataHex, setDataHex] = useState("");
  const [extended, setExtended] = useState(false);
  const [idFilter, setIdFilter] = useState("");
  const isOperator = role === "operator" || role === "admin";

  const fetchStatus = async () => {
    try {
      const r = await apiGet("/api/can/status");
      const d = await r.json();
      setStatus(d);
      if (idFilter === "" && d.id_filter?.length > 0) {
        setIdFilter(
          d.id_filter
            .map((id) => "0x" + id.toString(16).toUpperCase())
            .join(", ")
        );
      }
    } catch {}
  };

  useEffect(() => {
    fetchStatus();
    const sock = getSocket();
    if (!sock) return;

    const onStatus = (s) => setStatus(s);
    sock.on("can_status", onStatus);

    sock.on("can_message", (msg) => {
      setMessages((prev) => [
        { ...msg, _ts: Date.now() },
        ...prev,
      ].slice(0, 200));
    });
    return () => {
      sock.off("can_status", onStatus);
      sock.off("can_message");
    };
  }, []);
  const handleConnect = async () => {
    const r = await apiPost("/api/can/connect", { bitrate });
    const d = await r.json();
    if (r.ok) {
      showToast("CAN connected", "success");
      fetchStatus();
    } else {
      showToast(d.error || "Connect failed", "error");
    }
  };

  const handleDisconnect = async () => {
    const r = await apiPost("/api/can/disconnect", {});
    if (r.ok) {
      showToast("CAN disconnected", "success");
      fetchStatus();
    }
  };

  const handleSend = async () => {
    const id = parseInt(canId, 16);
    if (isNaN(id)) { showToast("Invalid CAN ID (hex)", "error"); return; }
    const bytes = dataHex
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .map((b) => parseInt(b, 16));
    if (bytes.some(isNaN) || bytes.length > 8) {
      showToast("Invalid data bytes (max 8 hex bytes)", "error");
      return;
    }
    const r = await apiPost("/api/can/send", { can_id: id, data: bytes, extended });
    const d = await r.json();
    if (r.ok) showToast(`Frame 0x${id.toString(16).toUpperCase()} sent`, "success");
    else showToast(d.error || "Send failed", "error");
  };

  const handleClear = async () => {
    await apiPost("/api/can/messages/clear", {});
    setMessages([]);
  };

  const handleApplyFilter = async () => {
    const ids = idFilter
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const r = await apiPost("/api/can/filter", { id_filter: ids });
    const d = await r.json();
    if (r.ok) {
      showToast(
        ids.length
          ? `Filter applied: ${ids.length} ID(s)`
          : "Filter cleared — listening to all",
        "success"
      );
      setStatus(d.status);
    } else {
      showToast(d.error || "Failed to apply filter", "error");
    }
  };

  const connected = status.connected;

  return (
    <div>
      <div className="page-header">
        <h2>CAN Bus</h2>
        <p>MCP2515 controller status and frame send/receive</p>
      </div>

      <div className="card">
        <div className="card-header">
          <StatusLed status={connected ? "ok" : "off"} label={connected ? "Connected" : "Disconnected"} />
          <div style={{ display: "flex", gap: "8px" }}>
            {isOperator && (
              <>
                <select value={bitrate} onChange={(e) => setBitrate(Number(e.target.value))}>
                  {BITRATES.map((b) => (
                    <option key={b} value={b}>{b / 1000}k</option>
                  ))}
                </select>
                <button className="btn-primary" onClick={handleConnect}>
                  Connect
                </button>
                <button className="btn-default" onClick={handleDisconnect}>
                  Disconnect
                </button>
              </>
            )}
          </div>
        </div>
        <div style={{ display: "flex", gap: "24px", fontSize: "13px", marginTop: "8px" }}>
          <span>RX: <strong className="mono">{status.rx_total ?? 0}</strong></span>
          <span>TX: <strong className="mono">{status.tx_total ?? 0}</strong></span>
          <span>Errors: <strong className="mono">{status.errors ?? 0}</strong></span>
          <span>
            Breaker:{" "}
            <strong className="mono">
              {status.circuit_breaker?.state || "—"}
            </strong>
          </span>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          Send Frame
          {isOperator && (
            <button className="btn-primary" onClick={handleSend}>Send</button>
          )}
        </div>
        <div className="form-inline">
          <div className="form-row">
            <label>CAN ID (hex)</label>
            <div className="hex-input">
              <input
                placeholder="0x123"
                value={canId}
                onChange={(e) => setCanId(e.target.value)}
                style={{ width: 120 }}
                disabled={!isOperator}
              />
            </div>
          </div>
          <div className="form-row">
            <label>Data (hex bytes)</label>
            <div className="hex-input">
              <input
                placeholder="DE AD BE EF"
                value={dataHex}
                onChange={(e) => setDataHex(e.target.value)}
                style={{ width: 220 }}
                disabled={!isOperator}
              />
            </div>
          </div>
          <div className="form-row">
            <label>Extended</label>
            <div className="toggle-switch">
              <input
                type="checkbox"
                checked={extended}
                onChange={(e) => setExtended(e.target.checked)}
                disabled={!isOperator}
              />
              <span className="slider" />
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          Live View Filter
          {isOperator && (
            <button
              className="btn-default"
              style={{ padding: "4px 12px", fontSize: "11px" }}
              onClick={handleApplyFilter}
            >
              Apply
            </button>
          )}
        </div>
        <div className="form-row">
          <label>CAN ID Filter — only affects this Message Log view, empty = all</label>
          <input
            value={idFilter}
            onChange={(e) => setIdFilter(e.target.value)}
            placeholder="e.g. 0x100, 0x200"
            disabled={!isOperator}
            style={{ fontFamily: "var(--font-mono)", fontSize: "11px" }}
          />
        </div>
        {status.id_filter?.length > 0 && (
          <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            Active:{" "}
            {status.id_filter
              .map((id) => "0x" + id.toString(16).toUpperCase())
              .join(", ")}
          </div>
        )}
        <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "4px" }}>
          Does not affect the MQTT bridge — set its filter separately on the MQTT page.
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          Message Log ({messages.length})
          <button className="btn-default" onClick={handleClear} style={{ fontSize: "11px" }}>
            Clear
          </button>
        </div>
        <div className="can-log">
          {messages.length === 0 && (
            <div style={{ color: "var(--text-muted)", padding: "12px" }}>
              Waiting for frames...
            </div>
          )}
          {messages.map((m, i) => (
            <div className="log-row" key={i}>
              <span className="log-time">{new Date(m._ts).toLocaleTimeString()}</span>
              <span className="log-id">0x{m.can_id?.toString(16)?.toUpperCase()}</span>
              <span className="log-dlc">{m.dlc ?? (m.data?.length ?? "?")}</span>
              <span className="log-data">
                {m.data?.map((b) => b.toString(16).padStart(2, "0").toUpperCase()).join(" ") || "—"}
              </span>
              <span className="log-ext">{m.extended ? "EXT" : ""}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
