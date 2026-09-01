"""Configuration beans backed by ``application.yml``.

Business services continue to read the complete configuration through
``get_config`` because the model catalog is intentionally dynamic.  These
beans provide the stable application metadata through SpringBootAI's
annotation lifecycle and keep configuration concerns out of controllers.
"""

from __future__ import annotations

from springbootai.annotations import Bean, Configuration, ConfigurationProperties, PostConstruct, Slf4j, Value


@Configuration
@Slf4j
class AppConfig:
    """Expose safe, non-secret application metadata as a managed Bean."""

    @Value("rose.application.name", default="大模型接口管理")
    def __init__(self, application_name: str = "大模型接口管理"):
        self.application_name = str(application_name or "大模型接口管理")

    @PostConstruct
    def init(self):
        if getattr(self, "_config_logged", False):
            return
        self._config_logged = True
        self.logger.info("应用配置已加载: %s", self.application_name)

    @Bean(name="application_metadata")
    def application_metadata(self) -> dict[str, str]:
        return {
            "name": self.application_name,
            "framework": "springbootAI",
        }


@Configuration
@ConfigurationProperties(prefix="rose.application")
class ApplicationProperties:
    """Typed holder for the public application name from YAML."""

    def __init__(self):
        self.name: str = "大模型接口管理"


__all__ = ["AppConfig", "ApplicationProperties"]
