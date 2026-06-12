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

import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Col,
  Form,
  Input,
  message,
  Modal,
  Progress,
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

import {
  formatBytes,
  formatDate,
  formatEta,
  formatSpeed,
} from '#/utils/format';

type SnapzLang = 'en' | 'zh';

const LANG_STORAGE_KEY = 'snapz-admin-lang';
const I18N = {
  en: {
    actionAddUser: 'Add user',
    actionDelete: 'Delete',
    actionDetails: 'Details',
    actionDevices: 'Devices',
    actionDisable: 'Disable',
    actionEnable: 'Enable',
    actionForgetToken: 'Forget token',
    actionHide: 'Hide',
    actionNext: 'Next',
    actionPassword: 'Password',
    actionPrev: 'Prev',
    actionRefresh: 'Refresh',
    actionRename: 'Rename',
    actionRevoke: 'Revoke',
    actionRevokeActive: 'Revoke active',
    active: 'Active',
    adminApiActive: 'Admin API active',
    adminConsole: 'Admin console',
    adminToken: 'Admin token',
    connected: 'Connected',
    connect: 'Connect',
    connectSuccess: 'Connected to snapz-server',
    content: 'Content',
    createUser: 'Create user',
    createUserCopy: 'Add an account to a tenant.',
    created: 'Created',
    compression: 'Compression',
    disabled: 'Disabled',
    enabled: 'Enabled',
    image: 'Image',
    language: 'Language',
    last: 'Last',
    lastSeen: 'Last seen',
    eta: 'ETA',
    heroTitle: 'snapz-server admin',
    loginCopy: 'Connect with your snapz-server admin token.',
    loginTitle: 'Connect to snapz-server',
    pageDescription: 'Manage snapz-server tenants, users, and sync devices.',
    password: 'Password',
    path: 'Path',
    placeholderSourceFilter: 'Filter tenant, image, path, or id',
    placeholderUserFilter: 'Filter tenant or username',
    pushedBy: 'Pushed by',
    pushedImages: 'Pushed images',
    pushedImagesCopy: 'Manage source bundles uploaded by snapz push.',
    remoteOnly: 'remote_only',
    requiredToken: 'Admin token is required',
    revoked: 'Revoked',
    selectUser: 'Select a user',
    selectUserCopy: 'Select a user to inspect registered devices.',
    snapshot: 'Snapshot',
    snapshotCount: '{count} snapshot(s)',
    snapshots: 'Snapshots',
    sources: 'Sources',
    snapshotsIn: 'Snapshots in {name}',
    status: 'Status',
    sync: 'Sync',
    tableActions: 'Actions',
    tenant: 'Tenant',
    note: 'Note',
    username: 'Username',
    users: 'Users',
    workspaceGroups: 'Workspace groups',
    loginAccounts: 'Login accounts',
    registeredClients: 'Registered clients',
    stored: '{size} stored',
    activeCount: '{active}/{total} active',
    fileCount: '{count} file(s)',
    memoryChecked: 'memory checked:',
    required: 'required',
    limit: 'limit',
    totalPage: '{total} total · page {page}/{pages}',
    deletePushedImage: 'Delete pushed image',
    deletePushedImageCopy: 'Delete {tenant}/{name}? This removes the uploaded bundle from the server.',
    deleteSnapshot: 'Delete snapshot',
    deleteSnapshotCopy: 'Delete {tenant}/{image}/{name}? This rewrites the uploaded bundle.',
    deleteUser: 'Delete user',
    deleteUserCopy: 'Delete {tenant}/{username}? Registered devices are removed too.',
    deviceRevoked: 'Device revoked',
    imageDeleted: 'Image deleted',
    imageRenamed: 'Image renamed',
    newImageName: 'New image name',
    newSnapshotName: 'New snapshot name',
    newUsername: 'New username',
    passwordReset: 'Password reset',
    resetPasswordFor: 'New password for {tenant}/{username}',
    revokeActiveDevices: 'Revoke active devices',
    revokeActiveDevicesCopy: 'Revoke all active devices for {tenant}/{username}?',
    revokeDevice: 'Revoke device',
    revokeDeviceCopy: 'Revoke {name} for {tenant}/{username}?',
    revokedDevices: 'Revoked {count} device(s)',
    snapshotDeleted: 'Snapshot deleted',
    snapshotRenamed: 'Snapshot renamed',
    toggleUserCopy: '{action} {tenant}/{username}?',
    userCreated: 'User created',
    userDeleted: 'User deleted',
    usernameUpdated: 'Username updated',
  },
  zh: {
    actionAddUser: '添加用户',
    actionDelete: '删除',
    actionDetails: '详情',
    actionDevices: '设备',
    actionDisable: '禁用',
    actionEnable: '启用',
    actionForgetToken: '忘记令牌',
    actionHide: '隐藏',
    actionNext: '下一页',
    actionPassword: '密码',
    actionPrev: '上一页',
    actionRefresh: '刷新',
    actionRename: '重命名',
    actionRevoke: '吊销',
    actionRevokeActive: '吊销活跃设备',
    active: '活跃',
    adminApiActive: '管理 API 已启用',
    adminConsole: '管理控制台',
    adminToken: '管理令牌',
    connected: '已连接',
    connect: '连接',
    connectSuccess: '已连接到 snapz-server',
    content: '内容',
    createUser: '创建用户',
    createUserCopy: '向租户添加账号。',
    created: '创建时间',
    compression: '压缩',
    disabled: '已禁用',
    enabled: '已启用',
    image: '镜像',
    language: '语言',
    last: '上次',
    lastSeen: '上次在线',
    eta: '预计剩余',
    heroTitle: 'snapz-server 管理台',
    loginCopy: '使用 snapz-server 管理令牌连接。',
    loginTitle: '连接到 snapz-server',
    pageDescription: '管理 snapz-server 租户、用户和同步设备。',
    password: '密码',
    path: '路径',
    placeholderSourceFilter: '按租户、镜像、路径或 ID 过滤',
    placeholderUserFilter: '按租户或用户名过滤',
    pushedBy: '推送者',
    pushedImages: '已推送镜像',
    pushedImagesCopy: '管理通过 snapz push 上传的源 bundle。',
    remoteOnly: '仅远端',
    requiredToken: '需要管理令牌',
    revoked: '已吊销',
    selectUser: '选择用户',
    selectUserCopy: '选择用户以查看已注册设备。',
    snapshot: '快照',
    snapshotCount: '{count} 个快照',
    snapshots: '快照',
    sources: '源',
    snapshotsIn: '{name} 中的快照',
    status: '状态',
    sync: '同步',
    tableActions: '操作',
    tenant: '租户',
    note: '备注',
    username: '用户名',
    users: '用户',
    workspaceGroups: '工作区分组',
    loginAccounts: '登录账号',
    registeredClients: '已注册客户端',
    stored: '已存储 {size}',
    activeCount: '{active}/{total} 活跃',
    fileCount: '{count} 个文件',
    memoryChecked: '内存检查：',
    required: '需要',
    limit: '限制',
    totalPage: '共 {total} 个 · 第 {page}/{pages} 页',
    deletePushedImage: '删除已推送镜像',
    deletePushedImageCopy: '删除 {tenant}/{name}？这会从服务端移除上传的 bundle。',
    deleteSnapshot: '删除快照',
    deleteSnapshotCopy: '删除 {tenant}/{image}/{name}？这会重写上传的 bundle。',
    deleteUser: '删除用户',
    deleteUserCopy: '删除 {tenant}/{username}？已注册设备也会被移除。',
    deviceRevoked: '设备已吊销',
    imageDeleted: '镜像已删除',
    imageRenamed: '镜像已重命名',
    newImageName: '新的镜像名称',
    newSnapshotName: '新的快照名称',
    newUsername: '新的用户名',
    passwordReset: '密码已重置',
    resetPasswordFor: '{tenant}/{username} 的新密码',
    revokeActiveDevices: '吊销活跃设备',
    revokeActiveDevicesCopy: '吊销 {tenant}/{username} 的所有活跃设备？',
    revokeDevice: '吊销设备',
    revokeDeviceCopy: '吊销 {tenant}/{username} 的设备 {name}？',
    revokedDevices: '已吊销 {count} 个设备',
    snapshotDeleted: '快照已删除',
    snapshotRenamed: '快照已重命名',
    toggleUserCopy: '{action} {tenant}/{username}？',
    userCreated: '用户已创建',
    userDeleted: '用户已删除',
    usernameUpdated: '用户名已更新',
  },
} satisfies Record<SnapzLang, Record<string, string>>;

function getInitialLang(): SnapzLang {
  const saved = localStorage.getItem(LANG_STORAGE_KEY);
  if (saved === 'en' || saved === 'zh') {
    return saved;
  }
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en';
}

const lang = ref<SnapzLang>(getInitialLang());

function t(key: keyof typeof I18N.en, params: Record<string, string | number> = {}) {
  return I18N[lang.value][key].replace(/\{(\w+)\}/g, (_, name) =>
    String(params[name] ?? ''),
  );
}

function setLang(next: SnapzLang) {
  lang.value = next;
  localStorage.setItem(LANG_STORAGE_KEY, next);
}

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

const userColumns = computed<TableColumnsType>(() => [
  { dataIndex: 'username', key: 'user', title: t('username'), width: 230 },
  { dataIndex: 'tenant', key: 'tenant', title: t('tenant'), width: 160 },
  { dataIndex: 'disabled', key: 'status', title: t('status'), width: 120 },
  { key: 'devices', title: t('actionDevices'), width: 170 },
  { key: 'actions', title: t('tableActions'), width: 340 },
]);

const deviceColumns = computed<TableColumnsType>(() => [
  { dataIndex: 'name', key: 'device', title: t('actionDevices'), width: 260 },
  { dataIndex: 'revoked', key: 'status', title: t('status'), width: 120 },
  { dataIndex: 'last_seen_at', key: 'last_seen_at', title: t('lastSeen'), width: 180 },
  { key: 'actions', title: t('tableActions'), width: 120 },
]);

const sourceColumns = computed<TableColumnsType>(() => [
  { dataIndex: 'display_name', key: 'image', title: t('image'), width: 260 },
  { dataIndex: 'tenant', key: 'tenant', title: t('tenant'), width: 160 },
  { dataIndex: 'path_hint', key: 'path', title: t('path') },
  { key: 'snapshots', title: t('snapshots'), width: 150 },
  { key: 'sync', title: t('sync'), width: 260 },
  { dataIndex: 'updated_at', key: 'updated_at', title: t('pushedBy'), width: 180 },
  { key: 'actions', title: t('tableActions'), width: 230 },
]);

const snapshotColumns = computed<TableColumnsType>(() => [
  { dataIndex: 'name', key: 'snapshot', title: t('snapshot'), width: 220 },
  { dataIndex: 'created', key: 'created', title: t('created'), width: 180 },
  { key: 'content', title: t('content'), width: 150 },
  { dataIndex: 'compression', key: 'compression', title: t('compression'), width: 140 },
  { dataIndex: 'note', key: 'note', title: t('note') },
  { key: 'actions', title: t('tableActions'), width: 160 },
]);

const statItems = computed(() => [
  { hint: t('workspaceGroups'), label: t('tenant'), value: stats.value.tenants },
  { hint: t('loginAccounts'), label: t('users'), value: stats.value.users },
  { hint: t('registeredClients'), label: t('actionDevices'), value: stats.value.devices },
  {
    hint: t('stored', { size: formatBytes(stats.value.bundle_bytes) }),
    label: t('sources'),
    value: stats.value.sources,
  },
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
    message.warning(t('requiredToken'));
    return;
  }
  setSnapzAdminToken(token);
  try {
    await loadAll();
    connected.value = true;
    message.success(t('connectSuccess'));
  } catch (error) {
    clearSnapzAdminToken();
    connected.value = false;
    message.error(error instanceof Error ? error.message : String(error));
  }
}

async function loadAll(showLoading = true) {
  if (showLoading) {
    loading.value = true;
  }
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
    if (showLoading) {
      loading.value = false;
    }
  }
}

let refreshingInBackground = false;

async function refreshInBackground() {
  if (refreshingInBackground) {
    return;
  }
  refreshingInBackground = true;
  try {
    await loadAll(false);
  } finally {
    refreshingInBackground = false;
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
  message.success(t('userCreated'));
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
  const username = window.prompt(t('newUsername'), user.username);
  if (!username || username.trim() === user.username) {
    return;
  }
  await updateSnapzUser(user.id, { username: username.trim() });
  await loadAll();
  message.success(t('usernameUpdated'));
}

async function renameSource(source: SnapzAdminSource) {
  const displayName = window.prompt(t('newImageName'), source.display_name);
  if (!displayName || displayName.trim() === source.display_name) {
    return;
  }
  await updateSnapzSource(source.tenant_id, source.id, {
    display_name: displayName.trim(),
  });
  await loadAll();
  message.success(t('imageRenamed'));
}

async function removeSource(source: SnapzAdminSource) {
  const allowed = await confirmAction(
    t('deletePushedImage'),
    t('deletePushedImageCopy', {
      name: source.display_name,
      tenant: source.tenant,
    }),
  );
  if (!allowed) {
    return;
  }
  await deleteSnapzSource(source.tenant_id, source.id);
  await loadAll();
  message.success(t('imageDeleted'));
}

const snapshotPanelRef = ref<HTMLElement | null>(null);

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

    if (showLoading) {
      setTimeout(() => {
        snapshotPanelRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 100);
    }
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
  const name = window.prompt(t('newSnapshotName'), snapshot.name);
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
  message.success(t('snapshotRenamed'));
}

async function removeSourceSnapshot(snapshot: SnapzAdminSourceSnapshot) {
  if (!selectedSource.value) {
    return;
  }
  const source = selectedSource.value;
  const allowed = await confirmAction(
    t('deleteSnapshot'),
    t('deleteSnapshotCopy', {
      image: source.display_name,
      name: snapshot.name,
      tenant: source.tenant,
    }),
  );
  if (!allowed) {
    return;
  }
  await deleteSnapzSourceSnapshot(source.tenant_id, source.id, snapshot.name);
  await loadAll();
  message.success(t('snapshotDeleted'));
}

async function toggleUser(user: SnapzAdminUser, disabled: boolean) {
  const action = disabled ? t('actionDisable') : t('actionEnable');
  const allowed = await confirmAction(
    action,
    t('toggleUserCopy', {
      action,
      tenant: user.tenant,
      username: user.username,
    }),
  );
  if (!allowed) {
    return;
  }
  await updateSnapzUser(user.id, { disabled });
  await loadAll();
}

async function resetPassword(user: SnapzAdminUser) {
  const password = window.prompt(t('resetPasswordFor', {
    tenant: user.tenant,
    username: user.username,
  }));
  if (!password) {
    return;
  }
  await resetSnapzUserPassword(user.id, password);
  message.success(t('passwordReset'));
}

async function removeUser(user: SnapzAdminUser) {
  const allowed = await confirmAction(
    t('deleteUser'),
    t('deleteUserCopy', {
      tenant: user.tenant,
      username: user.username,
    }),
  );
  if (!allowed) {
    return;
  }
  await deleteSnapzUser(user.id);
  selectedUserId.value = '';
  await loadAll();
  message.success(t('userDeleted'));
}

async function revokeDevice(device: SnapzAdminDevice) {
  const allowed = await confirmAction(
    t('revokeDevice'),
    t('revokeDeviceCopy', {
      name: device.name,
      tenant: device.tenant,
      username: device.username,
    }),
  );
  if (!allowed) {
    return;
  }
  await revokeSnapzDevice(device.id);
  await loadAll();
  message.success(t('deviceRevoked'));
}

async function revokeActiveDevices() {
  if (!selectedUser.value) {
    return;
  }
  const user = selectedUser.value;
  const allowed = await confirmAction(
    t('revokeActiveDevices'),
    t('revokeActiveDevicesCopy', {
      tenant: user.tenant,
      username: user.username,
    }),
  );
  if (!allowed) {
    return;
  }
  const result = await revokeSnapzUserDevices(user.id);
  await loadAll();
  message.success(t('revokedDevices', { count: result.revoked }));
}

function forgetToken() {
  clearSnapzAdminToken();
  connected.value = false;
  users.value = [];
  devices.value = [];
  sources.value = [];
  hideSourceSnapshots();
}

const anySyncRunning = computed(() =>
  sources.value.some((s) => s.sync_status?.status === 'running'),
);

let refreshTimer: any = null;

watch(
  anySyncRunning,
  (running) => {
    if (running) {
      if (!refreshTimer) {
        refreshTimer = setInterval(() => {
          refreshInBackground();
        }, 3000);
      }
    } else if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
  },
  { immediate: true },
);

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer);
  }
});

onMounted(() => {
  if (tokenInput.value) {
    connect();
  }
});
</script>

<template>
  <Page
    auto-content-height
    :description="t('pageDescription')"
    title="snapz-server"
  >
    <div class="snapz-admin">
      <div v-if="!connected" class="snapz-login">
        <Card :bordered="false" class="snapz-login-card">
          <Space direction="vertical" class="w-full" size="large">
            <div>
              <div class="snapz-eyebrow">{{ t('adminConsole') }}</div>
              <h2 class="snapz-login-title">{{ t('loginTitle') }}</h2>
              <p class="snapz-login-copy">{{ t('loginCopy') }}</p>
            </div>
            <Input.Password
              v-model:value="tokenInput"
              autocomplete="current-password"
              :placeholder="t('adminToken')"
              size="large"
              @press-enter="connect"
            />
            <Button block size="large" type="primary" @click="connect">
              {{ t('connect') }}
            </Button>
            <Space align="center">
              <span class="text-xs text-gray-500">{{ t('language') }}</span>
              <Button
                size="small"
                :type="lang === 'en' ? 'primary' : 'default'"
                @click="setLang('en')"
              >
                English
              </Button>
              <Button
                size="small"
                :type="lang === 'zh' ? 'primary' : 'default'"
                @click="setLang('zh')"
              >
                中文
              </Button>
            </Space>
          </Space>
        </Card>
      </div>

      <Space v-else direction="vertical" class="w-full" size="middle">
        <Card :bordered="false" class="snapz-hero">
          <div class="snapz-hero-content">
            <div>
              <div class="snapz-eyebrow">{{ t('connected') }}</div>
              <h2 class="snapz-hero-title">{{ t('heroTitle') }}</h2>
            </div>
            <Space wrap>
              <Tag color="success">{{ t('adminApiActive') }}</Tag>
              <Button type="primary" :loading="loading" @click="loadAll()">
                {{ t('actionRefresh') }}
              </Button>
              <Button @click="forgetToken">{{ t('actionForgetToken') }}</Button>
              <Button
                size="small"
                :type="lang === 'en' ? 'primary' : 'default'"
                @click="setLang('en')"
              >
                English
              </Button>
              <Button
                size="small"
                :type="lang === 'zh' ? 'primary' : 'default'"
                @click="setLang('zh')"
              >
                中文
              </Button>
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
              <div class="snapz-card-heading">{{ t('pushedImages') }}</div>
              <div class="snapz-card-subtitle">
                {{ t('pushedImagesCopy') }}
              </div>
            </div>
          </template>
          <template #extra>
            <Input
              v-model:value="sourceFilterText"
              allow-clear
              class="snapz-search"
              :placeholder="t('placeholderSourceFilter')"
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
                <Tag>{{ t('snapshotCount', { count: record.snapshot_count }) }}</Tag>
                <div class="text-xs text-gray-500">
                  {{ formatBytes(record.bundle_bytes) }}
                </div>
              </template>
              <template v-else-if="column.key === 'sync'">
                <div class="flex items-center gap-2">
                  <Tag
                    :color="
                      record.sync_status?.status === 'failed'
                        ? 'error'
                        : record.sync_status?.status === 'completed'
                          ? 'success'
                          : record.sync_status?.status === 'running'
                            ? 'processing'
                            : 'default'
                    "
                  >
                    {{ record.sync_status?.status || 'idle' }}
                  </Tag>
                  <Tag v-if="record.sync_status?.remote_only" color="warning">
                    {{ t('remoteOnly') }}
                  </Tag>
                </div>
                <div class="mt-2">
                  <Progress
                    :percent="Number(record.sync_status?.progress_percent || 0)"
                    :show-info="false"
                    :size="[180, 6]"
                    :status="
                      record.sync_status?.status === 'failed'
                        ? 'exception'
                        : record.sync_status?.status === 'completed'
                          ? 'success'
                          : 'active'
                    "
                    :stroke-color="{
                      '0%': '#1677ff',
                      '100%': '#10b981',
                    }"
                  />
                </div>
                <div class="mt-1 flex justify-between text-[11px] text-gray-500">
                  <span>
                    {{ Number(record.sync_status?.progress_percent || 0).toFixed(0) }}% ·
                    {{ formatSpeed(record.sync_status?.speed_bps) }}
                  </span>
                  <span>{{ t('eta') }} {{ formatEta(record.sync_status?.eta_seconds) }}</span>
                </div>
                <div class="mt-0.5 text-[11px] text-gray-400">
                  {{ t('last') }}: {{ formatDate(record.last_sync_at || record.sync_status?.last_sync_at) }}
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
                    {{ t('actionDetails') }}
                  </Button>
                  <Button size="small" @click="renameSource(record)">
                    {{ t('actionRename') }}
                  </Button>
                  <Button danger size="small" @click="removeSource(record)">
                    {{ t('actionDelete') }}
                  </Button>
                </Space>
              </template>
            </template>
          </Table>
          <div
            v-if="selectedSource"
            ref="snapshotPanelRef"
            class="snapz-snapshot-panel"
          >
            <div class="snapz-panel-header">
              <div>
                <div class="snapz-card-heading">
                  {{ t('snapshotsIn', { name: selectedSource.display_name }) }}
                </div>
                <div class="snapz-card-subtitle">
                  {{ t('totalPage', { total: snapshotTotal, page: snapshotPage, pages: snapshotTotalPages }) }}
                  <template v-if="snapshotMemory">
                    · {{ t('memoryChecked') }}
                    {{ formatBytes(snapshotMemory.required_bytes) }} {{ t('required') }} /
                    {{ formatBytes(snapshotMemory.limit_bytes) }} {{ t('limit') }}
                  </template>
                </div>
              </div>
              <Space>
                <Button
                  :disabled="snapshotPage <= 1"
                  size="small"
                  @click="loadSourceSnapshots(selectedSource, snapshotPage - 1)"
                >
                  {{ t('actionPrev') }}
                </Button>
                <Button
                  :disabled="snapshotPage >= snapshotTotalPages"
                  size="small"
                  @click="loadSourceSnapshots(selectedSource, snapshotPage + 1)"
                >
                  {{ t('actionNext') }}
                </Button>
                <Button size="small" @click="hideSourceSnapshots">
                  {{ t('actionHide') }}
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
                  <Tag>{{ t('fileCount', { count: record.file_count }) }}</Tag>
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
                      {{ t('actionRename') }}
                    </Button>
                    <Button danger size="small" @click="removeSourceSnapshot(record)">
                      {{ t('actionDelete') }}
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
              <div class="snapz-card-heading">{{ t('users') }}</div>
              <div class="snapz-card-subtitle">
                {{ t('selectUserCopy') }}
              </div>
            </div>
          </template>
          <template #extra>
            <Input
              v-model:value="filterText"
              allow-clear
              class="snapz-search snapz-search-sm"
              :placeholder="t('placeholderUserFilter')"
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
                  {{ record.disabled ? t('disabled') : t('enabled') }}
                </Tag>
              </template>
              <template v-else-if="column.key === 'devices'">
                <Tag>
                  {{ t('activeCount', { active: record.active_device_count, total: record.device_count }) }}
                </Tag>
                <div class="text-xs text-gray-500">
                  {{ formatDate(record.last_seen_at) }}
                </div>
              </template>
              <template v-else-if="column.key === 'actions'">
                <Space wrap>
                  <Button size="small" @click="selectUser(record.id)">
                    {{ t('actionDevices') }}
                  </Button>
                  <Button size="small" @click="renameUser(record)">
                    {{ t('actionRename') }}
                  </Button>
                  <Button
                    size="small"
                    @click="toggleUser(record, !record.disabled)"
                  >
                    {{ record.disabled ? t('actionEnable') : t('actionDisable') }}
                  </Button>
                  <Button size="small" @click="resetPassword(record)">
                    {{ t('actionPassword') }}
                  </Button>
                  <Button danger size="small" @click="removeUser(record)">
                    {{ t('actionDelete') }}
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
                <div class="snapz-card-heading">{{ t('createUser') }}</div>
                <div class="snapz-card-subtitle">
                  {{ t('createUserCopy') }}
                </div>
              </div>
            </template>
            <Form layout="vertical" :model="createForm" @finish="createUser">
              <Row :gutter="12">
                <Col :md="12" :xs="24">
                  <Form.Item :label="t('tenant')" name="tenant" required>
                    <Input v-model:value="createForm.tenant" placeholder="acme" />
                  </Form.Item>
                </Col>
                <Col :md="12" :xs="24">
                  <Form.Item :label="t('username')" name="username" required>
                    <Input v-model:value="createForm.username" placeholder="alice" />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item :label="t('password')" name="password" required>
                <Input.Password v-model:value="createForm.password" />
              </Form.Item>
              <div class="snapz-form-footer">
                <Form.Item :label="t('disabled')" name="disabled" value-prop-name="checked">
                  <Switch v-model:checked="createForm.disabled" />
                </Form.Item>
                <Form.Item>
                  <Button type="primary" html-type="submit">
                    {{ t('actionAddUser') }}
                  </Button>
                </Form.Item>
              </div>
            </Form>
          </Card>

          <Card :bordered="false" class="snapz-section-card">
            <template #title>
              <div class="snapz-card-title">
                <div class="snapz-card-heading">{{ t('actionDevices') }}</div>
                <div class="snapz-card-subtitle">
                  <template v-if="selectedUser">
                    {{ selectedUser.tenant }}/{{ selectedUser.username }}
                  </template>
                  <template v-else>{{ t('selectUser') }}</template>
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
                {{ t('actionRevokeActive') }}
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
                    {{ record.revoked ? t('revoked') : t('active') }}
                  </Tag>
                </template>
                <template v-else-if="column.key === 'last_seen_at'">
                  <div>{{ formatDate(record.last_seen_at) }}</div>
                  <div class="text-xs text-gray-500">
                    {{ t('created') }} {{ formatDate(record.created_at) }}
                  </div>
                </template>
                <template v-else-if="column.key === 'actions'">
                  <Button
                    danger
                    :disabled="record.revoked"
                    size="small"
                    @click="revokeDevice(record)"
                  >
                    {{ t('actionRevoke') }}
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
