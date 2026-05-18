const TOKEN_KEY = 'snapz-admin-token';

const baseURL = (import.meta.env.VITE_SNAPZ_SERVER_URL || '').replace(/\/$/, '');

export interface SnapzAdminStats {
  bundle_bytes: number;
  devices: number;
  sources: number;
  tenants: number;
  users: number;
}

export interface SnapzAdminUser {
  active_device_count: number;
  created_at: string;
  device_count: number;
  disabled: boolean;
  id: string;
  last_seen_at: string;
  tenant: string;
  tenant_id: string;
  username: string;
}

export interface SnapzAdminDevice {
  created_at: string;
  id: string;
  last_seen_at: string;
  name: string;
  revoked: boolean;
  revoked_at: string;
  tenant: string;
  tenant_id: string;
  user_id: string;
  username: string;
}

export interface SnapzAdminSource {
  bundle_bytes: number;
  display_name: string;
  id: string;
  origin_store_key: string;
  path_hint: string;
  pushed_by_device: string;
  pushed_by_device_name: string;
  pushed_by_user_id: string;
  pushed_by_username: string;
  snapshot_count: number;
  source_marker: string;
  tenant: string;
  tenant_id: string;
  updated_at: string;
}

export interface SnapzAdminBundleMemory {
  available_bytes: number;
  limit_bytes: number;
  limit_fraction: number;
  required_bytes: number;
}

export interface SnapzAdminSourceSnapshot {
  artifact: string;
  compression: string;
  created: string;
  error?: string;
  file_count: number;
  kind: string;
  meta: string;
  name: string;
  note: string;
  protected: boolean;
  size_bytes: number;
  total_bytes_in: number;
}

export interface CreateSnapzUserParams {
  disabled?: boolean;
  password: string;
  tenant: string;
  username: string;
}

export interface UpdateSnapzUserParams {
  disabled?: boolean;
  username?: string;
}

export interface UpdateSnapzSourceParams {
  display_name?: string;
}

export function getSnapzAdminToken() {
  return sessionStorage.getItem(TOKEN_KEY) || '';
}

export function setSnapzAdminToken(token: string) {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearSnapzAdminToken() {
  sessionStorage.removeItem(TOKEN_KEY);
}

async function request<T>(
  path: string,
  options: RequestInit & { body?: BodyInit | null } = {},
): Promise<T> {
  const token = getSnapzAdminToken();
  const headers = new Headers(options.headers);
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${baseURL}${path}`, {
    ...options,
    headers,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `${response.status} ${response.statusText}`);
  }
  return data as T;
}

export async function getSnapzAdminOverview() {
  return request<{ stats: SnapzAdminStats }>('/api/admin/overview');
}

export async function getSnapzUsers() {
  return request<{ users: SnapzAdminUser[] }>('/api/admin/users');
}

export async function getSnapzSources() {
  return request<{ sources: SnapzAdminSource[] }>('/api/admin/sources');
}

export async function getSnapzSourceSnapshots(
  tenantId: string,
  sourceId: string,
  page = 1,
  perPage = 25,
) {
  const params = new URLSearchParams({
    page: String(page),
    per_page: String(perPage),
  });
  return request<{
    has_next: boolean;
    has_prev: boolean;
    memory: SnapzAdminBundleMemory;
    page: number;
    per_page: number;
    snapshots: SnapzAdminSourceSnapshot[];
    source: SnapzAdminSource;
    total: number;
  }>(
    `/api/admin/sources/${encodeURIComponent(tenantId)}/${encodeURIComponent(sourceId)}/snapshots?${params}`,
  );
}

export async function createSnapzUser(data: CreateSnapzUserParams) {
  return request<{ user: SnapzAdminUser }>('/api/admin/users', {
    body: JSON.stringify(data),
    method: 'POST',
  });
}

export async function updateSnapzUser(
  userId: string,
  data: UpdateSnapzUserParams,
) {
  return request<{ user: SnapzAdminUser }>(
    `/api/admin/users/${encodeURIComponent(userId)}`,
    {
      body: JSON.stringify(data),
      method: 'PATCH',
    },
  );
}

export async function resetSnapzUserPassword(userId: string, password: string) {
  return request<{ ok: true }>(
    `/api/admin/users/${encodeURIComponent(userId)}/password`,
    {
      body: JSON.stringify({ password }),
      method: 'POST',
    },
  );
}

export async function deleteSnapzUser(userId: string) {
  return request<{ ok: true }>(
    `/api/admin/users/${encodeURIComponent(userId)}`,
    {
      method: 'DELETE',
    },
  );
}

export async function updateSnapzSource(
  tenantId: string,
  sourceId: string,
  data: UpdateSnapzSourceParams,
) {
  return request<{ source: SnapzAdminSource }>(
    `/api/admin/sources/${encodeURIComponent(tenantId)}/${encodeURIComponent(sourceId)}`,
    {
      body: JSON.stringify(data),
      method: 'PATCH',
    },
  );
}

export async function deleteSnapzSource(tenantId: string, sourceId: string) {
  return request<{ ok: true }>(
    `/api/admin/sources/${encodeURIComponent(tenantId)}/${encodeURIComponent(sourceId)}`,
    {
      method: 'DELETE',
    },
  );
}

export async function renameSnapzSourceSnapshot(
  tenantId: string,
  sourceId: string,
  oldName: string,
  newName: string,
) {
  return request<{
    bundle_bytes: number;
    memory: SnapzAdminBundleMemory;
    snapshot_count: number;
    source: SnapzAdminSource;
  }>(
    `/api/admin/sources/${encodeURIComponent(tenantId)}/${encodeURIComponent(sourceId)}/snapshots/${encodeURIComponent(oldName)}`,
    {
      body: JSON.stringify({ name: newName }),
      method: 'PATCH',
    },
  );
}

export async function deleteSnapzSourceSnapshot(
  tenantId: string,
  sourceId: string,
  name: string,
) {
  return request<{
    bundle_bytes: number;
    deleted_source: boolean;
    memory: SnapzAdminBundleMemory;
    snapshot_count: number;
    source?: SnapzAdminSource;
  }>(
    `/api/admin/sources/${encodeURIComponent(tenantId)}/${encodeURIComponent(sourceId)}/snapshots/${encodeURIComponent(name)}`,
    {
      method: 'DELETE',
    },
  );
}

export async function getSnapzUserDevices(userId: string) {
  return request<{ devices: SnapzAdminDevice[] }>(
    `/api/admin/users/${encodeURIComponent(userId)}/devices`,
  );
}

export async function revokeSnapzUserDevices(userId: string) {
  return request<{ revoked: number }>(
    `/api/admin/users/${encodeURIComponent(userId)}/devices/revoke`,
    { method: 'POST' },
  );
}

export async function revokeSnapzDevice(deviceId: string) {
  return request<{ ok: true }>(
    `/api/admin/devices/${encodeURIComponent(deviceId)}/revoke`,
    { method: 'POST' },
  );
}
