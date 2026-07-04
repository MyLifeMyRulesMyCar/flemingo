export default function StatusLed({ status, label, pulse }) {
  const dots = { ok: "ok", err: "err", warn: "warn" };
  const cls = dots[status] || "off";
  return (
    <span className="status-led">
      <span className={`led-dot ${cls}${pulse ? " pulse" : ""}`} />
      {label && <span>{label}</span>}
    </span>
  );
}
