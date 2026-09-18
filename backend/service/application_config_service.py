"""Safe administrator access to the application's YAML configuration."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import threading
from datetime import datetime, timezone

import yaml
from springbootai import PostConstruct, Service, get_config


class ConfigEditorDisabled(RuntimeError):
    pass


class ConfigRevisionConflict(RuntimeError):
    pass


class ConfigValidationError(ValueError):
    pass


class RestartUnavailable(RuntimeError):
    pass


class RestartAlreadyScheduled(RuntimeError):
    pass


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@Service("application_config_service")
class ApplicationConfigService:
    """Read and atomically replace one server-controlled YAML file only."""

    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[2]
        configured_path = os.environ.get("ROSE_APPLICATION_CONFIG_FILE", "").strip()
        self.config_path = (
            Path(configured_path).expanduser().resolve()
            if configured_path
            else (self.project_root / "application.yml").resolve()
        )
        restart_path = os.environ.get("ROSE_BACKEND_RESTART_SCRIPT", "").strip()
        self.restart_script = (
            Path(restart_path).expanduser().resolve()
            if restart_path
            else (self.project_root / "restart_backend.sh").resolve()
        )
        self.enabled = True
        self.max_bytes = 1_048_576
        self.restart_delay_seconds = 1.0
        self._write_lock = threading.RLock()
        self._restart_lock = threading.Lock()
        self._restart_pending = False

    @PostConstruct
    def init(self) -> None:
        options = get_config().get("rose", {}).get("admin-config", {}) or {}
        self.enabled = _as_bool(options.get("enabled"), True)
        try:
            requested_max = int(options.get("max-bytes", self.max_bytes))
        except (TypeError, ValueError):
            requested_max = self.max_bytes
        self.max_bytes = max(4096, min(requested_max, 5 * 1024 * 1024))
        try:
            delay = float(options.get("restart-delay-seconds", self.restart_delay_seconds))
        except (TypeError, ValueError):
            delay = self.restart_delay_seconds
        self.restart_delay_seconds = max(0.5, min(delay, 10.0))

    @staticmethod
    def _revision(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise ConfigEditorDisabled("YAML 在线管理功能已关闭")

    def _read_bytes(self) -> bytes:
        self._ensure_enabled()
        try:
            raw = self.config_path.read_bytes()
        except FileNotFoundError as exc:
            raise FileNotFoundError("服务器 application.yml 不存在") from exc
        if len(raw) > self.max_bytes:
            raise ConfigValidationError(f"application.yml 超过 {self.max_bytes} 字节限制")
        return raw

    def _decode(self, raw: bytes) -> str:
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ConfigValidationError("application.yml 必须使用 UTF-8 编码") from exc

    def _restart_available(self) -> bool:
        if os.name == "nt" or not Path("/bin/bash").is_file() or not self.restart_script.is_file():
            return False
        pid_file = self.project_root / ".run" / "backend.pid"
        try:
            return int(pid_file.read_text(encoding="utf-8").strip()) == os.getpid()
        except (OSError, ValueError):
            return False

    def read(self) -> dict:
        with self._write_lock:
            raw = self._read_bytes()
            stat = self.config_path.stat()
            return {
                "filename": self.config_path.name,
                "content": self._decode(raw),
                "revision": self._revision(raw),
                "size": len(raw),
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "backup_filename": f"{self.config_path.name}.bak",
                "restart_available": self._restart_available(),
                "restart_pending": self._restart_pending,
            }

    def validate(self, content: str) -> str:
        if not isinstance(content, str):
            raise ConfigValidationError("配置内容必须是文本")
        if "\x00" in content:
            raise ConfigValidationError("配置内容包含无效字符")
        raw = content.encode("utf-8")
        if len(raw) > self.max_bytes:
            raise ConfigValidationError(f"配置内容超过 {self.max_bytes} 字节限制")
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            location = f"（第 {mark.line + 1} 行，第 {mark.column + 1} 列）" if mark else ""
            detail = str(getattr(exc, "problem", "") or "YAML 语法错误")
            raise ConfigValidationError(f"YAML 校验失败{location}：{detail}") from exc
        if not isinstance(parsed, dict):
            raise ConfigValidationError("YAML 顶层必须是键值对象")
        return content if content.endswith("\n") else content + "\n"

    def save(self, content: str, expected_revision: str) -> dict:
        self._ensure_enabled()
        cleaned = self.validate(content)
        if not str(expected_revision or "").strip():
            raise ConfigRevisionConflict("缺少配置版本，请重新加载后再保存")

        with self._write_lock:
            current = self._read_bytes()
            if self._revision(current) != str(expected_revision).strip():
                raise ConfigRevisionConflict("服务器配置已被其他人修改，请重新加载后再保存")

            target = self.config_path
            backup = target.with_name(f"{target.name}.bak")
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            try:
                shutil.copy2(target, backup)
                with temporary.open("w", encoding="utf-8", newline="") as handle:
                    handle.write(cleaned)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.chmod(temporary, target.stat().st_mode)
                except OSError:
                    pass
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()

        result = self.read()
        result["backup_created"] = backup.name
        return result

    def schedule_restart(self) -> dict:
        self._ensure_enabled()
        if not self._restart_available():
            raise RestartUnavailable("服务器未提供可用的 restart_backend.sh")
        with self._restart_lock:
            if self._restart_pending:
                raise RestartAlreadyScheduled("后端重启已经在执行中")
            self._restart_pending = True
            timer = threading.Timer(self.restart_delay_seconds, self._spawn_restart)
            timer.daemon = True
            timer.start()
        return {"scheduled": True, "delay_seconds": self.restart_delay_seconds}

    def _spawn_restart(self) -> None:
        try:
            subprocess.Popen(
                ["/bin/bash", str(self.restart_script)],
                cwd=str(self.project_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            with self._restart_lock:
                self._restart_pending = False
            raise


__all__ = [
    "ApplicationConfigService",
    "ConfigEditorDisabled",
    "ConfigRevisionConflict",
    "ConfigValidationError",
    "RestartAlreadyScheduled",
    "RestartUnavailable",
]
