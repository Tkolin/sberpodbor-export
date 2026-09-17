<script setup lang="ts">
/** Photos live behind the same auth as everything else, so <img src> cannot fetch them
 *  directly — the bytes are pulled with the bearer token and handed to the tag as an
 *  object URL, which is revoked when the component goes away. */
const props = withDefaults(
  defineProps<{ mediaId?: number | null; name?: string; size?: number }>(),
  { size: 36 }
)

const api = useApi()
const url = ref<string | null>(null)
const failed = ref(false)

const initials = computed(() =>
  (props.name || '?')
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() || '')
    .join('')
)

async function load() {
  if (url.value) {
    URL.revokeObjectURL(url.value)
    url.value = null
  }
  failed.value = false
  if (!props.mediaId) return
  const u = await api.blobUrl(`/media/${props.mediaId}/file`)
  if (u) url.value = u
  else failed.value = true
}

watch(() => props.mediaId, load, { immediate: true })
onUnmounted(() => {
  if (url.value) URL.revokeObjectURL(url.value)
})
</script>

<template>
  <div
    class="rounded-full overflow-hidden bg-elevated flex items-center justify-center shrink-0"
    :style="{ width: `${size}px`, height: `${size}px` }"
  >
    <img v-if="url" :src="url" :alt="name || ''" class="w-full h-full object-cover" />
    <span v-else class="text-muted font-medium" :style="{ fontSize: `${size * 0.36}px` }">
      {{ initials }}
    </span>
  </div>
</template>
