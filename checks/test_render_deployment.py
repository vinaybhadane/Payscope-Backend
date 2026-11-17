"""Offline deployment checks: no live database, Gemini or email requests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.core.config import Settings
from app.render_server import server_options, validate_configuration


class RenderDeploymentChecks(unittest.TestCase):
    def test_render_port_is_used_with_one_worker(self):
        options = server_options({"PORT": "10000"})
        self.assertEqual(options["port"], 10000)
        self.assertEqual(options["host"], "0.0.0.0")
        self.assertEqual(options["workers"], 1)

    def test_invalid_port_fails_before_startup(self):
        for port in ("abc", "0", "65536"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                server_options({"PORT": port})

    def test_neon_migrations_use_direct_host_and_preserve_url_encoding(self):
        config = Settings(_env_file=None,
            DATABASE_URL="postgresql://user:pass%40word@ep-demo-pooler.region.aws.neon.tech/neondb?sslmode=require")
        self.assertIn("-pooler.", config.DATABASE_URL)
        self.assertNotIn("-pooler.", config.migration_database_url)
        self.assertIn("pass%40word", config.migration_database_url)
        self.assertIn("sslmode=require", config.migration_database_url)

    def test_explicit_direct_connection_takes_precedence(self):
        config = Settings(_env_file=None, DATABASE_URL="postgresql://user:pw@pool.example/db",
            DATABASE_URL_UNPOOLED="postgresql://user:pw@direct.example/db")
        self.assertIn("direct.example", config.migration_database_url)
        self.assertTrue(config.migration_database_url.startswith("postgresql+psycopg://"))

    def test_missing_configuration_never_prints_credentials(self):
        config = SimpleNamespace(DATABASE_URL="postgresql://user:private-password@remote.example/db",
            FIREBASE_PROJECT_ID="", GEMINI_API_KEY="private-provider-key", GEMINI_MODEL="model",
            ALLOW_GUEST_SESSIONS=True, API_SESSION_SECRET="short")
        with self.assertRaises(ValueError) as error:
            validate_configuration(config)
        self.assertIn("FIREBASE_PROJECT_ID", str(error.exception))
        self.assertNotIn("private-password", str(error.exception))
        self.assertNotIn("private-provider-key", str(error.exception))

    def test_startup_migration_config_uses_direct_connection(self):
        from app.main import run_migrations
        with patch("app.main.settings", SimpleNamespace(migration_database_url=
                "postgresql+psycopg://user:pass%40word@direct.example/db")), patch("alembic.command.upgrade") as upgrade:
            run_migrations()
        config = upgrade.call_args.args[0]
        self.assertEqual(config.get_main_option("sqlalchemy.url"),
            "postgresql+psycopg://user:pass%40word@direct.example/db")

    def test_blank_or_malformed_database_url_has_safe_error(self):
        for url in ("", "private-malformed-credential", "postgresql://user:pw@db.example:invalid/db"):
            config = SimpleNamespace(DATABASE_URL=url, FIREBASE_PROJECT_ID="project",
                GEMINI_API_KEY="provider-key", GEMINI_MODEL="model",
                ALLOW_GUEST_SESSIONS=False, API_SESSION_SECRET="")
            with self.subTest(url=url), self.assertRaises(ValueError) as error:
                validate_configuration(config)
            self.assertIn("DATABASE_URL", str(error.exception))
            if url:
                self.assertNotIn(url, str(error.exception))


if __name__ == "__main__":
    unittest.main()
