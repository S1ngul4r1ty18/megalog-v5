import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const routes: RouteRecordRaw[] = [
  { path: "/login", name: "login", component: () => import("@/pages/Login.vue"), meta: { public: true } },
  {
    path: "/",
    component: () => import("@/components/Layout.vue"),
    meta: { requiresAuth: true },
    children: [
      { path: "",                  name: "dashboard",      component: () => import("@/pages/Dashboard.vue") },
      { path: "search",            name: "search",         component: () => import("@/pages/Search.vue") },
      { path: "daily",             name: "daily",          component: () => import("@/pages/DailyLogs.vue") },
      { path: "anomaly/:date",     name: "anomaly",        component: () => import("@/pages/AnomalyDetail.vue") },
      { path: "change-password",   name: "change-password",component: () => import("@/pages/ChangePassword.vue") },
      { path: "admin/users",       name: "admin-users",    component: () => import("@/pages/AdminUsers.vue"),  meta: { requiresAdmin: true } },
      { path: "admin/audit",       name: "admin-audit",    component: () => import("@/pages/AdminAudit.vue"),  meta: { requiresAdmin: true } },
      { path: "admin/alerts",      name: "admin-alerts",   component: () => import("@/pages/AdminAlerts.vue"), meta: { requiresAdmin: true } },
      { path: "donate",            name: "donate",         component: () => import("@/pages/Donate.vue") },
      { path: "docs",              name: "docs",           component: () => import("@/pages/Documentation.vue") },
    ],
  },
  { path: "/:catchAll(.*)*", redirect: "/" },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (!auth.ready) await auth.bootstrap();

  if (to.meta.public) return true;
  if (to.meta.requiresAuth && !auth.me) {
    return { name: "login", query: { next: to.fullPath } };
  }
  if (to.meta.requiresAdmin && !auth.isAdmin()) {
    return { name: "dashboard" };
  }
  return true;
});
