<template>
  <div>
    <transition name="toast-fade">
      <div v-if="toast" class="toast" :class="`toast-${toast.variant}`" role="status">{{ toast.message }}</div>
    </transition>
    <div v-if="confirmState" class="modal-backdrop feedback-backdrop">
      <div class="modal-card feedback-card" role="dialog" aria-modal="true">
        <span class="eyebrow">CONFIRM ACTION</span>
        <h2>请确认</h2>
        <p>{{ confirmState.message }}</p>
        <div class="feedback-actions">
          <button class="secondary-btn" @click="finishConfirm(false)">取消</button>
          <button class="primary-btn" @click="finishConfirm(true)">确定</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { uiBus } from '../ui'

export default {
  name: 'FeedbackModal',
  data: () => ({ toast: null, confirmState: null, toastTimer: null }),
  created () {
    uiBus.$on('notify', this.showToast)
    uiBus.$on('confirm', state => { this.confirmState = state })
  },
  beforeDestroy () {
    uiBus.$off('notify', this.showToast)
    if (this.toastTimer) clearTimeout(this.toastTimer)
  },
  methods: {
    showToast (toast) {
      this.toast = toast
      if (this.toastTimer) clearTimeout(this.toastTimer)
      this.toastTimer = setTimeout(() => { this.toast = null }, 3000)
    },
    finishConfirm (value) {
      const state = this.confirmState
      this.confirmState = null
      if (state?.resolve) state.resolve(value)
    }
  }
}
</script>
