// Cliente da API (mesma origem: o Gateway manda /api para o serviço Java).
// O token fica só neste aparelho (localStorage) e vai no cabeçalho Authorization.

const TOKEN_KEY = "alerta.token";

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // modo privado sem storage: o token vale só nesta aba
  }
}

export class ApiError extends Error {
  constructor(status, problem) {
    super(problem?.detail || problem?.title || (status ? `HTTP ${status}` : "sem conexão"));
    this.status = status;
    this.problem = problem || {};
  }

  /** Erros de validação por campo (ProblemDetail com "errors"). */
  get fieldErrors() {
    return this.problem.errors || {};
  }
}

export async function request(path, { method = "GET", body, token = getToken() } = {}) {
  const headers = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, { detail: "Sem conexão com o servidor." });
  }
  if (response.status === 204) return null;
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text.slice(0, 200) };
  }
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}

export const api = {
  alerts: () => request("/alerts"),
  alert: (id) => request(`/alerts/${encodeURIComponent(id)}`),
  createAlert: (body) => request("/alerts", { method: "POST", body }),
  updateAlert: (id, body) => request(`/alerts/${encodeURIComponent(id)}`, { method: "PUT", body }),
  deleteAlert: (id) => request(`/alerts/${encodeURIComponent(id)}`, { method: "DELETE" }),
  events: (limit = 100) => request(`/events?limit=${limit}`),
  quotes: (asset, hours) => request(`/quotes/${encodeURIComponent(asset)}?hours=${hours}`),
  /** Confere um token sem gravá-lo. */
  check: (token) => request("/alerts", { token }),
};
