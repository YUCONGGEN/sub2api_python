import Vue from 'vue'

export const uiBus = new Vue()

export function notify (message, variant = 'info') {
  uiBus.$emit('notify', { message: String(message || ''), variant })
}

export function askConfirm (message) {
  return new Promise(resolve => uiBus.$emit('confirm', { message: String(message || ''), resolve }))
}

export function focusDialog (element) {
  if (!element) return
  const target = element.querySelector('[autofocus],input,select,textarea,button,[href]') || element
  target.focus({ preventScroll: true })
}

export function trapDialogFocus (event, element) {
  if (event.key !== 'Tab' || !element) return
  const items = Array.from(element.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])'))
  if (!items.length) return
  const first = items[0]
  const last = items[items.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}

// Works on both HTTPS and ordinary localhost HTTP pages. Browsers can reject
// navigator.clipboard outside a secure context, so retain a DOM fallback.
export async function copyToClipboard (value) {
  const text = String(value ?? '')
  if (!text) return false
  const location = typeof window !== 'undefined' ? window.location : null
  const secureContext = typeof window === 'undefined' || window.isSecureContext || location?.protocol === 'https:' || location?.hostname === 'localhost' || location?.hostname === '127.0.0.1'
  try {
    if (secureContext && typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch (error) {}
  if (typeof document === 'undefined' || !document.body || typeof document.execCommand !== 'function') return false
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.setAttribute('aria-hidden', 'true')
  Object.assign(textarea.style, { position: 'fixed', left: '-10000px', top: '0', width: '1px', height: '1px', padding: '0', opacity: '0' })
  document.body.appendChild(textarea)
  let copied = false
  try {
    textarea.focus({ preventScroll: true })
    textarea.select()
    textarea.setSelectionRange(0, text.length)
    copied = Boolean(document.execCommand('copy'))
  } catch (error) {
    copied = false
  } finally {
    textarea.remove()
  }
  return copied
}
