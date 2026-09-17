<script setup lang="ts">
import { VisXYContainer, VisLine, VisArea, VisAxis, VisCrosshair, VisTooltip } from '@unovis/vue'

/** Time series. One or two measures on ONE axis — both are counts, so a second
 *  y-scale would be a lie about their relative size. */
const props = withDefaults(
  defineProps<{
    data: Record<string, any>[]
    xKey: string
    series: { key: string; label: string }[]
    height?: number
    /** Format for the x tick labels; the raw key is a 'YYYY-MM' string. */
    formatX?: (v: string) => string
  }>(),
  { height: 240 }
)

const COLORS = ['var(--series-1)', 'var(--series-2)']

const rows = computed(() => props.data.map((d, i) => ({ ...d, _i: i })))
const x = (d: any) => d._i
const accessors = computed(() => props.series.map((s) => (d: any) => Number(d[s.key] ?? 0)))

const tickFormat = (i: number) => {
  const row = rows.value[Math.round(i)]
  if (!row) return ''
  const raw = String(row[props.xKey])
  return props.formatX ? props.formatX(raw) : raw
}

// Show ~8 ticks regardless of range, so labels never collide.
const tickValues = computed(() => {
  const n = rows.value.length
  if (n <= 8) return rows.value.map((_, i) => i)
  const step = Math.ceil(n / 8)
  return rows.value.map((_, i) => i).filter((i) => i % step === 0)
})

const tooltipTemplate = (d: any) => {
  const label = props.formatX ? props.formatX(String(d[props.xKey])) : String(d[props.xKey])
  const lines = props.series
    .map(
      (s, i) =>
        `<div style="display:flex;align-items:center;gap:6px">
           <span style="width:8px;height:8px;border-radius:2px;background:${COLORS[i]}"></span>
           <span>${s.label}</span>
           <b style="margin-left:auto">${Number(d[s.key] ?? 0).toLocaleString('ru-RU')}</b>
         </div>`
    )
    .join('')
  return `<div style="font-size:12px;min-width:150px"><div style="opacity:.7;margin-bottom:4px">${label}</div>${lines}</div>`
}
</script>

<template>
  <div class="viz-root">
    <!-- Two series always carry a legend; one is named by the card title. -->
    <div v-if="series.length > 1" class="flex items-center gap-4 mb-2 text-xs text-muted">
      <span v-for="(s, i) in series" :key="s.key" class="inline-flex items-center gap-1.5">
        <span class="size-2 rounded-[2px]" :style="{ background: COLORS[i] }" />
        {{ s.label }}
      </span>
    </div>

    <VisXYContainer :data="rows" :height="height" :margin="{ left: 8, right: 8, top: 8, bottom: 4 }">
      <VisArea
        v-if="series.length === 1"
        :x="x"
        :y="accessors[0]"
        :color="COLORS[0]"
        :opacity="0.12"
      />
      <VisLine :x="x" :y="accessors" :color="COLORS" :line-width="2" />
      <VisAxis type="x" :tick-format="tickFormat" :tick-values="tickValues" :grid-line="false" />
      <VisAxis
        type="y"
        :tick-format="(v: number) => v.toLocaleString('ru-RU')"
        :num-ticks="4"
      />
      <VisCrosshair :template="tooltipTemplate" :color="COLORS[0]" />
      <VisTooltip />
    </VisXYContainer>
  </div>
</template>
