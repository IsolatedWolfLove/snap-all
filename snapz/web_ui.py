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

from snapz import __version__, api, remote
from snapz.config import RuntimeConfig, default_config
from snapz.preferences import get_config_value
from snapz.store import DirEntry, SnapshotMeta
from snapz.util import format_size, is_auto_snapshot, now_iso, resolve_path


REMOTE_SYNC_STATUS_FILENAME = "_remote_sync_status.json"
REMOTE_SYNC_IDLE_STATUS = {
    "status": "idle",
    "phase": "idle",
    "source_id": "",
    "key": "",
    "display_name": "",
    "bytes_sent": 0,
    "bytes_total": 0,
    "progress_percent": 0.0,
    "speed_bps": 0.0,
    "eta_seconds": None,
    "last_sync_at": "",
    "started_at": "",
    "updated_at": "",
    "finished_at": "",
    "remote_only": False,
    "server_url": "",
    "message": "Not started",
    "error": "",
}


CLIENT_WEB_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>snapz Client</title>
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
    button, input, select, textarea {
      font: inherit;
      outline: none;
    }
    button {
      min-height: 2.35rem;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 .85rem;
      background: var(--panel-subtle);
      color: var(--ink);
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
      transition: background-color .15s ease, border-color .15s ease, color .15s ease;
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
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #12151c;
      color: var(--ink);
      transition: background-color .15s ease, border-color .15s ease;
    }
    input, select {
      height: 2.35rem;
      padding: 0 .75rem;
    }
    select {
      width: auto;
      min-width: 7rem;
    }
    textarea {
      min-height: 4.5rem;
      resize: vertical;
      padding: .65rem .75rem;
    }
    input:focus, select:focus, textarea:focus {
      border-color: var(--primary);
      background: #151a23;
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
      background: #111318;
    }
    .brand h1 {
      font-size: 1.35rem;
      font-weight: 700;
      letter-spacing: 0;
    }
    .brand p {
      margin-top: .2rem;
      color: var(--muted);
      font-size: .8rem;
      font-weight: 500;
    }
    main {
      width: min(1320px, 100%);
      margin: 0 auto;
      padding: 2rem 2rem 3rem;
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
      gap: .4rem;
      background: var(--panel-subtle);
      padding: 4px;
      border-radius: 8px;
      border: 1px solid var(--line);
    }
    .nav button {
      min-height: 2.1rem;
      border: none;
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
      padding: 0 1rem;
      font-size: .88rem;
    }
    .nav button:hover {
      color: var(--ink);
      background: #242832;
    }
    .nav button.active {
      background: #2a303b;
      color: #fff;
    }
    .header-actions {
      display: flex;
      flex-wrap: wrap;
      gap: .75rem;
      align-items: center;
      justify-content: flex-end;
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 1.25rem;
      align-items: center;
      margin-bottom: 1.5rem;
      padding: 1.5rem;
      border-color: var(--info-line);
      background: #151b28;
      box-shadow: var(--shadow);
    }
    .eyebrow {
      color: var(--primary-strong);
      font-size: .75rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .hero h2 {
      margin-top: .25rem;
      font-size: 1.75rem;
      font-weight: 700;
      line-height: 1.2;
      letter-spacing: 0;
    }
    .hero p {
      margin-top: .3rem;
      color: var(--muted);
      font-size: .92rem;
      line-height: 1.5;
    }
    .field {
      display: grid;
      gap: .4rem;
      color: var(--muted);
      font-size: .82rem;
      font-weight: 600;
      letter-spacing: 0;
    }
    .path-bar {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) auto;
      gap: .75rem;
      align-items: end;
      margin-bottom: 1.5rem;
      padding: 1.25rem;
    }
    .notice {
      min-height: 2.5rem;
      display: flex;
      align-items: center;
      margin-bottom: 1.5rem;
      padding: .75rem 1.1rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--muted);
      font-size: .88rem;
    }
    .notice.error {
      border-color: var(--danger-line);
      background: var(--danger-bg);
      color: var(--danger);
    }
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
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
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(350px, .44fr);
      gap: 1.5rem;
      align-items: start;
    }
    .stack {
      display: grid;
      gap: 1.5rem;
    }
    .panel-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      padding: 1.25rem;
      border-bottom: 1px solid var(--line);
    }
    .panel-title {
      display: grid;
      gap: .2rem;
      min-width: 0;
    }
    .panel-title h2,
    .panel-title h3 {
      font-size: 1.1rem;
      font-weight: 700;
      letter-spacing: 0;
    }
    .panel-title p {
      color: var(--muted);
      font-size: .78rem;
      line-height: 1.4;
    }
    .panel-body {
      padding: 1.25rem;
    }
    .form-grid {
      display: grid;
      gap: 1rem;
    }
    .form-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 1rem;
    }
    .table-wrap { overflow-x: auto; }
    table {
      width: 100%;
      min-width: 880px;
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
    tr.selected td {
      background: var(--info-bg);
      border-bottom-color: rgba(59, 130, 246, 0.2);
    }
    .cell-title {
      font-weight: 600;
      color: #fff;
    }
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
    .actions button {
      min-height: 1.85rem;
      padding: 0 .75rem;
      font-size: .78rem;
      border-radius: 8px;
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
    .empty {
      padding: 3rem 1rem;
      text-align: center;
      color: var(--muted);
      font-size: .9rem;
    }
    .view { display: none; }
    .view.active { display: block; }

    .storage-bars {
      display: grid;
      gap: 1rem;
    }
    .sync-metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: .75rem;
      margin-top: 1rem;
    }
    .sync-metric {
      padding: .85rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-subtle);
    }
    .sync-metric span {
      display: block;
      color: var(--muted);
      font-size: .72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .sync-metric strong {
      display: block;
      margin-top: .3rem;
      font-size: 1rem;
      font-weight: 700;
      line-height: 1.3;
      color: #fff;
    }
    .sync-progress {
      display: grid;
      gap: .5rem;
    }
    .sync-progress-head {
      display: flex;
      justify-content: space-between;
      gap: .75rem;
      color: var(--muted);
      font-size: .78rem;
      font-weight: 600;
    }
    .storage-row {
      display: grid;
      gap: .4rem;
    }
    .bar {
      height: .5rem;
      overflow: hidden;
      border-radius: 6px;
      background: #242832;
      border: 1px solid var(--line);
    }
    .bar span {
      display: block;
      height: 100%;
      border-radius: inherit;
      background: var(--primary);
    }
    .hidden { display: none !important; }

    @media (max-width: 980px) {
      header {
        align-items: flex-start;
        flex-direction: column;
        padding: 1rem;
      }
      main { padding: 1.25rem; }
      .hero,
      .path-bar,
      .grid {
        grid-template-columns: 1fr;
      }
      .stats-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 640px) {
      main { padding: 1rem; }
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
      <p data-i18n="brand.subtitle">Local snapshot control panel</p>
    </div>
    <div class="header-actions">
      <nav class="nav" aria-label="Primary" data-i18n-attr="aria-label:nav.primary">
        <button class="active" data-i18n="nav.dashboard" data-view="dashboard" type="button">Dashboard</button>
        <button data-i18n="nav.snapshots" data-view="snapshots" type="button">Snapshots</button>
        <button data-i18n="nav.sources" data-view="sources" type="button">Sources</button>
        <button data-i18n="nav.storage" data-view="storage" type="button">Storage</button>
      </nav>
      <label class="field" style="gap: .25rem;">
        <span data-i18n="language.label">Language</span>
        <select id="languageSelect" aria-label="Language" data-i18n-attr="aria-label:language.label">
          <option value="en">English</option>
          <option value="zh">中文</option>
        </select>
      </label>
    </div>
  </header>

  <main>
    <section class="panel hero">
      <div>
        <div class="eyebrow" data-i18n="hero.eyebrow">Local web UI</div>
        <h2 data-i18n="hero.title">snapz client workspace</h2>
        <p data-i18n="hero.copy">Manage local sources, snapshots, restore points, and storage from the browser.</p>
      </div>
      <div class="toolbar">
        <span id="healthBadge" class="badge warn" data-i18n="status.checkingApi">Checking API</span>
        <button id="refreshButton" class="primary" data-i18n="action.refresh" type="button">Refresh</button>
      </div>
    </section>

    <section class="panel path-bar">
      <label class="field">
        <span data-i18n="field.activeSourcePath">Active source path</span>
        <input id="pathInput" autocomplete="off" data-i18n-attr="placeholder:placeholder.path" placeholder="/path/to/project">
      </label>
      <button id="loadPathButton" class="primary" data-i18n="action.loadSource" type="button">Load source</button>
    </section>

    <div id="notice" class="notice" data-i18n="notice.ready">Ready.</div>

    <section id="dashboard" class="view active">
      <section class="stats-grid" id="statsGrid"></section>

      <section class="grid">
        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <h2 data-i18n="section.recentSnapshots">Recent snapshots</h2>
              <p data-i18n="section.recentSnapshots.copy">Latest restore points for the active source.</p>
            </div>
            <button data-i18n="action.viewAll" data-view-target="snapshots" type="button">View all</button>
          </div>
          <div id="recentSnapshots"></div>
        </section>

        <div class="stack">
          <section class="panel">
            <div class="panel-head">
              <div class="panel-title">
                <h2 data-i18n="section.createSnapshot">Create snapshot</h2>
                <p data-i18n="section.createSnapshot.copy">Capture the current state of a local directory.</p>
              </div>
            </div>
            <div class="panel-body">
              <form id="createSnapshotForm" class="form-grid">
                <label class="field">
                  <span data-i18n="field.path">Path</span>
                  <input name="path" data-i18n-attr="placeholder:placeholder.path" placeholder="/path/to/project" required>
                </label>
                <label class="field">
                  <span data-i18n="field.name">Name</span>
                  <input name="name" data-i18n-attr="placeholder:placeholder.snapshotName" placeholder="auto generated if empty">
                </label>
                <label class="field">
                  <span data-i18n="field.note">Note</span>
                  <textarea name="note" data-i18n-attr="placeholder:placeholder.note" placeholder="Optional context"></textarea>
                </label>
                <div class="toolbar">
                  <button class="primary" data-i18n="action.createSnapshot" type="submit">Create snapshot</button>
                  <label class="toolbar" style="color: var(--muted); font-size: .82rem;">
                    <input name="include_large" type="checkbox" style="width: 1rem; height: 1rem;">
                    <span data-i18n="field.includeLarge">Include large files</span>
                  </label>
                </div>
              </form>
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <div class="panel-title">
                <h2 data-i18n="section.remotePush">Remote push</h2>
                <p data-i18n="section.remotePush.copy">Upload local snapshots to the configured snapz-server.</p>
              </div>
              <button id="pushRemoteButton" class="primary" data-i18n="action.pushNow" type="button">Push now</button>
            </div>
            <div id="remoteSyncPanel" class="panel-body"></div>
          </section>
        </div>
      </section>
    </section>

    <section id="snapshots" class="view">
      <section class="panel">
        <div class="panel-head">
            <div class="panel-title">
              <h2 data-i18n="section.snapshots">Snapshots</h2>
              <p data-i18n="section.snapshots.copy">Create, inspect, restore, protect, rename, or delete local snapshots.</p>
            </div>
            <div class="toolbar">
            <input id="snapshotFilter" data-i18n-attr="placeholder:placeholder.snapshotFilter" placeholder="Filter name, note, or tag" style="width: min(320px, 100%);">
            <label class="toolbar" style="color: var(--muted); font-size: .82rem;">
              <input id="showAutoInput" type="checkbox" style="width: 1rem; height: 1rem;">
              <span data-i18n="field.showAuto">Show auto snapshots</span>
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
              <h2 data-i18n="section.sources">Sources</h2>
              <p data-i18n="section.sources.copy">Directories with snapshots in the local snapz store.</p>
            </div>
          </div>
          <div id="sourcesTable"></div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <h2 data-i18n="section.initSource">Initialize source</h2>
              <p data-i18n="section.initSource.copy">Add a stable source marker for move detection.</p>
            </div>
          </div>
          <div class="panel-body">
            <form id="initSourceForm" class="form-grid">
              <label class="field">
                <span data-i18n="field.path">Path</span>
                <input name="path" data-i18n-attr="placeholder:placeholder.path" placeholder="/path/to/project" required>
              </label>
              <button class="primary" data-i18n="action.initialize" type="submit">Initialize</button>
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
              <h2 data-i18n="section.storageBreakdown">Storage breakdown</h2>
              <p data-i18n="section.storageBreakdown.copy">On-disk usage grouped by source.</p>
            </div>
          </div>
          <div id="storagePanel" class="panel-body"></div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <h2 data-i18n="section.maintenance">Maintenance</h2>
              <p data-i18n="section.maintenance.copy">Preview local garbage collection for the active source.</p>
            </div>
          </div>
          <div class="panel-body stack">
            <button id="previewGcButton" data-i18n="action.previewGc" type="button">Preview GC</button>
            <button id="runGcButton" class="danger" data-i18n="action.runGc" type="button">Run GC</button>
          </div>
        </section>
      </section>
    </section>
  </main>

  <script>
    const LANG_STORAGE_KEY = 'snapzClientWebLang';
    const I18N = {
      en: {
        'action.createSnapshot': 'Create snapshot',
        'action.delete': 'Delete',
        'action.details': 'Details',
        'action.initialize': 'Initialize',
        'action.loadSource': 'Load source',
        'action.open': 'Open',
        'action.previewGc': 'Preview GC',
        'action.protect': 'Protect',
        'action.pushNow': 'Push now',
        'action.refresh': 'Refresh',
        'action.rename': 'Rename',
        'action.restore': 'Restore',
        'action.runGc': 'Run GC',
        'action.unprotect': 'Unprotect',
        'action.viewAll': 'View all',
        'brand.subtitle': 'Local snapshot control panel',
        'field.activeSourcePath': 'Active source path',
        'field.includeLarge': 'Include large files',
        'field.name': 'Name',
        'field.note': 'Note',
        'field.path': 'Path',
        'field.showAuto': 'Show auto snapshots',
        'hero.copy': 'Manage local sources, snapshots, restore points, and storage from the browser.',
        'hero.eyebrow': 'Local web UI',
        'hero.title': 'snapz client workspace',
        'label.created': 'Created',
        'label.compression': 'Compression',
        'label.dedupRatio': 'Dedup ratio',
        'label.eta': 'ETA',
        'label.files': 'Files',
        'label.lastSync': 'Last sync',
        'label.lastUsed': 'Last used',
        'label.logicalToUnique': 'Logical to unique bytes',
        'label.notLoggedIn': 'Not logged in',
        'label.onDisk': 'On disk',
        'label.protected': 'Protected',
        'label.ready': 'Ready',
        'label.remote': 'Remote',
        'label.size': 'Size',
        'label.snapshot': 'Snapshot',
        'label.snapshots': 'Snapshots',
        'label.source': 'Source',
        'label.sources': 'Sources',
        'label.speed': 'Speed',
        'label.status': 'Status',
        'label.storage': 'Storage',
        'label.storageUsed': 'Storage used',
        'label.totalSnapshots': 'Total snapshots',
        'label.transferred': 'Transferred',
        'language.label': 'Language',
        'nav.dashboard': 'Dashboard',
        'nav.primary': 'Primary',
        'nav.snapshots': 'Snapshots',
        'nav.sources': 'Sources',
        'nav.storage': 'Storage',
        'notice.gcComplete': 'GC complete.',
        'notice.loaded': 'Loaded current snapz state.',
        'notice.noSnapshots': 'No snapshots found.',
        'notice.noSources': 'No sources found.',
        'notice.noStorage': 'No storage data yet.',
        'notice.ready': 'Ready.',
        'notice.remotePushRequested': 'Remote push requested.',
        'notice.runLogin': 'Run snapz login first.',
        'notice.snapshotCreated': 'Snapshot created: {name}',
        'notice.snapshotDeleted': 'Snapshot deleted.',
        'notice.snapshotProtected': 'Snapshot protected.',
        'notice.snapshotRenamed': 'Snapshot renamed.',
        'notice.snapshotRestored': 'Restored {name}.',
        'notice.snapshotUnprotected': 'Snapshot unprotected.',
        'notice.sourceInitialized': 'Source initialized.',
        'placeholder.note': 'Optional context',
        'placeholder.path': '/path/to/project',
        'placeholder.snapshotFilter': 'Filter name, note, or tag',
        'placeholder.snapshotName': 'auto generated if empty',
        'prompt.deleteSnapshot': 'Delete snapshot {name}?',
        'prompt.gc': 'Run garbage collection for {path}?',
        'prompt.renameSnapshot': 'New snapshot name',
        'prompt.restore': 'Restore {name} over {path}?',
        'remote.localBlobs': 'local blobs retained',
        'remote.remoteOnly': 'remote_only enabled',
        'section.createSnapshot': 'Create snapshot',
        'section.createSnapshot.copy': 'Capture the current state of a local directory.',
        'section.initSource': 'Initialize source',
        'section.initSource.copy': 'Add a stable source marker for move detection.',
        'section.maintenance': 'Maintenance',
        'section.maintenance.copy': 'Preview local garbage collection for the active source.',
        'section.recentSnapshots': 'Recent snapshots',
        'section.recentSnapshots.copy': 'Latest restore points for the active source.',
        'section.remotePush': 'Remote push',
        'section.remotePush.copy': 'Upload local snapshots to the configured snapz-server.',
        'section.snapshots': 'Snapshots',
        'section.snapshots.copy': 'Create, inspect, restore, protect, rename, or delete local snapshots.',
        'section.sources': 'Sources',
        'section.sources.copy': 'Directories with snapshots in the local snapz store.',
        'section.storageBreakdown': 'Storage breakdown',
        'section.storageBreakdown.copy': 'On-disk usage grouped by source.',
        'status.checkingApi': 'Checking API',
        'stat.acrossSources': 'Across local sources',
        'stat.trackedDirs': 'Tracked directories',
        'table.actions': 'Actions',
      },
      zh: {
        'action.createSnapshot': '创建快照',
        'action.delete': '删除',
        'action.details': '详情',
        'action.initialize': '初始化',
        'action.loadSource': '加载源目录',
        'action.open': '打开',
        'action.previewGc': '预览清理',
        'action.protect': '保护',
        'action.pushNow': '立即推送',
        'action.refresh': '刷新',
        'action.rename': '重命名',
        'action.restore': '恢复',
        'action.runGc': '执行清理',
        'action.unprotect': '取消保护',
        'action.viewAll': '查看全部',
        'brand.subtitle': '本地快照控制台',
        'field.activeSourcePath': '当前源目录路径',
        'field.includeLarge': '包含大文件',
        'field.name': '名称',
        'field.note': '备注',
        'field.path': '路径',
        'field.showAuto': '显示自动快照',
        'hero.copy': '在浏览器中管理本地源目录、快照、恢复点和存储。',
        'hero.eyebrow': '本地 Web 界面',
        'hero.title': 'snapz 客户端工作台',
        'label.created': '创建时间',
        'label.compression': '压缩',
        'label.dedupRatio': '去重率',
        'label.eta': '预计剩余',
        'label.files': '文件数',
        'label.lastSync': '上次同步',
        'label.lastUsed': '上次使用',
        'label.logicalToUnique': '逻辑大小 / 唯一数据',
        'label.notLoggedIn': '未登录',
        'label.onDisk': '磁盘占用',
        'label.protected': '已保护',
        'label.ready': '就绪',
        'label.remote': '远端',
        'label.size': '大小',
        'label.snapshot': '快照',
        'label.snapshots': '快照',
        'label.source': '源目录',
        'label.sources': '源目录',
        'label.speed': '速度',
        'label.status': '状态',
        'label.storage': '存储',
        'label.storageUsed': '已用存储',
        'label.totalSnapshots': '快照总数',
        'label.transferred': '已传输',
        'language.label': '语言',
        'nav.dashboard': '概览',
        'nav.primary': '主导航',
        'nav.snapshots': '快照',
        'nav.sources': '源目录',
        'nav.storage': '存储',
        'notice.gcComplete': '清理完成。',
        'notice.loaded': '已加载当前 snapz 状态。',
        'notice.noSnapshots': '没有找到快照。',
        'notice.noSources': '没有找到源目录。',
        'notice.noStorage': '暂无存储数据。',
        'notice.ready': '就绪。',
        'notice.remotePushRequested': '已请求远端推送。',
        'notice.runLogin': '请先运行 snapz login。',
        'notice.snapshotCreated': '已创建快照：{name}',
        'notice.snapshotDeleted': '快照已删除。',
        'notice.snapshotProtected': '快照已保护。',
        'notice.snapshotRenamed': '快照已重命名。',
        'notice.snapshotRestored': '已恢复 {name}。',
        'notice.snapshotUnprotected': '快照已取消保护。',
        'notice.sourceInitialized': '源目录已初始化。',
        'placeholder.note': '可选说明',
        'placeholder.path': '/path/to/project',
        'placeholder.snapshotFilter': '按名称、备注或标签过滤',
        'placeholder.snapshotName': '留空则自动生成',
        'prompt.deleteSnapshot': '删除快照 {name}？',
        'prompt.gc': '对 {path} 执行垃圾清理？',
        'prompt.renameSnapshot': '新的快照名称',
        'prompt.restore': '将 {name} 恢复到 {path}？',
        'remote.localBlobs': '保留本地数据块',
        'remote.remoteOnly': '已启用 remote_only',
        'section.createSnapshot': '创建快照',
        'section.createSnapshot.copy': '捕获本地目录的当前状态。',
        'section.initSource': '初始化源目录',
        'section.initSource.copy': '添加稳定的源标记，用于目录移动检测。',
        'section.maintenance': '维护',
        'section.maintenance.copy': '预览当前源目录的本地垃圾清理。',
        'section.recentSnapshots': '最近快照',
        'section.recentSnapshots.copy': '当前源目录最近的恢复点。',
        'section.remotePush': '远端推送',
        'section.remotePush.copy': '将本地快照上传到已配置的 snapz-server。',
        'section.snapshots': '快照',
        'section.snapshots.copy': '创建、查看、恢复、保护、重命名或删除本地快照。',
        'section.sources': '源目录',
        'section.sources.copy': '本地 snapz 存储中包含快照的目录。',
        'section.storageBreakdown': '存储明细',
        'section.storageBreakdown.copy': '按源目录查看磁盘占用。',
        'status.checkingApi': '正在检查 API',
        'stat.acrossSources': '所有本地源目录',
        'stat.trackedDirs': '已跟踪目录',
        'table.actions': '操作',
      },
    };

    function preferredLanguage() {
      const saved = localStorage.getItem(LANG_STORAGE_KEY);
      if (saved === 'en' || saved === 'zh') return saved;
      return navigator.language && navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en';
    }

    const state = {
      health: null,
      overview: {},
      sources: [],
      snapshots: [],
      stats: [],
      remoteStatus: {},
      path: new URLSearchParams(location.search).get('path') || '.',
      filter: '',
      showAuto: false,
      lang: preferredLanguage(),
    };

    const el = (id) => document.getElementById(id);
    const qs = (selector) => document.querySelector(selector);

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
      renderAll();
      showNotice(t('notice.ready'));
    }

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

    function formatSpeed(value) {
      const speed = Number(value || 0);
      return `${formatBytes(speed)}/s`;
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
        badge.textContent = t('status.checkingApi');
        return;
      }
      badge.className = 'badge ok';
      badge.textContent = `API ${state.health.status} - ${state.health.version}`;
    }

    function renderStats() {
      const overview = state.overview || {};
      const items = [
        [t('label.totalSnapshots'), overview.total_snapshots || 0, t('stat.acrossSources'), 'sky'],
        [t('label.sources'), overview.total_sources || 0, t('stat.trackedDirs'), 'violet'],
        [t('label.storageUsed'), overview.total_storage || '0 B', t('label.onDisk'), 'emerald'],
        [t('label.dedupRatio'), `${overview.dedup_ratio || 1}x`, t('label.logicalToUnique'), 'amber'],
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
        return `<div class="empty">${t('notice.noSnapshots')}</div>`;
      }
      return `
        <div class="panel-body">
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style="width: 22%;">${t('label.snapshot')}</th>
                  <th style="width: 20%;">${t('label.created')}</th>
                  <th style="width: 14%;">${t('label.size')}</th>
                  <th style="width: 12%;">${t('label.files')}</th>
                  <th style="width: 12%;">${t('label.status')}</th>
                  <th style="width: 20%;">${t('table.actions')}</th>
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
                        ${snapshot.protected ? t('label.protected') : t('label.ready')}
                      </span>
                    </td>
                    <td>
                      <div class="actions">
                        <button data-action="details" data-name="${escapeHtml(snapshot.name)}" type="button">${t('action.details')}</button>
                        <button data-action="restore" data-name="${escapeHtml(snapshot.name)}" type="button">${t('action.restore')}</button>
                        <button data-action="rename" data-name="${escapeHtml(snapshot.name)}" type="button">${t('action.rename')}</button>
                        <button data-action="${snapshot.protected ? 'unprotect' : 'protect'}" data-name="${escapeHtml(snapshot.name)}" type="button">
                          ${snapshot.protected ? t('action.unprotect') : t('action.protect')}
                        </button>
                        <button class="danger" data-action="delete" data-name="${escapeHtml(snapshot.name)}" type="button">${t('action.delete')}</button>
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
        el('sourcesTable').innerHTML = `<div class="empty">${t('notice.noSources')}</div>`;
        return;
      }
      const sync = state.remoteStatus || {};
      el('sourcesTable').innerHTML = `
        <div class="panel-body">
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style="width: 30%;">${t('label.source')}</th>
                  <th style="width: 14%;">${t('label.snapshots')}</th>
                  <th style="width: 18%;">${t('label.lastUsed')}</th>
                  <th style="width: 14%;">${t('label.storage')}</th>
                  <th style="width: 12%;">${t('label.remote')}</th>
                  <th style="width: 14%;">${t('table.actions')}</th>
                </tr>
              </thead>
              <tbody>
                ${state.sources.map((source) => {
                  const isCurrentSync = sync.key && sync.key === source.key;
                  const pct = Math.max(0, Math.min(100, Number(sync.progress_percent || 0)));
                  return `
                    <tr class="${source.abspath === state.path ? 'selected' : ''}">
                      <td>
                        <div class="cell-title">${escapeHtml(source.name || source.abspath)}</div>
                        <div class="cell-subtitle">${escapeHtml(source.abspath)}</div>
                      </td>
                      <td><span class="badge">${source.snapshot_count} ${t('label.snapshots').toLowerCase()}</span></td>
                      <td>${formatDate(source.last_used)}</td>
                      <td>${formatBytes(source.on_disk_bytes || 0)}</td>
                      <td>
                        ${isCurrentSync ? `
                          <span class="badge ${sync.status === 'failed' ? 'bad' : sync.status === 'completed' ? 'ok' : 'warn'}">${escapeHtml(sync.phase || sync.status)}</span>
                          <div class="cell-subtitle">${pct.toFixed(0)}% · ${formatSpeed(sync.speed_bps)}</div>
                        ` : '<span class="badge">-</span>'}
                      </td>
                      <td>
                        <button data-action="select-source" data-path="${escapeHtml(source.abspath)}" type="button">${t('action.open')}</button>
                      </td>
                    </tr>
                  `;
                }).join('')}
              </tbody>
            </table>
          </div>
        </div>
      `;
    }

    function renderRemoteSync() {
      const sync = state.remoteStatus || {};
      const configured = Boolean(sync.configured);
      const running = Boolean(sync.running || sync.status === 'running');
      const pct = Math.max(0, Math.min(100, Number(sync.progress_percent || 0)));
      const badgeClass = !configured
        ? 'warn'
        : sync.status === 'failed'
          ? 'bad'
          : running
            ? 'warn'
            : sync.status === 'completed'
              ? 'ok'
              : '';
      el('pushRemoteButton').disabled = !configured || running;
      el('remoteSyncPanel').innerHTML = `
        <div class="toolbar" style="justify-content: space-between; margin-bottom: .75rem;">
          <span class="badge ${badgeClass}">
            ${configured ? escapeHtml(sync.status || 'idle') : t('label.notLoggedIn')}
          </span>
          <span class="cell-subtitle">${sync.remote_only ? t('remote.remoteOnly') : t('remote.localBlobs')}</span>
        </div>
        <div class="sync-progress">
          <div class="sync-progress-head">
            <span>${escapeHtml(sync.display_name || sync.server_url || '-')}</span>
            <span>${pct.toFixed(1)}%</span>
          </div>
          <div class="bar"><span style="width: ${pct}%;"></span></div>
        </div>
        <div class="sync-metrics">
          <div class="sync-metric">
            <span>${t('label.speed')}</span>
            <strong>${formatSpeed(sync.speed_bps)}</strong>
          </div>
          <div class="sync-metric">
            <span>${t('label.eta')}</span>
            <strong>${formatEta(sync.eta_seconds)}</strong>
          </div>
          <div class="sync-metric">
            <span>${t('label.transferred')}</span>
            <strong>${formatBytes(sync.bytes_sent)} / ${formatBytes(sync.bytes_total)}</strong>
          </div>
          <div class="sync-metric">
            <span>${t('label.lastSync')}</span>
            <strong>${formatDate(sync.last_sync_at)}</strong>
          </div>
        </div>
        <div class="cell-subtitle" style="margin-top: .75rem;">
          ${escapeHtml(sync.message || (configured ? t('notice.ready') : t('notice.runLogin')))}
        </div>
      `;
    }

    function renderStorage() {
      const total = Math.max(1, Number(state.overview.total_storage_bytes || 0));
      if (state.stats.length === 0) {
        el('storagePanel').innerHTML = `<div class="empty">${t('notice.noStorage')}</div>`;
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
                  <span class="cell-subtitle">${formatBytes(entry.on_disk_bytes)} / ${entry.snapshot_count} ${t('label.snapshots').toLowerCase()}</span>
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
      renderRemoteSync();
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
        const [health, overview, sources, snapshots, stats, remoteStatus] = await Promise.all([
          api('/api/health'),
          api('/api/overview'),
          api('/api/sources'),
          api(`/api/snapshots?${params}`),
          api('/api/stats'),
          api('/api/remote/status'),
        ]);
        state.health = health;
        state.overview = overview;
        state.sources = sources.sources || [];
        state.snapshots = snapshots.snapshots || [];
        state.path = snapshots.path || state.path;
        state.stats = stats.stats || [];
        state.remoteStatus = remoteStatus.status || {};
        renderAll();
        showNotice(t('notice.loaded'));
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    async function loadRemoteStatus() {
      try {
        const result = await api('/api/remote/status');
        state.remoteStatus = result.status || {};
        renderRemoteSync();
        renderSources();
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    async function pushRemoteNow() {
      try {
        const result = await api('/api/remote/push', { method: 'POST' });
        state.remoteStatus = result.status || {};
        renderRemoteSync();
        showNotice(result.message || t('notice.remotePushRequested'));
        pollRemoteStatus();
      } catch (error) {
        showNotice(error.message, true);
      }
    }

    let remotePollTimer = null;
    function pollRemoteStatus() {
      if (remotePollTimer) {
        clearTimeout(remotePollTimer);
      }
      remotePollTimer = setTimeout(async () => {
        await loadRemoteStatus();
        if (state.remoteStatus.running || state.remoteStatus.status === 'running') {
          pollRemoteStatus();
        }
      }, 800);
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
        showNotice(t('notice.snapshotCreated', { name: result.snapshot.name }));
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
            `${t('field.name')}: ${snapshot.name}`,
            `${t('label.source')}: ${snapshot.source}`,
            `${t('label.created')}: ${formatDate(snapshot.created)}`,
            `${t('label.size')}: ${formatBytes(snapshot.size_bytes)}`,
            `${t('label.files')}: ${Number(snapshot.file_count || 0).toLocaleString()}`,
            `${t('label.compression')}: ${snapshot.compression}`,
            `${t('field.note')}: ${snapshot.note || '-'}`,
          ].join('\n'));
        } else if (button.dataset.action === 'restore') {
          if (!confirm(t('prompt.restore', { name, path: state.path }))) return;
          const data = await api(`/api/snapshots/${encodeURIComponent(name)}/restore?path=${path}`, {
            method: 'POST',
            body: JSON.stringify({ auto_save: true, clean: false }),
          });
          await loadAll();
          showNotice(data.message || t('notice.snapshotRestored', { name }));
        } else if (button.dataset.action === 'rename') {
          const next = prompt(t('prompt.renameSnapshot'), name);
          if (!next || next.trim() === name) return;
          await api(`/api/snapshots/${encodeURIComponent(name)}/rename?path=${path}`, {
            method: 'POST',
            body: JSON.stringify({ new_name: next.trim() }),
          });
          await loadAll();
          showNotice(t('notice.snapshotRenamed'));
        } else if (button.dataset.action === 'protect' || button.dataset.action === 'unprotect') {
          await api(`/api/snapshots/${encodeURIComponent(name)}/${button.dataset.action}?path=${path}`, {
            method: 'POST',
          });
          await loadAll();
          showNotice(button.dataset.action === 'protect' ? t('notice.snapshotProtected') : t('notice.snapshotUnprotected'));
        } else if (button.dataset.action === 'delete') {
          if (!confirm(t('prompt.deleteSnapshot', { name }))) return;
          await api(`/api/snapshots/${encodeURIComponent(name)}?path=${path}`, {
            method: 'DELETE',
          });
          await loadAll();
          showNotice(t('notice.snapshotDeleted'));
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
        showNotice(data.message || t('notice.sourceInitialized'));
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
        showNotice(data.message || t('notice.gcComplete'));
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
    el('pushRemoteButton').addEventListener('click', pushRemoteNow);
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
      if (confirm(t('prompt.gc', { path: state.path }))) {
        runGc(false);
      }
    });

    el('languageSelect').addEventListener('change', (event) => {
      setLanguage(event.target.value);
    });

    state.path = el('pathInput').value = state.path;
    applyLanguage();
    loadAll().then(() => {
      if (state.remoteStatus.running || state.remoteStatus.status === 'running') {
        pollRemoteStatus();
      }
    });
  </script>
</body>
</html>
"""


class SnapzWebServer(ThreadingHTTPServer):
    """HTTP server carrying snapz runtime configuration."""

    daemon_threads = True
    push_lock: threading.Lock
    push_thread: threading.Thread | None

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        config: RuntimeConfig | None = None,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.config = config or default_config()
        self.push_lock = threading.Lock()
        self.push_thread = None


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


def _remote_status_path(config: RuntimeConfig) -> Path:
    return Path(config.root) / REMOTE_SYNC_STATUS_FILENAME


def _configured_remote_only(config: RuntimeConfig) -> bool:
    try:
        return bool(get_config_value(Path(config.root), "remote_only"))
    except (KeyError, ValueError):
        return bool(config.remote_only)


def _remote_auth_summary(config: RuntimeConfig) -> dict[str, Any]:
    try:
        auth = remote.load_auth(config)
    except FileNotFoundError:
        return {
            "configured": False,
            "server_url": "",
            "tenant": "",
            "username": "",
            "device_id": "",
            "device_name": "",
        }
    return {
        "configured": True,
        "server_url": auth.server_url,
        "tenant": auth.tenant,
        "username": auth.username,
        "device_id": auth.device_id,
        "device_name": auth.device_name,
    }


def _read_remote_sync_status(config: RuntimeConfig) -> dict[str, Any]:
    status = dict(REMOTE_SYNC_IDLE_STATUS)
    path = _remote_status_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, json.JSONDecodeError):
        data = {
            "status": "failed",
            "phase": "failed",
            "message": "Could not read remote sync status",
        }
    if isinstance(data, dict):
        status.update(data)
    status["remote_only"] = _configured_remote_only(config)
    status.update(_remote_auth_summary(config))
    return status


def _write_remote_sync_status(config: RuntimeConfig, status: dict[str, Any]) -> None:
    path = _remote_status_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _read_remote_sync_status(config)
    current.update(status)
    current["updated_at"] = str(status.get("updated_at") or now_iso())
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _start_remote_push(server: SnapzWebServer) -> tuple[bool, dict[str, Any]]:
    with server.push_lock:
        if server.push_thread is not None and server.push_thread.is_alive():
            status = _read_remote_sync_status(server.config)
            status["running"] = True
            return False, status
        started_at = now_iso()
        _write_remote_sync_status(
            server.config,
            {
                "status": "running",
                "phase": "starting",
                "source_id": "",
                "key": "",
                "display_name": "",
                "bytes_sent": 0,
                "bytes_total": 0,
                "progress_percent": 0.0,
                "speed_bps": 0.0,
                "eta_seconds": None,
                "started_at": started_at,
                "finished_at": "",
                "updated_at": started_at,
                "remote_only": _configured_remote_only(server.config),
                "message": "Starting push",
                "error": "",
            },
        )
        thread = threading.Thread(
            target=_run_remote_push,
            args=(server.config,),
            daemon=True,
            name="snapz-web-remote-push",
        )
        server.push_thread = thread
        thread.start()
        status = _read_remote_sync_status(server.config)
        status["running"] = True
        return True, status


def _run_remote_push(config: RuntimeConfig) -> None:
    started_at = now_iso()

    def record(status: dict[str, Any]) -> None:
        _write_remote_sync_status(
            config,
            {
                **status,
                "status": status.get("status") or "running",
                "started_at": started_at,
                "updated_at": now_iso(),
                "remote_only": _configured_remote_only(config),
                "error": "" if status.get("status") != "failed" else status.get("message", ""),
            },
        )

    try:
        outcome = remote.push_all(config=config, progress=record)
        finished_at = now_iso()
        total_bytes = sum(item.bundle_bytes for item in outcome.items)
        status = "completed" if outcome.ok else "failed"
        message = (
            f"Pushed {len(outcome.items)} source(s), {format_size(total_bytes)}."
            if outcome.ok
            else "; ".join(failure.message for failure in outcome.failures)
        )
        _write_remote_sync_status(
            config,
            {
                "status": status,
                "phase": "finished" if outcome.ok else "failed",
                "progress_percent": 100.0 if outcome.ok else 0.0,
                "speed_bps": 0.0,
                "eta_seconds": 0.0 if outcome.ok else None,
                "last_sync_at": finished_at if outcome.ok else _read_remote_sync_status(config).get("last_sync_at", ""),
                "finished_at": finished_at,
                "updated_at": finished_at,
                "remote_only": _configured_remote_only(config),
                "message": message,
                "error": "" if outcome.ok else message,
            },
        )
    except Exception as exc:  # noqa: BLE001
        finished_at = now_iso()
        _write_remote_sync_status(
            config,
            {
                "status": "failed",
                "phase": "failed",
                "finished_at": finished_at,
                "updated_at": finished_at,
                "remote_only": _configured_remote_only(config),
                "message": str(exc),
                "error": str(exc),
            },
        )


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
            elif parsed.path == "/api/remote/status":
                status = _read_remote_sync_status(self.server.config)
                thread = self.server.push_thread
                status["running"] = thread is not None and thread.is_alive()
                self._send_json({"status": status})
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
            elif parsed.path == "/api/remote/push":
                started, status = _start_remote_push(self.server)
                self._send_json(
                    {
                        "started": started,
                        "status": status,
                        "message": (
                            "Remote push started."
                            if started
                            else "Remote push is already running."
                        ),
                    },
                    HTTPStatus.ACCEPTED if started else HTTPStatus.OK,
                )
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
