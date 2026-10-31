const DEFAULT_API_BASE = 'http://localhost:8000';

const API_BASE_STORAGE_KEY = 'multi_tenant_rag_api_base';

export function getApiBaseUrl() {
  return localStorage.getItem(API_BASE_STORAGE_KEY) || DEFAULT_API_BASE;
}

export function setApiBaseUrl(value) {
  const base = (value || DEFAULT_API_BASE).trim();
  localStorage.setItem(API_BASE_STORAGE_KEY, base.replace(/\/$/, ''));
  return getApiBaseUrl();
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(options.headers || {}),
    },
    ...options,
  });

  const contentType = response.headers.get('content-type') || '';
  const isJson = contentType.includes('application/json');
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message = typeof payload === 'object' && payload && payload.detail
      ? payload.detail
      : 'Request failed';
    throw new Error(message);
  }

  return payload;
}

export function buildUrl(path) {
  return `${getApiBaseUrl()}${path}`;
}

export async function listTenants() {
  return request(buildUrl('/api/v1/tenants'));
}

export async function createTenant(payload) {
  return request(buildUrl('/api/v1/tenants'), {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function listDocuments(tenantId) {
  return request(buildUrl(`/api/v1/tenants/${tenantId}/documents`));
}

export async function uploadDocument(tenantId, file, metadata) {
  const formData = new FormData();
  formData.append('file', file);
  if (metadata) {
    formData.append('metadata', JSON.stringify(metadata));
  }

  return request(buildUrl(`/api/v1/tenants/${tenantId}/documents`), {
    method: 'POST',
    body: formData,
  });
}

export async function searchTenant(tenantId, query, k, scoreThreshold) {
  return request(buildUrl(`/api/v1/tenants/${tenantId}/search`), {
    method: 'POST',
    body: JSON.stringify({
      query,
      k,
      score_threshold: scoreThreshold,
    }),
  });
}

export async function listConversations(tenantId) {
  return request(buildUrl(`/api/v1/tenants/${tenantId}/conversations`));
}

export async function createConversation(tenantId, title, userId = null) {
  const payload = { title };
  if (userId !== null && userId !== undefined) {
    payload.user_id = userId;
  }

  return request(buildUrl(`/api/v1/tenants/${tenantId}/conversations`), {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function sendMessage(tenantId, conversationId, payload) {
  return request(buildUrl(`/api/v1/tenants/${tenantId}/conversations/${conversationId}/messages`), {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function getConversationMessages(tenantId, conversationId) {
  return request(buildUrl(`/api/v1/tenants/${tenantId}/conversations/${conversationId}/messages`));
}
