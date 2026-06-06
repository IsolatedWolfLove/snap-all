<script lang="ts" setup>
import type { TableColumnsType } from 'ant-design-vue';

import type {
  SnapzAdminDevice,
  SnapzAdminBundleMemory,
  SnapzAdminSource,
  SnapzAdminSourceSnapshot,
  SnapzAdminStats,
  SnapzAdminUser,
} from '#/api/snapz-admin';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Col,
  Form,
  Input,
  message,
  Modal,
  Row,
  Space,
  Switch,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  clearSnapzAdminToken,
  createSnapzUser,
  deleteSnapzSourceSnapshot,
  deleteSnapzSource,
  deleteSnapzUser,
  getSnapzAdminOverview,
  getSnapzSources,
  getSnapzSourceSnapshots,
  getSnapzAdminToken,
  getSnapzUserDevices,
  getSnapzUsers,
  renameSnapzSourceSnapshot,
  resetSnapzUserPassword,
  revokeSnapzDevice,
  revokeSnapzUserDevices,
  setSnapzAdminToken,
  updateSnapzSource,
  updateSnapzUser,
} from '#/api/snapz-admin';

const loading = ref(false);
const connected = ref(false);
const tokenInput = ref(getSnapzAdminToken());
const filterText = ref('');
const sourceFilterText = ref('');
const selectedUserId = ref('');
const selectedSourceKey = ref('');
const snapshotPage = ref(1);
const snapshotPerPage = ref(25);
const snapshotTotal = ref(0);
const snapshotMemory = ref<SnapzAdminBundleMemory | null>(null);

const stats = ref<SnapzAdminStats>({
  bundle_bytes: 0,
  devices: 0,
  sources: 0,
  tenants: 0,
  users: 0,
});
const users = ref<SnapzAdminUser[]>([]);
const devices = ref<SnapzAdminDevice[]>([]);
const sources = ref<SnapzAdminSource[]>([]);
const sourceSnapshots = ref<SnapzAdminSourceSnapshot[]>([]);

const createForm = reactive({
  disabled: false,
  password: '',
  tenant: '',
  username: '',
});

const userColumns: TableColumnsType = [
  { dataIndex: 'username', key: 'user', title: 'User', width: 230 },
  { dataIndex: 'tenant', key: 'tenant', title: 'Tenant', width: 160 },
  { dataIndex: 'disabled', key: 'status', title: 'Status', width: 120 },
  { key: 'devices', title: 'Devices', width: 170 },
  { key: 'actions', title: 'Actions', width: 340 },
];

const deviceColumns: TableColumnsType = [
  { dataIndex: 'name', key: 'device', title: 'Device', width: 260 },
  { dataIndex: 'revoked', key: 'status', title: 'Status', width: 120 },
  { dataIndex: 'last_seen_at', key: 'last_seen_at', title: 'Last seen' },
  { key: 'actions', title: 'Actions', width: 120 },
];

const sourceColumns: TableColumnsType = [
  { dataIndex: 'display_name', key: 'image', title: 'Image', width: 260 },
  { dataIndex: 'tenant', key: 'tenant', title: 'Tenant', width: 160 },
  { dataIndex: 'path_hint', key: 'path', title: 'Path' },
  { key: 'snapshots', title: 'Snapshots', width: 150 },
  { key: 'sync', title: 'Sync', width: 260 },
  { dataIndex: 'updated_at', key: 'updated_at', title: 'Pushed by', width: 180 },
  { key: 'actions', title: 'Actions', width: 230 },
];

const snapshotColumns: TableColumnsType = [
  { dataIndex: 'name', key: 'snapshot', title: 'Snapshot', width: 220 },
  { dataIndex: 'created', key: 'created', title: 'Created', width: 180 },
  { key: 'content', title: 'Content', width: 150 },
  { dataIndex: 'compression', key: 'compression', title: 'Compression', width: 140 },
  { dataIndex: 'note', key: 'note', title: 'Note' },
  { key: 'actions', title: 'Actions', width: 160 },
];

const statItems = computed(() => [
  { hint: 'Workspace groups', label: 'Tenants', value: stats.value.tenants },
  { hint: 'Login accounts', label: 'Users', value: stats.value.users },
  { hint: 'Registered clients', label: 'Devices', value: stats.value.devices },
  { hint: `${formatBytes(stats.value.bundle_bytes)} stored`, label: 'Sources', value: stats.value.sources },
]);

const filteredUsers = computed(() => {
  const needle = filterText.value.trim().toLowerCase();
  if (!needle) {
    return users.value;
  }
  return users.value.filter((user) =>
    `${user.tenant} ${user.username} ${user.id}`.toLowerCase().includes(needle),
  );
});

const filteredSources = computed(() => {
  const needle = sourceFilterText.value.trim().toLowerCase();
  if (!needle) {
    return sources.value;
  }
  return sources.value.filter((source) =>
    [
      source.tenant,
      source.display_name,
      source.path_hint,
      source.id,
      source.origin_store_key,
      source.pushed_by_username,
      source.pushed_by_device_name,
    ]
      .join(' ')
      .toLowerCase()
      .includes(needle),
  );
});

const selectedUser = computed(() =>
  users.value.find((user) => user.id === selectedUserId.value),
);

const selectedSource = computed(() =>
  sources.value.find((source) => sourceRowKey(source) === selectedSourceKey.value),
);

const snapshotTotalPages = computed(() =>
  Math.max(1, Math.ceil(snapshotTotal.value / snapshotPerPage.value)),
);

function sourceRowKey(source: SnapzAdminSource) {
  return `${source.tenant_id}/${source.id}`;
}

function sourceRowClassName(source: SnapzAdminSource) {
  return sourceRowKey(source) === selectedSourceKey.value ? 'snapz-table-row-selected' : '';
}

function userRowClassName(user: SnapzAdminUser) {
  return user.id === selectedUserId.value ? 'snapz-table-row-selected' : '';
}

async function connect() {
  const token = tokenInput.value.trim();
  if (!token) {
    message.warning('Admin token is required');
    return;
  }
  setSnapzAdminToken(token);
  try {
    await loadAll();
    connected.value = true;
    message.success('Connected to snapz-server');
  } catch (error) {
    clearSnapzAdminToken();
    connected.value = false;
    message.error(error instanceof Error ? error.message : String(error));
  }
}

async function loadAll() {
  loading.value = true;
  try {
    const [overview, userList, sourceList] = await Promise.all([
      getSnapzAdminOverview(),
      getSnapzUsers(),
      getSnapzSources(),
    ]);
    stats.value = overview.stats;
    users.value = userList.users;
    sources.value = sourceList.sources;
    if (!sources.value.some((source) => sourceRowKey(source) === selectedSourceKey.value)) {
      selectedSourceKey.value = '';
      sourceSnapshots.value = [];
      snapshotTotal.value = 0;
      snapshotPage.value = 1;
      snapshotMemory.value = null;
    } else if (selectedSource.value) {
      await loadSourceSnapshots(selectedSource.value, snapshotPage.value, false);
    }
    if (!users.value.some((user) => user.id === selectedUserId.value)) {
      selectedUserId.value = users.value[0]?.id || '';
    }
    if (selectedUserId.value) {
      await selectUser(selectedUserId.value, false);
    } else {
      devices.value = [];
    }
  } finally {
    loading.value = false;
  }
}

async function selectUser(userId: string, showLoading = true) {
  if (showLoading) {
    loading.value = true;
  }
  try {
    selectedUserId.value = userId;
    const result = await getSnapzUserDevices(userId);
    devices.value = result.devices;
  } finally {
    if (showLoading) {
      loading.value = false;
    }
  }
}

async function createUser() {
  await createSnapzUser({ ...createForm });
  Object.assign(createForm, {
    disabled: false,
    password: '',
    tenant: '',
    username: '',
  });
  await loadAll();
  message.success('User created');
}

function confirmAction(title: string, content: string) {
  return new Promise<boolean>((resolve) => {
    Modal.confirm({
      content,
      onCancel: () => resolve(false),
      onOk: () => resolve(true),
      title,
    });
  });
}

async function renameUser(user: SnapzAdminUser) {
  const username = window.prompt('New username', user.username);
  if (!username || username.trim() === user.username) {
    return;
  }
  await updateSnapzUser(user.id, { username: username.trim() });
  await loadAll();
  message.success('Username updated');
}

async function renameSource(source: SnapzAdminSource) {
  const displayName = window.prompt('New image name', source.display_name);
  if (!displayName || displayName.trim() === source.display_name) {
    return;
  }
  await updateSnapzSource(source.tenant_id, source.id, {
    display_name: displayName.trim(),
  });
  await loadAll();
  message.success('Image renamed');
}

async function removeSource(source: SnapzAdminSource) {
  const allowed = await confirmAction(
    'Delete pushed image',
    `Delete ${source.tenant}/${source.display_name}? This removes the uploaded bundle from the server.`,
  );
  if (!allowed) {
    return;
  }
  await deleteSnapzSource(source.tenant_id, source.id);
  await loadAll();
  message.success('Image deleted');
}

async function loadSourceSnapshots(
  source: SnapzAdminSource,
  page = 1,
  showLoading = true,
) {
  if (showLoading) {
    loading.value = true;
  }
  try {
    selectedSourceKey.value = sourceRowKey(source);
    const result = await getSnapzSourceSnapshots(
      source.tenant_id,
      source.id,
      page,
      snapshotPerPage.value,
    );
    sourceSnapshots.value = result.snapshots;
    snapshotTotal.value = result.total;
    snapshotPage.value = result.page;
    snapshotPerPage.value = result.per_page;
    snapshotMemory.value = result.memory;
  } finally {
    if (showLoading) {
      loading.value = false;
    }
  }
}

function hideSourceSnapshots() {
  selectedSourceKey.value = '';
  sourceSnapshots.value = [];
  snapshotTotal.value = 0;
  snapshotPage.value = 1;
  snapshotMemory.value = null;
}

async function renameSourceSnapshot(snapshot: SnapzAdminSourceSnapshot) {
  if (!selectedSource.value) {
    return;
  }
  const name = window.prompt('New snapshot name', snapshot.name);
  if (!name || name.trim() === snapshot.name) {
    return;
  }
  await renameSnapzSourceSnapshot(
    selectedSource.value.tenant_id,
    selectedSource.value.id,
    snapshot.name,
    name.trim(),
  );
  await loadAll();
  message.success('Snapshot renamed');
}

async function removeSourceSnapshot(snapshot: SnapzAdminSourceSnapshot) {
  if (!selectedSource.value) {
    return;
  }
  const source = selectedSource.value;
  const allowed = await confirmAction(
    'Delete snapshot',
    `Delete ${source.tenant}/${source.display_name}/${snapshot.name}? This rewrites the uploaded bundle.`,
  );
  if (!allowed) {
    return;
  }
  await deleteSnapzSourceSnapshot(source.tenant_id, source.id, snapshot.name);
  await loadAll();
  message.success('Snapshot deleted');
}

async function toggleUser(user: SnapzAdminUser, disabled: boolean) {
  const allowed = await confirmAction(
    disabled ? 'Disable user' : 'Enable user',
    `${disabled ? 'Disable' : 'Enable'} ${user.tenant}/${user.username}?`,
  );
  if (!allowed) {
    return;
  }
  await updateSnapzUser(user.id, { disabled });
  await loadAll();
}

async function resetPassword(user: SnapzAdminUser) {
  const password = window.prompt(`New password for ${user.tenant}/${user.username}`);
  if (!password) {
    return;
  }
  await resetSnapzUserPassword(user.id, password);
  message.success('Password reset');
}

async function removeUser(user: SnapzAdminUser) {
  const allowed = await confirmAction(
    'Delete user',
    `Delete ${user.tenant}/${user.username}? Registered devices are removed too.`,
  );
  if (!allowed) {
    return;
  }
  await deleteSnapzUser(user.id);
  selectedUserId.value = '';
  await loadAll();
  message.success('User deleted');
}

async function revokeDevice(device: SnapzAdminDevice) {
  const allowed = await confirmAction(
    'Revoke device',
    `Revoke ${device.name} for ${device.tenant}/${device.username}?`,
  );
  if (!allowed) {
    return;
  }
  await revokeSnapzDevice(device.id);
  await loadAll();
  message.success('Device revoked');
}

async function revokeActiveDevices() {
  if (!selectedUser.value) {
    return;
  }
  const user = selectedUser.value;
  const allowed = await confirmAction(
    'Revoke active devices',
    `Revoke all active devices for ${user.tenant}/${user.username}?`,
  );
  if (!allowed) {
    return;
  }
  const result = await revokeSnapzUserDevices(user.id);
  await loadAll();
  message.success(`Revoked ${result.revoked} device(s)`);
}

function forgetToken() {
  clearSnapzAdminToken();
  connected.value = false;
  users.value = [];
  devices.value = [];
  sources.value = [];
  hideSourceSnapshots();
}

function formatDate(value?: string) {
  if (!value) {
    return '-';
  }
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) {
    return value;
  }
  return new Date(parsed).toLocaleString();
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) {
    return '0 B';
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let scaled = value;
  let unit = 0;
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  const precision = scaled >= 10 || unit === 0 ? 0 : 1;
  return `${scaled.toFixed(precision)} ${units[unit]}`;
}

function formatSpeed(value?: number) {
  return `${formatBytes(Number(value || 0))}/s`;
}

function formatEta(value?: number | null) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) {
    return '-';
  }
  if (seconds < 1) {
    return '<1s';
  }
  const whole = Math.round(seconds);
  const minutes = Math.floor(whole / 60);
  const secs = whole % 60;
  if (minutes <= 0) {
    return `${secs}s`;
  }
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  if (hours <= 0) {
    return `${minutes}m ${secs}s`;
  }
  return `${hours}h ${remMinutes}m`;
}

onMounted(() => {
  if (tokenInput.value) {
    connect();
  }
});
</script>

<template>
  <Page
    auto-content-height
    description="Manage snapz-server tenants, users, and sync devices."
    title="snapz-server"
  >
    <div class="snapz-admin">
      <div v-if="!connected" class="snapz-login">
        <Card :bordered="false" class="snapz-login-card">
          <Space direction="vertical" class="w-full" size="large">
            <div>
              <div class="snapz-eyebrow">Admin console</div>
              <h2 class="snapz-login-title">Connect to snapz-server</h2>
            </div>
            <Input.Password
              v-model:value="tokenInput"
              autocomplete="current-password"
              placeholder="Admin token"
              size="large"
              @press-enter="connect"
            />
            <Button block size="large" type="primary" @click="connect">
              Connect
            </Button>
          </Space>
        </Card>
      </div>

      <Space v-else direction="vertical" class="w-full" size="middle">
        <Card :bordered="false" class="snapz-hero">
          <div class="snapz-hero-content">
            <div>
              <div class="snapz-eyebrow">Connected</div>
              <h2 class="snapz-hero-title">snapz-server admin</h2>
            </div>
            <Space wrap>
              <Tag color="success">Admin API active</Tag>
              <Button type="primary" :loading="loading" @click="loadAll">
                Refresh
              </Button>
              <Button @click="forgetToken"> Forget token </Button>
            </Space>
          </div>
        </Card>

        <Row :gutter="[16, 16]">
          <Col v-for="item in statItems" :key="item.label" :lg="6" :md="12" :xs="24">
            <Card :bordered="false" class="snapz-stat-card">
              <div class="snapz-stat-label">{{ item.label }}</div>
              <div class="snapz-stat-value">{{ item.value }}</div>
              <div class="snapz-stat-hint">{{ item.hint }}</div>
            </Card>
          </Col>
        </Row>

        <Card :bordered="false" class="snapz-section-card">
          <template #title>
            <div class="snapz-card-title">
              <div class="snapz-card-heading">Pushed images</div>
              <div class="snapz-card-subtitle">
                Manage source bundles uploaded by snapz push.
              </div>
            </div>
          </template>
          <template #extra>
            <Input
              v-model:value="sourceFilterText"
              allow-clear
              class="snapz-search"
              placeholder="Filter tenant, image, path, or id"
            />
          </template>
          <Table
            :columns="sourceColumns"
            :data-source="filteredSources"
            :loading="loading"
            :pagination="{ pageSize: 8 }"
            :row-key="sourceRowKey"
            :row-class-name="sourceRowClassName"
            :scroll="{ x: 1260 }"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'image'">
                <div class="font-medium">{{ record.display_name }}</div>
                <div class="text-xs text-gray-500">{{ record.id }}</div>
              </template>
              <template v-else-if="column.key === 'path'">
                <div>{{ record.path_hint || '-' }}</div>
                <div class="text-xs text-gray-500">
                  {{ record.origin_store_key }}
                </div>
              </template>
              <template v-else-if="column.key === 'snapshots'">
                <Tag>{{ record.snapshot_count }} snapshot(s)</Tag>
                <div class="text-xs text-gray-500">
                  {{ formatBytes(record.bundle_bytes) }}
                </div>
              </template>
              <template v-else-if="column.key === 'sync'">
                <div>
                  <Tag
                    :color="
                      record.sync_status?.status === 'failed'
                        ? 'error'
                        : record.sync_status?.status === 'completed'
                          ? 'success'
                          : record.sync_status?.status === 'running'
                            ? 'warning'
                            : 'default'
                    "
                  >
                    {{ record.sync_status?.status || 'idle' }}
                  </Tag>
                  <Tag v-if="record.sync_status?.remote_only" color="warning">
                    remote_only
                  </Tag>
                </div>
                <div class="snapz-sync-bar">
                  <div
                    class="snapz-sync-bar-fill"
                    :style="{
                      width: `${Math.max(
                        0,
                        Math.min(100, Number(record.sync_status?.progress_percent || 0)),
                      )}%`,
                    }"
                  />
                </div>
                <div class="text-xs text-gray-500">
                  {{ Number(record.sync_status?.progress_percent || 0).toFixed(0) }}%
                  · {{ formatSpeed(record.sync_status?.speed_bps) }}
                  · ETA {{ formatEta(record.sync_status?.eta_seconds) }}
                </div>
                <div class="text-xs text-gray-500">
                  Last {{ formatDate(record.last_sync_at || record.sync_status?.last_sync_at) }}
                </div>
              </template>
              <template v-else-if="column.key === 'updated_at'">
                <div>{{ formatDate(record.updated_at) }}</div>
                <div class="text-xs text-gray-500">
                  {{ record.pushed_by_username || record.pushed_by_device_name || '-' }}
                </div>
              </template>
              <template v-else-if="column.key === 'actions'">
                <Space wrap>
                  <Button size="small" @click="loadSourceSnapshots(record)">
                    Details
                  </Button>
                  <Button size="small" @click="renameSource(record)">
                    Rename
                  </Button>
                  <Button danger size="small" @click="removeSource(record)">
                    Delete
                  </Button>
                </Space>
              </template>
            </template>
          </Table>
          <div
            v-if="selectedSource"
            class="snapz-snapshot-panel"
          >
            <div class="snapz-panel-header">
              <div>
                <div class="snapz-card-heading">Snapshots in {{ selectedSource.display_name }}</div>
                <div class="snapz-card-subtitle">
                  {{ snapshotTotal }} total · page {{ snapshotPage }}/{{ snapshotTotalPages }}
                  <template v-if="snapshotMemory">
                    · memory checked:
                    {{ formatBytes(snapshotMemory.required_bytes) }} required /
                    {{ formatBytes(snapshotMemory.limit_bytes) }} limit
                  </template>
                </div>
              </div>
              <Space>
                <Button
                  :disabled="snapshotPage <= 1"
                  size="small"
                  @click="loadSourceSnapshots(selectedSource, snapshotPage - 1)"
                >
                  Prev
                </Button>
                <Button
                  :disabled="snapshotPage >= snapshotTotalPages"
                  size="small"
                  @click="loadSourceSnapshots(selectedSource, snapshotPage + 1)"
                >
                  Next
                </Button>
                <Button size="small" @click="hideSourceSnapshots">
                  Hide
                </Button>
              </Space>
            </div>
            <Table
              :columns="snapshotColumns"
              :data-source="sourceSnapshots"
              :loading="loading"
              :pagination="false"
              row-key="name"
              :scroll="{ x: 940 }"
              size="small"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'snapshot'">
                  <div class="font-medium">{{ record.name }}</div>
                  <div class="text-xs text-gray-500">{{ record.kind }}</div>
                </template>
                <template v-else-if="column.key === 'created'">
                  {{ formatDate(record.created) }}
                </template>
                <template v-else-if="column.key === 'content'">
                  <Tag>{{ record.file_count }} file(s)</Tag>
                  <div class="text-xs text-gray-500">
                    {{ formatBytes(record.total_bytes_in || record.size_bytes) }}
                  </div>
                </template>
                <template v-else-if="column.key === 'note'">
                  {{ record.note || '-' }}
                </template>
                <template v-else-if="column.key === 'actions'">
                  <Space wrap>
                    <Button size="small" @click="renameSourceSnapshot(record)">
                      Rename
                    </Button>
                    <Button danger size="small" @click="removeSourceSnapshot(record)">
                      Delete
                    </Button>
                  </Space>
                </template>
              </template>
            </Table>
          </div>
        </Card>

      <div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,.85fr)]">
        <Card :bordered="false" class="snapz-section-card">
          <template #title>
            <div class="snapz-card-title">
              <div class="snapz-card-heading">Users</div>
              <div class="snapz-card-subtitle">
                Select a user to inspect registered devices.
              </div>
            </div>
          </template>
          <template #extra>
            <Input
              v-model:value="filterText"
              allow-clear
              class="snapz-search snapz-search-sm"
              placeholder="Filter tenant or username"
            />
          </template>
          <Table
            :columns="userColumns"
            :data-source="filteredUsers"
            :loading="loading"
            :pagination="{ pageSize: 8 }"
            row-key="id"
            :row-class-name="userRowClassName"
            :scroll="{ x: 1000 }"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'user'">
                <div class="font-medium">{{ record.username }}</div>
                <div class="text-xs text-gray-500">{{ record.id }}</div>
              </template>
              <template v-else-if="column.key === 'status'">
                <Tag :color="record.disabled ? 'error' : 'success'">
                  {{ record.disabled ? 'Disabled' : 'Enabled' }}
                </Tag>
              </template>
              <template v-else-if="column.key === 'devices'">
                <Tag>{{ record.active_device_count }}/{{ record.device_count }} active</Tag>
                <div class="text-xs text-gray-500">
                  {{ formatDate(record.last_seen_at) }}
                </div>
              </template>
              <template v-else-if="column.key === 'actions'">
                <Space wrap>
                  <Button size="small" @click="selectUser(record.id)">
                    Devices
                  </Button>
                  <Button size="small" @click="renameUser(record)">
                    Rename
                  </Button>
                  <Button
                    size="small"
                    @click="toggleUser(record, !record.disabled)"
                  >
                    {{ record.disabled ? 'Enable' : 'Disable' }}
                  </Button>
                  <Button size="small" @click="resetPassword(record)">
                    Password
                  </Button>
                  <Button danger size="small" @click="removeUser(record)">
                    Delete
                  </Button>
                </Space>
              </template>
            </template>
          </Table>
        </Card>

        <Space direction="vertical" class="w-full" size="middle">
          <Card :bordered="false" class="snapz-section-card">
            <template #title>
              <div class="snapz-card-title">
                <div class="snapz-card-heading">Create user</div>
                <div class="snapz-card-subtitle">
                  Add an account to a tenant.
                </div>
              </div>
            </template>
            <Form layout="vertical" :model="createForm" @finish="createUser">
              <Row :gutter="12">
                <Col :md="12" :xs="24">
                  <Form.Item label="Tenant" name="tenant" required>
                    <Input v-model:value="createForm.tenant" placeholder="acme" />
                  </Form.Item>
                </Col>
                <Col :md="12" :xs="24">
                  <Form.Item label="Username" name="username" required>
                    <Input v-model:value="createForm.username" placeholder="alice" />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item label="Password" name="password" required>
                <Input.Password v-model:value="createForm.password" />
              </Form.Item>
              <div class="snapz-form-footer">
                <Form.Item label="Disabled" name="disabled">
                  <Switch v-model:checked="createForm.disabled" />
                </Form.Item>
                <Form.Item>
                  <Button type="primary" html-type="submit"> Add user </Button>
                </Form.Item>
              </div>
            </Form>
          </Card>

          <Card :bordered="false" class="snapz-section-card">
            <template #title>
              <div class="snapz-card-title">
                <div class="snapz-card-heading">Devices</div>
                <div class="snapz-card-subtitle">
                  <template v-if="selectedUser">
                    {{ selectedUser.tenant }}/{{ selectedUser.username }}
                  </template>
                  <template v-else>Select a user</template>
                </div>
              </div>
            </template>
            <template #extra>
              <Button
                v-if="selectedUser"
                danger
                size="small"
                @click="revokeActiveDevices"
              >
                Revoke active
              </Button>
            </template>
            <Table
              :columns="deviceColumns"
              :data-source="devices"
              :loading="loading"
              :pagination="{ pageSize: 8 }"
              row-key="id"
              :scroll="{ x: 720 }"
              size="small"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'device'">
                  <div class="font-medium">{{ record.name }}</div>
                  <div class="text-xs text-gray-500">{{ record.id }}</div>
                </template>
                <template v-else-if="column.key === 'status'">
                  <Tag :color="record.revoked ? 'warning' : 'success'">
                    {{ record.revoked ? 'Revoked' : 'Active' }}
                  </Tag>
                </template>
                <template v-else-if="column.key === 'last_seen_at'">
                  <div>{{ formatDate(record.last_seen_at) }}</div>
                  <div class="text-xs text-gray-500">
                    Created {{ formatDate(record.created_at) }}
                  </div>
                </template>
                <template v-else-if="column.key === 'actions'">
                  <Button
                    danger
                    :disabled="record.revoked"
                    size="small"
                    @click="revokeDevice(record)"
                  >
                    Revoke
                  </Button>
                </template>
              </template>
            </Table>
          </Card>
        </Space>
      </div>
    </Space>
    </div>
  </Page>
</template>

<style scoped>
.snapz-admin {
  min-height: 100%;
}

.snapz-login {
  display: flex;
  min-height: 440px;
  align-items: center;
  justify-content: center;
}

.snapz-login-card {
  width: min(100%, 520px);
  border: 1px solid rgba(15, 23, 42, 0.08);
  box-shadow: 0 18px 45px rgba(15, 23, 42, 0.08);
}

.snapz-eyebrow {
  margin-bottom: 8px;
  color: #1677ff;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

.snapz-login-title,
.snapz-hero-title {
  margin: 0;
  color: #111827;
  font-size: 24px;
  font-weight: 700;
  line-height: 1.25;
}

.snapz-login-copy,
.snapz-hero-copy {
  max-width: 640px;
  margin: 8px 0 0;
  color: #64748b;
  line-height: 1.6;
}

.snapz-hero {
  border: 1px solid rgba(22, 119, 255, 0.16);
  background:
    linear-gradient(135deg, rgba(22, 119, 255, 0.1), rgba(20, 184, 166, 0.08)),
    #fff;
}

.snapz-hero-content {
  display: flex;
  gap: 20px;
  align-items: center;
  justify-content: space-between;
}

.snapz-stat-card,
.snapz-section-card {
  border: 1px solid rgba(15, 23, 42, 0.08);
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.05);
}

.snapz-stat-label {
  color: #64748b;
  font-size: 13px;
}

.snapz-stat-value {
  margin-top: 6px;
  color: #111827;
  font-size: 28px;
  font-weight: 700;
  line-height: 1.15;
}

.snapz-stat-hint {
  margin-top: 6px;
  color: #94a3b8;
  font-size: 12px;
}

.snapz-card-title {
  min-width: 0;
}

.snapz-card-heading {
  color: #111827;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.4;
}

.snapz-card-subtitle {
  margin-top: 2px;
  color: #64748b;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.5;
}

.snapz-search {
  width: min(320px, 48vw);
}

.snapz-search-sm {
  width: min(240px, 42vw);
}

.snapz-snapshot-panel {
  margin-top: 16px;
  padding: 16px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 8px;
  background: #f8fafc;
}

.snapz-panel-header {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}

.snapz-form-footer {
  display: flex;
  gap: 16px;
  align-items: flex-end;
  justify-content: space-between;
}

.snapz-sync-bar {
  height: 6px;
  margin: 6px 0;
  overflow: hidden;
  border-radius: 999px;
  background: #eef2f7;
}

.snapz-sync-bar-fill {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #1677ff, #10b981);
}

:deep(.snapz-table-row-selected > td) {
  background: #e6f4ff !important;
}

:deep(.ant-table-cell) {
  vertical-align: top;
}

@media (max-width: 768px) {
  .snapz-hero-content,
  .snapz-panel-header,
  .snapz-form-footer {
    align-items: stretch;
    flex-direction: column;
  }

  .snapz-search,
  .snapz-search-sm {
    width: 100%;
  }
}
</style>
