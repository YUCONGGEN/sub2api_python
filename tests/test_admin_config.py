from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.controller.admin_config_controller import AdminConfigController
from backend.service.application_config_service import (
    ApplicationConfigService,
    ConfigRevisionConflict,
    ConfigValidationError,
)


def service_for(path: Path) -> ApplicationConfigService:
    service = ApplicationConfigService()
    service.config_path = path
    service.enabled = True
    return service


def test_application_config_read_and_atomic_save_create_backup(tmp_path):
    path = tmp_path / "application.yml"
    original = "server:\n  port: 8241\n"
    path.write_text(original, encoding="utf-8")
    service = service_for(path)

    loaded = service.read()
    saved = service.save("server:\n  port: 9000", loaded["revision"])

    assert path.read_text(encoding="utf-8") == "server:\n  port: 9000\n"
    assert (tmp_path / "application.yml.bak").read_text(encoding="utf-8") == original
    assert saved["content"] == "server:\n  port: 9000\n"
    assert saved["revision"] != loaded["revision"]


def test_application_config_rejects_invalid_yaml_without_overwriting(tmp_path):
    path = tmp_path / "application.yml"
    original = "rose:\n  application: {}\n"
    path.write_text(original, encoding="utf-8")
    service = service_for(path)

    with pytest.raises(ConfigValidationError, match="YAML 校验失败"):
        service.save("rose: [", service.read()["revision"])

    assert path.read_text(encoding="utf-8") == original
    assert not (tmp_path / "application.yml.bak").exists()


def test_application_config_rejects_stale_revision(tmp_path):
    path = tmp_path / "application.yml"
    path.write_text("server: {}\n", encoding="utf-8")
    service = service_for(path)
    revision = service.read()["revision"]
    path.write_text("server:\n  port: 8241\n", encoding="utf-8")

    with pytest.raises(ConfigRevisionConflict, match="其他人修改"):
        service.save("server:\n  port: 9000\n", revision)


def test_application_config_requires_mapping_root(tmp_path):
    path = tmp_path / "application.yml"
    path.write_text("server: {}\n", encoding="utf-8")
    service = service_for(path)

    with pytest.raises(ConfigValidationError, match="顶层"):
        service.save("- one\n- two\n", service.read()["revision"])


def test_backend_restart_is_scheduled_without_running_inside_request(tmp_path, monkeypatch):
    path = tmp_path / "application.yml"
    path.write_bytes(b"server: {}\n")
    service = service_for(path)
    service.restart_delay_seconds = 2.0
    monkeypatch.setattr(service, "_restart_available", lambda: True)
    scheduled = {}

    class FakeTimer:
        def __init__(self, delay, callback):
            scheduled["delay"] = delay
            scheduled["callback"] = callback
            self.daemon = False

        def start(self):
            scheduled["started"] = True

    monkeypatch.setattr("backend.service.application_config_service.threading.Timer", FakeTimer)

    result = service.schedule_restart()

    assert result == {"scheduled": True, "delay_seconds": 2.0}
    assert scheduled == {
        "delay": 2.0,
        "callback": service._spawn_restart,
        "started": True,
    }


def test_admin_config_controller_blocks_non_admin(tmp_path):
    path = tmp_path / "application.yml"
    path.write_text("server: {}\n", encoding="utf-8")
    auth = SimpleNamespace(user_from_authorization=lambda _: {"id": 7, "role": "USER"})
    controller = AdminConfigController(auth, service_for(path))

    response = controller.read_config("Bearer user")

    assert response.code == 403


def test_admin_config_controller_returns_content_to_admin(tmp_path):
    path = tmp_path / "application.yml"
    path.write_bytes(b"server: {}\n")
    auth = SimpleNamespace(user_from_authorization=lambda _: {"id": 1, "role": "ADMIN"})
    controller = AdminConfigController(auth, service_for(path))

    response = controller.read_config("Bearer admin")

    assert response.data["ok"] is True
    assert response.data["filename"] == "application.yml"
    assert response.data["content"] == "server: {}\n"
