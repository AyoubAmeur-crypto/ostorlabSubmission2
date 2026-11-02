from pathlib import Path

import pytest

from app.core.config import REPO_ROOT, Settings, get_settings, override_settings
from app.core.errors import ConfigError


def test_defaults_without_environment():
    settings = Settings.from_env({})
    assert settings.env == "dev"
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.page_limit_default == 50
    assert settings.fixtures_root == REPO_ROOT / "fixtures"


def test_reads_typed_values():
    settings = Settings.from_env(
        {
            "TW_ENV": "test",
            "TW_DB_ECHO": "yes",
            "TW_RATE_LIMIT_PER_MINUTE": "30",
            "TW_PLUGIN_TIMEOUT_S": "2.5",
            "TW_LOG_LEVEL": "debug",
            "TW_LOG_FORMAT": "TEXT",
        }
    )
    assert settings.is_test
    assert settings.db_echo is True
    assert settings.rate_limit_per_minute == 30
    assert settings.plugin_timeout_s == 2.5
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "text"


def test_relative_paths_resolve_against_repo_root(tmp_path: Path):
    settings = Settings.from_env({"TW_REPORTS_DIR": "var/out", "TW_FIXTURES_ROOT": str(tmp_path)})
    assert settings.reports_dir == REPO_ROOT / "var" / "out"
    assert settings.fixtures_root == tmp_path


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"TW_ENV": "staging"}, "TW_ENV"),
        ({"TW_DB_ECHO": "maybe"}, "TW_DB_ECHO"),
        ({"TW_RATE_LIMIT_PER_MINUTE": "lots"}, "TW_RATE_LIMIT_PER_MINUTE"),
        ({"TW_RATE_LIMIT_PER_MINUTE": "0"}, "TW_RATE_LIMIT_PER_MINUTE"),
        ({"TW_PLUGIN_TIMEOUT_S": "-1"}, "TW_PLUGIN_TIMEOUT_S"),
        ({"DATABASE_URL": "sqlite:///x.db"}, "DATABASE_URL"),
        ({"TW_LOG_LEVEL": "loud"}, "TW_LOG_LEVEL"),
        ({"TW_PAGE_LIMIT_DEFAULT": "900"}, "TW_PAGE_LIMIT_DEFAULT"),
        ({"TW_WORKER_HEARTBEAT_S": "90"}, "TW_WORKER_HEARTBEAT_S"),
        ({"TW_DEFAULT_TIMEZONE": "Mars/Olympus"}, "TW_DEFAULT_TIMEZONE"),
    ],
)
def test_invalid_values_name_the_variable(env, message):
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


def test_prod_refuses_the_demo_admin_token():
    with pytest.raises(ConfigError, match="TW_ADMIN_TOKEN"):
        Settings.from_env({"TW_ENV": "prod"})
    settings = Settings.from_env({"TW_ENV": "prod", "TW_ADMIN_TOKEN": "tw_demo_admin_rotated"})
    assert settings.env == "prod"


def test_override_settings_restores_previous_values():
    before = get_settings().rate_limit_per_minute
    with override_settings(rate_limit_per_minute=3) as patched:
        assert patched.rate_limit_per_minute == 3
        assert get_settings().rate_limit_per_minute == 3
    assert get_settings().rate_limit_per_minute == before


def test_test_run_points_at_the_test_database():
    assert get_settings().database_url.rsplit("/", 1)[-1].endswith("_test")
