<script setup lang="ts">
const api = useApi()
const offset = ref(0)
const LIMIT = 100

const { data: rows, pending } = await useAsyncData(
  'audit',
  () => api.get<any[]>('/audit', { limit: LIMIT, offset: offset.value }),
  { watch: [offset] }
)

const ACTION_LABEL: Record<string, string> = {
  'login.ok': 'вход',
  'login.failed': 'неудачный вход',
  'password.changed': 'смена пароля',
  'account.created': 'аккаунт ATS создан',
  'account.updated': 'аккаунт ATS изменён',
  'account.deleted': 'аккаунт ATS удалён',
  'account.checked': 'проверка аккаунта',
  'user.created': 'пользователь создан',
  'user.updated': 'пользователь изменён',
  'user.deleted': 'пользователь удалён',
  'sync.triggered': 'запуск синхронизации',
  'sync.cancelled': 'остановка синхронизации',
  'export.queued': 'выгрузка поставлена в очередь'
}

const isAlarming = (a: string) => a === 'login.failed' || a.endsWith('.deleted')
const dt = (v: string) => new Date(v).toLocaleString('ru-RU')
</script>

<template>
  <UDashboardPanel id="audit">
    <template #header>
      <UDashboardNavbar title="Аудит">
        <template #leading><UDashboardSidebarCollapse /></template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="text-left text-muted border-b border-default">
            <tr>
              <th class="py-2 pr-3 font-medium">Когда</th>
              <th class="py-2 pr-3 font-medium">Кто</th>
              <th class="py-2 pr-3 font-medium">Действие</th>
              <th class="py-2 pr-3 font-medium">Объект</th>
              <th class="py-2 pr-3 font-medium">IP</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rows || []" :key="r.id" class="border-b border-default/50">
              <td class="py-2 pr-3 text-muted whitespace-nowrap">{{ dt(r.created_at) }}</td>
              <td class="py-2 pr-3">{{ r.actor_email || '—' }}</td>
              <td class="py-2 pr-3">
                <UBadge
                  :color="isAlarming(r.action) ? 'warning' : 'neutral'"
                  variant="subtle"
                  size="sm"
                  :label="ACTION_LABEL[r.action] || r.action"
                />
              </td>
              <td class="py-2 pr-3 text-muted truncate max-w-64">{{ r.target || '—' }}</td>
              <td class="py-2 pr-3 text-dimmed font-mono text-xs">{{ r.ip || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!pending && !rows?.length" class="text-center text-muted py-10">Записей нет</p>
      </div>

      <div class="flex justify-center gap-2 mt-4">
        <UButton
          icon="i-lucide-chevron-left"
          color="neutral"
          variant="ghost"
          :disabled="offset === 0"
          label="Новее"
          @click="offset = Math.max(0, offset - LIMIT)"
        />
        <UButton
          trailing-icon="i-lucide-chevron-right"
          color="neutral"
          variant="ghost"
          :disabled="(rows?.length || 0) < LIMIT"
          label="Старее"
          @click="offset += LIMIT"
        />
      </div>
    </template>
  </UDashboardPanel>
</template>
