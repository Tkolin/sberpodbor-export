import type { UseFetchOptions } from 'nuxt/app'

export interface Me {
  id: number
  email: string
  full_name: string | null
  role: 'admin' | 'operator' | 'viewer'
  is_active: boolean
  last_login_at: string | null
}

/** Access token lives in memory + a session cookie; the refresh token is httpOnly-ish
 *  only by convention here, so it is kept in a cookie with a short path and sameSite=strict. */
export const useAuth = () => {
  const access = useCookie<string | null>('sp_access', { sameSite: 'strict', maxAge: 60 * 60 })
  const refresh = useCookie<string | null>('sp_refresh', { sameSite: 'strict', maxAge: 60 * 60 * 24 * 14 })
  const me = useState<Me | null>('me', () => null)
  return { access, refresh, me }
}

/** Thin wrapper over $fetch that attaches the token and refreshes once on 401. */
export const useApi = () => {
  const config = useRuntimeConfig()
  const { access, refresh, me } = useAuth()
  const base = config.public.apiBase

  async function tryRefresh(): Promise<boolean> {
    if (!refresh.value) return false
    try {
      const r = await $fetch<{ access_token: string; refresh_token: string }>(
        `${base}/refresh`,
        { method: 'POST', body: { refresh_token: refresh.value } }
      )
      access.value = r.access_token
      refresh.value = r.refresh_token
      return true
    } catch {
      access.value = null
      refresh.value = null
      me.value = null
      return false
    }
  }

  async function request<T>(path: string, opts: any = {}, retry = true): Promise<T> {
    try {
      return await $fetch<T>(`${base}${path}`, {
        ...opts,
        headers: {
          ...(opts.headers || {}),
          ...(access.value ? { Authorization: `Bearer ${access.value}` } : {})
        }
      })
    } catch (e: any) {
      if (e?.status === 401 && retry && (await tryRefresh())) {
        return request<T>(path, opts, false)
      }
      throw e
    }
  }

  return {
    get: <T>(path: string, params?: Record<string, any>) =>
      request<T>(path, { method: 'GET', params }),
    post: <T>(path: string, body?: any, params?: Record<string, any>) =>
      request<T>(path, { method: 'POST', body, params }),
    patch: <T>(path: string, body?: any) => request<T>(path, { method: 'PATCH', body }),
    del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
    /** Absolute URL for links the browser fetches itself (downloads). */
    url: (path: string, params?: Record<string, any>) => {
      const q = new URLSearchParams()
      for (const [k, v] of Object.entries(params || {})) {
        if (v !== undefined && v !== null && v !== '') q.append(k, String(v))
      }
      const qs = q.toString()
      return `${base}${path}${qs ? `?${qs}` : ''}`
    },
    token: () => access.value,
    /** Fetches a protected binary and returns an object URL. Needed because <img src>
     *  cannot carry an Authorization header. Caller must revoke the URL when done. */
    blobUrl: async (path: string): Promise<string | null> => {
      try {
        const res = await fetch(`${base}${path}`, {
          headers: access.value ? { Authorization: `Bearer ${access.value}` } : {}
        })
        if (!res.ok) return null
        return URL.createObjectURL(await res.blob())
      } catch {
        return null
      }
    }
  }
}

/** Formats large row counts the way the dashboard shows them. */
export const fmt = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : n.toLocaleString('ru-RU')
