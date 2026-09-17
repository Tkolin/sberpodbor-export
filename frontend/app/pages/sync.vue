<script setup lang="ts">
const api = useApi()
const toast = useToast()
const { me } = useAuth()

const canOperate = computed(() => me.value?.role === 'admin' || me.value?.role === 'operator')

const { data: status, refresh: refreshStatus } = await useAsyncData('sync-status', () =>
  api.get<any>('/sync/status')
)
const { data: runs, refresh: refreshRuns } = await useAsyncData('sync-runs', () =>
  api.get<any[]>('/sync/runs', { limit: 30 })
)
const { data: media, refresh: refreshMedia } = await useAsyncData('media-stats', () =>
  api.get<any>('/media/stats')
)

const timer = ref<ReturnType<typeof setInterval>>()
onMounted(() => {
  timer.value = setInterval(() => {
    refreshStatus()
    refreshRuns()
    refreshMedia()
  }, 10_000)
})
onUnmounted(() => clearInterval(timer.value))

const KINDS = [
  {
    kind: 'sweep',
    title: 'Развёртка списков',
    hint: 'Заново листает профили, вакансии и сотрудников (~5 300 страниц, около 20 минут). Находит новые профили и заявки.'
  },
  {
    kind: 'details',
    title: 'Резюме и контакты',
    hint: 'Догружает резюме и контакты для профилей, у которых их ещё нет.'
  },
  {
    kind: 'activity',
    title: 'Логи и комментарии',
    hint: 'Самая тяжёлая фаза: по два запроса на заявку.'
  },
  {
    kind: 'media',
    title: 'Фото и файлы резюме',
    hint: 'Качает аватарки и оригиналы резюме с media.sberpodbor.ru. Это отдельная CDN — токен не нужен, обходу не мешает.'
  },
  {
    kind: 'rolling',
    title: 'Перепроверка старых',
    hint: 'Берёт записи, которые дольше всех не сверялись, и обновляет их. Так ловятся правки задним числом.'
  },
  {
    kind: 'full',
    title: 'Полный обход',
    hint: 'Развёртка, затем обе тяжёлые фазы подряд. Десятки часов — обычно не требуется.'
  }
]

const triggering = ref<string | null>(null)

async function trigger(kind: string) {
  triggering.value = kind
  try {
    await api.post('/sync/trigger', { kind })
    toast.add({ title: 'Задача поставлена в очередь', color: 'success' })
    await Promise.all([refreshStatus(), refreshRuns()])
  } catch (e: any) {
    toast.add({
      title: e?.status === 409 ? 'Уже выполняется' : 'Не удалось запустить',
      description: e?.data?.detail || String(e),
      color: e?.status === 409 ? 'warning' : 'error'
    })
  } finally {
    triggering.value = null
  }
}

async function cancel(id: number) {
  try {
    await api.post(`/sync/runs/${id}/cancel`)
    toast.add({ title: 'Остановка запрошена', color: 'success' })
    await Promise.all([refreshStatus(), refreshRuns()])
  } catch (e: any) {
    toast.add({ title: 'Ошибка', description: e?.data?.detail || String(e), color: 'error' })
  }
}

const STATUS_COLOR: Record<string, any> = {
  queued: 'neutral',
  running: 'info',
  done: 'success',
  failed: 'error',
  cancelled: 'warning'
}
const STATUS_LABEL: Record<string, string> = {
  queued: 'в очереди',
  running: 'выполняется',
  done: 'готово',
  failed: 'ошибка',
  cancelled: 'отменено'
}
const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.kind, k.title]))

const dt = (v: string | null) => (v ? new Date(v).toLocaleString('ru-RU') : '—')

const mediaKinds = [
  { key: 'photo', label: 'Фото кандидатов' },
  { key: 'resume_file', label: 'Оригиналы резюме' }
]
const gb = (n?: number) =>
  !n ? '0' : n > 1073741824 ? `${(n / 1073741824).toFixed(1)} ГБ` : `${Math.round(n / 1048576)} МБ`
const pct = (r: any) => (r.total ? Math.min(100, (r.processed / r.total) * 100) : 0)
</script>

<template>
  <UDashboardPanel id="sync">
    <template #header>
      <UDashboardNavbar title="Синхронизация">
        <template #leading><UDashboardSidebarCollapse /></template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UCard v-if="status?.active" class="mb-4">
        <template #header>
          <div class="flex items-center justify-between gap-2">
            <h2 class="font-semibold">
              Сейчас: {{ KIND_LABEL[status.active.kind] || status.active.kind }}
            </h2>
            <div class="flex items-center gap-2">
              <UBadge
                :color="STATUS_COLOR[status.active.status]"
                variant="subtle"
                :label="STATUS_LABEL[status.active.status]"
              />
              <UButton
                v-if="canOperate"
                size="xs"
                color="error"
                variant="ghost"
                label="Остановить"
                @click="cancel(status.active.id)"
              />
            </div>
          </div>
        </template>

        <div class="flex items-baseline justify-between mb-1.5 text-sm">
          <span class="text-muted">Запустил: {{ status.active.triggered_by || '—' }}</span>
          <span class="tabular-nums">
            {{ fmt(status.active.processed) }} / {{ fmt(status.active.total) }}
          </span>
        </div>
        <UProgress :model-value="pct(status.active)" :max="100" />
        <p v-if="status.active.deferred" class="text-xs text-muted mt-2">
          Отложено до следующего прогона: {{ fmt(status.active.deferred) }}
        </p>
      </UCard>

      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <UCard :ui="{ body: 'p-4' }">
          <p class="text-xs text-muted">Без резюме и контактов</p>
          <p class="text-2xl font-semibold tabular-nums">{{ fmt(status?.queues?.details_pending) }}</p>
        </UCard>
        <UCard :ui="{ body: 'p-4' }">
          <p class="text-xs text-muted">Без логов и комментариев</p>
          <p class="text-2xl font-semibold tabular-nums">{{ fmt(status?.queues?.activity_pending) }}</p>
        </UCard>
        <UCard :ui="{ body: 'p-4' }">
          <p class="text-xs text-muted">Ждут перепроверки</p>
          <p class="text-2xl font-semibold tabular-nums">{{ fmt(status?.queues?.recheck_due) }}</p>
        </UCard>
      </div>

      <UCard class="mb-4">
        <template #header>
          <div class="flex items-center justify-between gap-2">
            <h2 class="font-semibold">Фото и оригиналы резюме</h2>
            <UBadge variant="subtle" color="neutral" :label="gb(media?.total_bytes)" />
          </div>
        </template>
        <div class="grid sm:grid-cols-2 gap-4">
          <div v-for="k in mediaKinds" :key="k.key">
            <div class="flex items-baseline justify-between mb-1.5 text-sm">
              <span>{{ k.label }}</span>
              <span class="tabular-nums">
                {{ fmt(media?.[k.key]?.done || 0) }} / {{ fmt(media?.[k.key]?.total || 0) }}
              </span>
            </div>
            <UProgress
              :model-value="media?.[k.key]?.total
                ? ((media[k.key].done || 0) / media[k.key].total) * 100 : 0"
              :max="100"
            />
            <p v-if="media?.[k.key]?.failed || media?.[k.key]?.gone" class="text-xs text-muted mt-1">
              <span v-if="media?.[k.key]?.failed">ошибок: {{ fmt(media[k.key].failed) }}</span>
              <span v-if="media?.[k.key]?.gone" class="ml-2">
                нет на сервере: {{ fmt(media[k.key].gone) }}
              </span>
            </p>
          </div>
        </div>
      </UCard>

      <UCard class="mb-4">
        <template #header><h2 class="font-semibold">Запустить вручную</h2></template>
        <UAlert
          color="info"
          variant="subtle"
          icon="i-lucide-info"
          class="mb-3"
          description="Планировщик и так поддерживает данные свежими: сначала добирает недостающее, потом раз в час перелистывает списки, а в свободное время перепроверяет самые старые записи. Ручной запуск нужен, только когда результат нужен прямо сейчас."
        />
        <div class="grid sm:grid-cols-2 gap-2">
          <div
            v-for="k in KINDS"
            :key="k.kind"
            class="flex items-start justify-between gap-3 rounded-md border border-default p-3"
          >
            <div class="min-w-0">
              <p class="font-medium text-sm">{{ k.title }}</p>
              <p class="text-xs text-muted mt-0.5">{{ k.hint }}</p>
            </div>
            <UButton
              size="xs"
              variant="soft"
              label="Запустить"
              :loading="triggering === k.kind"
              :disabled="!canOperate || !!status?.active"
              @click="trigger(k.kind)"
            />
          </div>
        </div>
      </UCard>

      <UCard>
        <template #header><h2 class="font-semibold">История прогонов</h2></template>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="text-left text-muted border-b border-default">
              <tr>
                <th class="py-2 pr-3 font-medium">Тип</th>
                <th class="py-2 pr-3 font-medium">Статус</th>
                <th class="py-2 pr-3 font-medium">Запустил</th>
                <th class="py-2 pr-3 font-medium">Начат</th>
                <th class="py-2 pr-3 font-medium text-right">Обработано</th>
                <th class="py-2 pr-3 font-medium text-right">Отложено</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in runs || []" :key="r.id" class="border-b border-default/50">
                <td class="py-2 pr-3">{{ KIND_LABEL[r.kind] || r.kind }}</td>
                <td class="py-2 pr-3">
                  <UBadge
                    :color="STATUS_COLOR[r.status]"
                    variant="subtle"
                    size="sm"
                    :label="STATUS_LABEL[r.status]"
                  />
                </td>
                <td class="py-2 pr-3 text-muted">{{ r.triggered_by || '—' }}</td>
                <td class="py-2 pr-3 text-muted">{{ dt(r.started_at) }}</td>
                <td class="py-2 pr-3 text-right tabular-nums">{{ fmt(r.processed) }}</td>
                <td class="py-2 pr-3 text-right tabular-nums">{{ fmt(r.deferred) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="!runs?.length" class="text-center text-muted py-8">Прогонов пока не было</p>
        </div>
      </UCard>
    </template>
  </UDashboardPanel>
</template>
