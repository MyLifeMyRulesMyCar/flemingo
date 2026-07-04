import { NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";

const NAV_ITEMS = [
  { to: "/",        label: "Overview", icon: "◉" },
  { to: "/io",      label: "I/O",      icon: "⊡" },
  { to: "/can",     label: "CAN Bus",  icon: "⇄" },
  { to: "/modbus",  label: "Modbus",   icon: "⧉" },
  { to: "/mqtt",    label: "MQTT",     icon: "⟐" },
  { to: "/system",  label: "System",   icon: "⚙" },
];

export default function Sidebar() {
  const { username, role, logout } = useAuth();

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <h1>FLEMINGO</h1>
        <span>edge-01</span>
      </div>
      <nav className="sidebar-nav">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            <span className="nav-icon">{item.icon}</span>
            <span className="nav-label">{item.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        <div className="role-badge">{role}</div>
        <div className="user-info">{username}</div>
        <div className="logout-link" onClick={logout}>
          Logout
        </div>
      </div>
    </aside>
  );
}
