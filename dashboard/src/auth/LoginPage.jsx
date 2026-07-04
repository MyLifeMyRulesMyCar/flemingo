import { useState, useEffect } from "react";
import { useAuth } from "./AuthContext.jsx";

export default function LoginPage() {
  const { login, changePassword, mustChangePassword, error, loading, setError } =
    useAuth();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changingPw, setChangingPw] = useState(false);

  useEffect(() => {
    if (mustChangePassword) {
      setChangingPw(true);
      setError("Password change required before continuing");
    }
  }, [mustChangePassword, setError]);

  const handleLogin = async (e) => {
    e.preventDefault();
    const ok = await login(username, password);
    if (ok) setPassword("");
  };

  const handleChangePw = async (e) => {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (newPassword.length < 10) {
      setError("Password must be at least 10 characters");
      return;
    }
    const ok = await changePassword(password, newPassword);
    if (ok) {
      setChangingPw(false);
      setNewPassword("");
      setConfirmPassword("");
      setPassword("");
      setError("");
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <h1>FLEMINGO</h1>
          <span>EdgeForce-1000</span>
        </div>

        {error && <div className="login-error">{error}</div>}

        {changingPw ? (
          <form onSubmit={handleChangePw}>
            <div className="form-row">
              <label>Current Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoFocus
              />
            </div>
            <div className="form-row">
              <label>New Password</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
            </div>
            <div className="form-row">
              <label>Confirm New Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
            </div>
            <button type="submit" className="btn-primary login-btn" disabled={loading}>
              {loading ? "Changing..." : "Change Password"}
            </button>
          </form>
        ) : (
          <form onSubmit={handleLogin}>
            <div className="form-row">
              <label>Username</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
              />
            </div>
            <div className="form-row">
              <label>Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            <button type="submit" className="btn-primary login-btn" disabled={loading}>
              {loading ? "Signing in..." : "Sign In"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
