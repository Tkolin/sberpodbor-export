export default defineNuxtConfig({
  compatibilityDate: '2025-01-01',
  modules: ['@nuxt/ui', '@vueuse/nuxt'],
  css: ['~/assets/css/main.css'],

  // SPA, not SSR. This panel is entirely behind a login, so server rendering buys nothing —
  // and it actively breaks: during SSR a relative '/api/...' resolves against Nitro itself,
  // which knows nothing about the nginx route to FastAPI, so every authenticated fetch fails.
  ssr: false,

  runtimeConfig: {
    public: {
      // nginx proxies /api to the FastAPI service; override for standalone dev.
      apiBase: process.env.NUXT_PUBLIC_API_BASE || '/api'
    }
  },

  ui: {
    colorMode: true
  },

  app: {
    head: {
      title: 'Сбер Подбор — админка',
      htmlAttrs: { lang: 'ru' },
      meta: [
        { charset: 'utf-8' },
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
        // The panel shows personal data; keep it out of any external referrer log.
        { name: 'referrer', content: 'same-origin' }
      ]
    }
  },

  nitro: {
    compressPublicAssets: true
  },

  devtools: { enabled: false }
})
