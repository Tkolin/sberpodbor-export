<script setup lang="ts">
const api = useApi()
const toast = useToast()
const { me } = useAuth()

const canOperate = computed(() => me.value?.role === 'admin' || me.value?.role === 'operator')

const { data: facets } = await useAsyncData('export-facets', () => api.get<any>('/data/facets'))
const { data: jobs, refresh: refreshJobs } = await useAsyncData('export-jobs', () =>
  api.get<any[]>('/exports/jobs', { limit: 30 })
)

const f = reactive<{
  city?: string
  vacancy_id?: number
  status_title?: string
  created_from?: string
  created_to?: string
  with_resume?: boolean
}>({})

const INLINE_LIMIT = 5000
const previewLimit = ref(1000)
const creating = ref(false)

const cleanFilters = computed(() =>
  Object.fromEntries(
    Object.entries(f).filter(([, v]) => v !== undefined && v !== null && v !== '')
  )
)

/** Inline download goes straight through the browser, so the token rides as a query
 *  param is NOT acceptable — instead fetch as a blob with the auth header. */
const downloading = ref(false)
async function downloadInline() {
  downloading.value = true
  try {
    const url = api.url('/exports/xml', { ...cleanFilters.value, limit: previewLimit.value })
    const res = await fetch(url, { headers: { Authorization: `Bearer ${api.token()}` } })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `sberpodbor-${new Date().toISOString().slice(0, 10)}.xml`
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (e: any) {
    toast.add({ title: 'Не удалось выгрузить', description: String(e), color: 'error' })
  } finally {
    downloading.value = false
  }
}

async function createJob() {
  creating.value = true
  try {
    await api.post('/exports/jobs', undefined, cleanFilters.value)
    toast.add({
      title: 'Задача создана',
      description: 'Файл соберётся в фоне — обновите список через минуту.',
      color: 'success'
    })
    await refreshJobs()
  } catch (e: any) {
    toast.add({ title: 'Ошибка', description: e?.data?.detail || String(e), color: 'error' })
  } finally {
    creating.value = false
  }
}

async function downloadJob(job: any) {
  const res = await fetch(api.url(`/exports/jobs/${job.id}/download`), {
    headers: { Authorization: `Bearer ${api.token()}` }
  })
  if (!res.ok) {
    toast.add({ title: 'Файл недоступен', color: 'error' })
    return
  }
  const blob = await res.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = job.filename
  a.click()
  URL.revokeObjectURL(a.href)
}

async function downloadSchema() {
  const res = await fetch(api.url('/exports/schema.xsd'), {
    headers: { Authorization: `Bearer ${api.token()}` }
  })
  const blob = await res.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'sberpodbor-export-1.0.xsd'
  a.click()
  URL.revokeObjectURL(a.href)
}

const STATUS_COLOR: Record<string, any> = {
  queued: 'neutral',
  running: 'info',
  done: 'success',
  failed: 'error'
}
const STATUS_LABEL: Record<string, string> = {
  queued: 'в очереди',
  running: 'собирается',
  done: 'готов',
  failed: 'ошибка'
}

const mb = (n: number | null) => (n ? `${(n / 1024 / 1024).toFixed(1)} МБ` : '—')
const dt = (v: string | null) => (v ? new Date(v).toLocaleString('ru-RU') : '—')

const resumeOptions = [
  { label: 'Все', value: undefined },
  { label: 'Только с резюме', value: true },
  { label: 'Только без резюме', value: false }
]

const timer = ref<ReturnType<typeof setInterval>>()
onMounted(() => {
  timer.value = setInterval(refreshJobs, 15_000)
})
onUnmounted(() => clearInterval(timer.value))
</script>

<template>
  <UDashboardPanel id="exports">
    <template #header>
      <UDashboardNavbar title="Выгрузка XML">
        <template #leading><UDashboardSidebarCollapse /></template>
        <template #right>
          <UButton
            icon="i-lucide-file-code-2"
            color="neutral"
            variant="ghost"
            label="Схема XSD"
            @click="downloadSchema"
          />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UCard class="mb-4">
        <template #header><h2 class="font-semibold">Отбор</h2></template>

        <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <UFormField label="Город">
            <USelectMenu v-model="f.city" :items="facets?.cities || []" searchable clearable class="w-full" />
          </UFormField>
          <UFormField label="Вакансия">
            <USelectMenu
              v-model="f.vacancy_id"
              :items="facets?.vacancies || []"
              label-key="title"
              value-key="id"
              searchable
              clearable
              class="w-full"
            />
          </UFormField>
          <UFormField label="Статус заявки">
            <USelectMenu v-model="f.status_title" :items="facets?.statuses || []" searchable clearable class="w-full" />
          </UFormField>
          <UFormField label="Заявки с даты">
            <UInput v-model="f.created_from" type="date" class="w-full" />
          </UFormField>
          <UFormField label="Заявки по дату">
            <UInput v-model="f.created_to" type="date" class="w-full" />
          </UFormField>
          <UFormField label="Резюме">
            <USelect v-model="f.with_resume" :items="resumeOptions" class="w-full" />
          </UFormField>
        </div>
      </UCard>

      <div class="grid md:grid-cols-2 gap-4 mb-4">
        <UCard>
          <template #header><h2 class="font-semibold">Быстрая выгрузка</h2></template>
          <p class="text-sm text-muted mb-3">
            Отдаётся сразу в браузер. Подходит для проверки формата и небольших наборов —
            не больше {{ fmt(INLINE_LIMIT) }} профилей.
          </p>
          <UFormField label="Сколько профилей" class="mb-3">
            <UInput v-model.number="previewLimit" type="number" :min="1" :max="INLINE_LIMIT" class="w-full" />
          </UFormField>
          <UButton
            icon="i-lucide-download"
            label="Скачать XML"
            :loading="downloading"
            block
            @click="downloadInline"
          />
        </UCard>

        <UCard>
          <template #header><h2 class="font-semibold">Полная выгрузка</h2></template>
          <p class="text-sm text-muted mb-3">
            Собирается в фоне и складывается файлом на сервере. Вся база — это гигабайты,
            поэтому в браузер напрямую такое не отдаём.
          </p>
          <UButton
            icon="i-lucide-package"
            label="Собрать файл"
            variant="soft"
            :loading="creating"
            :disabled="!canOperate"
            block
            @click="createJob"
          />
          <p v-if="!canOperate" class="text-xs text-muted mt-2 text-center">
            Нужна роль оператора или администратора
          </p>
        </UCard>
      </div>

      <UCard>
        <template #header><h2 class="font-semibold">Готовые файлы</h2></template>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="text-left text-muted border-b border-default">
              <tr>
                <th class="py-2 pr-3 font-medium">Файл</th>
                <th class="py-2 pr-3 font-medium">Статус</th>
                <th class="py-2 pr-3 font-medium">Создан</th>
                <th class="py-2 pr-3 font-medium text-right">Профилей</th>
                <th class="py-2 pr-3 font-medium text-right">Размер</th>
                <th class="py-2 w-10" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="j in jobs || []" :key="j.id" class="border-b border-default/50">
                <td class="py-2 pr-3 font-mono text-xs truncate max-w-64">{{ j.filename }}</td>
                <td class="py-2 pr-3">
                  <UBadge :color="STATUS_COLOR[j.status]" variant="subtle" size="sm" :label="STATUS_LABEL[j.status]" />
                </td>
                <td class="py-2 pr-3 text-muted">{{ dt(j.created_at) }}</td>
                <td class="py-2 pr-3 text-right tabular-nums">{{ fmt(j.row_count) }}</td>
                <td class="py-2 pr-3 text-right tabular-nums">{{ mb(j.size_bytes) }}</td>
                <td class="py-2">
                  <UButton
                    v-if="j.status === 'done'"
                    icon="i-lucide-download"
                    size="xs"
                    color="neutral"
                    variant="ghost"
                    @click="downloadJob(j)"
                  />
                  <UTooltip v-else-if="j.error" :text="j.error">
                    <UIcon name="i-lucide-triangle-alert" class="size-4 text-warning" />
                  </UTooltip>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!jobs?.length" class="text-center text-muted py-8">Файлов пока нет</p>
        </div>
      </UCard>
    </template>
  </UDashboardPanel>
</template>
