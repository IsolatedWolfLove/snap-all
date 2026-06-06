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
      --bg: #f3f6fb;
      --panel: #fff;
      --ink: #111827;
      --muted: #64748b;
      --subtle: #94a3b8;
      --line: #e5e7eb;
      --line-strong: #cbd5e1;
      --primary: #1677ff;
      --primary-strong: #0958d9;
      --danger: #a8071a;
      --danger-line: #ff4d4f;
      --success: #237804;
      --success-line: #52c41a;
      --warn: #ad6800;
      --warn-line: #faad14;
      --info-bg: #e6f4ff;
      --info-line: #bae6fd;
      --shadow: 0 10px 30px rgba(15, 23, 42, .05);
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
      padding: 1rem 2rem;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 1.25rem; font-weight: 700; line-height: 1.3; }
    h2 { font-size: 1rem; font-weight: 700; line-height: 1.4; }
    h3 { font-size: .95rem; font-weight: 700; line-height: 1.4; }
    main {
      width: min(1320px, 100%);
      margin: 0 auto;
      padding: 1.5rem 2rem 2rem;
    }
    .muted { color: var(--muted); }
    .subtle { color: var(--subtle); }
    .toolbar {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: .75rem;
    }
    .page-title {
      display: grid;
      gap: .25rem;
      margin-bottom: 1rem;
      padding-bottom: .25rem;
    }
    .page-title h2 {
      font-size: 1.5rem;
      line-height: 1.3;
    }
    .eyebrow {
      color: var(--primary);
      font-size: .75rem;
      font-weight: 700;
      text-transform: uppercase;
    }
    .hero {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1.25rem;
      min-height: 5.5rem;
      padding: 1.25rem 1.5rem;
      border-color: var(--info-line);
      background: #eef8ff;
    }
    .hero h2 {
      margin-top: .25rem;
      font-size: 1.5rem;
    }
    .hero-actions {
      justify-content: flex-end;
    }
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 1rem;
      margin-bottom: 1rem;
    }
    .stat, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .stat {
      min-height: 6.75rem;
      padding: 1.1rem;
      border-top-width: 3px;
    }
    .stat.sky { border-top-color: #38bdf8; }
    .stat.violet { border-top-color: #8b5cf6; }
    .stat.emerald { border-top-color: #10b981; }
    .stat.amber { border-top-color: #f59e0b; }
    .stat-label {
      color: var(--muted);
      font-size: .82rem;
    }
    .stat strong,
    .stat-value {
      display: block;
      margin-top: .35rem;
      font-size: 1.75rem;
      line-height: 1.15;
    }
    .stat-hint {
      margin-top: .35rem;
      color: var(--subtle);
      font-size: .75rem;
    }
    .panel { margin-bottom: 1rem; overflow: hidden; }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      padding: 1.1rem;
    }
    .panel-title {
      display: grid;
      gap: .15rem;
      min-width: 0;
    }
    .panel-title p {
      color: var(--muted);
      font-size: .75rem;
      font-weight: 400;
      line-height: 1.5;
    }
    .panel-body { padding: 0 1.1rem 1.1rem; }
    .split {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(360px, .44fr);
      gap: 1rem;
      align-items: start;
    }
    .side-stack {
      display: grid;
      gap: 1rem;
    }
    form {
      display: grid;
      grid-template-columns: 1fr;
      align-items: end;
      gap: .75rem;
    }
    .form-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: .75rem;
    }
    .form-footer {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 1rem;
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
    input::placeholder { color: var(--subtle); }
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
    button:disabled {
      cursor: not-allowed;
      opacity: .5;
    }
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
    .actions button {
      height: 2rem;
      padding: 0 .65rem;
      font-size: .78rem;
    }
    .table-wrap { overflow-x: auto; }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      background: var(--panel);
      border: 1px solid #f0f0f0;
      border-radius: 6px;
      overflow: hidden;
    }
    th, td {
      padding: .6rem .75rem;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: middle;
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      font-size: .78rem;
      font-weight: 700;
      background: #fafafa;
      text-transform: none;
    }
    tr:last-child td { border-bottom: 0; }
    tr.selected,
    tr.selected td {
      background: var(--info-bg);
    }
    .cell-title { font-weight: 600; }
    .cell-subtitle {
      margin-top: .12rem;
      color: var(--subtle);
      font-size: .72rem;
    }
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
      min-height: 1.35rem;
      border: 1px solid var(--line-strong);
      border-radius: 4px;
      padding: 0 .55rem;
      font-size: .75rem;
      font-weight: 600;
      background: #f8fafc;
      color: var(--muted);
    }
    .badge.ok {
      border-color: var(--success-line);
      background: #f6ffed;
      color: var(--success);
    }
    .badge.warn {
      border-color: var(--warn-line);
      background: #fffbe6;
      color: var(--warn);
    }
    .badge.bad {
      border-color: var(--danger-line);
      background: #fff1f0;
      color: var(--danger);
    }
    .notice {
      min-height: 2.4rem;
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
    .source-snapshot-row > td {
      padding: 0;
      background: #f8fafc;
    }
    .snapshot-panel {
      padding: 1rem;
      border: 1px solid var(--line);
      background: #f8fafc;
    }
    .snapshot-head {
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: flex-start;
      margin-bottom: .75rem;
    }
    .token-screen {
      display: flex;
      justify-content: center;
      min-height: 32rem;
      padding-top: 6vh;
    }
    .login-card {
      width: min(100%, 32.5rem);
      height: max-content;
      padding: 1.75rem;
    }
    .login-card h2 {
      margin-top: .35rem;
      font-size: 1.5rem;
    }
    .login-card p {
      margin-top: .6rem;
      color: var(--muted);
      line-height: 1.6;
    }
    .token-form {
      margin-top: 1rem;
    }
    .token-form button {
      width: 100%;
      height: 2.5rem;
    }
    .hidden { display: none !important; }
    @media (max-width: 980px) {
      header { padding: 1rem; }
      main { padding: 1rem; }
      .stats-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .split { grid-template-columns: 1fr; }
      .hero { align-items: flex-start; flex-direction: column; }
      .hero-actions { justify-content: flex-start; }
    }
    @media (max-width: 640px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding: .75rem; }
      .stats-grid, .form-row { grid-template-columns: 1fr; }
      .form-footer, .snapshot-head { align-items: stretch; flex-direction: column; }
      th, td { padding: .6rem; }
      .panel-head { align-items: flex-start; flex-direction: column; }
      .panel-head label { width: 100%; }
      .login-card { padding: 1.25rem; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>snapz-server Admin</h1>
      <p class="muted">Manage tenants, users, and registered sync devices.</p>
    </div>
  </header>

  <main>
    <section id="tokenScreen" class="token-screen">
      <div class="panel login-card">
        <div class="eyebrow">Admin console</div>
        <h2>Connect to snapz-server</h2>
        <p>Start snapz-server with --admin-token or SNAPZ_SERVER_ADMIN_TOKEN, then enter it here.</p>
        <form id="tokenForm" class="token-form">
          <label>
            Token
            <input id="tokenInput" autocomplete="current-password" placeholder="Admin token" type="password" required>
          </label>
          <button class="primary" type="submit">Connect</button>
        </form>
      </div>
    </section>

    <section id="appScreen" class="hidden">
      <div class="page-title">
        <h2>snapz-server</h2>
        <p class="muted">Manage snapz-server tenants, users, and sync devices.</p>
      </div>

      <section class="panel hero">
        <div>
          <div class="eyebrow">Connected</div>
          <h2>snapz-server admin</h2>
        </div>
        <div class="toolbar hero-actions">
          <span class="badge ok">Admin API active</span>
          <button id="refreshButton" class="primary hidden" type="button">Refresh</button>
          <button id="logoutButton" class="hidden" type="button">Forget token</button>
        </div>
      </section>

      <div id="notice" class="notice">Ready.</div>

      <section class="stats-grid" id="statsGrid"></section>

      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
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

      <section class="split">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <h2>Users</h2>
              <p>Select a user to inspect registered devices.</p>
            </div>
            <label style="min-width: 220px;">
              Filter
              <input id="userFilter" placeholder="tenant or username">
            </label>
          </div>
          <div id="usersTable"></div>
        </section>

        <div class="side-stack">
          <section class="panel">
            <div class="panel-head">
              <div class="panel-title">
                <h2>Create user</h2>
                <p>Add an account to a tenant.</p>
              </div>
            </div>
            <div class="panel-body">
              <form id="createUserForm">
                <div class="form-row">
                  <label>
                    Tenant
                    <input name="tenant" placeholder="acme" required>
                  </label>
                  <label>
                    Username
                    <input name="username" placeholder="alice" required>
                  </label>
                </div>
                <label>
                  Password
                  <input name="password" placeholder="Password" type="password" required>
                </label>
                <div class="form-footer">
                  <label>
                    Disabled
                    <span class="toolbar"><input name="disabled" type="checkbox"> Initially disabled</span>
                  </label>
                  <button class="primary" type="submit">Add user</button>
                </div>
              </form>
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <div class="panel-title">
                <h2>Devices</h2>
                <p id="deviceSubhead" class="muted">Select a user to view devices.</p>
              </div>
              <button id="revokeAllButton" class="danger hidden" type="button">Revoke active</button>
            </div>
            <div id="devicesTable"></div>
          </section>
        </div>
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
        ['tenants', 'Tenants', 'Workspace groups', 'sky'],
        ['users', 'Users', 'Login accounts', 'violet'],
        ['devices', 'Devices', 'Registered clients', 'emerald'],
        ['sources', 'Sources', `${formatBytes(state.stats.bundle_bytes)} stored`, 'amber'],
      ];
      el('statsGrid').innerHTML = labels.map(([key, label, hint, tone]) => `
        <article class="stat ${tone}">
          <span class="stat-label">${label}</span>
          <strong class="stat-value">${state.stats[key] ?? 0}</strong>
          <div class="stat-hint">${hint}</div>
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
        <div class="panel-body">
          <div class="table-wrap">
            <table style="min-width: 1080px;">
              <thead>
                <tr>
                  <th style="width: 20%;">Image</th>
                  <th style="width: 12%;">Tenant</th>
                  <th style="width: 24%;">Path</th>
                  <th style="width: 14%;">Snapshots</th>
                  <th style="width: 17%;">Pushed</th>
                  <th style="width: 13%;">Actions</th>
                </tr>
              </thead>
              <tbody>
                ${sources.map((source) => `
                  <tr class="${sourceKey(source) === state.selectedSourceKey ? 'selected' : ''}">
                    <td>
                      <div class="cell-title">${escapeHtml(source.display_name)}</div>
                      <div class="cell-subtitle mono">${escapeHtml(source.id)}</div>
                    </td>
                    <td>${escapeHtml(source.tenant)}</td>
                    <td>
                      <div class="cell-title">${escapeHtml(source.path_hint || '-')}</div>
                      <div class="cell-subtitle mono">${escapeHtml(source.origin_store_key || '')}</div>
                    </td>
                    <td>
                      <span class="badge">${source.snapshot_count} snapshot(s)</span>
                      <div class="cell-subtitle">${formatBytes(source.bundle_bytes)}</div>
                    </td>
                    <td>
                      <div class="cell-title">${formatDate(source.updated_at)}</div>
                      <div class="cell-subtitle">
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
                    <tr class="source-snapshot-row">
                      <td colspan="6">${renderSourceSnapshots()}</td>
                    </tr>
                  ` : ''}
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
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
        <div class="snapshot-panel">
          <div class="snapshot-head">
            <div class="panel-title">
              <h3>Snapshots in ${escapeHtml(source.display_name)}</h3>
              <p>${state.snapshotTotal} total - page ${state.snapshotPage}/${totalPages} - ${memoryText}</p>
            </div>
            <div class="actions">
              <button data-action="snapshots-prev" data-id="${source.id}" data-tenant-id="${source.tenant_id}" ${state.snapshotPage <= 1 ? 'disabled' : ''} type="button">Prev</button>
              <button data-action="snapshots-next" data-id="${source.id}" data-tenant-id="${source.tenant_id}" ${state.snapshotPage >= totalPages ? 'disabled' : ''} type="button">Next</button>
              <button data-action="hide-snapshots" data-id="${source.id}" data-tenant-id="${source.tenant_id}" type="button">Hide</button>
            </div>
          </div>
          <div class="table-wrap">
            <table style="min-width: 940px;">
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
        <div class="panel-body">
          <div class="table-wrap">
            <table style="min-width: 1000px;">
              <thead>
                <tr>
                  <th style="width: 22%;">User</th>
                  <th style="width: 15%;">Tenant</th>
                  <th style="width: 12%;">Status</th>
                  <th style="width: 18%;">Devices</th>
                  <th style="width: 33%;">Actions</th>
                </tr>
              </thead>
              <tbody>
                ${users.map((user) => `
                  <tr class="${user.id === state.selectedUserId ? 'selected' : ''}">
                    <td>
                      <div class="cell-title">${escapeHtml(user.username)}</div>
                      <div class="cell-subtitle mono">${escapeHtml(user.id)}</div>
                    </td>
                    <td>${escapeHtml(user.tenant)}</td>
                    <td>
                      <span class="badge ${user.disabled ? 'bad' : 'ok'}">
                        ${user.disabled ? 'Disabled' : 'Enabled'}
                      </span>
                    </td>
                    <td>
                      <span class="badge">${user.active_device_count}/${user.device_count} active</span>
                      <div class="cell-subtitle">${formatDate(user.last_seen_at)}</div>
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
          </div>
        </div>
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
        el('devicesTable').innerHTML = '<div class="panel-body"><div class="empty">No user selected.</div></div>';
        return;
      }
      if (state.devices.length === 0) {
        el('devicesTable').innerHTML = '<div class="panel-body"><div class="empty">This user has no devices.</div></div>';
        return;
      }
      el('devicesTable').innerHTML = `
        <div class="panel-body">
          <div class="table-wrap">
            <table style="min-width: 720px;">
              <thead>
                <tr>
                  <th style="width: 32%;">Device</th>
                  <th style="width: 18%;">Status</th>
                  <th style="width: 32%;">Last seen</th>
                  <th style="width: 18%;">Actions</th>
                </tr>
              </thead>
              <tbody>
                ${state.devices.map((device) => `
                  <tr>
                    <td>
                      <div class="cell-title">${escapeHtml(device.name)}</div>
                      <div class="cell-subtitle mono">${escapeHtml(device.id)}</div>
                    </td>
                    <td>
                      <span class="badge ${device.revoked ? 'warn' : 'ok'}">
                        ${device.revoked ? 'Revoked' : 'Active'}
                      </span>
                    </td>
                    <td>
                      <div class="cell-title">${formatDate(device.last_seen_at)}</div>
                      <div class="cell-subtitle">Created ${formatDate(device.created_at)}</div>
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
          </div>
        </div>
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
