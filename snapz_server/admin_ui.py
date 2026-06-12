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
      color-scheme: dark;
      --bg: #101114;
      --panel: #17191f;
      --panel-subtle: #1d2027;
      --ink: #f4f5f7;
      --muted: #a0a7b4;
      --subtle: #737b89;
      --line: #2a2f3a;
      --line-strong: #3b4250;
      --primary: #3b82f6;
      --primary-strong: #2563eb;
      --violet: #8b5cf6;
      --emerald: #10b981;
      --amber: #f59e0b;
      --danger: #f87171;
      --danger-line: rgba(248, 113, 113, .35);
      --danger-bg: rgba(248, 113, 113, .12);
      --success: #34d399;
      --success-line: rgba(52, 211, 153, .35);
      --success-bg: rgba(52, 211, 153, .12);
      --warn: #fbbf24;
      --warn-line: rgba(251, 191, 36, .35);
      --warn-bg: rgba(251, 191, 36, .12);
      --info-bg: rgba(59, 130, 246, .12);
      --info-line: rgba(59, 130, 246, .35);
      --shadow: 0 12px 30px rgba(0, 0, 0, .18);
    }
    * { box-sizing: border-box; }
    ::-webkit-scrollbar {
      width: 8px;
      height: 8px;
    }
    ::-webkit-scrollbar-track {
      background: var(--bg);
    }
    ::-webkit-scrollbar-thumb {
      background: var(--line-strong);
      border-radius: 99px;
    }
    ::-webkit-scrollbar-thumb:hover {
      background: var(--subtle);
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
      min-height: 100vh;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      padding: 1rem 2rem;
      border-bottom: 1px solid var(--line);
      background: #111318;
      position: sticky;
      top: 0;
      z-index: 10;
    }
    h1, h2, h3, p { margin: 0; }
    h1 {
      font-size: 1.35rem;
      font-weight: 700;
      letter-spacing: 0;
    }
    h2 { font-size: 1.1rem; font-weight: 700; line-height: 1.4; }
    h3 { font-size: .95rem; font-weight: 700; line-height: 1.4; }
    main {
      width: min(1320px, 100%);
      margin: 0 auto;
      padding: 2rem 2rem 3rem;
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
      margin-bottom: 1.5rem;
      padding-bottom: .25rem;
    }
    .page-title h2 {
      font-size: 1.75rem;
      font-weight: 700;
      line-height: 1.3;
    }
    .eyebrow {
      color: var(--primary-strong);
      font-size: .75rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .hero {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1.25rem;
      min-height: 5.5rem;
      padding: 1.5rem;
      border-color: var(--info-line);
      background: #151b28;
      box-shadow: var(--shadow);
    }
    .hero h2 {
      margin-top: .25rem;
      font-size: 1.75rem;
      font-weight: 700;
    }
    .hero-actions {
      justify-content: flex-end;
    }
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    .stat, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .stat {
      min-height: 7rem;
      padding: 1.25rem;
      border-top-width: 4px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .stat.sky { border-top-color: var(--primary); }
    .stat.violet { border-top-color: var(--violet); }
    .stat.emerald { border-top-color: var(--emerald); }
    .stat.amber { border-top-color: var(--amber); }
    .stat-label {
      color: var(--muted);
      font-size: .82rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .stat strong,
    .stat-value {
      display: block;
      margin-top: .35rem;
      font-size: 1.85rem;
      font-weight: 700;
      line-height: 1.15;
      letter-spacing: 0;
      color: #fff;
    }
    .stat-hint {
      margin-top: .35rem;
      color: var(--subtle);
      font-size: .75rem;
      font-weight: 500;
    }
    .panel { margin-bottom: 1.5rem; overflow: hidden; }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      padding: 1.25rem;
      border-bottom: 1px solid var(--line);
    }
    .panel-title {
      display: grid;
      gap: .2rem;
      min-width: 0;
    }
    .panel-title p {
      color: var(--muted);
      font-size: .78rem;
      font-weight: 400;
      line-height: 1.5;
    }
    .panel-body { padding: 1.25rem; }
    .split {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(360px, .44fr);
      gap: 1.5rem;
      align-items: start;
    }
    .side-stack {
      display: grid;
      gap: 1.5rem;
    }
    form {
      display: grid;
      grid-template-columns: 1fr;
      align-items: end;
      gap: 1rem;
    }
    .form-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 1rem;
    }
    .form-footer {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 1rem;
    }
    label {
      display: grid;
      gap: .4rem;
      color: var(--muted);
      font-size: .82rem;
      font-weight: 600;
      letter-spacing: 0;
    }
    input, select {
      width: 100%;
      height: 2.35rem;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 .75rem;
      color: var(--ink);
      background: #12151c;
      font: inherit;
      transition: background-color .15s ease, border-color .15s ease;
      outline: none;
    }
    select {
      width: auto;
      min-width: 7rem;
    }
    input:focus, select:focus {
      border-color: var(--primary);
      background: #151a23;
    }
    input::placeholder { color: var(--subtle); }
    input[type="checkbox"] { width: 1.1rem; height: 1.1rem; accent-color: var(--primary); cursor: pointer; }
    button {
      height: 2.35rem;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 .85rem;
      background: var(--panel-subtle);
      color: var(--ink);
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
      transition: background-color .15s ease, border-color .15s ease, color .15s ease;
      outline: none;
    }
    button:hover {
      border-color: var(--line-strong);
      background: #242832;
    }
    button:disabled {
      cursor: not-allowed;
      opacity: .55;
    }
    button.primary {
      border-color: var(--primary);
      background: var(--primary);
      color: #fff;
    }
    button.primary:hover {
      background: var(--primary-strong);
    }
    button.danger {
      border-color: var(--danger-line);
      color: var(--danger);
      background: var(--danger-bg);
    }
    button.danger:hover {
      background: var(--danger);
      border-color: var(--danger);
      color: #111827;
    }
    .actions button {
      height: 1.85rem;
      padding: 0 .75rem;
      font-size: .78rem;
      border-radius: 8px;
    }
    .table-wrap { overflow-x: auto; }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      background: transparent;
    }
    th, td {
      padding: .85rem 1rem;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: middle;
      overflow-wrap: anywhere;
    }
    th {
      background: var(--panel-subtle);
      color: var(--muted);
      font-size: .8rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    tr:last-child td { border-bottom: 0; }
    tr {
      transition: background-color 0.2s ease;
    }
    tr:hover td {
      background: #1b1f28;
    }
    tr.selected,
    tr.selected td {
      background: var(--info-bg);
      border-bottom-color: rgba(59, 130, 246, 0.2);
    }
    .cell-title { font-weight: 600; color: #fff; }
    .cell-subtitle {
      margin-top: .2rem;
      color: var(--muted);
      font-size: .75rem;
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
      min-height: 1.45rem;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      padding: 0 .7rem;
      background: var(--panel-subtle);
      color: var(--muted);
      font-size: .75rem;
      font-weight: 600;
    }
    .badge.ok {
      border-color: var(--success-line);
      background: var(--success-bg);
      color: var(--success);
    }
    .badge.warn {
      border-color: var(--warn-line);
      background: var(--warn-bg);
      color: var(--warn);
    }
    .badge.bad {
      border-color: var(--danger-line);
      background: var(--danger-bg);
      color: var(--danger);
    }
    .notice {
      min-height: 2.5rem;
      display: flex;
      align-items: center;
      padding: .75rem 1.1rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--muted);
      margin-bottom: 1.5rem;
      font-size: .88rem;
    }
    .notice.error {
      border-color: var(--danger-line);
      background: var(--danger-bg);
      color: var(--danger);
    }
    .empty {
      padding: 3rem 1rem;
      text-align: center;
      color: var(--muted);
      font-size: .9rem;
    }
    .source-snapshot-row > td {
      padding: 0;
      background: #15181f;
    }
    .snapshot-panel {
      padding: 1.5rem;
      border: 1px solid var(--line);
      background: var(--panel-subtle);
      border-radius: 8px;
      margin: 1rem;
    }
    .snapshot-head {
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: flex-start;
      margin-bottom: 1rem;
    }
    .mini-progress {
      display: grid;
      gap: .3rem;
      min-width: 8rem;
    }
    .mini-progress-head {
      display: flex;
      justify-content: space-between;
      gap: .5rem;
      color: var(--muted);
      font-size: .72rem;
      font-weight: 600;
    }
    .mini-bar {
      height: .45rem;
      overflow: hidden;
      border-radius: 6px;
      background: #242832;
    }
    .mini-bar span {
      display: block;
      height: 100%;
      border-radius: inherit;
      background: var(--primary);
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
      padding: 2.25rem;
      border: 1px solid var(--info-line);
      background: var(--panel);
      box-shadow: var(--shadow);
    }
    .login-card h2 {
      margin-top: .35rem;
      font-size: 1.75rem;
      font-weight: 800;
      color: #fff;
    }
    .login-card p {
      margin-top: .6rem;
      color: var(--muted);
      line-height: 1.6;
      font-size: .92rem;
    }
    .token-form {
      margin-top: 1.5rem;
    }
    .token-form button {
      width: 100%;
      height: 2.5rem;
    }
    .hidden { display: none !important; }
    @media (max-width: 980px) {
      header { padding: 1rem; }
      main { padding: 1.25rem; }
      .stats-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .split { grid-template-columns: 1fr; }
      .hero { align-items: flex-start; flex-direction: column; }
      .hero-actions { justify-content: flex-start; }
    }
    @media (max-width: 640px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding: 1rem; }
      .stats-grid, .form-row { grid-template-columns: 1fr; }
      .form-footer, .snapshot-head { align-items: stretch; flex-direction: column; }
      th, td { padding: .75rem; }
      .panel-head { align-items: flex-start; flex-direction: column; }
      .panel-head label { width: 100%; }
      .login-card { padding: 1.5rem; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>snapz-server Admin</h1>
      <p class="muted"><span data-i18n="brand.subtitle">Manage tenants, users, and registered sync devices.</span> <span id="headerVersion"></span></p>
    </div>
    <label>
      <span data-i18n="language.label">Language</span>
      <select id="languageSelect" aria-label="Language" data-i18n-attr="aria-label:language.label">
        <option value="en">English</option>
        <option value="zh">中文</option>
      </select>
    </label>
  </header>

  <main>
    <section id="tokenScreen" class="token-screen">
      <div class="panel login-card">
        <div class="eyebrow" data-i18n="login.eyebrow">Admin console</div>
        <h2 data-i18n="login.title">Connect to snapz-server</h2>
        <p data-i18n="login.copy">Start snapz-server with --admin-token or SNAPZ_SERVER_ADMIN_TOKEN, then enter it here.</p>
        <form id="tokenForm" class="token-form">
          <label>
            <span data-i18n="field.token">Token</span>
            <input id="tokenInput" autocomplete="current-password" data-i18n-attr="placeholder:placeholder.adminToken" placeholder="Admin token" type="password" required>
          </label>
          <button class="primary" data-i18n="action.connect" type="submit">Connect</button>
        </form>
      </div>
    </section>

    <section id="appScreen" class="hidden">
      <div class="page-title">
        <h2>snapz-server</h2>
        <p class="muted" data-i18n="app.description">Manage snapz-server tenants, users, and sync devices.</p>
      </div>

      <section class="panel hero">
        <div>
          <div class="eyebrow" data-i18n="hero.eyebrow">Connected</div>
          <h2 data-i18n="hero.title">snapz-server admin</h2>
        </div>
        <div class="toolbar hero-actions">
          <span id="versionBadge" class="badge" data-i18n="label.versionUnknown">Version unknown</span>
          <span class="badge ok" data-i18n="label.adminApiActive">Admin API active</span>
          <button id="refreshButton" class="primary hidden" data-i18n="action.refresh" type="button">Refresh</button>
          <button id="logoutButton" class="hidden" data-i18n="action.forgetToken" type="button">Forget token</button>
        </div>
      </section>

      <div id="notice" class="notice" data-i18n="notice.ready">Ready.</div>

      <section class="stats-grid" id="statsGrid"></section>

      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            <h2 data-i18n="section.images">Pushed images</h2>
            <p class="muted" data-i18n="section.images.copy">Manage source bundles uploaded by snapz push.</p>
          </div>
          <label style="min-width: 260px;">
            <span data-i18n="field.filter">Filter</span>
            <input id="sourceFilter" data-i18n-attr="placeholder:placeholder.sourceFilter" placeholder="tenant, image name, path, or id">
          </label>
        </div>
        <div id="sourcesTable"></div>
      </section>

      <section class="split">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <h2 data-i18n="section.users">Users</h2>
              <p data-i18n="section.users.copy">Select a user to inspect registered devices.</p>
            </div>
            <label style="min-width: 220px;">
              <span data-i18n="field.filter">Filter</span>
              <input id="userFilter" data-i18n-attr="placeholder:placeholder.userFilter" placeholder="tenant or username">
            </label>
          </div>
          <div id="usersTable"></div>
        </section>

        <div class="side-stack">
          <section class="panel">
            <div class="panel-head">
              <div class="panel-title">
                <h2 data-i18n="section.createUser">Create user</h2>
                <p data-i18n="section.createUser.copy">Add an account to a tenant.</p>
              </div>
            </div>
            <div class="panel-body">
              <form id="createUserForm">
                <div class="form-row">
                  <label>
                    <span data-i18n="field.tenant">Tenant</span>
                    <input name="tenant" placeholder="acme" required>
                  </label>
                  <label>
                    <span data-i18n="field.username">Username</span>
                    <input name="username" placeholder="alice" required>
                  </label>
                </div>
                <label>
                  <span data-i18n="field.password">Password</span>
                  <input name="password" data-i18n-attr="placeholder:field.password" placeholder="Password" type="password" required>
                </label>
                <div class="form-footer">
                  <label>
                    <span data-i18n="field.disabled">Disabled</span>
                    <span class="toolbar"><input name="disabled" type="checkbox"> <span data-i18n="field.initiallyDisabled">Initially disabled</span></span>
                  </label>
                  <button class="primary" data-i18n="action.addUser" type="submit">Add user</button>
                </div>
              </form>
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <div class="panel-title">
                <h2 data-i18n="section.devices">Devices</h2>
                <p id="deviceSubhead" class="muted" data-i18n="notice.selectUserDevices">Select a user to view devices.</p>
              </div>
              <button id="revokeAllButton" class="danger hidden" data-i18n="action.revokeActive" type="button">Revoke active</button>
            </div>
            <div id="devicesTable"></div>
          </section>
        </div>
      </section>
    </section>
  </main>

  <script>
    const LANG_STORAGE_KEY = 'snapzServerAdminLang';
    const I18N = {
      en: {
        'action.addUser': 'Add user',
        'action.connect': 'Connect',
        'action.delete': 'Delete',
        'action.details': 'Details',
        'action.disable': 'Disable',
        'action.enable': 'Enable',
        'action.forgetToken': 'Forget token',
        'action.hide': 'Hide',
        'action.next': 'Next',
        'action.password': 'Password',
        'action.prev': 'Prev',
        'action.refresh': 'Refresh',
        'action.rename': 'Rename',
        'action.revoke': 'Revoke',
        'action.revokeActive': 'Revoke active',
        'action.devices': 'Devices',
        'app.description': 'Manage snapz-server tenants, users, and sync devices.',
        'brand.subtitle': 'Manage tenants, users, and registered sync devices.',
        'field.disabled': 'Disabled',
        'field.filter': 'Filter',
        'field.password': 'Password',
        'field.tenant': 'Tenant',
        'field.token': 'Token',
        'field.username': 'Username',
        'field.initiallyDisabled': 'Initially disabled',
        'hero.eyebrow': 'Connected',
        'hero.title': 'snapz-server admin',
        'label.active': 'Active',
        'label.adminApiActive': 'Admin API active',
        'label.compression': 'Compression',
        'label.content': 'Content',
        'label.created': 'Created',
        'label.devices': 'Devices',
        'label.enabled': 'Enabled',
        'label.eta': 'ETA',
        'label.image': 'Image',
        'label.last': 'Last',
        'label.lastSeen': 'Last seen',
        'label.machine': 'Machine',
        'label.note': 'Note',
        'label.path': 'Path',
        'label.pushedBy': 'Pushed by',
        'label.revoked': 'Revoked',
        'label.snapshot': 'Snapshot',
        'label.snapshots': 'Snapshots',
        'label.status': 'Status',
        'label.sync': 'Sync',
        'label.versionUnknown': 'Version unknown',
        'label.version': 'Version {version}',
        'language.label': 'Language',
        'login.copy': 'Start snapz-server with --admin-token or SNAPZ_SERVER_ADMIN_TOKEN, then enter it here.',
        'login.eyebrow': 'Admin console',
        'login.title': 'Connect to snapz-server',
        'notice.imageDeleted': 'Image deleted.',
        'notice.imageRenamed': 'Image renamed.',
        'notice.loaded': 'Loaded current server state.',
        'notice.loadedSnapshots': 'Loaded image snapshots.',
        'notice.noImageSelected': 'No image selected.',
        'notice.noImages': 'No pushed images found.',
        'notice.noSnapshots': 'No snapshots in this image.',
        'notice.noUserSelected': 'No user selected.',
        'notice.noUsers': 'No users found.',
        'notice.noDevices': 'This user has no devices.',
        'notice.ready': 'Ready.',
        'notice.revokedDevice': 'Device revoked.',
        'notice.revokedDevices': 'Revoked active devices.',
        'notice.selectUserDevices': 'Select a user to view devices.',
        'notice.snapshotDeleted': 'Snapshot deleted.',
        'notice.snapshotRenamed': 'Snapshot renamed.',
        'notice.userCreated': 'User created.',
        'notice.userDeleted': 'User deleted.',
        'notice.userDisabled': 'User disabled.',
        'notice.userEnabled': 'User enabled.',
        'notice.usernameUpdated': 'Username updated.',
        'notice.passwordReset': 'Password reset.',
        'placeholder.adminToken': 'Admin token',
        'placeholder.sourceFilter': 'tenant, image name, path, or id',
        'placeholder.userFilter': 'tenant or username',
        'prompt.deleteImage': 'Delete image {tenant}/{name}? This removes the uploaded bundle from the server.',
        'prompt.deleteSnapshot': 'Delete {tenant}/{image}/{name}? This rewrites the uploaded bundle.',
        'prompt.deleteUser': 'Delete {tenant}/{username}? Registered devices are removed too.',
        'prompt.newImageName': 'New image name',
        'prompt.newSnapshotName': 'New snapshot name',
        'prompt.newUsername': 'New username',
        'prompt.resetPassword': 'New password for {tenant}/{username}',
        'prompt.revokeDevice': 'Revoke {name} for {tenant}/{username}?',
        'prompt.toggleUser': '{action} {tenant}/{username}?',
        'prompt.revokeActive': 'Revoke all active devices for {tenant}/{username}?',
        'section.createUser': 'Create user',
        'section.createUser.copy': 'Add an account to a tenant.',
        'section.devices': 'Devices',
        'section.images': 'Pushed images',
        'section.images.copy': 'Manage source bundles uploaded by snapz push.',
        'section.users': 'Users',
        'section.users.copy': 'Select a user to inspect registered devices.',
        'stat.devices': 'Devices',
        'stat.devicesHint': 'Registered clients',
        'stat.sources': 'Sources',
        'stat.sourcesHint': '{size} stored',
        'stat.tenants': 'Tenants',
        'stat.tenantsHint': 'Workspace groups',
        'stat.users': 'Users',
        'stat.usersHint': 'Login accounts',
        'table.actions': 'Actions',
        'text.activeCount': '{active}/{total} active',
        'text.fileCount': '{count} file(s)',
        'text.memoryChecked': 'Memory checked: {required} required / {limit} limit',
        'text.memoryPending': 'Memory checked before reading bundle.',
        'text.page': '{total} total - page {page}/{pages} - {memory}',
        'text.snapshotsCount': '{count} snapshot(s)',
        'text.updatedByFingerprint': 'Updated by client fingerprint',
      },
      zh: {
        'action.addUser': '添加用户',
        'action.connect': '连接',
        'action.delete': '删除',
        'action.details': '详情',
        'action.disable': '禁用',
        'action.enable': '启用',
        'action.forgetToken': '忘记令牌',
        'action.hide': '隐藏',
        'action.next': '下一页',
        'action.password': '密码',
        'action.prev': '上一页',
        'action.refresh': '刷新',
        'action.rename': '重命名',
        'action.revoke': '吊销',
        'action.revokeActive': '吊销活跃设备',
        'action.devices': '设备',
        'app.description': '管理 snapz-server 租户、用户和同步设备。',
        'brand.subtitle': '管理租户、用户和已注册同步设备。',
        'field.disabled': '禁用',
        'field.filter': '过滤',
        'field.password': '密码',
        'field.tenant': '租户',
        'field.token': '令牌',
        'field.username': '用户名',
        'field.initiallyDisabled': '初始禁用',
        'hero.eyebrow': '已连接',
        'hero.title': 'snapz-server 管理台',
        'label.active': '活跃',
        'label.adminApiActive': '管理 API 已启用',
        'label.compression': '压缩',
        'label.content': '内容',
        'label.created': '创建时间',
        'label.devices': '设备',
        'label.enabled': '启用',
        'label.eta': '预计剩余',
        'label.image': '镜像',
        'label.last': '上次',
        'label.lastSeen': '上次在线',
        'label.machine': '机器',
        'label.note': '备注',
        'label.path': '路径',
        'label.pushedBy': '推送者',
        'label.revoked': '已吊销',
        'label.snapshot': '快照',
        'label.snapshots': '快照',
        'label.status': '状态',
        'label.sync': '同步',
        'label.versionUnknown': '版本未知',
        'label.version': '版本 {version}',
        'language.label': '语言',
        'login.copy': '使用 --admin-token 或 SNAPZ_SERVER_ADMIN_TOKEN 启动 snapz-server，然后在这里输入令牌。',
        'login.eyebrow': '管理控制台',
        'login.title': '连接到 snapz-server',
        'notice.imageDeleted': '镜像已删除。',
        'notice.imageRenamed': '镜像已重命名。',
        'notice.loaded': '已加载当前服务端状态。',
        'notice.loadedSnapshots': '已加载镜像快照。',
        'notice.noImageSelected': '未选择镜像。',
        'notice.noImages': '没有找到已推送镜像。',
        'notice.noSnapshots': '该镜像中没有快照。',
        'notice.noUserSelected': '未选择用户。',
        'notice.noUsers': '没有找到用户。',
        'notice.noDevices': '该用户没有设备。',
        'notice.ready': '就绪。',
        'notice.revokedDevice': '设备已吊销。',
        'notice.revokedDevices': '活跃设备已吊销。',
        'notice.selectUserDevices': '选择用户以查看设备。',
        'notice.snapshotDeleted': '快照已删除。',
        'notice.snapshotRenamed': '快照已重命名。',
        'notice.userCreated': '用户已创建。',
        'notice.userDeleted': '用户已删除。',
        'notice.userDisabled': '用户已禁用。',
        'notice.userEnabled': '用户已启用。',
        'notice.usernameUpdated': '用户名已更新。',
        'notice.passwordReset': '密码已重置。',
        'placeholder.adminToken': '管理令牌',
        'placeholder.sourceFilter': '租户、镜像名称、路径或 ID',
        'placeholder.userFilter': '租户或用户名',
        'prompt.deleteImage': '删除镜像 {tenant}/{name}？这会从服务端移除上传的 bundle。',
        'prompt.deleteSnapshot': '删除 {tenant}/{image}/{name}？这会重写上传的 bundle。',
        'prompt.deleteUser': '删除 {tenant}/{username}？已注册设备也会被移除。',
        'prompt.newImageName': '新的镜像名称',
        'prompt.newSnapshotName': '新的快照名称',
        'prompt.newUsername': '新的用户名',
        'prompt.resetPassword': '{tenant}/{username} 的新密码',
        'prompt.revokeDevice': '吊销 {tenant}/{username} 的设备 {name}？',
        'prompt.toggleUser': '{action} {tenant}/{username}？',
        'prompt.revokeActive': '吊销 {tenant}/{username} 的所有活跃设备？',
        'section.createUser': '创建用户',
        'section.createUser.copy': '向租户添加账号。',
        'section.devices': '设备',
        'section.images': '已推送镜像',
        'section.images.copy': '管理通过 snapz push 上传的源 bundle。',
        'section.users': '用户',
        'section.users.copy': '选择用户以查看已注册设备。',
        'stat.devices': '设备',
        'stat.devicesHint': '已注册客户端',
        'stat.sources': '源',
        'stat.sourcesHint': '已存储 {size}',
        'stat.tenants': '租户',
        'stat.tenantsHint': '工作区分组',
        'stat.users': '用户',
        'stat.usersHint': '登录账号',
        'table.actions': '操作',
        'text.activeCount': '{active}/{total} 活跃',
        'text.fileCount': '{count} 个文件',
        'text.memoryChecked': '内存检查：需要 {required} / 限制 {limit}',
        'text.memoryPending': '读取 bundle 前会检查内存。',
        'text.page': '共 {total} 个 - 第 {page}/{pages} 页 - {memory}',
        'text.snapshotsCount': '{count} 个快照',
        'text.updatedByFingerprint': '由客户端指纹更新',
      },
    };

    function preferredLanguage() {
      const saved = localStorage.getItem(LANG_STORAGE_KEY);
      if (saved === 'en' || saved === 'zh') return saved;
      return navigator.language && navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en';
    }

    const state = {
      token: sessionStorage.getItem('snapzAdminToken') || '',
      stats: {},
      version: '',
      serverVersion: '',
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
      lang: preferredLanguage(),
    };
    let refreshTimer = null;

    const el = (id) => document.getElementById(id);

    function t(key, params = {}) {
      const template = (I18N[state.lang] && I18N[state.lang][key]) || I18N.en[key] || key;
      return template.replace(/\{(\w+)\}/g, (_match, name) => String(params[name] ?? ''));
    }

    function applyLanguage() {
      document.documentElement.lang = state.lang === 'zh' ? 'zh-CN' : 'en';
      el('languageSelect').value = state.lang;
      document.querySelectorAll('[data-i18n]').forEach((node) => {
        node.textContent = t(node.dataset.i18n);
      });
      document.querySelectorAll('[data-i18n-attr]').forEach((node) => {
        node.dataset.i18nAttr.split(';').forEach((pair) => {
          const [attr, key] = pair.split(':');
          if (attr && key) node.setAttribute(attr, t(key));
        });
      });
    }

    function setLanguage(lang) {
      state.lang = lang === 'zh' ? 'zh' : 'en';
      localStorage.setItem(LANG_STORAGE_KEY, state.lang);
      applyLanguage();
      renderStats();
      renderSources();
      renderUsers();
      renderDevices();
      showNotice(t('notice.ready'));
    }

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
      const versionText = state.version
        ? `snapz ${state.version}`
        : t('label.versionUnknown');
      el('versionBadge').textContent = versionText;
      el('headerVersion').textContent = state.version ? t('label.version', { version: state.version }) : '';
      const labels = [
        ['tenants', t('stat.tenants'), t('stat.tenantsHint'), 'sky'],
        ['users', t('stat.users'), t('stat.usersHint'), 'violet'],
        ['devices', t('stat.devices'), t('stat.devicesHint'), 'emerald'],
        ['sources', t('stat.sources'), t('stat.sourcesHint', { size: formatBytes(state.stats.bundle_bytes) }), 'amber'],
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

    function formatSpeed(value) {
      return `${formatBytes(Number(value || 0))}/s`;
    }

    function formatEta(value) {
      const seconds = Number(value);
      if (!Number.isFinite(seconds) || seconds < 0) return '-';
      if (seconds < 1) return '<1s';
      const whole = Math.round(seconds);
      const mins = Math.floor(whole / 60);
      const secs = whole % 60;
      if (mins <= 0) return `${secs}s`;
      const hours = Math.floor(mins / 60);
      const remMins = mins % 60;
      if (hours <= 0) return `${mins}m ${secs}s`;
      return `${hours}h ${remMins}m`;
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
        el('sourcesTable').innerHTML = `<div class="empty">${t('notice.noImages')}</div>`;
        return;
      }
      el('sourcesTable').innerHTML = `
        <div class="panel-body">
          <div class="table-wrap">
            <table style="min-width: 1260px;">
              <thead>
                <tr>
                  <th style="width: 18%;">${t('label.image')}</th>
                  <th style="width: 10%;">${t('field.tenant')}</th>
                  <th style="width: 22%;">${t('label.path')}</th>
                  <th style="width: 12%;">${t('label.snapshots')}</th>
                  <th style="width: 20%;">${t('label.sync')}</th>
                  <th style="width: 10%;">${t('label.pushedBy')}</th>
                  <th style="width: 8%;">${t('table.actions')}</th>
                </tr>
              </thead>
              <tbody>
                ${sources.map((source) => {
                  const sync = source.sync_status || {};
                  const pct = Math.max(0, Math.min(100, Number(sync.progress_percent || 0)));
                  const badgeClass = sync.status === 'failed'
                    ? 'bad'
                    : sync.status === 'completed'
                      ? 'ok'
                      : sync.status === 'running'
                        ? 'warn'
                        : '';
                  return `
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
                        <span class="badge">${t('text.snapshotsCount', { count: source.snapshot_count })}</span>
                        <div class="cell-subtitle">${formatBytes(source.bundle_bytes)}</div>
                      </td>
                      <td>
                        <div class="toolbar" style="gap: .4rem; margin-bottom: .35rem;">
                          <span class="badge ${badgeClass}">${escapeHtml(sync.status || 'idle')}</span>
                          ${sync.remote_only ? '<span class="badge warn">remote_only</span>' : ''}
                        </div>
                        <div class="mini-progress">
                          <div class="mini-progress-head">
                            <span>${escapeHtml(sync.phase || '-')}</span>
                            <span>${pct.toFixed(0)}%</span>
                          </div>
                          <div class="mini-bar"><span style="width: ${pct}%;"></span></div>
                        </div>
                        <div class="cell-subtitle">
                          ${formatSpeed(sync.speed_bps)} · ${t('label.eta')} ${formatEta(sync.eta_seconds)}
                        </div>
                        <div class="cell-subtitle">${t('label.last')} ${formatDate(source.last_sync_at || sync.last_sync_at)}</div>
                      </td>
                      <td>
                        <div class="cell-title">${escapeHtml(source.pushed_by_username || source.pushed_by_device_name || '-')}</div>
                        <div class="cell-subtitle">${formatDate(source.updated_at)}</div>
                      </td>
                      <td>
                        <div class="actions">
                          <button
                            data-action="details-source"
                            data-id="${source.id}"
                            data-tenant-id="${source.tenant_id}"
                            type="button"
                          >${t('action.details')}</button>
                          <button
                            data-action="rename-source"
                            data-id="${source.id}"
                            data-tenant-id="${source.tenant_id}"
                            type="button"
                          >${t('action.rename')}</button>
                          <button
                            class="danger"
                            data-action="delete-source"
                            data-id="${source.id}"
                            data-tenant-id="${source.tenant_id}"
                            type="button"
                          >${t('action.delete')}</button>
                        </div>
                      </td>
                    </tr>
                    ${sourceKey(source) === state.selectedSourceKey ? `
                      <tr class="source-snapshot-row">
                        <td colspan="7">${renderSourceSnapshots()}</td>
                      </tr>
                    ` : ''}
                  `;
                }).join('')}
              </tbody>
            </table>
          </div>
        </div>
      `;
    }

    function renderSourceSnapshots() {
      const source = selectedSource();
      if (!source) return `<div class="empty">${t('notice.noImageSelected')}</div>`;
      const totalPages = Math.max(1, Math.ceil(state.snapshotTotal / state.snapshotPerPage));
      const memory = state.snapshotMemory;
      const memoryText = memory
        ? t('text.memoryChecked', {
            required: formatBytes(memory.required_bytes),
            limit: formatBytes(memory.limit_bytes),
          })
        : t('text.memoryPending');
      const rows = state.sourceSnapshots.length === 0
        ? `<tr><td colspan="6" class="empty">${t('notice.noSnapshots')}</td></tr>`
        : state.sourceSnapshots.map((snapshot) => `
          <tr>
            <td>
              <strong>${escapeHtml(snapshot.name)}</strong>
              <div class="muted mono">${escapeHtml(snapshot.kind || '-')}</div>
            </td>
            <td>${formatDate(snapshot.created)}</td>
            <td>
              <span class="badge">${t('text.fileCount', { count: Number(snapshot.file_count || 0) })}</span>
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
                >${t('action.rename')}</button>
                <button
                  class="danger"
                  data-action="delete-snapshot"
                  data-name="${escapeHtml(snapshot.name)}"
                  data-id="${source.id}"
                  data-tenant-id="${source.tenant_id}"
                  type="button"
                >${t('action.delete')}</button>
              </div>
            </td>
          </tr>
        `).join('');
      return `
        <div class="snapshot-panel">
          <div class="snapshot-head">
            <div class="panel-title">
              <h3>${t('label.snapshots')} - ${escapeHtml(source.display_name)}</h3>
              <p>${t('text.page', { total: state.snapshotTotal, page: state.snapshotPage, pages: totalPages, memory: memoryText })}</p>
            </div>
            <div class="actions">
              <button data-action="snapshots-prev" data-id="${source.id}" data-tenant-id="${source.tenant_id}" ${state.snapshotPage <= 1 ? 'disabled' : ''} type="button">${t('action.prev')}</button>
              <button data-action="snapshots-next" data-id="${source.id}" data-tenant-id="${source.tenant_id}" ${state.snapshotPage >= totalPages ? 'disabled' : ''} type="button">${t('action.next')}</button>
              <button data-action="hide-snapshots" data-id="${source.id}" data-tenant-id="${source.tenant_id}" type="button">${t('action.hide')}</button>
            </div>
          </div>
          <div class="table-wrap">
            <table style="min-width: 940px;">
              <thead>
                <tr>
                  <th style="width: 22%;">${t('label.snapshot')}</th>
                  <th style="width: 18%;">${t('label.created')}</th>
                  <th style="width: 16%;">${t('label.content')}</th>
                  <th style="width: 12%;">${t('label.compression')}</th>
                  <th style="width: 18%;">${t('label.note')}</th>
                  <th style="width: 14%;">${t('table.actions')}</th>
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
        el('usersTable').innerHTML = `<div class="empty">${t('notice.noUsers')}</div>`;
        return;
      }
      el('usersTable').innerHTML = `
        <div class="panel-body">
          <div class="table-wrap">
            <table style="min-width: 1000px;">
              <thead>
                <tr>
                  <th style="width: 22%;">${t('field.username')}</th>
                  <th style="width: 15%;">${t('field.tenant')}</th>
                  <th style="width: 12%;">${t('label.status')}</th>
                  <th style="width: 18%;">${t('label.devices')}</th>
                  <th style="width: 33%;">${t('table.actions')}</th>
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
                        ${user.disabled ? t('field.disabled') : t('label.enabled')}
                      </span>
                    </td>
                    <td>
                      <span class="badge">${t('text.activeCount', { active: user.active_device_count, total: user.device_count })}</span>
                      <div class="cell-subtitle">${formatDate(user.last_seen_at)}</div>
                    </td>
                    <td>
                      <div class="actions">
                        <button data-action="select" data-id="${user.id}" type="button">${t('action.devices')}</button>
                        <button data-action="rename" data-id="${user.id}" type="button">${t('action.rename')}</button>
                        <button data-action="toggle" data-id="${user.id}" type="button">
                          ${user.disabled ? t('action.enable') : t('action.disable')}
                        </button>
                        <button data-action="password" data-id="${user.id}" type="button">${t('action.password')}</button>
                        <button class="danger" data-action="delete" data-id="${user.id}" type="button">${t('action.delete')}</button>
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
        : t('notice.selectUserDevices');
      el('revokeAllButton').classList.toggle('hidden', !user);
      if (!user) {
        el('devicesTable').innerHTML = `<div class="panel-body"><div class="empty">${t('notice.noUserSelected')}</div></div>`;
        return;
      }
      if (state.devices.length === 0) {
        el('devicesTable').innerHTML = `<div class="panel-body"><div class="empty">${t('notice.noDevices')}</div></div>`;
        return;
      }
      el('devicesTable').innerHTML = `
        <div class="panel-body">
          <div class="table-wrap">
            <table style="min-width: 900px;">
              <thead>
                <tr>
                  <th style="width: 28%;">${t('label.devices')}</th>
                  <th style="width: 26%;">${t('label.machine')}</th>
                  <th style="width: 16%;">${t('label.status')}</th>
                  <th style="width: 18%;">${t('label.lastSeen')}</th>
                  <th style="width: 12%;">${t('table.actions')}</th>
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
                      <div class="cell-title mono">${escapeHtml(device.machine_id || '-')}</div>
                      <div class="cell-subtitle">${t('text.updatedByFingerprint')}</div>
                    </td>
                    <td>
                      <span class="badge ${device.revoked ? 'warn' : 'ok'}">
                        ${device.revoked ? t('label.revoked') : t('label.active')}
                      </span>
                    </td>
                    <td>
                      <div class="cell-title">${formatDate(device.last_seen_at)}</div>
                      <div class="cell-subtitle">${t('label.created')} ${formatDate(device.created_at)}</div>
                    </td>
                    <td>
                      <button
                        class="danger"
                        data-action="revoke-device"
                        data-id="${device.id}"
                        ${device.revoked ? 'disabled' : ''}
                        type="button"
                      >${t('action.revoke')}</button>
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
      state.version = overview.version || '';
      state.serverVersion = overview.server_version || '';
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
      showNotice(t('notice.loaded'));
      scheduleRefreshIfRunning();
    }

    function scheduleRefreshIfRunning() {
      if (refreshTimer) {
        clearTimeout(refreshTimer);
        refreshTimer = null;
      }
      if (!state.sources.some((source) => (source.sync_status || {}).status === 'running')) {
        return;
      }
      refreshTimer = setTimeout(() => {
        refreshAll().catch((error) => showNotice(error.message, true));
      }, 1000);
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
      if (refreshTimer) {
        clearTimeout(refreshTimer);
        refreshTimer = null;
      }
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
        showNotice(t('notice.userCreated'));
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
          showNotice(t('notice.loadedSnapshots'));
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
          const newName = prompt(t('prompt.newSnapshotName'), oldName);
          if (newName && newName.trim() !== oldName) {
            await renameSnapshot(source, oldName, newName.trim());
            showNotice(t('notice.snapshotRenamed'));
          }
        } else if (button.dataset.action === 'delete-snapshot') {
          const name = button.dataset.name;
          if (confirm(t('prompt.deleteSnapshot', {
            tenant: source.tenant,
            image: source.display_name,
            name,
          }))) {
            await deleteSnapshot(source, name);
            showNotice(t('notice.snapshotDeleted'));
          }
        } else if (button.dataset.action === 'rename-source') {
          const displayName = prompt(t('prompt.newImageName'), source.display_name);
          if (displayName && displayName.trim() !== source.display_name) {
            await updateSource(source, { display_name: displayName.trim() });
            showNotice(t('notice.imageRenamed'));
          }
        } else if (button.dataset.action === 'delete-source') {
          if (confirm(t('prompt.deleteImage', {
            tenant: source.tenant,
            name: source.display_name,
          }))) {
            await deleteSource(source);
            showNotice(t('notice.imageDeleted'));
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
          const username = prompt(t('prompt.newUsername'), user.username);
          if (username && username.trim() !== user.username) {
            await updateUser(user.id, { username: username.trim() });
            showNotice(t('notice.usernameUpdated'));
          }
        } else if (action === 'toggle') {
          const actionText = user.disabled ? t('action.enable') : t('action.disable');
          if (!confirm(t('prompt.toggleUser', {
            action: actionText,
            tenant: user.tenant,
            username: user.username,
          }))) return;
          await updateUser(user.id, { disabled: !user.disabled });
          showNotice(user.disabled ? t('notice.userEnabled') : t('notice.userDisabled'));
        } else if (action === 'password') {
          const password = prompt(t('prompt.resetPassword', {
            tenant: user.tenant,
            username: user.username,
          }));
          if (password) {
            await api(`/api/admin/users/${encodeURIComponent(user.id)}/password`, {
              method: 'POST',
              body: JSON.stringify({ password }),
            });
            showNotice(t('notice.passwordReset'));
          }
        } else if (action === 'delete') {
          if (confirm(t('prompt.deleteUser', {
            tenant: user.tenant,
            username: user.username,
          }))) {
            await api(`/api/admin/users/${encodeURIComponent(user.id)}`, { method: 'DELETE' });
            state.selectedUserId = '';
            await refreshAll();
            showNotice(t('notice.userDeleted'));
          }
        }
      } catch (error) {
        showNotice(error.message, true);
      }
    });

    el('devicesTable').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-action="revoke-device"]');
      const device = state.devices.find((item) => item.id === button?.dataset.id);
      if (!button || !device) return;
      if (!confirm(t('prompt.revokeDevice', {
        name: device.name,
        tenant: device.tenant,
        username: device.username,
      }))) return;
      try {
        await api(`/api/admin/devices/${encodeURIComponent(button.dataset.id)}/revoke`, {
          method: 'POST',
        });
        await refreshAll();
        showNotice(t('notice.revokedDevice'));
      } catch (error) {
        showNotice(error.message, true);
      }
    });

    el('revokeAllButton').addEventListener('click', async () => {
      const user = selectedUser();
      if (!user || !confirm(t('prompt.revokeActive', {
        tenant: user.tenant,
        username: user.username,
      }))) return;
      try {
        const data = await api(`/api/admin/users/${encodeURIComponent(user.id)}/devices/revoke`, {
          method: 'POST',
        });
        await refreshAll();
        showNotice(`${t('notice.revokedDevices')} (${data.revoked || 0})`);
      } catch (error) {
        showNotice(error.message, true);
      }
    });

    el('languageSelect').addEventListener('change', (event) => {
      setLanguage(event.target.value);
    });

    applyLanguage();
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
