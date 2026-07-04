import { useState, useEffect } from "react";
import { apiGet } from "../api/client.js";
import { getSocket } from "../api/socket.js";
import MetricCard from "../components/MetricCard.jsx";
import StatusLed from "../components/StatusLed.jsx";

export default function Overview() {
  const [info, setInfo] = useState({});
  const [health, setHealth] = useState({});
  const [di, setDi] = useState([]);
  const [do_, setDo] = useState([]);
  const [metrics, setMetrics] = useState({});
  const [mqttOk, setMqttOk] = useState(false);

  useEffect(() => {
    apiGet("/api/system/info")
      .then((r) => r.json())
      .then(setInfo)
      .catch(() => {});
    apiGet("/api/health/detailed")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => {});
    apiGet("/api/io")
      .then((r) => r.json())
      .then((d) => {
        setDi(d.di || []);
        setDo(d.do || []);
      })
      .catch(() => {});
    apiGet("/api/system/metrics")
      .then((r) => r.json())
      .then(setMetrics)
      .catch(() => {});
    apiGet("/api/mqtt/status")
      .then((r) => r.json())
      .then((d) => setMqttOk(d.connected === true))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const sock = getSocket();
    if (!sock) return;

    const onIoUpdate = (data) => {
      setDi(data.di || []);
      setDo(data.do || []);
    };
    sock.on("io_update", onIoUpdate);
    sock.on("system_metrics", setMetrics);

    return () => {
      sock.off("io_update", onIoUpdate);
      sock.off("system_metrics");
    };
  }, []);

  const canOk = health?.can?.connected;
  const modbusOk = (health?.modbus?.connected_count || 0) > 0;
  const watchdogOk = health?.watchdog?.alive;

  const uptimeH = info.uptime_seconds
    ? `${Math.floor(info.uptime_seconds / 3600)}h ${Math.floor((info.uptime_seconds % 3600) / 60)}m`
    : "--";

  return (
    <div>
      <div className="page-header">
        <h2>Overview</h2>
        <p>{info.hostname || "..."} — {info.firmware_version || "..."}</p>
      </div>

      <div className="overview-header">
        <div className="info-chip">
          IP <strong>{info.ip || "--"}</strong>
        </div>
        <div className="info-chip">
          Uptime <strong>{uptimeH}</strong>
        </div>
        <div className="info-chip">
          Version <strong>{info.firmware_version || "--"}</strong>
        </div>
      </div>

      <div className="status-pills-row">
        <span className={`status-pill ${canOk ? "ok" : "off"}`}>
          CAN {canOk ? "OK" : "—"}
        </span>
        <span className={`status-pill ${modbusOk ? "ok" : "off"}`}>
          Modbus {modbusOk ? "OK" : "—"}
        </span>
        <span className={`status-pill ${mqttOk ? "ok" : "off"}`}>
          MQTT {mqttOk ? "OK" : "—"}
        </span>
        <span className={`status-pill ${watchdogOk ? "ok" : "warn"}`}>
          Watchdog {watchdogOk ? "Alive" : "?"}
        </span>
      </div>

      <div className="metrics-grid">
        <MetricCard
          title="CPU"
          value={metrics.cpu_percent != null ? metrics.cpu_percent : "--"}
          unit="%"
          subtitle={
            metrics.load_average
              ? `Load ${metrics.load_average["1min"].toFixed(1)}`
              : ""
          }
        />
        <MetricCard
          title="RAM"
          value={metrics.memory?.percent != null ? metrics.memory.percent : "--"}
          unit="%"
          subtitle={
            metrics.memory
              ? `${(metrics.memory.used / 1024**3).toFixed(1)} / ${(metrics.memory.total / 1024**3).toFixed(1)} GB`
              : ""
          }
        />
        <MetricCard
          title="Temperature"
          value={metrics.temperature_c != null ? metrics.temperature_c.toFixed(1) : "--"}
          unit="°C"
        />
      </div>

      <div className="card">
        <div className="card-header">Digital Inputs</div>
        <div className="io-grid">
          {[0, 1, 2, 3].map((ch) => (
            <div className="io-cell" key={`di-${ch}`}>
              <div className="io-label">DI{ch}</div>
              <StatusLed status={di[ch] ? "ok" : "off"} />
              <div className="io-value" style={{ color: di[ch] ? "var(--status-ok)" : "var(--status-off)" }}>
                {di[ch] ? "HIGH" : "LOW"}
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
              <div className="io-value" style={{ color: do_[ch] ? "var(--accent)" : "var(--status-off)" }}>
                {do_[ch] ? "ON" : "OFF"}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
