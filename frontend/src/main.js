import Vue from 'vue'
import App from './App.vue'
import router from './router'
import './styles.css'
import FeedbackModal from './components/FeedbackModal.vue'

Vue.config.productionTip = false
Vue.component('FeedbackModal', FeedbackModal)

// Mount immediately.  The router guard still handles the first redirect, but
// waiting for `onReady` can leave the whole page blank when a stale token or a
// failed first navigation prevents the initial transition from completing.
new Vue({ router, render: h => h(App) }).$mount('#app')
