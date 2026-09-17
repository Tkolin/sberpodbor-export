<script setup lang="ts">
import { VisSingleContainer, VisDonut, VisTooltip } from '@unovis/vue'

/** Part-to-whole for a handful of slices. Anything past the fifth folds into
 *  "Другие" — a donut stops being readable long before the palette runs out. */
const props = withDefaults(
  defineProps<{
    items: Record<string, any>[]
    labelKey: string
    valueKey: string
    height?: number
    maxSlices?: number
  }>(),
  { height: 220, maxSlices: 5 }
)

const COLORS = [
  'var(--series-1)',
  'var(--series-2)',
  'color-mix(in oklab, var(--series-1) 55%, var(--viz-surface))',
  'color-mix(in oklab, var(--series-2) 55%, var(--viz-surface))',
  'color-mix(in oklab, var(--series-1) 30%, var(--viz-surface))',
  'var(--ui-text-dimmed)'
]

const slices = computed(() => {
  const sorted = [...props.items].sort(
    (a, b) => Number(b[props.valueKey]) - Number(a[props.valueKey])
  )
  const head = sorted.slice(0, props.maxSlices)
  const tail = sorted.slice(props.maxSlices)
  const rest = tail.reduce((s, r) => s + Number(r[props.valueKey] || 0), 0)
  return rest > 0
    ? [...head, { [props.labelKey]: 'Другие', [props.valueKey]: rest }]
    : head
})

const total = computed(() => slices.value.reduce((s, r) => s + Number(r[props.valueKey] || 0), 0))
const value = (d: any) => Number(d[props.valueKey] || 0)
const color = (_: any, i: number) => COLORS[i % COLORS.length]
</script>

<template>
  <div class="viz-root">
    <p v-if="!slices.length" class="text-sm text-muted py-6 text-center">Нет данных</p>

    <template v-else>
      <VisSingleContainer :data="slices" :height="height">
        <VisDonut
          :value="value"
          :color="color"
          :arc-width="26"
          :corner-radius="3"
          :pad-angle="0.012"
        />
        <VisTooltip />
      </VisSingleContainer>

      <!-- Legend is mandatory past one series; percentages keep identity off colour alone. -->
      <div class="grid grid-cols-2 gap-x-4 gap-y-1 mt-3">
        <div
          v-for="(s, i) in slices"
          :key="String(s[labelKey])"
          class="flex items-center gap-2 text-xs min-w-0"
        >
          <span class="size-2 rounded-[2px] shrink-0" :style="{ background: COLORS[i % COLORS.length] }" />
          <span class="truncate">{{ s[labelKey] }}</span>
          <span class="ml-auto tabular-nums text-muted shrink-0">
            {{ total ? ((Number(s[valueKey]) / total) * 100).toFixed(1) : '0' }}%
          </span>
        </div>
      </div>
    </template>
  </div>
</template>
