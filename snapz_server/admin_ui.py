"""Built-in admin UI for ``snapz-server``."""

from __future__ import annotations

ADMIN_UI_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>snapz-server Admin</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fa;
      --panel: #ffffff;
      --ink: #111827;
      --muted: #667085;
      --line: #d8dee8;
      --primary: #2563eb;
      --primary-strong: #1d4ed8;
      --danger: #b42318;
      --success: #047857;
      --warn: #b54708;
      --shadow: 0 10px 24px rgba(15, 23, 42, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      padding: 1rem 1.5rem;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 1.25rem; font-weight: 700; }
    h2 { font-size: 1rem; font-weight: 700; }
    h3 { font-size: .95rem; font-weight: 700; }
    main {
      width: min(1280px, 100%);
      margin: 0 auto;
      padding: 1.25rem;
    }
    .muted { color: var(--muted); }
    .toolbar {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: .75rem;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: .75rem;
      margin-bottom: 1rem;
    }
    .stat, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .stat { padding: 1rem; }
    .stat strong {
      display: block;
      margin-top: .35rem;
      font-size: 1.5rem;
    }
    .panel { margin-bottom: 1rem; overflow: hidden; }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: .75rem;
      padding: 1rem;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }
    .panel-body { padding: 1rem; }
    .split {
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(320px, .85fr);
      gap: 1rem;
    }
    form {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr)) auto;
      align-items: end;
      gap: .75rem;
    }
    label {
      display: grid;
      gap: .35rem;
      color: var(--muted);
      font-size: .82rem;
      font-weight: 600;
    }
    input {
      width: 100%;
      height: 2.35rem;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 .7rem;
      color: var(--ink);
      background: #fff;
      font: inherit;
    }
    input[type="checkbox"] { width: 1rem; height: 1rem; }
    button {
      height: 2.35rem;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 .8rem;
      background: #fff;
      color: var(--ink);
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
    }
    button:hover { border-color: #aab4c3; }
    button.primary {
      background: var(--primary);
      border-color: var(--primary);
      color: #fff;
    }
    button.primary:hover { background: var(--primary-strong); }
    button.danger {
      color: var(--danger);
      border-color: #f2b8b5;
    }
    button.ghost {
      background: transparent;
      border-color: transparent;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      background: var(--panel);
    }
    th, td {
      padding: .7rem .8rem;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: middle;
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      font-size: .78rem;
      font-weight: 700;
      text-transform: uppercase;
    }
    tr.selected { background: #eff6ff; }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: .4rem;
    }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      font-size: .78rem;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 1.5rem;
      border-radius: 999px;
      padding: 0 .55rem;
      font-size: .78rem;
      font-weight: 700;
      background: #eef2f7;
      color: var(--muted);
    }
    .badge.ok { background: #ecfdf3; color: var(--success); }
    .badge.warn { background: #fff7ed; color: var(--warn); }
    .badge.bad { background: #fff1f0; color: var(--danger); }
    .notice {
      min-height: 2.35rem;
      display: flex;
      align-items: center;
      padding: .6rem .8rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--muted);
      margin-bottom: 1rem;
    }
    .notice.error {
      border-color: #f2b8b5;
      background: #fff7f6;
      color: var(--danger);
    }
    .empty {
      padding: 2rem 1rem;
      text-align: center;
      color: var(--muted);
    }
    .token-screen {
      max-width: 440px;
      margin: 8vh auto 0;
    }
    .hidden { display: none !important; }
    @media (max-width: 980px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .split { grid-template-columns: 1fr; }
      form { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 640px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding: .75rem; }
      .grid, form { grid-template-columns: 1fr; }
      th, td { padding: .6rem; }
      .panel-head { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>snapz-server Admin</h1>
      <p class="muted">Manage tenants, users, and registered sync devices.</p>
    </div>
    <div class="toolbar">
      <button id="refreshButton" class="primary hidden" type="button">Refresh</button>
      <button id="logoutButton" class="hidden" type="button">Forget token</button>
    </div>
  </header>

  <main>
    <section id="tokenScreen" class="token-screen panel">
      <div class="panel-head">
        <h2>Admin token</h2>
      </div>
      <div class="panel-body">
        <p class="muted" style="margin-bottom: .85rem;">
          Start snapz-server with --admin-token or SNAPZ_SERVER_ADMIN_TOKEN, then enter it here.
        </p>
        <form id="tokenForm" style="grid-template-columns: 1fr auto;">
          <label>
            Token
            <input id="tokenInput" autocomplete="current-password" type="password" required>
          </label>
          <button class="primary" type="submit">Connect</button>
        </form>
      </div>
    </section>

    <section id="appScreen" class="hidden">
      <div id="notice" class="notice">Ready.</div>

      <section class="grid" id="statsGrid"></section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>Pushed images</h2>
            <p class="muted">Manage source bundles uploaded by snapz push.</p>
          </div>
          <label style="min-width: 260px;">
            Filter
            <input id="sourceFilter" placeholder="tenant, image name, path, or id">
          </label>
        </div>
        <div id="sourcesTable"></div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Create user</h2>
        </div>
        <div class="panel-body">
          <form id="createUserForm">
            <label>
              Tenant
              <input name="tenant" placeholder="acme" required>
            </label>
            <label>
              Username
              <input name="username" placeholder="alice" required>
            </label>
            <label>
              Password
              <input name="password" type="password" required>
            </label>
            <label>
              Disabled
              <span class="toolbar"><input name="disabled" type="checkbox"> Initially disabled</span>
            </label>
            <button class="primary" type="submit">Add user</button>
          </form>
        </div>
      </section>

      <section class="split">
        <section class="panel">
          <div class="panel-head">
            <h2>Users</h2>
            <label style="min-width: 220px;">
              Filter
              <input id="userFilter" placeholder="tenant or username">
            </label>
          </div>
          <div id="usersTable"></div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div>
              <h2>Devices</h2>
              <p id="deviceSubhead" class="muted">Select a user to view devices.</p>
            </div>
            <button id="revokeAllButton" class="danger hidden" type="button">Revoke active</button>
          </div>
          <div id="devicesTable"></div>
        </section>
      </section>
    </section>
  </main>

  <script>
    const state = {
      token: sessionStorage.getItem('snapzAdminToken') || '',
      stats: {},
      users: [],
      devices: [],
      sources: [],
      sourceSnapshots: [],
      selectedSourceKey: '',
      snapshotPage: 1,
      snapshotPerPage: 25,
      snapshotTotal: 0,
      snapshotMemory: null,
      selectedUserId: '',
      filter: '',
      sourceFilter: '',
    };

    const el = (id) => document.getElementById(id);

    function showNotice(message, isError = false) {
      const node = el('notice');
      node.textContent = message;
      node.classList.toggle('error', isError);
    }

    async function api(path, options = {}) {
      const headers = {
        Authorization: `Bearer ${state.token}`,
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.headers || {}),
      };
      const response = await fetch(path, { ...options, headers });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || `${response.status} ${response.statusText}`);
      }
      return data;
    }

    function setAuthed(isAuthed) {
      el('tokenScreen').classList.toggle('hidden', isAuthed);
      el('appScreen').classList.toggle('hidden', !isAuthed);
      el('refreshButton').classList.toggle('hidden', !isAuthed);
      el('logoutButton').classList.toggle('hidden', !isAuthed);
    }

    function formatDate(value) {
      if (!value) return '-';
      const parsed = Date.parse(value);
      if (Number.isNaN(parsed)) return value;
      return new Date(parsed).toLocaleString();
    }

    function renderStats() {
      const labels = [
        ['tenants', 'Tenants'],
        ['users', 'Users'],
        ['devices', 'Devices'],
        ['sources', 'Sources'],
      ];
      el('statsGrid').innerHTML = labels.map(([key, label]) => `
        <article class="stat">
          <span class="muted">${label}</span>
          <strong>${state.stats[key] ?? 0}</strong>
        </article>
      `).join('');
    }

    function formatBytes(value) {
      const size = Number(value || 0);
      if (!Number.isFinite(size) || size <= 0) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let scaled = size;
      let unit = 0;
      while (scaled >= 1024 && unit < units.length - 1) {
        scaled /= 1024;
        unit += 1;
      }
      const precision = scaled >= 10 || unit === 0 ? 0 : 1;
      return `${scaled.toFixed(precision)} ${units[unit]}`;
    }

    function filteredSources() {
      const needle = state.sourceFilter.trim().toLowerCase();
      if (!needle) return state.sources;
      return state.sources.filter((source) =>
        [
          source.tenant,
          source.display_name,
          source.path_hint,
          source.id,
          source.origin_store_key,
          source.pushed_by_username,
          source.pushed_by_device_name,
        ].join(' ').toLowerCase().includes(needle)
      );
    }

    function sourceKey(source) {
      return `${source.tenant_id}/${source.id}`;
    }

    function selectedSource() {
      return state.sources.find((source) => sourceKey(source) === state.selectedSourceKey);
    }

    function renderSources() {
      const sources = filteredSources();
      if (sources.length === 0) {
        el('sourcesTable').innerHTML = '<div class="empty">No pushed images found.</div>';
        return;
      }
      el('sourcesTable').innerHTML = `
        <table>
          <thead>
            <tr>
              <th style="width: 22%;">Image</th>
              <th style="width: 16%;">Tenant</th>
              <th style="width: 22%;">Path</th>
              <th style="width: 13%;">Snapshots</th>
              <th style="width: 15%;">Pushed</th>
              <th style="width: 12%;">Actions</th>
            </tr>
          </thead>
          <tbody>
            ${sources.map((source) => `
              <tr>
                <td>
                  <strong>${escapeHtml(source.display_name)}</strong>
                  <div class="muted mono">${escapeHtml(source.id)}</div>
                </td>
                <td>${escapeHtml(source.tenant)}</td>
                <td>
                  <div>${escapeHtml(source.path_hint || '-')}</div>
                  <div class="muted mono">${escapeHtml(source.origin_store_key || '')}</div>
                </td>
                <td>
                  <span class="badge">${source.snapshot_count} snapshot(s)</span>
                  <div class="muted">${formatBytes(source.bundle_bytes)}</div>
                </td>
                <td>
                  ${formatDate(source.updated_at)}
                  <div class="muted">
                    ${escapeHtml(source.pushed_by_username || source.pushed_by_device_name || '-')}
                  </div>
                </td>
                <td>
                  <div class="actions">
	                    <button
	                      data-action="details-source"
	                      data-id="${source.id}"
	                      data-tenant-id="${source.tenant_id}"
	                      type="button"
	                    >Details</button>
	                    <button
	                      data-action="rename-source"
	                      data-id="${source.id}"
	                      data-tenant-id="${source.tenant_id}"
                      type="button"
                    >Rename</button>
                    <button
                      class="danger"
                      data-action="delete-source"
                      data-id="${source.id}"
                      data-tenant-id="${source.tenant_id}"
                      type="button"
                    >Delete</button>
                  </div>
                </td>
	              </tr>
	              ${sourceKey(source) === state.selectedSourceKey ? `
	                <tr>
	                  <td colspan="6">${renderSourceSnapshots()}</td>
	                </tr>
	              ` : ''}
	            `).join('')}
	          </tbody>
	        </table>
	      `;
	    }

    function renderSourceSnapshots() {
      const source = selectedSource();
      if (!source) return '<div class="empty">No image selected.</div>';
      const totalPages = Math.max(1, Math.ceil(state.snapshotTotal / state.snapshotPerPage));
      const memory = state.snapshotMemory;
      const memoryText = memory
        ? `Memory checked: ${formatBytes(memory.required_bytes)} required / ${formatBytes(memory.limit_bytes)} limit`
        : 'Memory checked before reading bundle.';
      const rows = state.sourceSnapshots.length === 0
        ? '<tr><td colspan="6" class="empty">No snapshots in this image.</td></tr>'
        : state.sourceSnapshots.map((snapshot) => `
          <tr>
            <td>
              <strong>${escapeHtml(snapshot.name)}</strong>
              <div class="muted mono">${escapeHtml(snapshot.kind || '-')}</div>
            </td>
            <td>${formatDate(snapshot.created)}</td>
            <td>
              <span class="badge">${Number(snapshot.file_count || 0)} file(s)</span>
              <div class="muted">${formatBytes(snapshot.total_bytes_in || snapshot.size_bytes)}</div>
            </td>
            <td>${escapeHtml(snapshot.compression || '-')}</td>
            <td>${escapeHtml(snapshot.note || '-')}</td>
            <td>
              <div class="actions">
                <button
                  data-action="rename-snapshot"
                  data-name="${escapeHtml(snapshot.name)}"
                  data-id="${source.id}"
                  data-tenant-id="${source.tenant_id}"
                  type="button"
                >Rename</button>
                <button
                  class="danger"
                  data-action="delete-snapshot"
                  data-name="${escapeHtml(snapshot.name)}"
                  data-id="${source.id}"
                  data-tenant-id="${source.tenant_id}"
                  type="button"
                >Delete</button>
              </div>
            </td>
          </tr>
        `).join('');
      return `
        <div class="panel-body" style="padding: .75rem 0 0;">
          <div class="toolbar" style="justify-content: space-between; padding: 0 .8rem .7rem;">
            <div>
              <strong>Snapshots in ${escapeHtml(source.display_name)}</strong>
              <div class="muted">${state.snapshotTotal} total · page ${state.snapshotPage}/${totalPages} · ${memoryText}</div>
            </div>
            <div class="actions">
              <button data-action="snapshots-prev" data-id="${source.id}" data-tenant-id="${source.tenant_id}" ${state.snapshotPage <= 1 ? 'disabled' : ''} type="button">Prev</button>
              <button data-action="snapshots-next" data-id="${source.id}" data-tenant-id="${source.tenant_id}" ${state.snapshotPage >= totalPages ? 'disabled' : ''} type="button">Next</button>
              <button data-action="hide-snapshots" data-id="${source.id}" data-tenant-id="${source.tenant_id}" type="button">Hide</button>
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th style="width: 22%;">Snapshot</th>
                <th style="width: 18%;">Created</th>
                <th style="width: 16%;">Content</th>
                <th style="width: 12%;">Compression</th>
                <th style="width: 18%;">Note</th>
                <th style="width: 14%;">Actions</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
    }

    function filteredUsers() {
      const needle = state.filter.trim().toLowerCase();
      if (!needle) return state.users;
      return state.users.filter((user) =>
        `${user.tenant} ${user.username} ${user.id}`.toLowerCase().includes(needle)
      );
    }

    function renderUsers() {
      const users = filteredUsers();
      if (users.length === 0) {
        el('usersTable').innerHTML = '<div class="empty">No users found.</div>';
        return;
      }
      el('usersTable').innerHTML = `
        <table>
          <thead>
            <tr>
              <th style="width: 22%;">User</th>
              <th style="width: 18%;">Tenant</th>
              <th style="width: 13%;">Status</th>
              <th style="width: 17%;">Devices</th>
              <th style="width: 30%;">Actions</th>
            </tr>
          </thead>
          <tbody>
            ${users.map((user) => `
              <tr class="${user.id === state.selectedUserId ? 'selected' : ''}">
                <td>
                  <strong>${escapeHtml(user.username)}</strong>
                  <div class="muted">${escapeHtml(user.id)}</div>
                </td>
                <td>${escapeHtml(user.tenant)}</td>
                <td>
                  <span class="badge ${user.disabled ? 'bad' : 'ok'}">
                    ${user.disabled ? 'Disabled' : 'Enabled'}
                  </span>
                </td>
                <td>
                  <span class="badge">${user.active_device_count}/${user.device_count} active</span>
                  <div class="muted">${formatDate(user.last_seen_at)}</div>
                </td>
                <td>
                  <div class="actions">
                    <button data-action="select" data-id="${user.id}" type="button">Devices</button>
                    <button data-action="rename" data-id="${user.id}" type="button">Rename</button>
                    <button data-action="toggle" data-id="${user.id}" type="button">
                      ${user.disabled ? 'Enable' : 'Disable'}
                    </button>
                    <button data-action="password" data-id="${user.id}" type="button">Password</button>
                    <button class="danger" data-action="delete" data-id="${user.id}" type="button">Delete</button>
                  </div>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }

    function selectedUser() {
      return state.users.find((user) => user.id === state.selectedUserId);
    }

    function renderDevices() {
      const user = selectedUser();
      el('deviceSubhead').textContent = user
        ? `${user.tenant}/${user.username}`
        : 'Select a user to view devices.';
      el('revokeAllButton').classList.toggle('hidden', !user);
      if (!user) {
        el('devicesTable').innerHTML = '<div class="empty">No user selected.</div>';
        return;
      }
      if (state.devices.length === 0) {
        el('devicesTable').innerHTML = '<div class="empty">This user has no devices.</div>';
        return;
      }
      el('devicesTable').innerHTML = `
        <table>
          <thead>
            <tr>
              <th style="width: 34%;">Device</th>
              <th style="width: 18%;">Status</th>
              <th style="width: 28%;">Last seen</th>
              <th style="width: 20%;">Actions</th>
            </tr>
          </thead>
          <tbody>
            ${state.devices.map((device) => `
              <tr>
                <td>
                  <strong>${escapeHtml(device.name)}</strong>
                  <div class="muted">${escapeHtml(device.id)}</div>
                </td>
                <td>
                  <span class="badge ${device.revoked ? 'warn' : 'ok'}">
                    ${device.revoked ? 'Revoked' : 'Active'}
                  </span>
                </td>
                <td>
                  ${formatDate(device.last_seen_at)}
                  <div class="muted">Created ${formatDate(device.created_at)}</div>
                </td>
                <td>
                  <button
                    class="danger"
                    data-action="revoke-device"
                    data-id="${device.id}"
                    ${device.revoked ? 'disabled' : ''}
                    type="button"
                  >Revoke</button>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[ch]));
    }

    async function refreshAll() {
      const [overview, users, sources] = await Promise.all([
        api('/api/admin/overview'),
        api('/api/admin/users'),
        api('/api/admin/sources'),
      ]);
      state.stats = overview.stats || {};
      state.users = users.users || [];
      state.sources = sources.sources || [];
      if (!state.sources.some((source) => sourceKey(source) === state.selectedSourceKey)) {
        state.selectedSourceKey = '';
        state.sourceSnapshots = [];
        state.snapshotTotal = 0;
        state.snapshotPage = 1;
      } else if (state.selectedSourceKey) {
        await loadSourceSnapshots(selectedSource(), state.snapshotPage, false);
      }
      if (!state.users.some((user) => user.id === state.selectedUserId)) {
        state.selectedUserId = state.users[0]?.id || '';
      }
      if (state.selectedUserId) {
        await loadDevices(state.selectedUserId, false);
      } else {
        state.devices = [];
      }
      renderStats();
      renderSources();
      renderUsers();
      renderDevices();
      showNotice('Loaded current server state.');
    }

    async function loadDevices(userId, render = true) {
      state.selectedUserId = userId;
      const data = await api(`/api/admin/users/${encodeURIComponent(userId)}/devices`);
      state.devices = data.devices || [];
      if (render) {
        renderUsers();
        renderDevices();
      }
    }

    async function updateUser(userId, patch) {
      await api(`/api/admin/users/${encodeURIComponent(userId)}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      });
      await refreshAll();
    }

    async function updateSource(source, patch) {
      await api(
        `/api/admin/sources/${encodeURIComponent(source.tenant_id)}/${encodeURIComponent(source.id)}`,
        {
          method: 'PATCH',
          body: JSON.stringify(patch),
        },
      );
      await refreshAll();
    }

    async function deleteSource(source) {
      await api(
        `/api/admin/sources/${encodeURIComponent(source.tenant_id)}/${encodeURIComponent(source.id)}`,
        { method: 'DELETE' },
      );
      await refreshAll();
    }

    async function loadSourceSnapshots(source, page = 1, render = true) {
      state.selectedSourceKey = sourceKey(source);
      state.snapshotPage = page;
      const data = await api(
        `/api/admin/sources/${encodeURIComponent(source.tenant_id)}/${encodeURIComponent(source.id)}/snapshots?page=${page}&per_page=${state.snapshotPerPage}`
      );
      state.sourceSnapshots = data.snapshots || [];
      state.snapshotTotal = Number(data.total || 0);
      state.snapshotPage = Number(data.page || page);
      state.snapshotPerPage = Number(data.per_page || state.snapshotPerPage);
      state.snapshotMemory = data.memory || null;
      if (render) {
        renderSources();
      }
    }

    async function renameSnapshot(source, oldName, newName) {
      await api(
        `/api/admin/sources/${encodeURIComponent(source.tenant_id)}/${encodeURIComponent(source.id)}/snapshots/${encodeURIComponent(oldName)}`,
        {
          method: 'PATCH',
          body: JSON.stringify({ name: newName }),
        },
      );
      await refreshAll();
    }

    async function deleteSnapshot(source, name) {
      await api(
        `/api/admin/sources/${encodeURIComponent(source.tenant_id)}/${encodeURIComponent(source.id)}/snapshots/${encodeURIComponent(name)}`,
        { method: 'DELETE' },
      );
      await refreshAll();
    }

    el('tokenForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      state.token = el('tokenInput').value.trim();
      sessionStorage.setItem('snapzAdminToken', state.token);
      try {
        setAuthed(true);
        await refreshAll();
      } catch (error) {
        sessionStorage.removeItem('snapzAdminToken');
        setAuthed(false);
        showNotice(error.message, true);
        alert(error.message);
      }
    });

    el('refreshButton').addEventListener('click', () => {
      refreshAll().catch((error) => showNotice(error.message, true));
    });

    el('logoutButton').addEventListener('click', () => {
      sessionStorage.removeItem('snapzAdminToken');
      state.token = '';
      setAuthed(false);
    });

    el('createUserForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await api('/api/admin/users', {
          method: 'POST',
          body: JSON.stringify({
            tenant: form.get('tenant'),
            username: form.get('username'),
            password: form.get('password'),
            disabled: form.get('disabled') === 'on',
          }),
        });
        event.currentTarget.reset();
        await refreshAll();
        showNotice('User created.');
      } catch (error) {
        showNotice(error.message, true);
      }
    });

    el('userFilter').addEventListener('input', (event) => {
      state.filter = event.target.value;
      renderUsers();
    });

    el('sourceFilter').addEventListener('input', (event) => {
      state.sourceFilter = event.target.value;
      renderSources();
    });

    el('sourcesTable').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const source = state.sources.find((item) =>
        item.id === button.dataset.id && item.tenant_id === button.dataset.tenantId
      );
      if (!source) return;
      try {
        if (button.dataset.action === 'details-source') {
          await loadSourceSnapshots(source, 1);
          showNotice('Loaded image snapshots.');
        } else if (button.dataset.action === 'snapshots-prev') {
          await loadSourceSnapshots(source, Math.max(1, state.snapshotPage - 1));
        } else if (button.dataset.action === 'snapshots-next') {
          await loadSourceSnapshots(source, state.snapshotPage + 1);
        } else if (button.dataset.action === 'hide-snapshots') {
          state.selectedSourceKey = '';
          state.sourceSnapshots = [];
          state.snapshotTotal = 0;
          renderSources();
        } else if (button.dataset.action === 'rename-snapshot') {
          const oldName = button.dataset.name;
          const newName = prompt('New snapshot name', oldName);
          if (newName && newName.trim() !== oldName) {
            await renameSnapshot(source, oldName, newName.trim());
            showNotice('Snapshot renamed.');
          }
        } else if (button.dataset.action === 'delete-snapshot') {
          const name = button.dataset.name;
          if (confirm(`Delete snapshot ${source.tenant}/${source.display_name}/${name}? This rewrites the uploaded bundle.`)) {
            await deleteSnapshot(source, name);
            showNotice('Snapshot deleted.');
          }
        } else if (button.dataset.action === 'rename-source') {
          const displayName = prompt('New image name', source.display_name);
          if (displayName && displayName.trim() !== source.display_name) {
            await updateSource(source, { display_name: displayName.trim() });
            showNotice('Image renamed.');
          }
        } else if (button.dataset.action === 'delete-source') {
          if (confirm(`Delete image ${source.tenant}/${source.display_name}? This removes the uploaded bundle from the server.`)) {
            await deleteSource(source);
            showNotice('Image deleted.');
          }
        }
      } catch (error) {
        showNotice(error.message, true);
      }
    });

    el('usersTable').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const action = button.dataset.action;
      const user = state.users.find((item) => item.id === button.dataset.id);
      if (!user) return;
      try {
        if (action === 'select') {
          await loadDevices(user.id);
        } else if (action === 'rename') {
          const username = prompt('New username', user.username);
          if (username && username.trim() !== user.username) {
            await updateUser(user.id, { username: username.trim() });
            showNotice('Username updated.');
          }
        } else if (action === 'toggle') {
          await updateUser(user.id, { disabled: !user.disabled });
          showNotice(user.disabled ? 'User enabled.' : 'User disabled.');
        } else if (action === 'password') {
          const password = prompt(`New password for ${user.tenant}/${user.username}`);
          if (password) {
            await api(`/api/admin/users/${encodeURIComponent(user.id)}/password`, {
              method: 'POST',
              body: JSON.stringify({ password }),
            });
            showNotice('Password reset.');
          }
        } else if (action === 'delete') {
          if (confirm(`Delete ${user.tenant}/${user.username}? This also removes registered devices.`)) {
            await api(`/api/admin/users/${encodeURIComponent(user.id)}`, { method: 'DELETE' });
            state.selectedUserId = '';
            await refreshAll();
            showNotice('User deleted.');
          }
        }
      } catch (error) {
        showNotice(error.message, true);
      }
    });

    el('devicesTable').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-action="revoke-device"]');
      if (!button || !confirm('Revoke this device token?')) return;
      try {
        await api(`/api/admin/devices/${encodeURIComponent(button.dataset.id)}/revoke`, {
          method: 'POST',
        });
        await refreshAll();
        showNotice('Device revoked.');
      } catch (error) {
        showNotice(error.message, true);
      }
    });

    el('revokeAllButton').addEventListener('click', async () => {
      const user = selectedUser();
      if (!user || !confirm(`Revoke all active devices for ${user.tenant}/${user.username}?`)) return;
      try {
        const data = await api(`/api/admin/users/${encodeURIComponent(user.id)}/devices/revoke`, {
          method: 'POST',
        });
        await refreshAll();
        showNotice(`Revoked ${data.revoked || 0} device(s).`);
      } catch (error) {
        showNotice(error.message, true);
      }
    });

    if (state.token) {
      setAuthed(true);
      refreshAll().catch((error) => {
        showNotice(error.message, true);
        setAuthed(false);
      });
    } else {
      setAuthed(false);
    }
  </script>
</body>
</html>
"""
