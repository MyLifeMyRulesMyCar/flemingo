import { createContext, useContext, useState, useCallback, useEffect } from "react";
import {
  apiPost,
  storeAuth,
  clearAuth,
  getStoredRole,
  getStoredUsername,
  getToken,
} from "../api/client.js";
import { connectSocket, disconnectSocket } from "../api/socket.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const storedToken = sessionStorage.getItem("flemingo_access");
  const [token, setToken] = useState(storedToken);
  const [role, setRole] = useState(getStoredRole() || null);
  const [username, setUsername] = useState(getStoredUsername() || null);
  const [mustChangePassword, setMustChangePassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const login = useCallback(async (u, p) => {
    setLoading(true);
    setError("");
    try {
      const res = await apiPost("/api/auth/login", {
        username: u,
        password: p,
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Login failed");
        setLoading(false);
        return false;
      }

      const user = data.user || {};
      const mcp = user.must_change_password === true;

      storeAuth(data.access_token, data.refresh_token, user.role, u);
      setToken(data.access_token);
      setRole(user.role);
      setUsername(u);
      setMustChangePassword(mcp);
      setLoading(false);

      if (!mcp) {
        connectSocket(data.access_token);
      }
      return true;
    } catch (e) {
      setError("Network error — is Flask running?");
      setLoading(false);
      return false;
    }
  }, []);

  useEffect(() => {
    if (storedToken) {
      connectSocket(storedToken);
    }
  }, []);

  const logout = useCallback(() => {
    apiPost("/api/auth/logout", {}).catch(() => {});
    disconnectSocket();
    clearAuth();
    setToken(null);
    setRole(null);
    setUsername(null);
    setMustChangePassword(false);
  }, []);

  const changePassword = useCallback(async (oldPw, newPw) => {
    setLoading(true);
    setError("");
    try {
      const res = await apiPost("/api/auth/change-password", {
        old_password: oldPw,
        new_password: newPw,
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Password change failed");
        setLoading(false);
        return false;
      }
      clearAuth();
      setToken(null);
      setRole(null);
      setUsername(null);
      setMustChangePassword(false);
      setLoading(false);
      return true;
    } catch (e) {
      setError("Network error");
      setLoading(false);
      return false;
    }
  }, []);

  const value = {
    token,
    role,
    username,
    mustChangePassword,
    error,
    loading,
    login,
    logout,
    changePassword,
    setError,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
}
