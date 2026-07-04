const TOKEN_KEY = "flemingo_access";
const REFRESH_KEY = "flemingo_refresh";
const ROLE_KEY = "flemingo_role";
const USERNAME_KEY = "flemingo_user";

const BASE = "";

function decodeJWT(token) {
  try {
    const payload = token.split(".")[1];
    return JSON.parse(atob(payload));
  } catch {
    return null;
  }
}

function getToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

function setToken(token) {
  sessionStorage.setItem(TOKEN_KEY, token);
}

function getRefreshToken() {
  return sessionStorage.getItem(REFRESH_KEY);
}

export function clearAuth() {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(REFRESH_KEY);
  sessionStorage.removeItem(ROLE_KEY);
  sessionStorage.removeItem(USERNAME_KEY);
}

async function maybeRefresh() {
  const token = getToken();
  if (!token) return false;

  const decoded = decodeJWT(token);
  if (!decoded || !decoded.exp) return false;

  const expiresIn = decoded.exp - Math.floor(Date.now() / 1000);
  if (expiresIn > 300) return true;

  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;

  try {
    const res = await fetch(`${BASE}/api/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!res.ok) {
      clearAuth();
      window.location.href = "/login";
      return false;
    }

    const data = await res.json();
    setToken(data.access_token);
    return true;
  } catch {
    return false;
  }
}

async function request(method, path, body, isFormData) {
  await maybeRefresh();

  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let options = { method, headers };

  if (body && isFormData) {
    options.body = body;
  } else if (body) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
    options.headers = headers;
  }

  const res = await fetch(`${BASE}${path}`, options);

  if (res.status === 401) {
    clearAuth();
    window.location.href = "/login";
    throw new Error("Session expired");
  }

  return res;
}

export function apiGet(path) {
  return request("GET", path);
}

export function apiPost(path, body) {
  return request("POST", path, body, false);
}

export function apiPostForm(path, formData) {
  return request("POST", path, formData, true);
}

export function apiDelete(path) {
  return request("DELETE", path);
}

export function storeAuth(accessToken, refreshToken, role, username) {
  setToken(accessToken);
  sessionStorage.setItem(REFRESH_KEY, refreshToken);
  sessionStorage.setItem(ROLE_KEY, role);
  sessionStorage.setItem(USERNAME_KEY, username);
}

export function getStoredRole() {
  return sessionStorage.getItem(ROLE_KEY);
}

export function getStoredUsername() {
  return sessionStorage.getItem(USERNAME_KEY);
}

export { getToken, decodeJWT };
