# snapz-server UI for Vue Vben Admin

These files are a drop-in module for
[`vbenjs/vue-vben-admin`](https://github.com/vbenjs/vue-vben-admin) 5.x,
matching the current `apps/web-antd` structure.

## Install into a Vben checkout

Copy this directory's `src/` tree into `vue-vben-admin/apps/web-antd/src/`:

```bash
cp -R web/vue-vben-admin-snapz/src/* /path/to/vue-vben-admin/apps/web-antd/src/
```

Then run the Vben app:

```bash
cd /path/to/vue-vben-admin
pnpm install
VITE_SNAPZ_SERVER_URL=https://127.0.0.1:8765 pnpm dev:antd
```

Start `snapz-server` with an admin token:

```bash
snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --tls-cert /etc/snapz/tls/fullchain.pem \
  --tls-key /etc/snapz/tls/privkey.pem \
  --admin-token "$(openssl rand -hex 32)" \
  --cors-origin http://127.0.0.1:5173
```

The page is available at `/snapz-server/users` inside Vben Admin. The built-in
snapz-server fallback page is also served from `https://127.0.0.1:8765/admin`.

## Capabilities

- List tenants and users.
- Create users.
- Rename, enable, disable, reset password, and delete users.
- List devices for a selected user.
- Revoke a single device or all active devices for a user.
