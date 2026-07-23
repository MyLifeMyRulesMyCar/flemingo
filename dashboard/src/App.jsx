import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext.jsx";
import { ToastProvider } from "./components/Toast.jsx";
import LoginPage from "./auth/LoginPage.jsx";
import Sidebar from "./components/Sidebar.jsx";
import Overview from "./pages/Overview.jsx";
import IO from "./pages/IO.jsx";
import CAN from "./pages/CAN.jsx";
import Modbus from "./pages/Modbus.jsx";
import MQTT from "./pages/MQTT.jsx";
import System from "./pages/System.jsx";
import ModbusTCP from "./pages/ModbusTCP.jsx";
import { Outlet } from "react-router-dom";

function ProtectedRoute() {
  const { token } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  return <Outlet />;
}

function GuestRoute() {
  const { token } = useAuth();
  if (token) return <Navigate to="/" replace />;
  return <Outlet />;
}

function Shell() {
  return (
    <div className="app-layout">
      <Sidebar />
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <Routes>
            <Route element={<GuestRoute />}>
              <Route path="/login" element={<LoginPage />} />
            </Route>
            <Route element={<ProtectedRoute />}>
              <Route element={<Shell />}>
                <Route path="/" element={<Overview />} />
                <Route path="/io" element={<IO />} />
                <Route path="/can" element={<CAN />} />
                <Route path="/modbus" element={<Modbus />} />
                <Route path="/mqtt" element={<MQTT />} />
                <Route path="/system" element={<System />} />
                <Route path="/modbus-tcp" element={<ModbusTCP />} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
