import { useState, useEffect, useRef } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { apiGet, apiPost, apiPostForm, apiDelete } from "../api/client.js";
import MetricCard from "../components/MetricCard.jsx";
import ConfirmModal from "../components/ConfirmModal.jsx";
import { useToast } from "../components/Toast.jsx";

export default function System() {
  const { role } = useAuth();
  const { showToast } = useToast();
  const [metrics, setMetrics] = useState({});
  const [users, setUsers] = useState([]);
  const [restartBanner, setRestartBanner] = useState(false);
  const [newUser, setNewUser] = useState({ username: "", password: "", role: "viewer" });
  const [delUser, setDelUser] = useState(null);
  const fileRef = useRef(null);
  const isAdmin = role === "admin";

  useEffect(() => {
    const poll = () => {
      apiGet("/api/system/metrics")
        .then((r) => r.json())
        .then(setMetrics)
        .catch(() => {});
    };
    poll();
    const t = setInterval(poll, 10000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (isAdmin) {
      apiGet("/api/auth/users")
        .then((r) => r.json())
        .then((d) => setUsers(d.users || []))
        .catch(() => {});
    }
  }, [isAdmin]);

  const handleBackup = () => {
    apiGet("/api/system/backup")
      .then((r) => r.blob())
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `flemingo-backup-${new Date().toISOString().slice(0, 10)}.zip`;
        a.click();
        URL.revokeObjectURL(url);
        showToast("Backup downloaded", "success");
      })
      .catch(() => showToast("Backup failed", "error"));
  };

  const handleRestore = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) { showToast("Select a backup zip first", "error"); return; }
    const fd = new FormData();
    fd.append("file", file);
    const r = await apiPostForm("/api/system/restore", fd);
    const d = await r.json();
    if (r.ok) {
      setRestartBanner(true);
      showToast("Restored — restart required", "success");
    } else {
      showToast(d.error || "Restore failed", "error");
    }
  };

  const handleAddUser = async () => {
    const r = await apiPost("/api/auth/users", newUser);
    const d = await r.json();
    if (r.ok) {
      showToast(`User '${newUser.username}' created`, "success");
      setNewUser({ username: "", password: "", role: "viewer" });
      const usersR = await apiGet("/api/auth/users");
      setUsers((await usersR.json()).users || []);
    } else {
      showToast(d.error || "Add user failed", "error");
    }
  };

  const handleDeleteUser = async () => {
    const r = await apiDelete(`/api/auth/users/${delUser}`);
    if (r.ok) {
      showToast(`User '${delUser}' deleted`, "success");
      setDelUser(null);
      const usersR = await apiGet("/api/auth/users");
      setUsers((await usersR.json()).users || []);
    } else {
      showToast((await r.json()).error || "Delete failed", "error");
      setDelUser(null);
    }
  };

  const mem = metrics.memory || {};
  const disk = metrics.disk || {};
  const net = metrics.network || {};
  const proc = metrics.process || {};

  return (
    <div>
      <div className="page-header">
        <h2>System</h2>
        <p>Metrics, backup, restore, and user management</p>
      </div>

      {restartBanner && (
        <div className="restart-banner">
          Restart required — run <code>sudo systemctl restart flemingo</code> or{" "}
          <code>python3 api/app.py</code>
        </div>
      )}

      <div className="metrics-grid">
        <MetricCard title="CPU" value={metrics.cpu_percent != null ? metrics.cpu_percent.toFixed(1) : "--"} unit="%" />
        <MetricCard title="Load 1min" value={metrics.load_average?.["1min"]?.toFixed(1) ?? "--"} />
        <MetricCard title="Memory" value={mem.percent != null ? mem.percent.toFixed(1) : "--"} unit="%"
          subtitle={mem.total ? `${(mem.used / 1024**3).toFixed(1)} / ${(mem.total / 1024**3).toFixed(1)} GB` : ""} />
        <MetricCard title="Disk" value={disk.percent != null ? disk.percent.toFixed(1) : "--"} unit="%" />
        <MetricCard title="Temperature" value={metrics.temperature_c != null ? metrics.temperature_c.toFixed(1) : "--"} unit="°C" />
        <MetricCard title="Network" value={net.bytes_recv ? `${((net.bytes_recv + net.bytes_sent) / 1024**2).toFixed(0)}` : "--"} unit="MB"
          subtitle={net.bytes_recv ? `↓${(net.bytes_recv / 1024**2).toFixed(0)} ↑${(net.bytes_sent / 1024**2).toFixed(0)} MB` : ""} />
      </div>

      <div className="card">
        <div className="card-header">Process</div>
        <div style={{ display: "flex", gap: "24px", fontSize: "13px" }}>
          <span>PID: <strong className="mono">{proc.pid ?? "--"}</strong></span>
          <span>RSS: <strong className="mono">{proc.rss_mb ?? "--"} MB</strong></span>
          <span>Threads: <strong className="mono">{proc.threads ?? "--"}</strong></span>
        </div>
      </div>

      {isAdmin && (
        <div className="card">
          <div className="card-header">Backup & Restore</div>
          <div style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn-primary" onClick={handleBackup}>Download Backup</button>
            <input type="file" accept=".zip" ref={fileRef} />
            <button className="btn-primary" onClick={handleRestore}>Restore Config</button>
          </div>
        </div>
      )}

      {isAdmin && (
        <div className="card">
          <div className="card-header">Users ({users.length})</div>

          <div className="form-inline">
            <div className="form-row">
              <label>Username</label>
              <input value={newUser.username}
                onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
                style={{ width: 140 }} />
            </div>
            <div className="form-row">
              <label>Password</label>
              <input type="password" value={newUser.password}
                onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                style={{ width: 140 }} />
            </div>
            <div className="form-row">
              <label>Role</label>
              <select value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}>
                <option value="viewer">viewer</option>
                <option value="operator">operator</option>
                <option value="admin">admin</option>
              </select>
            </div>
            <button className="btn-primary" onClick={handleAddUser}
              disabled={!newUser.username || !newUser.password}>Add User</button>
          </div>

          <table className="data-table">
            <thead>
              <tr><th>Username</th><th>Role</th><th></th></tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.username}>
                  <td style={{ fontFamily: "var(--font-sans)" }}>{u.username}</td>
                  <td>{u.role}</td>
                  <td>
                    <button className="btn-danger" style={{ padding: "2px 8px", fontSize: "11px" }}
                      onClick={() => setDelUser(u.username)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmModal isOpen={!!delUser}
        title="Delete User"
        message={`Permanently delete user '${delUser}'?`}
        confirmLabel="Delete"
        danger
        onConfirm={handleDeleteUser}
        onCancel={() => setDelUser(null)} />
    </div>
  );
}
