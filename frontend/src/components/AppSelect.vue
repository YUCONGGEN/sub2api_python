<template>
  <div ref="root" class="app-select" :class="{ open, disabled }">
    <button
      ref="trigger"
      type="button"
      class="app-select-trigger"
      :disabled="disabled"
      :aria-expanded="String(open)"
      aria-haspopup="listbox"
      @click="toggle"
      @keydown.down.prevent="openAndMove(1)"
      @keydown.up.prevent="openAndMove(-1)"
      @keydown.esc.prevent="close"
    >
      <span><b>{{ selectedOption ? selectedOption.label : placeholder }}</b><small v-if="selectedOption && selectedOption.description">{{ selectedOption.description }}</small></span>
      <i aria-hidden="true"></i>
    </button>
    <div v-if="open" class="app-select-menu" role="listbox" :aria-label="ariaLabel">
      <button
        v-for="(option, index) in options"
        :key="optionKey(option, index)"
        type="button"
        role="option"
        :aria-selected="String(isSelected(option))"
        :class="{ selected: isSelected(option) }"
        :disabled="option.disabled"
        @click="choose(option)"
        @keydown.down.prevent="focusOption(index + 1)"
        @keydown.up.prevent="focusOption(index - 1)"
        @keydown.esc.prevent="close(true)"
      >
        <span><b>{{ option.label }}</b><small v-if="option.description">{{ option.description }}</small></span>
        <i v-if="isSelected(option)" aria-hidden="true">✓</i>
      </button>
    </div>
  </div>
</template>

<script>
export default {
  name: 'AppSelect',
  props: {
    value: { default: null },
    options: { type: Array, default: () => [] },
    placeholder: { type: String, default: '请选择' },
    ariaLabel: { type: String, default: '选项' },
    disabled: Boolean
  },
  data: () => ({ open: false }),
  computed: {
    selectedOption () { return this.options.find(option => this.sameValue(option.value, this.value)) || null }
  },
  mounted () { document.addEventListener('mousedown', this.handleOutside) },
  beforeDestroy () { document.removeEventListener('mousedown', this.handleOutside) },
  methods: {
    sameValue (left, right) { return left === right || (left != null && right != null && String(left) === String(right)) },
    isSelected (option) { return this.sameValue(option.value, this.value) },
    optionKey (option, index) { return option.key == null ? `${String(option.value)}-${index}` : option.key },
    toggle () { if (!this.disabled) this.open ? this.close() : this.openAndMove(0, false) },
    close (restoreFocus = false) { this.open = false; if (restoreFocus) this.$nextTick(() => this.$refs.trigger?.focus?.()) },
    choose (option) { if (option.disabled) return; this.$emit('input', option.value); this.$emit('change', option.value); this.close(true) },
    openAndMove (direction, focus = true) { if (this.disabled) return; this.open = true; if (focus) this.$nextTick(() => { const selected = Math.max(0, this.options.findIndex(this.isSelected)); this.focusOption(selected + direction) }) },
    focusOption (index) { const buttons = Array.from(this.$refs.root?.querySelectorAll('.app-select-menu button:not(:disabled)') || []); if (!buttons.length) return; buttons[(index + buttons.length) % buttons.length].focus() },
    handleOutside (event) { if (this.open && !this.$refs.root?.contains(event.target)) this.close() }
  }
}
</script>

<style scoped>
.app-select{position:relative;min-width:0}.app-select-trigger{display:flex;align-items:center;justify-content:space-between;gap:14px;width:100%;min-height:50px;padding:10px 14px;border:1px solid var(--line);border-radius:12px;background:var(--paper);color:var(--ink);text-align:left;transition:border-color .18s,box-shadow .18s,background .18s}.app-select-trigger:hover,.app-select.open .app-select-trigger{border-color:#70a1be;box-shadow:0 0 0 3px #70a1be18}.app-select-trigger>span,.app-select-menu button>span{display:grid;gap:3px;min-width:0}.app-select-trigger b,.app-select-menu b{overflow:hidden;font:500 12px var(--sans);text-overflow:ellipsis;white-space:nowrap}.app-select-trigger small,.app-select-menu small{overflow:hidden;color:var(--muted);font:9px var(--mono);text-overflow:ellipsis;white-space:nowrap}.app-select-trigger>i{width:8px;height:8px;flex:0 0 8px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(45deg) translateY(-2px);transition:transform .18s}.app-select.open .app-select-trigger>i{transform:rotate(225deg) translate(-2px,-1px)}.app-select-menu{position:absolute;z-index:80;top:calc(100% + 6px);left:0;right:0;display:grid;max-height:250px;overflow:auto;padding:6px;border:1px solid var(--line);border-radius:13px;background:var(--card);box-shadow:0 18px 45px #20384d2b}.app-select-menu button{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;min-height:46px;padding:9px 11px;border:0;border-radius:9px;background:transparent;color:var(--ink);text-align:left}.app-select-menu button:hover,.app-select-menu button:focus{outline:0;background:color-mix(in srgb,var(--blue) 52%,var(--card))}.app-select-menu button.selected{background:color-mix(in srgb,var(--acid) 21%,var(--card))}.app-select-menu button>i{color:#43836b;font-style:normal}.app-select.disabled{opacity:.6;pointer-events:none}:global(html[data-theme="dark"]) .app-select-trigger{background:#142330;border-color:#365064}:global(html[data-theme="dark"]) .app-select-menu{background:#172735;border-color:#365064;box-shadow:0 20px 48px #03070bbb}:global(html[data-theme="dark"]) .app-select-menu button:hover,:global(html[data-theme="dark"]) .app-select-menu button:focus{background:#20394a}:global(html[data-theme="dark"]) .app-select-menu button.selected{background:#244638}
.app-select-trigger{min-height:43px;padding:3px 11px;border-color:#c7d9e5;border-radius:10px;background:#ffffffd9;color:#214662;font:12px var(--sans);box-shadow:0 2px 8px #45647a0b}
.app-select-trigger>span{gap:1px}
.app-select-trigger b,.app-select-menu b{color:inherit;font:400 12px/16px var(--sans)}
.app-select-trigger small,.app-select-menu small{color:#8296a5;font:9px/12px var(--sans)}
.app-select-menu{border-color:#c7d9e5;border-radius:10px;background:#fff}
.app-select-menu button{color:#214662;font:12px var(--sans)}
</style>
