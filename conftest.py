import os


def pytest_sessionstart(session):
    os.environ.setdefault("DB_PATH", "test_trader.db")


def pytest_sessionfinish(session, exitstatus):
    test_dbs = [
        "test_trader.db",
        "test_sprint1.db",
    ]
    for db in test_dbs:
        if os.path.exists(db):
            os.remove(db)
