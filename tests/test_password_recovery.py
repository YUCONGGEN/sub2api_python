from unittest.mock import patch

import pytest

from backend.service.password_recovery_service import PasswordRecoveryService
from backend.service.payment_service import PaymentService


SECRET = "test-password-recovery-secret-at-least-32-characters"


def recovery_config(**overrides):
    recovery = {
        "enabled": True,
        "reset-token-minutes": 15,
        "request-cooldown-seconds": 60,
        "frontend-base-url": "https://example.test",
        "admin-email": "admin@example.test",
        "token-secret": SECRET,
        "smtp": {"username": "sender@example.test", "password": "not-a-real-secret", "sender": "sender@example.test"},
    }
    recovery.update(overrides)
    return {"rose": {"password-recovery": recovery}, "jwt": {"secret_key": SECRET}}


class RecoveryStore:
    def __init__(self, email="user@example.test"):
        self.user = {"id": 7, "username": "alice", "email": email, "enabled": 1, "session_version": 3}

    def find_by_username(self, username):
        return self.user if username == "alice" else None

    def find_user(self, user_id):
        return self.user if int(user_id) == 7 else None


def test_reset_link_is_invalid_after_session_version_changes():
    store = RecoveryStore()
    service = PasswordRecoveryService(store)
    with patch("backend.service.password_recovery_service.get_config", return_value=recovery_config()):
        token = service.create_token(store.user)
        assert service.verify_token(token)["id"] == 7
        store.user["session_version"] += 1
        with pytest.raises(ValueError, match="已使用或已过期"):
            service.verify_token(token)


def test_missing_email_uses_contact_admin_flow_without_sending_mail():
    service = PasswordRecoveryService(RecoveryStore(email=""))
    with patch("backend.service.password_recovery_service.get_config", return_value=recovery_config()), patch.object(service, "_send_mail") as send:
        result = service.request_reset("alice")
    assert result["channel"] == "contact_admin"
    assert "未填写邮箱" in result["message"]
    send.assert_not_called()


def test_lookup_shows_masked_email_or_contact_route():
    service = PasswordRecoveryService(RecoveryStore())
    with patch("backend.service.password_recovery_service.get_config", return_value=recovery_config()):
        found = service.lookup_account("alice")
        missing = service.lookup_account("missing")
    assert found == {
        "email_available": True,
        "channel": "email",
        "masked_email": "us***@example.test",
        "message": "已找到绑定邮箱，请确认后发送验证码。",
    }
    assert missing["email_available"] is False
    assert missing["channel"] == "contact_admin"


def test_verification_code_is_single_use_and_not_stored_in_plaintext():
    PasswordRecoveryService._attempts.clear()
    PasswordRecoveryService._codes.clear()
    service = PasswordRecoveryService(RecoveryStore())
    captured = {}

    def capture_mail(_recipient, _subject, content):
        import re
        captured["code"] = re.search(r"(\d{6})", content).group(1)

    with patch("backend.service.password_recovery_service.get_config", return_value=recovery_config()), patch.object(service, "_send_mail", side_effect=capture_mail):
        result = service.request_code("alice")
        challenge = PasswordRecoveryService._codes[7]
        assert captured["code"] not in challenge.values()
        assert service.verify_code("alice", captured["code"])["id"] == 7
        with pytest.raises(ValueError, match="不正确或已过期"):
            service.verify_code("alice", captured["code"])
    assert result["channel"] == "email_code"


def test_verification_code_expires_after_maximum_wrong_attempts():
    PasswordRecoveryService._attempts.clear()
    PasswordRecoveryService._codes.clear()
    service = PasswordRecoveryService(RecoveryStore())
    config = recovery_config(**{"verification-code-max-attempts": 2})
    with patch("backend.service.password_recovery_service.get_config", return_value=config), patch("backend.service.password_recovery_service.secrets.randbelow", return_value=123456), patch.object(service, "_send_mail"):
        service.request_code("alice")
        for _ in range(2):
            with pytest.raises(ValueError, match="不正确或已过期"):
                service.verify_code("alice", "000000")
    assert 7 not in PasswordRecoveryService._codes


def test_active_verification_code_cannot_be_sent_twice():
    PasswordRecoveryService._attempts.clear()
    PasswordRecoveryService._codes.clear()
    service = PasswordRecoveryService(RecoveryStore())
    with patch("backend.service.password_recovery_service.get_config", return_value=recovery_config()), patch.object(service, "_send_mail") as send:
        service.request_code("alice")
        with pytest.raises(ValueError, match="无需重复发送"):
            service.request_code("alice")
    assert send.call_count == 1


def test_contact_admin_rejects_account_that_can_use_email_code():
    PasswordRecoveryService._attempts.clear()
    service = PasswordRecoveryService(RecoveryStore())
    with patch("backend.service.password_recovery_service.get_config", return_value=recovery_config()), patch.object(service, "_send_mail"):
        with pytest.raises(ValueError, match="邮箱验证码"):
            service.contact_admin("alice", "Alice")


def test_bound_email_is_masked_and_reset_secret_never_returned():
    PasswordRecoveryService._attempts.clear()
    service = PasswordRecoveryService(RecoveryStore())
    with patch("backend.service.password_recovery_service.get_config", return_value=recovery_config()), patch.object(service, "_send_mail") as send:
        result = service.request_reset("alice")
    assert result["channel"] == "email"
    assert result["masked_email"] == "us***@example.test"
    assert "token" not in result
    assert "example.test/reset-password?token=" in send.call_args.args[2]


def test_all_payment_methods_are_hidden_by_default_and_individually_enabled():
    hidden = {"rose": {"payment": {"personal-wechat": {"enabled": True}, "wechat": {"enabled": True}, "alipay": {"enabled": True}}}}
    with patch("backend.service.payment_service.get_config", return_value=hidden):
        assert PaymentService.public_payment_methods() == []

    visible = {"rose": {"payment": {"personal-wechat": {"enabled": True, "visible-to-users": True}, "wechat": {"enabled": True, "visible-to-users": False}, "alipay": {"enabled": True, "visible-to-users": True}}}}
    with patch("backend.service.payment_service.get_config", return_value=visible):
        assert [item["provider"] for item in PaymentService.public_payment_methods()] == ["WECHAT_PERSONAL", "ALIPAY"]
        assert PaymentService.payment_method_visible("wechat_personal") is True
        assert PaymentService.payment_method_visible("wechat") is False


def test_password_recovery_mail_rejects_proxy_mode():
    service = PasswordRecoveryService(RecoveryStore())
    config = recovery_config()
    config["rose"]["password-recovery"]["smtp"]["use-proxy"] = True
    with patch("backend.service.password_recovery_service.get_config", return_value=config):
        with pytest.raises(RuntimeError, match="不允许通过代理"):
            service._send_mail("user@example.test", "subject", "content")
