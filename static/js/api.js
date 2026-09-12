/* Thin wrapper around fetch for the JSON API. */

class ApiError extends Error {
  constructor(status, message, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request(method, path, { body, query, raw } = {}) {
  let url = path;
  if (query) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') params.set(key, value);
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }
  const options = { method, headers: {}, credentials: 'same-origin' };
  if (body instanceof FormData) {
    options.body = body;
  } else if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  if (raw) {
    if (!response.ok) throw new ApiError(response.status, 'ダウンロードに失敗しました');
    return response;
  }
  let data = null;
  const text = await response.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = { error: text.slice(0, 200) }; }
  }
  if (!response.ok) {
    throw new ApiError(response.status, data?.error || `エラー (${response.status})`, data?.detail);
  }
  return data;
}

export const api = {
  ApiError,
  get: (path, query) => request('GET', path, { query }),
  post: (path, body) => request('POST', path, { body }),
  put: (path, body) => request('PUT', path, { body }),
  patch: (path, body) => request('PATCH', path, { body }),
  del: (path) => request('DELETE', path),
  download: (path) => request('GET', path, { raw: true }),

  // auth
  me: () => request('GET', '/api/auth/me'),
  login: (email, password) => request('POST', '/api/auth/login', { body: { email, password } }),
  logout: () => request('POST', '/api/auth/logout'),
  meta: () => request('GET', '/api/meta'),

  // projects & tasks
  projects: (query) => request('GET', '/api/projects', { query }),
  project: (id) => request('GET', `/api/projects/${id}`),
  projectTasks: (id) => request('GET', `/api/projects/${id}/tasks`),
  tasks: (query) => request('GET', '/api/tasks', { query }),
  task: (id) => request('GET', `/api/tasks/${id}`),

  // people
  users: (query) => request('GET', '/api/users', { query }),
  groups: () => request('GET', '/api/groups'),

  // notifications & daily
  notifications: (query) => request('GET', '/api/notifications', { query }),
  daily: () => request('GET', '/api/daily'),
};

export { ApiError };
