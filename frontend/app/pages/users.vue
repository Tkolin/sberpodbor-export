<script setup lang="ts">
const api = useApi()
const toast = useToast()
const { me } = useAuth()

const { data: users, refresh } = await useAsyncData('admin-users', () => api.get<any[]>('/users'))

const modal = ref(false)
const editing = ref<any | null>(null)
const form = reactive({ email: '', password: '', full_name: '', role: 'viewer', is_active: true })
const saving = ref(false)

const ROLES = [
  { label: 'Администратор', value: 'admin', hint: 'всё, включая аккаунты ATS и пользователей' },
  { label: 'Оператор', value: 'operator', hint: 'просмотр, выгрузки, запуск синхронизации' },
  { label: 'Наблюдатель', value: 'viewer', hint: 'только просмотр' }
]
const ROLE_LABEL = Object.fromEntries(ROLES.map((r) => [r.value, r.label]))

function openCreate() {
  editing.value = null
  Object.assign(form, {
    email: '',
    password: '',
    full_name: '',
    role: 'viewer',
    is_active: true
  })
  modal.value = true
}

function openEdit(u: any) {
  editing.value = u
  Object.assign(form, {
    email: u.email,
    password: '',
    full_name: u.full_name || '',
    role: u.role,
    is_active: u.is_active
  })
  modal.value = true
}

async function save() {
  saving.value = true
  try {
    if (editing.value) {
      const body: Record<string, any> = {
        full_name: form.full_name,
        role: form.role,
        is_active: form.is_active
      }
      if (form.password) body.password = form.password
      await api.patch(`/users/${editing.value.id}`, body)
    } else {
      await api.post('/users', {
        email: form.email,
        password: form.password,
        full_name: form.full_name || null,
        role: form.role
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

async function remove(u: any) {
  if (!confirm(`Удалить пользователя ${u.email}?`)) return
  try {
    await api.del(`/users/${u.id}`)
    await refresh()
    toast.add({ title: 'Пользователь удалён', color: 'success' })
  } catch (e: any) {
    toast.add({ title: 'Ошибка', description: e?.data?.detail || String(e), color: 'error' })
  }
}

const dt = (v: string | null) => (v ? new Date(v).toLocaleString('ru-RU') : 'ни разу')
</script>

<template>
  <UDashboardPanel id="users">
    <template #header>
      <UDashboardNavbar title="Пользователи">
        <template #leading><UDashboardSidebarCollapse /></template>
        <template #right>
          <UButton icon="i-lucide-plus" label="Добавить" @click="openCreate" />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="text-left text-muted border-b border-default">
            <tr>
              <th class="py-2 pr-3 font-medium">Почта</th>
              <th class="py-2 pr-3 font-medium">Имя</th>
              <th class="py-2 pr-3 font-medium">Роль</th>
              <th class="py-2 pr-3 font-medium">Статус</th>
              <th class="py-2 pr-3 font-medium">Последний вход</th>
              <th class="py-2 w-20" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in users || []" :key="u.id" class="border-b border-default/50">
              <td class="py-2 pr-3">
                {{ u.email }}
                <UBadge v-if="u.id === me?.id" size="sm" variant="subtle" label="вы" class="ml-1" />
              </td>
              <td class="py-2 pr-3 text-muted">{{ u.full_name || '—' }}</td>
              <td class="py-2 pr-3">
                <UBadge
                  :color="u.role === 'admin' ? 'primary' : 'neutral'"
                  variant="subtle"
                  size="sm"
                  :label="ROLE_LABEL[u.role] || u.role"
                />
              </td>
              <td class="py-2 pr-3">
                <UBadge
                  :color="u.is_active ? 'success' : 'neutral'"
                  variant="subtle"
                  size="sm"
                  :label="u.is_active ? 'активен' : 'отключён'"
                />
              </td>
              <td class="py-2 pr-3 text-muted">{{ dt(u.last_login_at) }}</td>
              <td class="py-2">
                <div class="flex gap-1">
                  <UButton icon="i-lucide-pencil" size="xs" color="neutral" variant="ghost" @click="openEdit(u)" />
                  <UButton
                    v-if="u.id !== me?.id"
                    icon="i-lucide-trash-2"
                    size="xs"
                    color="error"
                    variant="ghost"
                    @click="remove(u)"
                  />
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <UModal v-model:open="modal" :title="editing ? 'Изменить пользователя' : 'Новый пользователь'">
        <template #body>
          <div class="space-y-4">
            <UFormField label="Почта" required>
              <UInput v-model="form.email" type="email" :disabled="!!editing" class="w-full" />
            </UFormField>
            <UFormField label="Имя">
              <UInput v-model="form.full_name" class="w-full" />
            </UFormField>
            <UFormField
              label="Пароль"
              :required="!editing"
              :hint="editing ? 'пусто — не менять' : 'минимум 10 символов'"
            >
              <UInput v-model="form.password" type="password" class="w-full" />
            </UFormField>
            <UFormField label="Роль" required>
              <USelect v-model="form.role" :items="ROLES" class="w-full" />
              <template #help>
                {{ ROLES.find((r) => r.value === form.role)?.hint }}
              </template>
            </UFormField>
            <UCheckbox v-if="editing" v-model="form.is_active" label="Активен" />
          </div>
        </template>
        <template #footer>
          <div class="flex justify-end gap-2 w-full">
            <UButton color="neutral" variant="ghost" label="Отмена" @click="modal = false" />
            <UButton
              label="Сохранить"
              :loading="saving"
              :disabled="!form.email || (!editing && form.password.length < 10)"
              @click="save"
            />
          </div>
        </template>
      </UModal>
    </template>
  </UDashboardPanel>
</template>
