export default function MetricCard({ title, value, unit, subtitle }) {
  return (
    <div className="metric-card">
      <div className="metric-title">{title}</div>
      <div className="metric-value">
        {value !== null && value !== undefined ? value : "--"}
        {unit && <span className="metric-unit">{unit}</span>}
      </div>
      {subtitle && <div className="metric-sub">{subtitle}</div>}
    </div>
  );
}
