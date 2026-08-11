import json
import logging
import sys
from logging.handlers import RotatingFileHandler

import pytest

from traderos.domain.exceptions import CLIError
from traderos.domain.exceptions import ConfigError
from traderos.domain.exceptions import DatabaseError
from traderos.domain.exceptions import DomainError
from traderos.domain.exceptions import DuplicateEntityError
from traderos.domain.exceptions import EntityNotFoundError
from traderos.domain.exceptions import EntityValidationError
from traderos.domain.exceptions import InfrastructureError
from traderos.domain.exceptions import RepositoryError
from traderos.domain.exceptions import ServiceError
from traderos.domain.exceptions import TraderOSError
from traderos.domain.exceptions import ValidationError
from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.events import Event
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.logging import JsonFormatter
from traderos.infrastructure.logging import StructuredLogger
from traderos.infrastructure.logging import setup_json_logging


class TestConfigV2:
    def test_default_values(self) -> None:
        config = Config()
        assert config.db_path == "data/trader.db"
        assert config.log_level == "INFO"
        assert config.paper_trading is False

    def test_frozen(self) -> None:
        config = Config()
        with pytest.raises(AttributeError):
            config.db_path = "/new/path"

    def test_validate_passes(self, tmp_path) -> None:
        config = Config(db_path=str(tmp_path / "trader.db"))
        config.validate()

    def test_validate_empty_db_path(self) -> None:
        with pytest.raises(ConfigError):
            Config(db_path="").validate()

    def test_validate_invalid_log_level(self) -> None:
        with pytest.raises(ConfigError):
            Config(log_level="TRACE").validate()

    def test_load_from_file(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("DB_PATH", raising=False)
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        config_file = config_dir / "settings.yaml"
        config_file.write_text("db_path: /tmp/test.db\nlog_level: DEBUG\n")
        config = Config.load(str(config_file))
        assert config.db_path == "/tmp/test.db"
        assert config.log_level == "DEBUG"

    def test_load_creates_missing_db_directory(self, monkeypatch, tmp_path) -> None:
        """A fresh checkout with no `database/` dir must load without a manual
        mkdir — previously `Config.load()` raised ConfigError here (bootstrap)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("DB_PATH", str(tmp_path / "database" / "market.db"))
        config = Config.load()
        assert config.db_path == str(tmp_path / "database" / "market.db")
        assert (tmp_path / "database").is_dir()


class TestErrorHierarchy:
    def test_base_exception(self) -> None:
        assert issubclass(DomainError, TraderOSError)
        assert issubclass(InfrastructureError, TraderOSError)
        assert issubclass(RepositoryError, InfrastructureError)
        assert issubclass(EntityValidationError, DomainError)

    def test_specific_exceptions(self) -> None:
        assert issubclass(EntityNotFoundError, RepositoryError)
        assert issubclass(DuplicateEntityError, RepositoryError)
        assert issubclass(ConfigError, InfrastructureError)
        assert issubclass(DatabaseError, InfrastructureError)
        assert issubclass(ServiceError, TraderOSError)
        assert issubclass(CLIError, TraderOSError)
        assert issubclass(ValidationError, TraderOSError)

    def test_exception_raise_and_catch_base(self) -> None:
        with pytest.raises(TraderOSError):
            raise EntityValidationError("Invalid entity")

    def test_exception_message(self) -> None:
        try:
            raise EntityNotFoundError("Market not found")
        except TraderOSError as e:
            assert "Market not found" in str(e)


class TestStructuredLogger:
    def test_info_logs_json(self, capsys) -> None:
        logger = StructuredLogger("test", level="DEBUG")
        logger.info("test_event", key="value")
        captured = capsys.readouterr()
        record = json.loads(captured.out)
        assert record["event"] == "test_event"
        assert record["data"]["key"] == "value"
        assert record["logger"] == "test"

    def test_error_logs_correct_level(self, capsys) -> None:
        logger = StructuredLogger("test", level="DEBUG")
        logger.error("error_event")
        captured = capsys.readouterr()
        record = json.loads(captured.out)
        assert record["event"] == "error_event"

    def test_debug_suppressed_when_info(self, capsys) -> None:
        logger = StructuredLogger("test", level="INFO")
        logger.debug("debug_event")
        captured = capsys.readouterr()
        assert captured.out == ""


class TestLoggingInfrastructure:
    def test_setup_json_logging_with_file(self, tmp_path, monkeypatch) -> None:
        log_file = tmp_path / "app.json.log"
        monkeypatch.setenv("LOG_FILE", str(log_file))
        original = logging.getLogger().handlers[:]
        try:
            setup_json_logging()
            root = logging.getLogger()
            assert any(isinstance(h, RotatingFileHandler) for h in root.handlers)
            logging.getLogger().info("hello")
            assert log_file.exists()
        finally:
            logging.getLogger().handlers[:] = original

    def test_setup_json_logging_without_file(self, monkeypatch, capsys) -> None:
        monkeypatch.delenv("LOG_FILE", raising=False)
        original = logging.getLogger().handlers[:]
        try:
            setup_json_logging()
            logging.getLogger().info("hello")
            record = json.loads(capsys.readouterr().out)
            assert record["message"] == "hello"
        finally:
            logging.getLogger().handlers[:] = original

    def test_json_formatter_exception_and_extra(self, capsys) -> None:
        logger = logging.getLogger("json_test")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("failed", extra={"extra": {"user_id": 42}})
        record = json.loads(capsys.readouterr().out)
        assert record["message"] == "failed"
        assert "ValueError" in record["exception"]
        assert record["data"]["user_id"] == "42"

    def test_structured_logger_file_handler_warning_critical(self, tmp_path) -> None:
        log_file = tmp_path / "structured.log"
        logger = StructuredLogger("file_test", log_file=str(log_file))
        logger.warning("warn_event", key="value")
        logger.critical("crit_event")
        content = log_file.read_text()
        assert '"warn_event"' in content
        assert '"data": {"key": "value"}' in content
        assert '"crit_event"' in content


class TestEventBus:
    def test_publish_and_subscribe(self) -> None:
        bus = InMemoryEventBus()
        received: list[Event] = []

        def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("test.event", handler)
        event = Event(event_type="test.event", payload={"msg": "hello"})
        bus.publish(event)
        assert len(received) == 1
        assert received[0].payload["msg"] == "hello"

    def test_unsubscribe(self) -> None:
        bus = InMemoryEventBus()
        received: list[Event] = []

        def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("test.event", handler)
        bus.unsubscribe("test.event", handler)
        bus.publish(Event(event_type="test.event", payload={}))
        assert len(received) == 0

    def test_multiple_subscribers(self) -> None:
        bus = InMemoryEventBus()
        results: list[int] = []

        def handler1(event: Event) -> None:
            results.append(1)

        def handler2(event: Event) -> None:
            results.append(2)

        bus.subscribe("test.event", handler1)
        bus.subscribe("test.event", handler2)
        bus.publish(Event(event_type="test.event", payload={}))
        assert len(results) == 2

    def test_handler_exception_logged_and_bus_continues(self, caplog) -> None:
        bus = InMemoryEventBus()
        received: list[Event] = []

        def bad_handler(event: Event) -> None:
            raise RuntimeError("boom")

        def good_handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("test.event", bad_handler)
        bus.subscribe("test.event", good_handler)
        with caplog.at_level(logging.ERROR):
            bus.publish(Event(event_type="test.event", payload={}))
        assert len(received) == 1
        assert any("Event handler failed" in r.message for r in caplog.records)

    def test_event_frozen(self) -> None:
        event = Event(event_type="test", payload={})
        with pytest.raises(AttributeError):
            event.event_type = "changed"
