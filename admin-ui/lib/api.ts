'use client';

const API_BASE = '';

export async function login(username: string, password: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/enterprise/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error('Login failed');
  const data = await res.json();
  const token = data.token || data.access_token;
  if (typeof window !== 'undefined') localStorage.setItem('soul_token', token);
  return token;
}

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('soul_token');
}

export function clearToken() {
  if (typeof window !== 'undefined') localStorage.removeItem('soul_token');
}

export async function apiFetch(path: string, opts: RequestInit = {}) {
  const token = getToken();
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string>) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  // 根因修（GLAND-④ Gate3 逼出）：带 JSON 体必声明 application/json——
  // fetch 对字符串体默认发 text/plain，FastAPI 对非 JSON content-type 不解析
  // JSON 直接按裸串校验 → 422「Input should be a valid dictionary」（apiFetch
  // 全部 POST/PATCH/PUT JSON 面的长期静默断点：添加Provider/加钥/预算/编辑全中）。
  const hasContentType = Object.keys(headers).some(k => k.toLowerCase() === 'content-type');
  if (opts.body && !hasContentType) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${API_BASE}${path}`, { ...opts, headers });
  if (res.status === 401) { clearToken(); throw new Error('Unauthorized'); }
  if (!res.ok) {
    // 错误真因（FastAPI detail）原样进 message——禁丢 detail 只报状态码
    //（GLAND-④：PATCH 409 持钥改道真因必须对用户可见；422 校验错为列表形态同收）
    let detail = '';
    try {
      const data = await res.json();
      const d = data?.detail;
      if (typeof d === 'string') detail = d;
      else if (Array.isArray(d)) {
        detail = d.map((x: any) => (x?.loc ? x.loc.join('.') + ': ' : '') + (x?.msg || '')).join('; ');
      }
    } catch { /* 非 JSON 错误体如实只报状态码 */ }
    throw new Error(detail ? `API error ${res.status}: ${detail}` : `API error ${res.status}`);
  }
  return res.json();
}
