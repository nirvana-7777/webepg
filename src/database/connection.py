"""
Database connection management with thread-safe pooling.
"""

import logging
import sqlite3
import threading
from contextlib import contextmanager
from typing import Generator, Optional

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Thread-safe database connection manager."""

    def __init__(self, db_path: str):
        """
        Initialize database connection manager.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()

    def _get_connection(self) -> sqlite3.Connection:
        """
        Get thread-local database connection.

        Returns:
            SQLite connection for current thread
        """
        if not hasattr(self._local, "connection") or self._local.connection is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            # Enable foreign keys
            conn.execute("PRAGMA foreign_keys = ON")
            # Use WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode = WAL")
            # Row factory for easier data access
            conn.row_factory = sqlite3.Row

            self._local.connection = conn
            logger.debug(
                f"Created new database connection for thread {threading.get_ident()}"
            )

        return self._local.connection

    @contextmanager
    def get_cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """
        Get database cursor with automatic transaction management.

        Yields:
            SQLite cursor

        Example:
            with db.get_cursor() as cursor:
                cursor.execute("SELECT * FROM channels")
                results = cursor.fetchall()
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error, rolling back: {e}")
            raise
        finally:
            cursor.close()

    def execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
        """
        Execute SQL statement with automatic commit.

        Args:
            sql: SQL statement
            parameters: Query parameters

        Returns:
            Cursor after execution
        """
        with self.get_cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor

    def executemany(self, sql: str, parameters: list) -> int:
        """
        Execute SQL statement with multiple parameter sets.

        Args:
            sql: SQL statement
            parameters: List of parameter tuples

        Returns:
            Number of rows affected
        """
        with self.get_cursor() as cursor:
            cursor.executemany(sql, parameters)
            return cursor.rowcount

    def fetchone(self, sql: str, parameters: tuple = ()) -> Optional[sqlite3.Row]:
        """
        Execute query and fetch one result.

        Args:
            sql: SQL query
            parameters: Query parameters

        Returns:
            Single row or None
        """
        with self.get_cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchone()

    def fetchall(self, sql: str, parameters: tuple = ()) -> list:
        """
        Execute query and fetch all results.

        Args:
            sql: SQL query
            parameters: Query parameters

        Returns:
            List of rows
        """
        with self.get_cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchall()

    def close(self):
        """Close thread-local connection if it exists."""
        if hasattr(self._local, "connection") and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
            logger.debug(
                f"Closed database connection for thread {threading.get_ident()}"
            )

    def close_all(self):
        """Close all connections (call on shutdown)."""
        # Note: This only closes the connection for the current thread
        # In a multi-threaded environment, each thread should close its own connection
        self.close()


# Global database instance
_db_instance: Optional[DatabaseConnection] = None
_db_lock = threading.Lock()


def initialize_db(db_path: str) -> DatabaseConnection:
    """
    Initialize global database connection.

    Args:
        db_path: Path to SQLite database file

    Returns:
        DatabaseConnection instance
    """
    global _db_instance

    with _db_lock:
        if _db_instance is None:
            _db_instance = DatabaseConnection(db_path)
            logger.info(f"Initialized database connection: {db_path}")
        return _db_instance


def get_db() -> DatabaseConnection:
    """
    Get global database connection instance.

    Returns:
        DatabaseConnection instance

    Raises:
        RuntimeError: If database not initialized
    """
    if _db_instance is None:
        raise RuntimeError("Database not initialized. Call initialize_db() first.")
    return _db_instance


def close_db():
    """Close global database connection."""
    global _db_instance

    with _db_lock:
        if _db_instance:
            _db_instance.close_all()
            _db_instance = None
            logger.info("Closed database connection")
