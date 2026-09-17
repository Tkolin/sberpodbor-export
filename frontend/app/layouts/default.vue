<script setup lang="ts">
import type { NavigationMenuItem } from '@nuxt/ui'

const { me, access, refresh } = useAuth()
const route = useRoute()
const open = ref(false)

const isAdmin = computed(() => me.value?.role === 'admin')
const canOperate = computed(() => me.value?.role === 'admin' || me.value?.role === 'operator')

const links = computed<NavigationMenuItem[][]>(() => [
  [
    { label: 'Дашборд', icon: 'i-lucide-chart-line', to: '/' },
    { label: 'Профили', icon: 'i-lucide-users', to: '/profiles' },
    { label: 'Вакансии', icon: 'i-lucide-briefcase', to: '/vacancies' },
    { label: 'Выгрузка XML', icon: 'i-lucide-file-code', to: '/exports' },
    { label: 'Синхронизация', icon: 'i-lucide-refresh-cw', to: '/sync' }
  ],
  [
    ...(isAdmin.value
      ? [
          { label: 'Аккаунты ATS', icon: 'i-lucide-key-round', to: '/accounts' },
          { label: 'Пользователи', icon: 'i-lucide-shield', to: '/users' },
          { label: 'Аудит', icon: 'i-lucide-scroll-text', to: '/audit' }
        ]
      : [])
  ]
])

function logout() {
  access.value = null
  refresh.value = null
  me.value = null
  navigateTo('/login')
}

const roleLabel: Record<string, string> = {
  admin: 'администратор',
  operator: 'оператор',
  viewer: 'наблюдатель'
}
</script>

<template>
  <UDashboardGroup unit="rem">
    <UDashboardSidebar id="sp" v-model:open="open" collapsible resizable class="bg-elevated/25">
      <template #header="{ collapsed }">
        <div class="flex items-center gap-2 px-1">
          <UIcon name="i-lucide-database" class="size-5 shrink-0 text-primary" />
          <span v-if="!collapsed" class="font-semibold truncate">Сбер Подбор</span>
        </div>
      </template>

      <template #default="{ collapsed }">
        <UNavigationMenu :collapsed="collapsed" :items="links[0]" orientation="vertical" tooltip />
        <UNavigationMenu
          v-if="links[1].length"
          :collapsed="collapsed"
          :items="links[1]"
          orientation="vertical"
          tooltip
          class="mt-auto"
        />
      </template>

      <template #footer="{ collapsed }">
        <div class="flex items-center gap-2 w-full min-w-0">
          <UAvatar :alt="me?.email || '?'" size="sm" />
          <div v-if="!collapsed" class="min-w-0 flex-1">
            <p class="text-sm truncate">{{ me?.full_name || me?.email }}</p>
            <p class="text-xs text-muted truncate">{{ roleLabel[me?.role || ''] }}</p>
          </div>
          <UButton
            v-if="!collapsed"
            icon="i-lucide-log-out"
            color="neutral"
            variant="ghost"
            size="sm"
            aria-label="Выйти"
            @click="logout"
          />
        </div>
      </template>
    </UDashboardSidebar>

    <slot />
  </UDashboardGroup>
</template>
