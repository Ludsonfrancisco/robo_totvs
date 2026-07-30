from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from flows.financeiro_medicao.config import Settings


class FinanceiroMedicaoSettingsTests(unittest.TestCase):
    def test_uses_portable_defaults_for_valid_https_url(self):
        settings = Settings.from_mapping(
            {"FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid/dashboard"}
        )

        self.assertEqual(
            settings.runtime_root,
            Path("/app/data_pipeline/financeiro_medicao"),
        )
        self.assertFalse(settings.schedule_enabled)
        self.assertEqual((settings.schedule_hour, settings.schedule_minute), (0, 1))
        self.assertEqual(settings.timezone, "America/Sao_Paulo")
        self.assertEqual(settings.lock_wait_seconds, 1200)

    def test_reads_dashboard_credentials_from_files_without_newlines(self):
        with TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            user_file = directory / "user.txt"
            password_file = directory / "password.txt"
            user_file.write_text("dashboard-user\n", encoding="utf-8")
            password_file.write_text("dashboard-password\r\n", encoding="utf-8")

            settings = Settings.from_mapping(
                {
                    "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                    "LOGA_DASHBOARD_USER_FILE": str(user_file),
                    "LOGA_DASHBOARD_PASSWORD_FILE": str(password_file),
                }
            )

        self.assertEqual(settings.username, "dashboard-user")
        self.assertEqual(settings.password, "dashboard-password")

    def test_password_values_preserve_spaces_while_file_newlines_are_removed(self):
        with TemporaryDirectory() as temp_dir:
            password_file = Path(temp_dir) / "password.txt"
            password_file.write_text(" password from file \r\n", encoding="utf-8")

            file_settings = Settings.from_mapping(
                {
                    "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                    "LOGA_DASHBOARD_PASSWORD_FILE": str(password_file),
                }
            )
            inline_settings = Settings.from_mapping(
                {
                    "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                    "LOGA_DASHBOARD_PASSWORD": " password inline ",
                }
            )
            fallback_settings = Settings.from_mapping(
                {
                    "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                    "MULTIPLICA_LOGA_PASSWORD": " password fallback ",
                }
            )

        self.assertEqual(file_settings.password, " password from file ")
        self.assertEqual(inline_settings.password, " password inline ")
        self.assertEqual(fallback_settings.password, " password fallback ")

    def test_dashboard_credentials_take_precedence_over_existing_multiplica_values(self):
        settings = Settings.from_mapping(
            {
                "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                "LOGA_DASHBOARD_USER": "dashboard-user",
                "LOGA_DASHBOARD_PASSWORD": "dashboard-password",
                "MULTIPLICA_LOGA_USER": "multiplica-user",
                "MULTIPLICA_LOGA_PASSWORD": "multiplica-password",
            }
        )

        self.assertEqual(settings.username, "dashboard-user")
        self.assertEqual(settings.password, "dashboard-password")

    def test_dashboard_credential_files_take_precedence_over_environment_values(self):
        with TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            user_file = directory / "user.txt"
            password_file = directory / "password.txt"
            user_file.write_text("file-user", encoding="utf-8")
            password_file.write_text("file-password", encoding="utf-8")

            settings = Settings.from_mapping(
                {
                    "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                    "LOGA_DASHBOARD_USER_FILE": str(user_file),
                    "LOGA_DASHBOARD_PASSWORD_FILE": str(password_file),
                    "LOGA_DASHBOARD_USER": "dashboard-user",
                    "LOGA_DASHBOARD_PASSWORD": "dashboard-password",
                    "MULTIPLICA_LOGA_USER": "multiplica-user",
                    "MULTIPLICA_LOGA_PASSWORD": "multiplica-password",
                }
            )

        self.assertEqual(settings.username, "file-user")
        self.assertEqual(settings.password, "file-password")

    def test_uses_existing_multiplica_credentials_as_fallback(self):
        settings = Settings.from_mapping(
            {
                "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                "MULTIPLICA_LOGA_USER": "multiplica-user",
                "MULTIPLICA_LOGA_PASSWORD": "multiplica-password",
            }
        )

        self.assertEqual(settings.username, "multiplica-user")
        self.assertEqual(settings.password, "multiplica-password")

    def test_rejects_non_https_url(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            Settings.from_mapping(
                {"FINANCEIRO_MEDICAO_LOGA_URL": "http://example.invalid"}
            )

    def test_rejects_non_absolute_or_wrongly_named_runtime_root(self):
        for runtime_root in ("relative/financeiro_medicao", "/app/data_pipeline/other"):
            with self.subTest(runtime_root=runtime_root):
                with self.assertRaisesRegex(ValueError, "RUNTIME_ROOT"):
                    Settings.from_mapping(
                        {
                            "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                            "FINANCEIRO_MEDICAO_RUNTIME_ROOT": runtime_root,
                        }
                    )

    def test_rejects_schedule_and_lock_values_outside_allowed_ranges(self):
        invalid_values = (
            ("FINANCEIRO_MEDICAO_SCHEDULE_HOUR", "-1"),
            ("FINANCEIRO_MEDICAO_SCHEDULE_HOUR", "24"),
            ("FINANCEIRO_MEDICAO_SCHEDULE_MINUTE", "-1"),
            ("FINANCEIRO_MEDICAO_SCHEDULE_MINUTE", "60"),
            ("FINANCEIRO_MEDICAO_LOCK_WAIT_SECONDS", "-1"),
            ("FINANCEIRO_MEDICAO_LOCK_WAIT_SECONDS", "3601"),
        )
        for key, value in invalid_values:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    Settings.from_mapping(
                        {
                            "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                            key: value,
                        }
                    )

    def test_rejects_non_integer_schedule_and_lock_values_with_context(self):
        invalid_values = (
            ("FINANCEIRO_MEDICAO_SCHEDULE_HOUR", "not-an-integer"),
            ("FINANCEIRO_MEDICAO_SCHEDULE_MINUTE", "not-an-integer"),
            ("FINANCEIRO_MEDICAO_LOCK_WAIT_SECONDS", "not-an-integer"),
        )
        for key, value in invalid_values:
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, key):
                    Settings.from_mapping(
                        {
                            "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                            key: value,
                        }
                    )

    def test_accepts_explicit_boolean_values(self):
        enabled_values = ("true", "1", "yes", "TRUE")
        disabled_values = ("false", "0", "no", "FALSE")

        for value in enabled_values:
            with self.subTest(value=value):
                settings = Settings.from_mapping(
                    {
                        "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                        "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED": value,
                    }
                )
                self.assertTrue(settings.schedule_enabled)

        for value in disabled_values:
            with self.subTest(value=value):
                settings = Settings.from_mapping(
                    {
                        "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                        "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED": value,
                    }
                )
                self.assertFalse(settings.schedule_enabled)

    def test_rejects_unknown_schedule_boolean_with_context(self):
        with self.assertRaisesRegex(ValueError, "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED"):
            Settings.from_mapping(
                {
                    "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                    "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED": "unknown",
                }
            )

    def test_normalizes_and_validates_timezone(self):
        settings = Settings.from_mapping(
            {
                "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                "FINANCEIRO_MEDICAO_TIMEZONE": "  America/Sao_Paulo  ",
            }
        )

        self.assertEqual(settings.timezone, "America/Sao_Paulo")

    def test_rejects_empty_or_unknown_timezone_with_context(self):
        for timezone in ("   ", "Unknown/Timezone"):
            with self.subTest(timezone=timezone):
                with self.assertRaisesRegex(ValueError, "FINANCEIRO_MEDICAO_TIMEZONE"):
                    Settings.from_mapping(
                        {
                            "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                            "FINANCEIRO_MEDICAO_TIMEZONE": timezone,
                        }
                    )

    def test_storage_state_path_is_under_runtime_directory(self):
        settings = Settings.from_mapping(
            {"FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid"}
        )

        self.assertEqual(
            settings.storage_state_path,
            Path("/app/data_pipeline/financeiro_medicao/runtime/loga-storage-state.json"),
        )

    def test_does_not_create_runtime_directories(self):
        with TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "financeiro_medicao"
            Settings.from_mapping(
                {
                    "FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid",
                    "FINANCEIRO_MEDICAO_RUNTIME_ROOT": str(runtime_root),
                }
            )

            self.assertFalse(runtime_root.exists())

    def test_settings_are_frozen(self):
        settings = Settings.from_mapping(
            {"FINANCEIRO_MEDICAO_LOGA_URL": "https://example.invalid"}
        )

        with self.assertRaises(FrozenInstanceError):
            settings.schedule_enabled = True
