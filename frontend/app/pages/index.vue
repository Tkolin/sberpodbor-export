<script setup lang="ts">
const api = useApi()

const { data, pending, error, refresh } = await useAsyncData('dashboard', () =>
  api.get<any>('/stats/dashboard')
)

// The dashboard is a live view of a crawl that is always running somewhere.
const timer = ref<ReturnType<typeof setInterval>>()
onMounted(() => {
  timer.value = setInterval(refresh, 30_000)
})
onUnmounted(() => clearInterval(timer.value))

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const formatMonth = (v: string) => {
  const [y, m] = v.split('-')
  return `${MONTHS[Number(m) - 1]} ${y!.slice(2)}`
}

const tiles = computed(() => {
  const t = data.value?.totals || {}
  return [
    { label: 'Профили', value: t.profiles, icon: 'i-lucide-users' },
    { label: 'Заявки', value: t.candidates, icon: 'i-lucide-file-user' },
    { label: 'Резюме', value: t.resumes, icon: 'i-lucide-file-text' },
    { label: 'Контакты', value: t.contacts, icon: 'i-lucide-at-sign' },
    { label: 'Логи', value: t.logs, icon: 'i-lucide-history' },
    { label: 'Комментарии', value: t.comments, icon: 'i-lucide-message-square' },
    { label: 'Вакансии', value: t.vacancies, icon: 'i-lucide-briefcase' },
    { label: 'Сотрудники', value: t.ats_users, icon: 'i-lucide-id-card' }
  ]
})

const cov = computed(() => data.value?.coverage || {})
</script>

<template>
  <UDashboardPanel id="dashboard">
    <template #header>
      <UDashboardNavbar title="Дашборд">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <UButton
            icon="i-lucide-refresh-cw"
            color="neutral"
            variant="ghost"
            :loading="pending"
            aria-label="Обновить"
            @click="refresh()"
          />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UAlert
        v-if="error"
        color="error"
        variant="subtle"
        icon="i-lucide-triangle-alert"
        title="Не удалось загрузить статистику"
        :description="String((error as any)?.data?.detail || error)"
        class="mb-4"
      />

      <!-- Headline counts: single numbers, so tiles rather than a chart. -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <UCard v-for="t in tiles" :key="t.label" :ui="{ body: 'p-4' }">
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0">
              <p class="text-xs text-muted truncate">{{ t.label }}</p>
              <p class="text-2xl font-semibold tabular-nums mt-0.5">{{ fmt(t.value) }}</p>
            </div>
            <UIcon :name="t.icon" class="size-4 text-dimmed shrink-0 mt-1" />
          </div>
        </UCard>
      </div>

      <UCard class="mb-4">
        <template #header>
          <div class="flex items-center justify-between gap-2">
            <h2 class="font-semibold">Полнота выгрузки</h2>
            <UBadge
              :color="cov.details_pending || cov.activity_pending ? 'warning' : 'success'"
              variant="subtle"
              :label="
                cov.details_pending || cov.activity_pending
                  ? `в очереди: ${fmt((cov.details_pending || 0) + (cov.activity_pending || 0))}`
                  : 'всё собрано'
              "
            />
          </div>
        </template>

        <div class="grid md:grid-cols-2 gap-6">
          <div>
            <div class="flex items-baseline justify-between mb-1.5">
              <span class="text-sm">Резюме и контакты</span>
              <span class="text-sm tabular-nums">
                {{ fmt(cov.details_done) }} / {{ fmt(cov.profiles) }}
                <span class="text-muted ml-1">{{ cov.details_pct }}%</span>
              </span>
            </div>
            <UProgress :model-value="cov.details_pct" :max="100" />
          </div>
          <div>
            <div class="flex items-baseline justify-between mb-1.5">
              <span class="text-sm">Логи и комментарии</span>
              <span class="text-sm tabular-nums">
                {{ fmt(cov.activity_done) }} / {{ fmt(cov.candidates) }}
                <span class="text-muted ml-1">{{ cov.activity_pct }}%</span>
              </span>
            </div>
            <UProgress :model-value="cov.activity_pct" :max="100" />
          </div>
        </div>

        <p v-if="cov.recheck_due" class="text-xs text-muted mt-4">
          Ожидают перепроверки на изменения: {{ fmt(cov.recheck_due) }} заявок —
          фоновая «карусель» разбирает их постепенно.
        </p>
      </UCard>

      <div class="grid lg:grid-cols-2 gap-4">
        <UCard>
          <template #header><h2 class="font-semibold">Новые заявки по месяцам</h2></template>
          <LineChart
            :data="data?.candidates_over_time || []"
            x-key="month"
            :series="[{ key: 'count', label: 'Заявки' }]"
            :format-x="formatMonth"
          />
        </UCard>

        <UCard>
          <template #header><h2 class="font-semibold">Активность рекрутёров</h2></template>
          <LineChart
            :data="data?.activity_over_time || []"
            x-key="month"
            :series="[
              { key: 'logs', label: 'Действия' },
              { key: 'comments', label: 'Комментарии' }
            ]"
            :format-x="formatMonth"
          />
        </UCard>

        <UCard>
          <template #header><h2 class="font-semibold">Заявки по статусам</h2></template>
          <BarList :items="data?.candidates_by_status || []" label-key="status" value-key="count" />
        </UCard>

        <UCard>
          <template #header><h2 class="font-semibold">Города кандидатов</h2></template>
          <BarList :items="data?.top_cities || []" label-key="city" value-key="count" />
        </UCard>

        <UCard>
          <template #header><h2 class="font-semibold">Источники резюме</h2></template>
          <DonutChart :items="data?.resume_sources || []" label-key="source" value-key="count" />
        </UCard>

        <UCard>
          <template #header><h2 class="font-semibold">Нагрузка рекрутёров</h2></template>
          <BarList
            :items="data?.recruiter_workload || []"
            label-key="recruiter"
            value-key="count"
          />
        </UCard>
      </div>
    </template>
  </UDashboardPanel>
</template>
