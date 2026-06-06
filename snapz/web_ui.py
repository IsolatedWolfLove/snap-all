"""Built-in local web UI for the snapz client."""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import asdict, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from snapz import __version__, api
from snapz.config import RuntimeConfig, default_config
from snapz.store import DirEntry, SnapshotMeta
from snapz.util import format_size, is_auto_snapshot, resolve_path


CLIENT_WEB_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>snapz Client</title>
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
      --violet: #8b5cf6;
      --emerald: #10b981;
      --amber: #f59e0b;
      --danger: #a8071a;
      --success: #237804;
      --warn: #ad6800;
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
    button, input, textarea {
      font: inherit;
    }
    button {
      min-height: 2.35rem;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 .8rem;
      background: #fff;
      color: var(--ink);
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
    }
    button:hover { border-color: #aab4c3; }
    button:disabled {
      cursor: not-allowed;
      opacity: .55;
    }
    button.primary {
      border-color: var(--primary);
      background: var(--primary);
      color: #fff;
    }
    button.primary:hover { background: var(--primary-strong); }
    button.danger {
      border-color: #ffccc7;
      color: var(--danger);
    }
    input, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
    }
    input {
      height: 2.35rem;
      padding: 0 .7rem;
    }
    textarea {
      min-height: 4.5rem;
      resize: vertical;
      padding: .65rem .7rem;
    }
    input::placeholder, textarea::placeholder { color: var(--subtle); }
    h1, h2, h3, p { margin: 0; }
    header {
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      padding: 1rem 2rem;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, .92);
      backdrop-filter: blur(10px);
    }
    .brand h1 {
      font-size: 1.25rem;
      line-height: 1.3;
    }
    .brand p {
      margin-top: .2rem;
      color: var(--muted);
      font-size: .85rem;
    }
    main {
      width: min(1320px, 100%);
      margin: 0 auto;
      padding: 1.5rem 2rem 2rem;
    }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: .75rem;
    }
    .nav {
      display: flex;
      flex-wrap: wrap;
      gap: .35rem;
    }
    .nav button {
      min-height: 2rem;
      border-color: transparent;
      background: transparent;
      color: var(--muted);
    }
    .nav button.active {
      border-color: #bfdbfe;
      background: #eff6ff;
      color: var(--primary);
    }
    .panel, .stat {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
    }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 1.25rem;
      align-items: center;
      margin-bottom: 1rem;
      padding: 1.25rem 1.5rem;
      border-color: #bae6fd;
      background: #eef8ff;
    }
    .eyebrow {
      color: var(--primary);
      font-size: .75rem;
      font-weight: 700;
      text-transform: uppercase;
    }
    .hero h2 {
      margin-top: .25rem;
      font-size: 1.5rem;
      line-height: 1.3;
    }
    .hero p {
      margin-top: .3rem;
      color: var(--muted);
      font-size: .88rem;
      line-height: 1.5;
    }
    .field {
      display: grid;
      gap: .35rem;
      color: var(--muted);
      font-size: .82rem;
      font-weight: 600;
    }
    .path-bar {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) auto;
      gap: .75rem;
      align-items: end;
      margin-bottom: 1rem;
      padding: 1rem;
    }
    .notice {
      min-height: 2.35rem;
      display: flex;
      align-items: center;
      margin-bottom: 1rem;
      padding: .6rem .8rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--muted);
    }
    .notice.error {
      border-color: #ffccc7;
      background: #fff1f0;
      color: var(--danger);
    }
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 1rem;
      margin-bottom: 1rem;
    }
    .stat {
      min-height: 6.75rem;
      padding: 1.1rem;
      border-top-width: 3px;
    }
    .stat.sky { border-top-color: #38bdf8; }
    .stat.violet { border-top-color: var(--violet); }
    .stat.emerald { border-top-color: var(--emerald); }
    .stat.amber { border-top-color: var(--amber); }
    .stat-label {
      color: var(--muted);
      font-size: .82rem;
    }
    .stat-value {
      display: block;
      margin-top: .35rem;
      font-size: 1.75rem;
      font-weight: 700;
      line-height: 1.15;
    }
    .stat-hint {
      margin-top: .35rem;
      color: var(--subtle);
      font-size: .75rem;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(340px, .44fr);
      gap: 1rem;
      align-items: start;
    }
    .stack {
      display: grid;
      gap: 1rem;
    }
    .panel-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      padding: 1.1rem;
    }
    .panel-title {
      display: grid;
      gap: .15rem;
      min-width: 0;
    }
    .panel-title h2,
    .panel-title h3 {
      font-size: 1rem;
      line-height: 1.4;
    }
    .panel-title p {
      color: var(--muted);
      font-size: .75rem;
      line-height: 1.5;
    }
    .panel-body {
      padding: 0 1.1rem 1.1rem;
    }
    .form-grid {
      display: grid;
      gap: .75rem;
    }
    .form-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: .75rem;
    }
    .table-wrap { overflow-x: auto; }
    table {
      width: 100%;
      min-width: 880px;
      border-collapse: collapse;
      table-layout: fixed;
      border: 1px solid #f0f0f0;
      border-radius: 6px;
      overflow: hidden;
      background: #fff;
    }
    th, td {
      padding: .6rem .75rem;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: middle;
      overflow-wrap: anywhere;
    }
    th {
      background: #fafafa;
      color: var(--muted);
      font-size: .78rem;
      font-weight: 700;
    }
    tr:last-child td { border-bottom: 0; }
    tr.selected td { background: #e6f4ff; }
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
    .actions button {
      min-height: 2rem;
      padding: 0 .65rem;
      font-size: .78rem;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 1.35rem;
      border: 1px solid var(--line-strong);
      border-radius: 4px;
      padding: 0 .55rem;
      background: #f8fafc;
      color: var(--muted);
      font-size: .75rem;
      font-weight: 600;
    }
    .badge.ok {
      border-color: #52c41a;
      background: #f6ffed;
      color: var(--success);
    }
    .badge.warn {
      border-color: #faad14;
      background: #fffbe6;
      color: var(--warn);
    }
    .badge.bad {
      border-color: #ff4d4f;
      background: #fff1f0;
      color: var(--danger);
    }
    .empty {
      padding: 2rem 1rem;
      text-align: center;
      color: var(--muted);
    }
    .view { display: none; }
    .view.active { display: block; }
    .storage-bars {
      display: grid;
      gap: .75rem;
    }
    .storage-row {
      display: grid;
      gap: .35rem;
    }
    .bar {
      height: .6rem;
      overflow: hidden;
      border-radius: 999px;
      background: #eef2f7;
    }
    .bar span {
      display: block;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #1677ff, #10b981);
    }
    .hidden { display: none !important; }
    @media (max-width: 980px) {
      header {
        align-items: flex-start;
        flex-direction: column;
        padding: 1rem;
      }
      main { padding: 1rem; }
      .hero,
      .path-bar,
      .grid {
        grid-template-columns: 1fr;
      }
      .stats-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 640px) {
      main { padding: .75rem; }
      .stats-grid,
      .form-row {
        grid-template-columns: 1fr;
      }
      .panel-head {
        align-items: stretch;
        flex-direction: column;
      }
      .toolbar,
      .actions {
        align-items: stretch;
        flex-direction: column;
      }
      button { width: 100%; }
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <h1>snapz Client</h1>
      <p>Local snapshot control panel</p>
    </div>
    <nav class="nav" aria-label="Primary">
      <button class="active" data-view="dashboard" type="button">Dashboard</button>
      <button data-view="snapshots" type="button">Snapshots</button>
      <button data-view="sources" type="button">Sources</button>
      <button data-view="storage" type="button">Storage</button>
    </nav>
  </header>

  <main>
    <section class="panel hero">
      <div>
        <div class="eyebrow">Local web UI</div>
        <h2>snapz client workspace</h2>
        <p>Manage local sources, snapshots, restore points, and storage from the browser.</p>
      </div>
      <div class="toolbar">
        <span id="healthBadge" class="badge warn">Checking API</span>
        <button id="refreshButton" class="primary" type="button">Refresh</button>
      </div>
    </section>

    <section class="panel path-bar">
      <label class="field">
        Active source path
        <input id="pathInput" autocomplete="off" placeholder="/path/to/project">
      </label>
      <button id="loadPathButton" class="primary" type="button">Load source</button>
    </section>

    <div id="notice" class="notice">Ready.</div>

    <section id="dashboard" class="view active">
      <section class="stats-grid" id="statsGrid"></section>

      <section class="grid">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <h2>Recent snapshots</h2>
              <p>Latest restore points for the active source.</p>
            </div>
            <button data-view-target="snapshots" type="button">View all</button>
          </div>
          <div id="recentSnapshots"></div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <h2>Create snapshot</h2>
              <p>Capture the current state of a local directory.</p>
            </div>
          </div>
          <div class="panel-body">
            <form id="createSnapshotForm" class="form-grid">
              <label class="field">
                Path
                <input name="path" placeholder="/path/to/project" required>
              </label>
              <label class="field">
                Name
                <input name="name" placeholder="auto generated if empty">
              </label>
              <label class="field">
                Note
                <textarea name="note" placeholder="Optional context"></textarea>
              </label>
              <div class="toolbar">
                <button class="primary" type="submit">Create snapshot</button>
                <label class="toolbar" style="color: var(--muted); font-size: .82rem;">
                  <input name="include_large" type="checkbox" style="width: 1rem; height: 1rem;">
                  Include large files
                </label>
              </div>
            </form>
          </div>
        </section>
      </section>
    </section>

    <section id="snapshots" class="view">
      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">
            <h2>Snapshots</h2>
            <p>Create, inspect, restore, protect, rename, or delete local snapshots.</p>
          </div>
          <div class="toolbar">
            <input id="snapshotFilter" placeholder="Filter name, note, or tag" style="width: min(320px, 100%);">
            <label class="toolbar" style="color: var(--muted); font-size: .82rem;">
              <input id="showAutoInput" type="checkbox" style="width: 1rem; height: 1rem;">
              Show auto snapshots
            </label>
          </div>
        </div>
        <div id="snapshotsTable"></div>
      </section>
    </section>

    <section id="sources" class="view">
      <section class="grid">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <h2>Sources</h2>
              <p>Directories with snapshots in the local snapz store.</p>
            </div>
          </div>
          <div id="sourcesTable"></div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <h2>Initialize source</h2>
              <p>Add a stable source marker for move detection.</p>
            </div>
          </div>
          <div class="panel-body">
            <form id="initSourceForm" class="form-grid">
              <label class="field">
                Path
                <input name="path" placeholder="/path/to/project" required>
              </label>
              <button class="primary" type="submit">Initialize</button>
            </form>
          </div>
        </section>
      </section>
    </section>

    <section id="storage" class="view">
      <section class="grid">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <h2>Storage breakdown</h2>
              <p>On-disk usage grouped by source.</p>
            </div>
          </div>
          <div id="storagePanel" class="panel-body"></div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <h2>Maintenance</h2>
              <p>Preview local garbage collection for the active source.</p>
            </div>
          </div>
          <div class="panel-body stack">
            <button id="previewGcButton" type="button">Preview GC</button>
            <button id="runGcButton" class="danger" type="button">Run GC</button>
          </div>
        </section>
      </section>
    </section>
  </main>

  <script>
    const state = {
      health: null,
      overview: {},
      sources: [],
      snapshots: [],
      stats: [],
      path: new URLSearchParams(location.search).get('path') || '.',
      filter: '',
      showAuto: false,
    };

    const el = (id) => document.getElementById(id);
    const qs = (selector) => document.querySelector(selector);

    function showNotice(message, isError = false) {
      const node = el('notice');
      node.textContent = message;
      node.classList.toggle('error', isError);
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

    function formatDate(value) {
      if (!value) return '-';
      const parsed = Date.parse(value);
      if (Number.isNaN(parsed)) return value;
      return new Date(parsed).toLocaleString();
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: {
          ...(options.body ? { 'Content-Type': 'application/json' } : {}),
          ...(options.headers || {}),
        },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || `${response.status} ${response.statusText}`);
      }
      return data;
    }

    function setActiveView(id) {
      document.querySelectorAll('.view').forEach((node) => {
        node.classList.toggle('active', node.id === id);
      });
      document.querySelectorAll('.nav button').forEach((button) => {
        button.classList.toggle('active', button.dataset.view === id);
      });
    }

    function renderHealth() {
      const badge = el('healthBadge');
      if (!state.health) {
        badge.className = 'badge warn';
        badge.textContent = 'Checking API';
        return;
      }
      badge.className = 'badge ok';
      badge.textContent = `API ${state.health.status} - ${state.health.version}`;
    }

    function renderStats() {
      const overview = state.overview || {};
      const items = [
        ['Total snapshots', overview.total_snapshots || 0, 'Across local sources', 'sky'],
        ['Sources', overview.total_sources || 0, 'Tracked directories', 'violet'],
        ['Storage used', overview.total_storage || '0 B', 'On disk', 'emerald'],
        ['Dedup ratio', `${overview.dedup_ratio || 1}x`, 'Logical to unique bytes', 'amber'],
      ];
      el('statsGrid').innerHTML = items.map(([label, value, hint, tone]) => `
        <article class="stat ${tone}">
          <span class="stat-label">${label}</span>
          <strong class="stat-value">${value}</strong>
          <div class="stat-hint">${hint}</div>
        </article>
      `).join('');
    }

    function filteredSnapshots() {
      const needle = state.filter.trim().toLowerCase();
      if (!needle) return state.snapshots;
      return state.snapshots.filter((snapshot) =>
        [
          snapshot.name,
          snapshot.note,
          snapshot.source,
          (snapshot.tags || []).join(' '),
        ].join(' ').toLowerCase().includes(needle)
      );
    }

    function snapshotRows(snapshots, limit = null) {
      const rows = limit ? snapshots.slice(0, limit) : snapshots;
      if (rows.length === 0) {
        return '<div class="empty">No snapshots found.</div>';
      }
      return `
        <div class="panel-body">
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style="width: 22%;">Snapshot</th>
                  <th style="width: 20%;">Created</th>
                  <th style="width: 14%;">Size</th>
                  <th style="width: 12%;">Files</th>
                  <th style="width: 12%;">Status</th>
                  <th style="width: 20%;">Actions</th>
                </tr>
              </thead>
              <tbody>
                ${rows.map((snapshot) => `
                  <tr>
                    <td>
                      <div class="cell-title">${escapeHtml(snapshot.name)}</div>
                      <div class="cell-subtitle">${escapeHtml(snapshot.note || snapshot.compression || '')}</div>
                    </td>
                    <td>${formatDate(snapshot.created)}</td>
                    <td>${formatBytes(snapshot.size_bytes)}</td>
                    <td>${Number(snapshot.file_count || 0).toLocaleString()}</td>
                    <td>
                      <span class="badge ${snapshot.protected ? 'warn' : 'ok'}">
                        ${snapshot.protected ? 'Protected' : 'Ready'}
                      </span>
                    </td>
                    <td>
                      <div class="actions">
                        <button data-action="details" data-name="${escapeHtml(snapshot.name)}" type="button">Details</button>
                        <button data-action="restore" data-name="${escapeHtml(snapshot.name)}" type="button">Restore</button>
                        <button data-action="rename" data-name="${escapeHtml(snapshot.name)}" type="button">Rename</button>
                        <button data-action="${snapshot.protected ? 'unprotect' : 'protect'}" data-name="${escapeHtml(snapshot.name)}" type="button">
                          ${snapshot.protected ? 'Unprotect' : 'Protect'}
                        </button>
                        <button class="danger" data-action="delete" data-name="${escapeHtml(snapshot.name)}" type="button">Delete</button>
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

    function renderSnapshots() {
      const snapshots = filteredSnapshots();
      el('recentSnapshots').innerHTML = snapshotRows(state.snapshots, 5);
      el('snapshotsTable').innerHTML = snapshotRows(snapshots);
    }

    function renderSources() {
      if (state.sources.length === 0) {
        el('sourcesTable').innerHTML = '<div class="empty">No sources found.</div>';
        return;
      }
      el('sourcesTable').innerHTML = `
        <div class="panel-body">
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style="width: 36%;">Source</th>
                  <th style="width: 14%;">Snapshots</th>
                  <th style="width: 22%;">Last used</th>
                  <th style="width: 14%;">Storage</th>
                  <th style="width: 14%;">Actions</th>
                </tr>
              </thead>
              <tbody>
                ${state.sources.map((source) => `
                  <tr class="${source.abspath === state.path ? 'selected' : ''}">
                    <td>
                      <div class="cell-title">${escapeHtml(source.name || source.abspath)}</div>
                      <div class="cell-subtitle">${escapeHtml(source.abspath)}</div>
                    </td>
                    <td><span class="badge">${source.snapshot_count} snapshot(s)</span></td>
                    <td>${formatDate(source.last_used)}</td>
                    <td>${formatBytes(source.on_disk_bytes || 0)}</td>
                    <td>
                      <button data-action="select-source" data-path="${escapeHtml(source.abspath)}" type="button">Open</button>
                    </td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      `;
    }

    function renderStorage() {
      const total = Math.max(1, Number(state.overview.total_storage_bytes || 0));
      if (state.stats.length === 0) {
        el('storagePanel').innerHTML = '<div class="empty">No storage data yet.</div>';
        return;
      }
      el('storagePanel').innerHTML = `
        <div class="storage-bars">
          ${state.stats.map((entry) => {
            const pct = Math.max(3, Math.round((Number(entry.on_disk_bytes || 0) / total) * 100));
            return `
              <div class="storage-row">
                <div class="toolbar" style="justify-content: space-between;">
                  <strong>${escapeHtml(entry.name || entry.abspath)}</strong>
                  <span class="cell-subtitle">${formatBytes(entry.on_disk_bytes)} / ${entry.snapshot_count} snapshot(s)</span>
                </div>
                <div class="bar"><span style="width: ${Math.min(100, pct)}%;"></span></div>
              </div>
            `;
          }).join('')}
        </div>
      `;
    }

    function renderAll() {
      renderHealth();
      renderStats();
      renderSnapshots();
      renderSources();
      renderStorage();
      el('pathInput').value = state.path;
      qs('#createSnapshotForm input[name="path"]').value = state.path;
      qs('#initSourceForm input[name="path"]').value = state.path;
    }

    async function loadAll() {
      try {
        const params = new URLSearchParams({
          path: state.path,
          show_auto: String(state.showAuto),
        });
        const [health, overview, sources, snapshots, stats] = await Promise.all([
          api('/api/health'),
          api('/api/overview'),
          api('/api/sources'),
          api(`/api/snapshots?${params}`),
          api('/api/stats'),
        ]);
        state.health = health;
        state.overview = overview;
        state.sources = sources.sources || [];
        state.snapshots = snapshots.snapshots || [];
        state.path = snapshots.path || state.path;
        state.stats = stats.stats || [];
        renderAll();
        showNotice('Loaded current snapz state.');
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    async function createSnapshot(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const payload = {
        path: form.get('path'),
        name: form.get('name') || null,
        note: form.get('note') || '',
        include_large: form.get('include_large') === 'on',
      };
      try {
        const result = await api('/api/snapshots', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        state.path = result.snapshot.source;
        event.currentTarget.reset();
        await loadAll();
        showNotice(`Snapshot created: ${result.snapshot.name}`);
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    async function handleSnapshotAction(button) {
      const name = button.dataset.name;
      const path = encodeURIComponent(state.path);
      try {
        if (button.dataset.action === 'details') {
          const data = await api(`/api/snapshots/${encodeURIComponent(name)}?path=${path}`);
          const snapshot = data.snapshot;
          alert([
            `Name: ${snapshot.name}`,
            `Source: ${snapshot.source}`,
            `Created: ${formatDate(snapshot.created)}`,
            `Size: ${formatBytes(snapshot.size_bytes)}`,
            `Files: ${Number(snapshot.file_count || 0).toLocaleString()}`,
            `Compression: ${snapshot.compression}`,
            `Note: ${snapshot.note || '-'}`,
          ].join('\n'));
        } else if (button.dataset.action === 'restore') {
          if (!confirm(`Restore ${name} over ${state.path}?`)) return;
          const data = await api(`/api/snapshots/${encodeURIComponent(name)}/restore?path=${path}`, {
            method: 'POST',
            body: JSON.stringify({ auto_save: true, clean: false }),
          });
          await loadAll();
          showNotice(data.message || `Restored ${name}.`);
        } else if (button.dataset.action === 'rename') {
          const next = prompt('New snapshot name', name);
          if (!next || next.trim() === name) return;
          await api(`/api/snapshots/${encodeURIComponent(name)}/rename?path=${path}`, {
            method: 'POST',
            body: JSON.stringify({ new_name: next.trim() }),
          });
          await loadAll();
          showNotice('Snapshot renamed.');
        } else if (button.dataset.action === 'protect' || button.dataset.action === 'unprotect') {
          await api(`/api/snapshots/${encodeURIComponent(name)}/${button.dataset.action}?path=${path}`, {
            method: 'POST',
          });
          await loadAll();
          showNotice(button.dataset.action === 'protect' ? 'Snapshot protected.' : 'Snapshot unprotected.');
        } else if (button.dataset.action === 'delete') {
          if (!confirm(`Delete snapshot ${name}?`)) return;
          await api(`/api/snapshots/${encodeURIComponent(name)}?path=${path}`, {
            method: 'DELETE',
          });
          await loadAll();
          showNotice('Snapshot deleted.');
        }
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    async function initSource(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        const data = await api('/api/sources/init', {
          method: 'POST',
          body: JSON.stringify({ path: form.get('path') }),
        });
        await loadAll();
        showNotice(data.message || 'Source initialized.');
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    async function runGc(dryRun) {
      try {
        const data = await api('/api/gc', {
          method: 'POST',
          body: JSON.stringify({ path: state.path, dry_run: dryRun }),
        });
        await loadAll();
        showNotice(data.message || 'GC complete.');
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    document.querySelectorAll('.nav button').forEach((button) => {
      button.addEventListener('click', () => setActiveView(button.dataset.view));
    });
    document.querySelectorAll('[data-view-target]').forEach((button) => {
      button.addEventListener('click', () => setActiveView(button.dataset.viewTarget));
    });
    el('refreshButton').addEventListener('click', loadAll);
    el('loadPathButton').addEventListener('click', () => {
      state.path = el('pathInput').value.trim() || '.';
      loadAll();
    });
    el('snapshotFilter').addEventListener('input', (event) => {
      state.filter = event.target.value;
      renderSnapshots();
    });
    el('showAutoInput').addEventListener('change', (event) => {
      state.showAuto = event.target.checked;
      loadAll();
    });
    el('createSnapshotForm').addEventListener('submit', createSnapshot);
    el('initSourceForm').addEventListener('submit', initSource);
    el('snapshotsTable').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-action]');
      if (button) handleSnapshotAction(button);
    });
    el('recentSnapshots').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-action]');
      if (button) handleSnapshotAction(button);
    });
    el('sourcesTable').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-action="select-source"]');
      if (!button) return;
      state.path = button.dataset.path;
      setActiveView('snapshots');
      loadAll();
    });
    el('previewGcButton').addEventListener('click', () => runGc(true));
    el('runGcButton').addEventListener('click', () => {
      if (confirm(`Run garbage collection for ${state.path}?`)) {
        runGc(false);
      }
    });

    state.path = el('pathInput').value = state.path;
    loadAll();
  </script>
</body>
</html>
"""


class SnapzWebServer(ThreadingHTTPServer):
    """HTTP server carrying snapz runtime configuration."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        config: RuntimeConfig | None = None,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.config = config or default_config()


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def _snapshot_to_dict(snapshot: SnapshotMeta) -> dict[str, Any]:
    data = snapshot.to_dict()
    data["size_label"] = format_size(snapshot.size_bytes)
    data["total_label"] = format_size(snapshot.total_bytes_in)
    return data


def _source_to_dict(entry: DirEntry) -> dict[str, Any]:
    latest = entry.snapshots[0] if entry.snapshots else None
    path = Path(entry.meta.abspath)
    return {
        "key": entry.key,
        "name": path.name or entry.key,
        "abspath": entry.meta.abspath,
        "first_seen": entry.meta.first_seen,
        "last_used": entry.meta.last_used,
        "snapshot_count": len(entry.snapshots),
        "snapshot_count_cached": entry.meta.snapshot_count_cached,
        "on_disk_bytes": entry.meta.on_disk_bytes_cached,
        "source_marker": entry.meta.source_marker,
        "archived": entry.archived,
        "archive_reason": entry.archive_reason,
        "latest_snapshot": _snapshot_to_dict(latest) if latest else None,
    }


def _stats_to_dict(entry: api.StatsEntry) -> dict[str, Any]:
    path = Path(entry.abspath)
    return {
        "key": entry.key,
        "name": path.name or entry.key,
        "abspath": str(entry.abspath),
        "snapshot_count": entry.snapshot_count,
        "logical_bytes": entry.logical_bytes,
        "marginal_bytes": entry.marginal_bytes,
        "on_disk_bytes": entry.on_disk_bytes,
        "blob_count": entry.blob_count,
        "blob_bytes": entry.blob_bytes,
        "unique_logical_bytes": entry.unique_logical_bytes,
        "legacy_count": entry.legacy_count,
        "legacy_bytes": entry.legacy_bytes,
        "oldest": entry.oldest,
        "newest": entry.newest,
        "dedup_ratio": round(entry.dedup_ratio, 2),
        "largest": _snapshot_to_dict(entry.largest) if entry.largest else None,
    }


def _overview(config: RuntimeConfig) -> dict[str, Any]:
    sources = api.list_all(config=config)
    stats_rows = api.stats(config=config)
    total_snapshots = sum(len(entry.snapshots) for entry in sources)
    total_disk = sum(entry.on_disk_bytes for entry in stats_rows)
    total_logical = sum(entry.logical_bytes for entry in stats_rows)
    total_unique = sum(
        entry.unique_logical_bytes or entry.blob_bytes or entry.on_disk_bytes
        for entry in stats_rows
    )
    dedup_ratio = round(total_logical / total_unique, 2) if total_unique > 0 else 1.0
    latest: SnapshotMeta | None = None
    for source in sources:
        if source.snapshots and (
            latest is None or source.snapshots[0].created > latest.created
        ):
            latest = source.snapshots[0]
    return {
        "total_snapshots": total_snapshots,
        "total_sources": len(sources),
        "total_storage": format_size(total_disk),
        "total_storage_bytes": total_disk,
        "total_logical_bytes": total_logical,
        "dedup_ratio": dedup_ratio,
        "latest_snapshot": _snapshot_to_dict(latest) if latest else None,
    }


def _query_first(query: dict[str, list[str]], name: str, default: str = "") -> str:
    values = query.get(name)
    if not values:
        return default
    return values[0]


def _query_bool(query: dict[str, list[str]], name: str, default: bool = False) -> bool:
    raw = _query_first(query, name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def _resolve_request_path(raw: str) -> Path:
    return resolve_path(raw or ".")


class SnapzWebHandler(BaseHTTPRequestHandler):
    server: SnapzWebServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_empty(HTTPStatus.NO_CONTENT)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path in {"/", "/index.html"}:
                self._send_html(CLIENT_WEB_HTML)
            elif parsed.path == "/api/health":
                self._send_json({
                    "status": "ok",
                    "service": "snapz-client-web",
                    "version": __version__,
                    "root": str(self.server.config.root),
                })
            elif parsed.path == "/api/overview":
                self._send_json(_overview(self.server.config))
            elif parsed.path == "/api/sources":
                sources = api.list_all(config=self.server.config)
                self._send_json({"sources": [_source_to_dict(entry) for entry in sources]})
            elif parsed.path == "/api/stats":
                rows = api.stats(config=self.server.config)
                self._send_json({"stats": [_stats_to_dict(entry) for entry in rows]})
            elif parsed.path == "/api/snapshots":
                self._handle_list_snapshots(query)
            elif parsed.path.startswith("/api/snapshots/"):
                self._handle_get_snapshot(parsed.path, query)
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        body = self._read_json_body()
        try:
            if parsed.path == "/api/snapshots":
                self._handle_create_snapshot(body)
            elif parsed.path.startswith("/api/snapshots/"):
                self._handle_snapshot_action(parsed.path, query, body)
            elif parsed.path == "/api/sources/init":
                self._handle_init_source(body)
            elif parsed.path == "/api/gc":
                self._handle_gc(body)
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path.startswith("/api/snapshots/"):
                name = self._snapshot_name_from_path(parsed.path)
                path = _resolve_request_path(_query_first(query, "path", "."))
                removed = api.delete(path, name, config=self.server.config)
                if not removed:
                    self._send_json(
                        {"error": f"no snapshot named {name!r}"},
                        HTTPStatus.NOT_FOUND,
                    )
                    return
                self._send_json({"ok": True, "message": f"Snapshot deleted: {name}"})
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _handle_list_snapshots(self, query: dict[str, list[str]]) -> None:
        path = _resolve_request_path(_query_first(query, "path", "."))
        show_auto = _query_bool(query, "show_auto", False)
        snapshots = api.list_snapshots(path, config=self.server.config)
        visible = snapshots if show_auto else [
            snapshot for snapshot in snapshots if not is_auto_snapshot(snapshot.name)
        ]
        self._send_json({
            "path": str(path),
            "show_auto": show_auto,
            "hidden_auto": len(snapshots) - len(visible),
            "snapshots": [_snapshot_to_dict(snapshot) for snapshot in visible],
        })

    def _handle_get_snapshot(
        self,
        path_info: str,
        query: dict[str, list[str]],
    ) -> None:
        name = self._snapshot_name_from_path(path_info)
        path = _resolve_request_path(_query_first(query, "path", "."))
        snapshot = api.show(path, name, config=self.server.config)
        if snapshot is None:
            self._send_json(
                {"error": f"no snapshot named {name!r}"},
                HTTPStatus.NOT_FOUND,
            )
            return
        self._send_json({"snapshot": _snapshot_to_dict(snapshot)})

    def _handle_create_snapshot(self, body: dict[str, Any]) -> None:
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            self._send_json({"error": "path is required"}, HTTPStatus.BAD_REQUEST)
            return
        name_raw = body.get("name")
        name = str(name_raw).strip() if name_raw is not None else ""
        outcome = api.save(
            _resolve_request_path(raw_path),
            name or None,
            config=self.server.config,
            include_large=bool(body.get("include_large", False)),
            overwrite=bool(body.get("overwrite", False)),
            note=str(body.get("note") or ""),
        )
        self._send_json(
            {
                "snapshot": _snapshot_to_dict(outcome.snapshot),
                "message": f"Snapshot created: {outcome.snapshot.name}",
            },
            HTTPStatus.CREATED,
        )

    def _handle_snapshot_action(
        self,
        path_info: str,
        query: dict[str, list[str]],
        body: dict[str, Any],
    ) -> None:
        parts = [unquote(part) for part in path_info.split("/") if part]
        if len(parts) < 3:
            self._send_json({"error": "snapshot action required"}, HTTPStatus.NOT_FOUND)
            return
        name = parts[2]
        action = parts[3] if len(parts) >= 4 else ""
        path = _resolve_request_path(_query_first(query, "path", "."))

        if action == "restore":
            outcome = api.restore(
                path,
                name,
                config=self.server.config,
                auto_save=bool(body.get("auto_save", True)),
                clean=bool(body.get("clean", False)),
            )
            self._send_json({
                "ok": True,
                "message": f"Snapshot restored: {name}",
                "extracted_count": outcome.extracted_count,
                "cleaned_count": outcome.cleaned_count,
                "pre_restore": (
                    _snapshot_to_dict(outcome.pre_restore)
                    if outcome.pre_restore else None
                ),
            })
        elif action == "rename":
            new_name = str(body.get("new_name") or "").strip()
            if not new_name:
                self._send_json({"error": "new_name is required"}, HTTPStatus.BAD_REQUEST)
                return
            renamed = api.rename(path, name, new_name, config=self.server.config)
            if not renamed:
                self._send_json(
                    {"error": f"no snapshot named {name!r}"},
                    HTTPStatus.NOT_FOUND,
                )
                return
            self._send_json({"ok": True, "message": f"Snapshot renamed: {new_name}"})
        elif action == "protect":
            snapshot = api.protect(path, name, config=self.server.config)
            self._send_json({"snapshot": _snapshot_to_dict(snapshot)})
        elif action == "unprotect":
            snapshot = api.unprotect(path, name, config=self.server.config)
            self._send_json({"snapshot": _snapshot_to_dict(snapshot)})
        else:
            self._send_json({"error": "unknown snapshot action"}, HTTPStatus.NOT_FOUND)

    def _handle_init_source(self, body: dict[str, Any]) -> None:
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            self._send_json({"error": "path is required"}, HTTPStatus.BAD_REQUEST)
            return
        outcome = api.init_source(
            _resolve_request_path(raw_path),
            config=self.server.config,
            force=bool(body.get("force", False)),
        )
        self._send_json({
            "source": str(outcome.source),
            "marker_path": str(outcome.marker_path),
            "marker_id": outcome.marker_id,
            "created": outcome.created,
            "message": f"Source initialized: {outcome.source}",
        })

    def _handle_gc(self, body: dict[str, Any]) -> None:
        path_raw = str(body.get("path") or ".")
        all_dirs = bool(body.get("all_dirs", False))
        result = api.gc(
            None if all_dirs else _resolve_request_path(path_raw),
            all_dirs=all_dirs,
            dry_run=bool(body.get("dry_run", False)),
            rebuild_index=bool(body.get("rebuild_index", False)),
            config=self.server.config,
        )
        action = "Would reclaim" if result.dry_run else "Reclaimed"
        self._send_json({
            "blobs_removed": result.blobs_removed,
            "bytes_freed": result.bytes_freed,
            "dirs_scanned": result.dirs_scanned,
            "dry_run": result.dry_run,
            "message": (
                f"{action} {format_size(result.bytes_freed)} "
                f"from {result.blobs_removed} blob(s)."
            ),
        })

    def _snapshot_name_from_path(self, path_info: str) -> str:
        parts = [unquote(part) for part in path_info.split("/") if part]
        if len(parts) < 3:
            raise ValueError("snapshot name is required")
        return parts[2]

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("request body must be JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def _send_empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Allow-Origin", "http://localhost")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_json(
        self,
        payload: Any,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        raw = (
            json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)


def create_server(
    host: str,
    port: int,
    *,
    config: RuntimeConfig | None = None,
) -> SnapzWebServer:
    return SnapzWebServer((host, int(port)), SnapzWebHandler, config=config)


def is_loopback_host(host: str) -> bool:
    if host in {"", "localhost"}:
        return True
    try:
        return any(
            address[4][0].startswith("127.")
            or address[4][0] == "::1"
            for address in socket.getaddrinfo(host, None)
        )
    except socket.gaierror:
        return False


def server_url(server: ThreadingHTTPServer, host: str) -> str:
    port = int(server.server_address[1])
    display_host = "127.0.0.1" if host in {"", "0.0.0.0", "::"} else host
    return f"http://{display_host}:{port}/"


def run_server(
    host: str,
    port: int,
    *,
    config: RuntimeConfig | None = None,
) -> None:
    server = create_server(host, port, config=config)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def run_in_thread(
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    config: RuntimeConfig | None = None,
) -> tuple[SnapzWebServer, threading.Thread]:
    server = create_server(host, port, config=config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread

