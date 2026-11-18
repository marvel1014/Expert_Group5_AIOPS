import { createRouter, createWebHashHistory } from 'vue-router'

/**
 * 使用 hash 路由：后端以 StaticFiles(html=True) 托管 dist，未配置 SPA fallback，
 * hash 模式可保证刷新 /alerts 等深链接不会 404。
 */
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      name: 'overview',
      component: () => import('@/views/OverviewView.vue'),
      meta: { title: '概览' },
    },
    {
      path: '/alerts',
      name: 'alerts',
      component: () => import('@/views/AlertsView.vue'),
      meta: { title: '告警列表' },
    },
    {
      path: '/incidents',
      name: 'incidents',
      component: () => import('@/views/IncidentsView.vue'),
      meta: { title: '事件列表' },
    },
    {
      path: '/incidents/:id',
      name: 'incident-detail',
      component: () => import('@/views/IncidentDetailView.vue'),
      props: true,
      meta: { title: '事件详情' },
    },
    {
      path: '/reports/:id?',
      name: 'reports',
      component: () => import('@/views/ReportView.vue'),
      props: true,
      meta: { title: '诊断报告' },
    },
    {
      path: '/evaluations',
      name: 'evaluations',
      component: () => import('@/views/EvaluationsView.vue'),
      meta: { title: '效果评估' },
    },
    {
      path: '/simulate',
      name: 'simulate',
      component: () => import('@/views/SimulateView.vue'),
      meta: { title: '模拟注入' },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
  scrollBehavior() {
    return { top: 0 }
  },
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : '运维看板'
  document.title = `${title} · AIOps 看板`
})

export default router
