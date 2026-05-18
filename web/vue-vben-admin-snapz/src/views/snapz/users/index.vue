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
  { dataIndex: 'updated_at', key: 'updated_at', title: 'Pushed', width: 220 },
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
  { label: 'Tenants', value: stats.value.tenants },
  { label: 'Users', value: stats.value.users },
  { label: 'Devices', value: stats.value.devices },
  { label: 'Sources', value: stats.value.sources },
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
    <Card v-if="!connected" class="max-w-[520px]" title="Admin token">
      <Space direction="vertical" class="w-full" size="middle">
        <Input.Password
          v-model:value="tokenInput"
          autocomplete="current-password"
          placeholder="SNAPZ_SERVER_ADMIN_TOKEN"
          @press-enter="connect"
        />
        <Button type="primary" @click="connect"> Connect </Button>
      </Space>
    </Card>

    <Space v-else direction="vertical" class="w-full" size="middle">
      <Space>
        <Button type="primary" :loading="loading" @click="loadAll">
          Refresh
        </Button>
        <Button @click="forgetToken"> Forget token </Button>
      </Space>

      <Row :gutter="[16, 16]">
        <Col v-for="item in statItems" :key="item.label" :lg="6" :md="12" :xs="24">
          <Card :bordered="false">
            <div class="text-sm text-gray-500">{{ item.label }}</div>
            <div class="mt-2 text-2xl font-semibold">{{ item.value }}</div>
          </Card>
        </Col>
      </Row>

      <Card>
        <template #title>
          <div>
            <div>Pushed images</div>
            <div class="text-xs font-normal text-gray-500">
              Manage source bundles uploaded by snapz push.
            </div>
          </div>
        </template>
        <template #extra>
          <Input
            v-model:value="sourceFilterText"
            allow-clear
            placeholder="Filter tenant, image, path, or id"
            style="width: 320px"
          />
        </template>
        <Table
          :columns="sourceColumns"
          :data-source="filteredSources"
          :loading="loading"
          :pagination="{ pageSize: 8 }"
          :row-key="sourceRowKey"
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
        <Card
          v-if="selectedSource"
          class="mt-4"
          size="small"
        >
          <template #title>
            <div>
              <div>Snapshots in {{ selectedSource.display_name }}</div>
              <div class="text-xs font-normal text-gray-500">
                {{ snapshotTotal }} total · page {{ snapshotPage }}/{{ snapshotTotalPages }}
                <template v-if="snapshotMemory">
                  · memory checked:
                  {{ formatBytes(snapshotMemory.required_bytes) }} required /
                  {{ formatBytes(snapshotMemory.limit_bytes) }} limit
                </template>
              </div>
            </div>
          </template>
          <template #extra>
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
          </template>
          <Table
            :columns="snapshotColumns"
            :data-source="sourceSnapshots"
            :loading="loading"
            :pagination="false"
            row-key="name"
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
        </Card>
      </Card>

      <Card title="Create user">
        <Form layout="inline" :model="createForm" @finish="createUser">
          <Form.Item label="Tenant" name="tenant" required>
            <Input v-model:value="createForm.tenant" placeholder="acme" />
          </Form.Item>
          <Form.Item label="Username" name="username" required>
            <Input v-model:value="createForm.username" placeholder="alice" />
          </Form.Item>
          <Form.Item label="Password" name="password" required>
            <Input.Password v-model:value="createForm.password" />
          </Form.Item>
          <Form.Item label="Disabled" name="disabled">
            <Switch v-model:checked="createForm.disabled" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" html-type="submit"> Add user </Button>
          </Form.Item>
        </Form>
      </Card>

      <div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,.85fr)]">
        <Card title="Users">
          <template #extra>
            <Input
              v-model:value="filterText"
              allow-clear
              placeholder="Filter tenant or username"
              style="width: 240px"
            />
          </template>
          <Table
            :columns="userColumns"
            :data-source="filteredUsers"
            :loading="loading"
            :pagination="{ pageSize: 8 }"
            row-key="id"
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

        <Card>
          <template #title>
            <div>
              <div>Devices</div>
              <div class="text-xs font-normal text-gray-500">
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
      </div>
    </Space>
  </Page>
</template>
