import { io } from "socket.io-client";

let socket = null;

export function connectSocket(token) {
  if (socket?.connected) return socket;
  socket = io("/", { auth: { token } });

  socket.on("connect_error", (err) => {
    sessionStorage.clear();
    window.location.href = "/login";
  });

  return socket;
}

export function getSocket() {
  return socket;
}

export function disconnectSocket() {
  if (socket) {
    socket.disconnect();
    socket = null;
  }
}
