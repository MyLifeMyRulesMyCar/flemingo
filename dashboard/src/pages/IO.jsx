import { useState, useEffect } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { apiGet, apiPost } from "../api/client.js";
import { getSocket } from "../api/socket.js";
import StatusLed from "../components/StatusLed.jsx";
import { useToast } from "../components/Toast.jsx";

export default function IO() {
  const { role } = useAuth();
  const { showToast } = useToast();
  const [di, setDi] = useState([]);
  const [do_, setDo] = useState([]);
  const [lastDi, setLastDi] = useState([]);
  const [lastDo, setLastDo] = useState([]);
  const [changes, setChanges] = useState({});

  const now = () => new Date().toLocaleTimeString();

  useEffect(() => {
    apiGet("/api/io")
      .then((r) => r.json())
      .then((d) => {
        setDi(d.di || []);
        setDo(d.do || []);
        setLastDi(d.di || []);
        setLastDo(d.do || []);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const sock = getSocket();
    if (!sock) return;
    sock.on("io_update", (data) => {
      const newDi = data.di || [];
      const newDo = data.do || [];
      setDi(newDi);
      setDo(newDo);
      setLastDi((prev) => {
        setChanges((ch) => {
          const next = { ...ch };
          for (let i = 0; i < newDi.length; i++) {
            if (newDi[i] !== prev[i]) next[`di-${i}`] = now();
          }
          return next;
        });
        return newDi;
      });
      setLastDo((prev) => {
        setChanges((ch) => {
          const next = { ...ch };
          for (let i = 0; i < newDo.length; i++) {
            if (newDo[i] !== prev[i]) next[`do-${i}`] = now();
          }
          return next;
        });
        return newDo;
      });
    });
    return () => sock.off("io_update");
  }, []);

  const toggleDO = async (ch) => {
    const newVal = do_[ch] ? 0 : 1;
    try {
      const res = await apiPost(`/api/io/do/${ch}`, { state: !!newVal });
      const data = await res.json();
      if (res.ok) {
        setDo((prev) => {
          const next = [...prev];
          next[ch] = data.value;
          setChanges((c) => ({ ...c, [`do-${ch}`]: now() }));
          return next;
        });
        showToast(`DO${ch} set to ${newVal ? "ON" : "OFF"}`, "success");
      } else {
        showToast(data.error || "Write failed", "error");
      }
    } catch {
      showToast("Network error", "error");
    }
  };

  const isOperator = role === "operator" || role === "admin";

  return (
    <div>
      <div className="page-header">
        <h2>I/O Control</h2>
        <p>Digital inputs (read-only) and outputs (toggle)</p>
      </div>

      <div className="card">
        <div className="card-header">Digital Inputs</div>
        <div className="io-grid">
          {[0, 1, 2, 3].map((ch) => (
            <div className="io-cell" key={`di-${ch}`}>
              <div className="io-label">DI{ch}</div>
              <StatusLed status={di[ch] ? "ok" : "off"} pulse={!!di[ch]} />
              <div className="io-value" style={{ color: di[ch] ? "var(--status-ok)" : "var(--status-off)" }}>
                {di[ch] ? "HIGH" : "LOW"}
              </div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "4px" }}>
                {changes[`di-${ch}`] || "—"}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-header">Digital Outputs</div>
        <div className="io-grid">
          {[0, 1, 2, 3].map((ch) => (
            <div className="io-cell" key={`do-${ch}`}>
              <div className="io-label">DO{ch}</div>
              <StatusLed status={do_[ch] ? "ok" : "off"} />
              <button
                className={`do-toggle ${do_[ch] ? "on" : "off"}`}
                disabled={!isOperator}
                onClick={() => toggleDO(ch)}
              >
                {do_[ch] ? "ON" : "OFF"}
              </button>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "4px" }}>
                {changes[`do-${ch}`] || "—"}
              </div>
            </div>
          ))}
        </div>
        {!isOperator && (
          <p style={{ color: "var(--text-muted)", fontSize: "12px", marginTop: "8px" }}>
            Operator role required to toggle outputs
          </p>
        )}
      </div>
    </div>
  );
}
