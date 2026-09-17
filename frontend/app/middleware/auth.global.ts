/** Everything except /login requires a session. Runs on client navigation and SSR alike. */
export default defineNuxtRouteMiddleware(async (to) => {
  const { access, me } = useAuth()
  if (to.path === '/login') return

  if (!access.value) return navigateTo('/login')

  if (!me.value) {
    try {
      me.value = await useApi().get('/me')
    } catch {
      return navigateTo('/login')
    }
  }
})
