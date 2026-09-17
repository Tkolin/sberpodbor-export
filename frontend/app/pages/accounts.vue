<script setup lang="ts">
const api = useApi()
const toast = useToast()

interface Account {
  id: number
  label: string | null
  email: string
  proxy: string | null
  is_active: boolean
  last_login_at: string | null
  last_ok_at: string | null
  last_error: string | null
  requests_ok: number
  requests_failed: number
}

const { data: accounts, refresh, pending } = await useAsyncData('accounts', () =>
  api.get<Account[]>('/accounts')
)

const modal = ref(false)
const editing = ref<Account | null>(null)
const form = reactive({ label: '', email: '', password: '', proxy: '', is_active: true })
const saving = ref(false)
const checkingId = ref<number | null>(null)

function openCreate() {
  editing.value = null
  Object.assign(form, { label: '', email: '', password: '', proxy: '', is_active: true })
  modal.value = true
}

function openEdit(acc: Account) {
  editing.value = acc
  // Password is never sent back to the browser; empty means "leave unchanged".
  Object.assign(form, {
    label: acc.label || '',
    email: acc.email,
    password: '',
    proxy: acc.proxy || '',
    is_active: acc.is_active
  })
  modal.value = true
}

async function save() {
  saving.value = true
  try {
    if (editing.value) {
      const body: Record<string, any> = {
        label: form.label,
        is_active: form.is_active
      }
      if (form.password) body.password = form.password
      // Sending the masked value back would overwrite the real proxy with "***".
      if (form.proxy && !form.proxy.includes('***')) body.proxy = form.proxy
      await api.patch(`/accounts/${editing.value.id}`, body)
    } else {
      await api.post('/accounts', {
        label: form.label || null,
        email: form.email,
        password: form.password,
        proxy: form.proxy || null,
        is_active: form.is_active
      })
    }
    modal.value = false
    await refresh()
    toast.add({ title: 'Сохранено', color: 'success' })
  } catch (e: any) {
    toast.add({
      title: 'Не удалось сохранить',
      description: e?.data?.detail || String(e),
      color: 'error'
    })
  } finally {
    saving.value = false
  }
}

async function check(acc: Account, force = false) {
  checkingId.value = acc.id
  try {
    const r = await api.post<{ ok: boolean; detail: string }>(
      `/accounts/${acc.id}/check`,
      undefined,
      force ? { force: true } : undefined
    )
    toast.add({
      title: r.ok ? 'Вход выполнен' : 'Вход не удался',
      description: r.detail,
      color: r.ok ? 'success' : 'error'
    })
    await refresh()
  } catch (e: any) {
    // 409 means a crawl is running — checking would knock out its token.
    if (e?.status === 409) {
      toast.add({
        title: 'Идёт синхронизация',
        description:
          'Проверка выполнит новый вход и отберёт токен у работающего обхода. ' +
          'Остановите синхронизацию или нажмите «Проверить всё равно».',
        color: 'warning',
        actions: [
          {
            label: 'Проверить всё равно',
            color: 'warning',
            variant: 'outline',
            onClick: () => check(acc, true)
          }
        ]
      })
    } else {
      toast.add({ title: 'Ошибка', description: e?.data?.detail || String(e), color: 'error' })
    }
  } finally {
    checkingId.value = null
  }
}

async function remove(acc: Account) {
  if (!confirm(`Удалить аккаунт ${acc.email}? Он перестанет использоваться для обхода.`)) return
  try {
    await api.del(`/accounts/${acc.id}`)
    await refresh()
    toast.add({ title: 'Аккаунт удалён', color: 'success' })
  } catch (e: any) {
    toast.add({ title: 'Ошибка', description: e?.data?.detail || String(e), color: 'error' })
  }
}

const dt = (v: string | null) => (v ? new Date(v).toLocaleString('ru-RU') : '—')
</script>

<template>
  <UDashboardPanel id="accounts">
    <template #header>
      <UDashboardNavbar title="Аккаунты ATS">
        <template #leading><UDashboardSidebarCollapse /></template>
        <template #right>
          <UButton icon="i-lucide-plus" label="Добавить" @click="openCreate" />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UAlert
        color="info"
        variant="subtle"
        icon="i-lucide-info"
        class="mb-4"
        title="Один активный токен на аккаунт"
        description="Сбер Подбор разрешает только одну активную сессию на пользователя. Пока идёт обход, не входите этими учётными данными ни здесь, ни в браузере — обход получит 401 и уйдёт в анти-абуз-блок."
      />

      <div class="grid gap-3">
        <UCard v-for="acc in accounts" :key="acc.id">
          <div class="flex flex-wrap items-start gap-4">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-medium">{{ acc.label || acc.email }}</span>
                <UBadge
                  :color="acc.is_active ? 'success' : 'neutral'"
                  variant="subtle"
                  :label="acc.is_active ? 'активен' : 'выключен'"
                />
              </div>
              <p class="text-sm text-muted mt-0.5">{{ acc.email }}</p>
              <p class="text-xs text-dimmed mt-1 font-mono">
                {{ acc.proxy || 'без прокси' }}
              </p>
            </div>

            <div class="text-xs text-muted space-y-0.5 shrink-0">
              <p>Последний вход: {{ dt(acc.last_login_at) }}</p>
              <p>Успешных запросов: {{ fmt(acc.requests_ok) }}</p>
              <p>Ошибок: {{ fmt(acc.requests_failed) }}</p>
            </div>

            <div class="flex gap-1 shrink-0">
              <UButton
                icon="i-lucide-plug-zap"
                color="neutral"
                variant="ghost"
                :loading="checkingId === acc.id"
                title="Проверить вход"
                @click="check(acc)"
              />
              <UButton
                icon="i-lucide-pencil"
                color="neutral"
                variant="ghost"
                title="Изменить"
                @click="openEdit(acc)"
              />
              <UButton
                icon="i-lucide-trash-2"
                color="error"
                variant="ghost"
                title="Удалить"
                @click="remove(acc)"
              />
            </div>
          </div>

          <UAlert
            v-if="acc.last_error"
            color="warning"
            variant="subtle"
            class="mt-3"
            icon="i-lucide-triangle-alert"
            :description="`Последняя ошибка: ${acc.last_error}`"
          />
        </UCard>

        <UCard v-if="!pending && !accounts?.length">
          <p class="text-center text-muted py-6">
            Аккаунтов нет. Без них обход не запустится — добавьте хотя бы один.
          </p>
        </UCard>
      </div>

      <UModal v-model:open="modal" :title="editing ? 'Изменить аккаунт' : 'Новый аккаунт'">
        <template #body>
          <div class="space-y-4">
            <UFormField label="Название" hint="необязательно">
              <UInput v-model="form.label" placeholder="Основной" class="w-full" />
            </UFormField>

            <UFormField label="Почта" required>
              <UInput
                v-model="form.email"
                type="email"
                :disabled="!!editing"
                class="w-full"
                placeholder="user@example.com"
              />
            </UFormField>

            <UFormField
              label="Пароль"
              :required="!editing"
              :hint="editing ? 'пусто — не менять' : undefined"
            >
              <UInput v-model="form.password" type="password" class="w-full" />
            </UFormField>

            <UFormField label="Прокси" hint="http://user:pass@host:port">
              <UInput v-model="form.proxy" class="w-full" placeholder="http://..." />
            </UFormField>

            <UCheckbox v-model="form.is_active" label="Использовать в обходе" />
          </div>
        </template>

        <template #footer>
          <div class="flex justify-end gap-2 w-full">
            <UButton color="neutral" variant="ghost" label="Отмена" @click="modal = false" />
            <UButton
              label="Сохранить"
              :loading="saving"
              :disabled="!form.email || (!editing && !form.password)"
              @click="save"
            />
          </div>
        </template>
      </UModal>
    </template>
  </UDashboardPanel>
</template>
