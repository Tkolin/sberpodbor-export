<script setup lang="ts">
const api = useApi()

const q = ref('')
const city = ref<string | undefined>()
const statusTitle = ref<string | undefined>()
const vacancyId = ref<number | undefined>()
const withResume = ref<boolean | undefined>()
const page = ref(1)
const perPage = 50

const { data: facets } = await useAsyncData('facets', () => api.get<any>('/data/facets'))

const params = computed(() => ({
  q: q.value || undefined,
  city: city.value,
  status_title: statusTitle.value,
  vacancy_id: vacancyId.value,
  with_resume: withResume.value,
  page: page.value,
  per_page: perPage
}))

const { data, pending, refresh } = await useAsyncData(
  'profiles',
  () => api.get<any>('/data/profiles', params.value),
  { watch: [params] }
)

// Any filter change invalidates the current page number.
watch([q, city, statusTitle, vacancyId, withResume], () => {
  page.value = 1
})

const selected = ref<number | null>(null)
const { data: detail, pending: detailPending } = await useAsyncData(
  'profile-detail',
  () => (selected.value ? api.get<any>(`/data/profiles/${selected.value}`) : Promise.resolve(null)),
  { watch: [selected] }
)

const fullName = (p: any) =>
  [p.last_name, p.first_name, p.middle_name].filter(Boolean).join(' ') || `#${p.id}`

const dt = (v: string | null) => (v ? new Date(v).toLocaleDateString('ru-RU') : '—')

const photoId = computed(
  () => detail.value?.media?.find((m: any) => m.kind === 'photo' && m.status === 'done')?.id
)
const resumeFile = computed(
  () => detail.value?.media?.find((m: any) => m.kind === 'resume_file' && m.status === 'done')
)
const kb = (n: number | null) => (n ? `${Math.round(n / 1024)} КБ` : '')

const period = (w: any) => {
  const f = (d: string | null) => (d ? new Date(d).toLocaleDateString('ru-RU', { month: '2-digit', year: 'numeric' }) : '?')
  return `${f(w.started_at)} — ${w.is_current ? 'по наст. время' : f(w.finished_at)}`
}

const downloading = ref(false)
async function downloadResume() {
  if (!resumeFile.value) return
  downloading.value = true
  try {
    const u = await api.blobUrl(`/media/${resumeFile.value.id}/file`)
    if (!u) return
    const a = document.createElement('a')
    a.href = u
    a.download = `resume-${selected.value}.pdf`
    a.click()
    URL.revokeObjectURL(u)
  } finally {
    downloading.value = false
  }
}

const resumeOptions = [
  { label: 'Все', value: undefined },
  { label: 'С резюме', value: true },
  { label: 'Без резюме', value: false }
]
</script>

<template>
  <UDashboardPanel id="profiles">
    <template #header>
      <UDashboardNavbar title="Профили">
        <template #leading><UDashboardSidebarCollapse /></template>
        <template #right>
          <UBadge variant="subtle" color="neutral" :label="`найдено: ${fmt(data?.total)}`" />
        </template>
      </UDashboardNavbar>

      <UDashboardToolbar>
        <div class="flex flex-wrap gap-2 w-full">
          <UInput
            v-model="q"
            icon="i-lucide-search"
            placeholder="Имя, почта или телефон"
            class="min-w-56 flex-1"
            :loading="pending"
          />
          <USelectMenu
            v-model="city"
            :items="facets?.cities || []"
            placeholder="Город"
            class="w-44"
            searchable
            clearable
          />
          <USelectMenu
            v-model="statusTitle"
            :items="facets?.statuses || []"
            placeholder="Статус заявки"
            class="w-52"
            searchable
            clearable
          />
          <USelectMenu
            v-model="vacancyId"
            :items="facets?.vacancies || []"
            label-key="title"
            value-key="id"
            placeholder="Вакансия"
            class="w-56"
            searchable
            clearable
          />
          <USelect v-model="withResume" :items="resumeOptions" class="w-36" />
        </div>
      </UDashboardToolbar>
    </template>

    <template #body>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="text-left text-muted border-b border-default">
            <tr>
              <th class="py-2 pr-3 font-medium">ФИО</th>
              <th class="py-2 pr-3 font-medium">Город</th>
              <th class="py-2 pr-3 font-medium">Должность</th>
              <th class="py-2 pr-3 font-medium">Контакты</th>
              <th class="py-2 pr-3 font-medium">Собрано</th>
              <th class="py-2 w-8" />
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="p in data?.items || []"
              :key="p.id"
              class="border-b border-default/50 hover:bg-elevated/40 cursor-pointer"
              @click="selected = p.id"
            >
              <td class="py-2 pr-3">
                <div class="flex items-center gap-2 min-w-0">
                  <ProfilePhoto :media-id="data?.photos?.[String(p.id)]" :name="fullName(p)" :size="30" />
                  <span class="truncate">{{ fullName(p) }}</span>
                </div>
              </td>
              <td class="py-2 pr-3 text-muted">{{ p.city || '—' }}</td>
              <td class="py-2 pr-3 text-muted truncate max-w-56">
                {{ p.cur_position || '—' }}
                <span v-if="p.cur_company" class="text-dimmed">· {{ p.cur_company }}</span>
              </td>
              <td class="py-2 pr-3 text-muted">
                <span class="block truncate max-w-48">{{ p.email || '—' }}</span>
                <span class="block text-xs text-dimmed">{{ p.phone || '' }}</span>
              </td>
              <td class="py-2 pr-3">
                <UBadge
                  :color="p.details_synced_at ? 'success' : 'neutral'"
                  variant="subtle"
                  size="sm"
                  :label="p.details_synced_at ? dt(p.details_synced_at) : 'в очереди'"
                />
              </td>
              <td class="py-2">
                <UIcon name="i-lucide-chevron-right" class="size-4 text-dimmed" />
              </td>
            </tr>
          </tbody>
        </table>

        <p v-if="!pending && !data?.items?.length" class="text-center text-muted py-10">
          Ничего не найдено
        </p>
      </div>

      <div v-if="(data?.total || 0) > perPage" class="flex justify-center mt-4">
        <UPagination
          v-model:page="page"
          :total="data?.total || 0"
          :items-per-page="perPage"
        />
      </div>

      <USlideover
        :open="selected !== null"
        :title="detail ? fullName(detail.profile) : 'Профиль'"
        @update:open="(v: boolean) => { if (!v) selected = null }"
      >
        <template #body>
          <div v-if="detailPending" class="py-10 text-center text-muted">Загрузка…</div>

          <div v-else-if="detail" class="space-y-5">
            <div class="flex items-start gap-4">
              <ProfilePhoto
                :media-id="photoId"
                :name="fullName(detail.profile)"
                :size="88"
              />
              <div class="min-w-0 flex-1 space-y-2">
                <p class="text-sm text-muted">{{ detail.profile.email || '—' }}</p>
                <p class="text-sm text-muted">{{ detail.profile.phone || '' }}</p>
                <UButton
                  v-if="resumeFile"
                  size="xs"
                  variant="soft"
                  icon="i-lucide-file-down"
                  :loading="downloading"
                  :label="`Оригинал резюме (${kb(resumeFile.file_size)})`"
                  @click="downloadResume"
                />
              </div>
            </div>

            <div class="grid grid-cols-2 gap-3 text-sm">
              <div>
                <p class="text-xs text-muted">Город</p>
                <p>{{ detail.profile.city || '—' }}</p>
              </div>
              <div>
                <p class="text-xs text-muted">Опыт</p>
                <p>{{ detail.profile.experience || '—' }}</p>
              </div>
              <div class="col-span-2">
                <p class="text-xs text-muted">Сейчас</p>
                <p>
                  {{ detail.profile.cur_position || '—' }}
                  <span v-if="detail.profile.cur_company" class="text-muted">
                    · {{ detail.profile.cur_company }}
                  </span>
                </p>
              </div>
            </div>

            <div v-if="detail.contacts?.length">
              <h3 class="font-medium mb-2">Контакты</h3>
              <div class="flex flex-wrap gap-2">
                <UBadge
                  v-for="c in detail.contacts"
                  :key="c.id"
                  variant="subtle"
                  color="neutral"
                  :label="`${c.type}: ${c.value}`"
                />
              </div>
            </div>

            <div v-if="detail.work_history?.length">
              <h3 class="font-medium mb-2">Опыт работы ({{ detail.work_history.length }})</h3>
              <div class="space-y-2">
                <div
                  v-for="w in detail.work_history"
                  :key="w.id"
                  class="rounded-md border border-default p-2.5 text-sm"
                >
                  <div class="flex items-start justify-between gap-2">
                    <span class="font-medium">{{ w.company || '—' }}</span>
                    <span class="text-xs text-muted shrink-0">{{ period(w) }}</span>
                  </div>
                  <p class="text-muted">{{ w.position || '' }}</p>
                  <p v-if="w.description" class="text-xs mt-1 whitespace-pre-wrap text-dimmed">
                    {{ w.description }}
                  </p>
                </div>
              </div>
            </div>

            <div v-if="detail.candidates?.length">
              <h3 class="font-medium mb-2">Заявки ({{ detail.candidates.length }})</h3>
              <div class="space-y-2">
                <div
                  v-for="c in detail.candidates"
                  :key="c.candidate_id"
                  class="rounded-md border border-default p-2.5 text-sm"
                >
                  <div class="flex items-start justify-between gap-2">
                    <span class="font-medium">{{ c.vacancy_title || `Вакансия #${c.vacancy_id}` }}</span>
                    <UBadge variant="subtle" size="sm" :label="c.status_title || '—'" />
                  </div>
                  <p class="text-xs text-muted mt-1">Создана: {{ dt(c.created_at) }}</p>
                </div>
              </div>
            </div>

            <div v-if="detail.comments?.length">
              <h3 class="font-medium mb-2">Комментарии ({{ detail.comments.length }})</h3>
              <div class="space-y-2">
                <div v-for="c in detail.comments" :key="c.id" class="text-sm">
                  <p class="text-xs text-muted">
                    {{ c.user_full_name || 'без автора' }} · {{ dt(c.created_at) }}
                  </p>
                  <p class="whitespace-pre-wrap">{{ c.comment }}</p>
                </div>
              </div>
            </div>

            <div v-if="detail.logs?.length">
              <h3 class="font-medium mb-2">История ({{ detail.logs.length }})</h3>
              <ul class="space-y-1 text-sm">
                <li v-for="l in detail.logs" :key="l.id" class="flex gap-2">
                  <span class="text-xs text-dimmed shrink-0 w-20">{{ dt(l.date_time_at) }}</span>
                  <span class="min-w-0">{{ l.message }}</span>
                </li>
              </ul>
            </div>

            <div v-if="detail.resume?.body_html">
              <h3 class="font-medium mb-2">
                Резюме
                <UBadge
                  v-if="detail.resume.source"
                  size="sm"
                  variant="subtle"
                  :label="detail.resume.source"
                />
              </h3>
              <!-- Resume HTML comes from the ATS. Rendered as text, never v-html:
                   this is third-party markup and the panel shows personal data. -->
              <pre class="text-xs whitespace-pre-wrap bg-elevated/50 rounded p-3 max-h-80 overflow-auto">{{ detail.resume.body_html }}</pre>
            </div>
          </div>
        </template>
      </USlideover>
    </template>
  </UDashboardPanel>
</template>
