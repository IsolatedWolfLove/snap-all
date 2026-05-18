import type { RouteRecordRaw } from 'vue-router';

const routes: RouteRecordRaw[] = [
  {
    meta: {
      icon: 'lucide:server-cog',
      order: 10,
      title: 'snapz-server',
    },
    name: 'SnapzServer',
    path: '/snapz-server',
    children: [
      {
        component: () => import('#/views/snapz/users/index.vue'),
        meta: {
          icon: 'lucide:users',
          title: 'Users & Devices',
        },
        name: 'SnapzServerUsers',
        path: '/snapz-server/users',
      },
    ],
  },
];

export default routes;
