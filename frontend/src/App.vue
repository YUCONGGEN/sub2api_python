<template>
  <div
    class="app-shell"
    :class="[
      { 'login-shell': standaloneRoute, 'theme-dark': theme === 'dark' },
      themeClass,
    ]"
  >
    <FeedbackModal />
    <template v-if="!standaloneRoute">
      <aside class="sidebar" :class="{ 'menu-open': mobileMenuOpen }">
        <div class="brand" @click="$router.push('/dashboard')">
          <span>{{ siteName }}</span
          ><button
            class="mobile-menu-button"
            type="button"
            :aria-expanded="String(mobileMenuOpen)"
            aria-label="切换导航菜单"
            @click.stop="mobileMenuOpen = !mobileMenuOpen"
          >
            {{ mobileMenuOpen ? "关闭" : "菜单" }}
          </button>
        </div>
        <div class="workspace-label">AI GATEWAY <span>LIVE</span></div>
        <nav @click="mobileMenuOpen = false">
          <router-link to="/dashboard"
            ><span class="nav-index">01</span>控制台</router-link
          >
          <router-link to="/models"
            ><span class="nav-index">02</span>模型广场</router-link
          >
          <router-link to="/billing"
            ><span class="nav-index">03</span>余额与充值</router-link
          >
          <router-link to="/docs"
            ><span class="nav-index">04</span>配置教程</router-link
          >
          <router-link to="/monitoring"
            ><span class="nav-index">05</span>模型监控</router-link
          >
          <router-link to="/keys"
            ><span class="nav-index">06</span>API 密钥</router-link
          >
          <router-link to="/profile"
            ><span class="nav-index">07</span>个人资料</router-link
          >
          <router-link v-if="user && user.role === 'ADMIN'" to="/admin"
            ><span class="nav-index">08</span>管理后台</router-link
          >
          <router-link
            v-if="
              user &&
              (user.role === 'ADMIN' || subscriptionContributionsEnabled)
            "
            to="/subscriptions"
            ><span class="nav-index">09</span>共享账号池</router-link
          >
        </nav>
        <div class="sidebar-bottom">
          <router-link
            to="/monitoring"
            :class="['status-dot', systemStatus.level]"
            ><i></i>{{ systemStatus.text }}</router-link
          >
          <div v-if="user" class="user-mini" @click="logout">
            <div class="avatar">{{ initials }}</div>
            <div>
              <strong>{{ user.username }}</strong
              ><small>退出登录</small>
            </div>
            <span class="arrow">↗</span>
          </div>
        </div>
      </aside>
      <main class="main-content">
        <header class="topbar">
          <button
            class="topbar-menu"
            type="button"
            @click="mobileMenuOpen = !mobileMenuOpen"
          >
            ☰
          </button>
          <div class="crumb">
            <span>{{ siteName }}</span
            ><b>/</b>{{ pageTitle || $route.meta.title }}
          </div>
          <div class="top-actions">
            <button class="theme-switch" type="button" @click="toggleTheme">
              主题：{{ theme === "dark" ? "夜晚" : "白天" }}</button
            ><router-link to="/docs">新手指南 ↗</router-link>
          </div>
        </header>
        <router-view
          :user="user"
          :app-name="siteName"
          :api-base-url="apiBaseUrl"
          :recharge-code-placeholder="rechargeCodePlaceholder"
          :codex-config="codexConfig"
          :model-options="modelOptions"
          :subscription-contributions-enabled="subscriptionContributionsEnabled"
          @refresh-user="refreshUser"
        />
      </main>
    </template>
    <div v-else class="standalone-route">
      <router-view
        :app-name="siteName"
        :api-base-url="apiBaseUrl"
        :recharge-code-placeholder="rechargeCodePlaceholder"
        :codex-config="codexConfig"
        :model-options="modelOptions"
      />
    </div>
  </div>
</template>

<script>
import { api, clearAuthCache } from "./api";

export default {
  name: "App",
  data: () => ({
    user: null,
    siteName: "",
    apiBaseUrl: "",
    rechargeCodePlaceholder: "",
    codexConfig: null,
    modelOptions: [],
    subscriptionContributionsEnabled: false,
    theme: "light",
    mobileMenuOpen: false,
    healthTimer: null,
    systemStatus: { text: "正在检测系统状态", level: "checking" },
  }),
  computed: {
    standaloneRoute() {
      return (
        !!this.$route.meta.guestOnly ||
        (!!this.$route.meta.public && !localStorage.getItem("rose_token"))
      );
    },
    pageTitle() {
      return (
        {
          "/dashboard": "控制台",
          "/models": "模型广场",
          "/billing": "余额与充值",
          "/admin": "管理后台",
          "/admin/upstream-subscriptions": "订阅账号",
          "/subscriptions": "共享账号池",
          "/docs": "配置教程",
          "/monitoring": "模型监控",
          "/keys": "API 密钥",
          "/profile": "个人资料",
          "/conversations": "会话记录",
        }[this.$route.path] || ""
      );
    },
    initials() {
      return this.user?.username
        ? this.user.username.slice(0, 1).toUpperCase()
        : "";
    },
    themeClass() {
      return this.theme === "dark" ? "theme-dark" : "theme-light";
    },
  },
  watch: {
    "$route.path"() {
      this.mobileMenuOpen = false;
      this.loadUser();
      this.updateTitle();
    },
  },
  created() {
    this.loadConfig();
    this.loadUser();
    this.loadTheme();
    this.loadSystemStatus();
    this.healthTimer = window.setInterval(this.loadSystemStatus, 60000);
    this.updateTitle();
    window.addEventListener("rose:auth-expired", this.handleAuthExpired);
  },
  beforeDestroy() {
    window.removeEventListener("rose:auth-expired", this.handleAuthExpired);
    window.clearInterval(this.healthTimer);
  },
  methods: {
    async loadConfig() {
      try {
        const data = await api.publicConfig();
        if (data.name) this.siteName = data.name;
        if (data.api_base_url)
          this.apiBaseUrl = String(data.api_base_url).replace(/\/$/, "");
        if (data.recharge_code_placeholder)
          this.rechargeCodePlaceholder = data.recharge_code_placeholder;
        if (data.codex) this.codexConfig = data.codex;
        if (Array.isArray(data.models)) this.modelOptions = data.models;
        this.subscriptionContributionsEnabled =
          data.subscription_contributions_enabled === true;
        this.updateTitle();
      } catch (e) {}
    },
    async loadSystemStatus() {
      if (!localStorage.getItem("rose_token")) return;
      try {
        const data = await api.monitoring({ page: 1, page_size: 1 });
        const summary = data.summary || {};
        if (!summary.total)
          this.systemStatus = { text: "尚未配置模型", level: "warning" };
        else if (summary.available === summary.total)
          this.systemStatus = {
            text: `系统正常 · ${summary.available} 个模型`,
            level: "healthy",
          };
        else
          this.systemStatus = {
            text: `${summary.total - summary.available} 个模型异常`,
            level: "warning",
          };
      } catch (e) {
        this.systemStatus = { text: "状态检测失败", level: "error" };
      }
    },
    loadTheme() {
      const saved = localStorage.getItem("rose_theme");
      if (saved === "dark" || saved === "light") this.theme = saved;
      this.applyTheme();
    },
    applyTheme() {
      if (typeof document === "undefined") return;
      document.documentElement.setAttribute("data-theme", this.theme);
      localStorage.setItem("rose_theme", this.theme);
    },
    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      this.applyTheme();
    },
    updateTitle() {
      if (typeof document !== "undefined")
        document.title = this.siteName
          ? this.pageTitle
            ? `${this.pageTitle} - ${this.siteName}`
            : this.siteName
          : "";
    },
    async loadUser(force = false) {
      if (!localStorage.getItem("rose_token")) {
        this.user = null;
        return;
      }
      try {
        const data = await api.me({ force });
        this.user = data.user;
      } catch (e) {
        this.logout(false);
      }
    },
    refreshUser() {
      this.loadUser(true);
    },
    handleAuthExpired() {
      this.user = null;
      if (this.$route.path !== "/login")
        this.$router.push("/login").catch(() => {});
    },
    async logout(notifyServer = true) {
      if (notifyServer) {
        try {
          await api.logout();
        } catch (e) {}
      }
      localStorage.removeItem("rose_token");
      window.sessionStorage.removeItem("rose_fresh_api_key");
      clearAuthCache();
      this.user = null;
      if (this.$route.path !== "/login")
        this.$router.push("/login").catch(() => {});
    },
  },
};
</script>
