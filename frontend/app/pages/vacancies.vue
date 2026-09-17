<script setup lang="ts">
const api = useApi()

const statusFilter = ref<string | undefined>()
const page = ref(1)
const perPage = 50

const params = computed(() => ({
  status_filter: statusFilter.value,
  page: page.value,
  per_page: perPage
}))

const { data, pending } = await useAsyncData(
  'vacancies',
  () => api.get<any>('/data/vacancies', params.value),
  { watch: [params] }
)

watch(statusFilter, () => {
  page.value = 1
})

const STATUS_LABEL: Record<string, string> = {
  open: 'открыта',
  closed: 'закрыта',
  paused: 'на паузе',
  draft: 'черновик'
}
const statusOptions = [
  { label: 'Все', value: undefined },
  { label: 'Открытые', value: 'open' },
  { label: 'Закрытые', value: 'closed' }
]

const dt = (v: string | null) => (v ? new Date(v).toLocaleDateString('ru-RU') : '—')
</script>

<template>
  <UDashboardPanel id="vacancies">
    <template #header>
      <UDashboardNavbar title="Вакансии">
        <template #leading><UDashboardSidebarCollapse /></template>
        <template #right>
          <UBadge variant="subtle" color="neutral" :label="`всего: ${fmt(data?.total)}`" />
        </template>
      </UDashboardNavbar>
      <UDashboardToolbar>
        <USelect v-model="statusFilter" :items="statusOptions" class="w-44" />
      </UDashboardToolbar>
    </template>

    <template #body>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="text-left text-muted border-b border-default">
            <tr>
              <th class="py-2 pr-3 font-medium">Название</th>
              <th class="py-2 pr-3 font-medium">Статус</th>
              <th class="py-2 pr-3 font-medium">Город</th>
              <th class="py-2 pr-3 font-medium text-right">Человек</th>
              <th class="py-2 pr-3 font-medium">Создана</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="v in data?.items || []" :key="v.id" class="border-b border-default/50">
              <td class="py-2 pr-3">{{ v.title || `#${v.id}` }}</td>
              <td class="py-2 pr-3">
                <UBadge
                  :color="v.status === 'open' ? 'success' : 'neutral'"
                  variant="subtle"
                  size="sm"
                  :label="STATUS_LABEL[v.status] || v.status || '—'"
                />
              </td>
              <td class="py-2 pr-3 text-muted">{{ v.city || '—' }}</td>
              <td class="py-2 pr-3 text-right tabular-nums">{{ v.persons_count ?? '—' }}</td>
              <td class="py-2 pr-3 text-muted">{{ dt(v.created_at) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!pending && !data?.items?.length" class="text-center text-muted py-10">
          Ничего не найдено
        </p>
      </div>

      <div v-if="(data?.total || 0) > perPage" class="flex justify-center mt-4">
        <UPagination v-model:page="page" :total="data?.total || 0" :items-per-page="perPage" />
      </div>
    </template>
  </UDashboardPanel>
</template>
