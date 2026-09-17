<script setup lang="ts">
definePageMeta({ layout: false })

const { access, refresh, me } = useAuth()
const api = useApi()

const email = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const r = await $fetch<{ access_token: string; refresh_token: string }>(
      `${useRuntimeConfig().public.apiBase}/login`,
      { method: 'POST', body: { email: email.value, password: password.value } }
    )
    access.value = r.access_token
    refresh.value = r.refresh_token
    me.value = await api.get('/me')
    await navigateTo('/')
  } catch (e: any) {
    error.value =
      e?.status === 401
        ? 'Неверная почта или пароль'
        : e?.data?.detail || 'Не удалось войти. Попробуйте ещё раз.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center p-4 bg-elevated/25">
    <UCard class="w-full max-w-sm">
      <template #header>
        <div class="flex items-center gap-2">
          <UIcon name="i-lucide-database" class="size-5 text-primary" />
          <div>
            <h1 class="font-semibold">Сбер Подбор</h1>
            <p class="text-xs text-muted">Панель управления выгрузкой</p>
          </div>
        </div>
      </template>

      <form class="space-y-4" @submit.prevent="submit">
        <UFormField label="Почта" required>
          <UInput
            v-model="email"
            type="email"
            autocomplete="username"
            placeholder="admin@local"
            class="w-full"
            autofocus
          />
        </UFormField>

        <UFormField label="Пароль" required>
          <UInput
            v-model="password"
            type="password"
            autocomplete="current-password"
            class="w-full"
          />
        </UFormField>

        <UAlert
          v-if="error"
          color="error"
          variant="subtle"
          icon="i-lucide-triangle-alert"
          :description="error"
        />

        <UButton
          type="submit"
          block
          :loading="loading"
          :disabled="!email || !password"
          label="Войти"
        />
      </form>
    </UCard>
  </div>
</template>
