<template>
  <component :is="tag" v-html="html" />
</template>

<script setup>
import { computed } from 'vue'
import { renderSafeMarkdown } from '../utils/safeMarkdown'

const props = defineProps({
  content: {
    type: [String, Number],
    default: ''
  },
  stripLeadingH2: {
    type: Boolean,
    default: false
  },
  tag: {
    type: String,
    default: 'div'
  }
})

const html = computed(() => renderSafeMarkdown(props.content, { stripLeadingH2: props.stripLeadingH2 }))
</script>
