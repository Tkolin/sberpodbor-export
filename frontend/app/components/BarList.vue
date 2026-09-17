<script setup lang="ts">
/** Ranked categories. Deliberately not an SVG bar chart: these labels are long
 *  Russian phrases ("Интервью с компанией 1"), and any vertical-bar form would have
 *  to rotate or truncate them. A horizontal list direct-labels every row instead,
 *  which is the point of the form. */
const props = withDefaults(
  defineProps<{
    items: Record<string, any>[]
    labelKey: string
    valueKey: string
    max?: number
  }>(),
  { max: 10 }
)

const rows = computed(() => props.items.slice(0, props.max))
const peak = computed(() => Math.max(1, ...rows.value.map((r) => Number(r[props.valueKey]) || 0)))
const total = computed(() =>
  props.items.reduce((s, r) => s + (Number(r[props.valueKey]) || 0), 0)
)
</script>

<template>
  <div class="viz-root space-y-2">
    <p v-if="!rows.length" class="text-sm text-muted py-6 text-center">Нет данных</p>

    <div v-for="row in rows" :key="String(row[labelKey])" class="group">
      <div class="flex items-baseline justify-between gap-3 mb-1">
        <span class="text-sm truncate" :title="String(row[labelKey])">
          {{ row[labelKey] || '—' }}
        </span>
        <span class="text-sm font-medium tabular-nums shrink-0">
          {{ fmt(row[valueKey]) }}
          <span class="text-muted text-xs ml-1">
            {{ total ? ((row[valueKey] / total) * 100).toFixed(1) : '0' }}%
          </span>
        </span>
      </div>
      <!-- Thin mark, rounded data-end, anchored to the baseline. -->
      <div class="h-1.5 rounded-full bg-elevated overflow-hidden">
        <div
          class="h-full rounded-full transition-[width] duration-500"
          :style="{
            width: `${Math.max(1.5, (Number(row[valueKey]) / peak) * 100)}%`,
            background: 'var(--series-1)'
          }"
        />
      </div>
    </div>
  </div>
</template>
