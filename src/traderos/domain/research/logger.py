import pandas as pd

from traderos.infrastructure.database.db_manager import DatabaseManager


class JournalLogger:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def log_entry(self, content: str, category: str = "Observation", tags: str = ""):
        """Log a new journal entry to the database."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO journal_entries (category, content, tags) VALUES (?, ?, ?)",
            (category, content, tags),
        )
        self.db.conn.commit()
        print(f"Journal Entry Logged: [{category}] {content[:50]}...")

    def get_recent_entries(self, limit: int = 5):
        query = "SELECT * FROM journal_entries ORDER BY timestamp DESC LIMIT ?"
        return pd.read_sql_query(query, self.db.conn, params=[limit])
