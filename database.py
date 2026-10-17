"""
Comprehensive SQLite Wrapper for PostgreSQL Compatibility
Provides thread-safe connection pooling, schema management, and credit system
"""

import sys
import types
import sqlite3
import queue
import threading
import time
import logging
import re
import os
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOGGING SETUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [DB] %(message)s'
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PSYCOPG2 MOCKING - Inject SQLite as drop-in replacement
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _setup_psycopg2_mocks():
    """Dynamically inject mock psycopg2 modules if not installed"""
    try:
        import psycopg2
        import psycopg2.pool
        import psycopg2.extensions
        import psycopg2.extras
        logger.info("psycopg2 found - using native PostgreSQL")
        return False  # psycopg2 is installed
    except ImportError:
        logger.info("psycopg2 not found - using SQLite with mocked psycopg2")
        
        class MockModule(types.ModuleType):
            def __getattr__(self, name):
                if name == "RealDictCursor":
                    return object
                if name == "STATUS_READY":
                    return 0
                if name == "ThreadedConnectionPool":
                    return globals().get('SQLiteThreadedPool', object)
                return object

        mock_modules = {
            "psycopg2": MockModule("psycopg2"),
            "psycopg2.pool": MockModule("psycopg2.pool"),
            "psycopg2.extensions": MockModule("psycopg2.extensions"),
            "psycopg2.extras": MockModule("psycopg2.extras"),
        }
        
        # Set up module hierarchy
        mock_modules["psycopg2"].pool = mock_modules["psycopg2.pool"]
        mock_modules["psycopg2"].extensions = mock_modules["psycopg2.extensions"]
        mock_modules["psycopg2"].extras = mock_modules["psycopg2.extras"]
        mock_modules["psycopg2"].connect = lambda *args, **kwargs: _create_sqlite_connection(*args, **kwargs)
        
        # Inject into sys.modules
        for module_name, module_obj in mock_modules.items():
            sys.modules[module_name] = module_obj
        
        import psycopg2
        import psycopg2.pool
        import psycopg2.extensions
        import psycopg2.extras
        
        return True  # Using mocked psycopg2

_IS_SQLITE_MODE = _setup_psycopg2_mocks()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATETIME HANDLING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_sqlite_timestamp(val: Any) -> Any:
    """Parse SQLite timestamp from bytes, string, or datetime"""
    if isinstance(val, datetime):
        return val
    
    if isinstance(val, bytes):
        val_str = val.decode('utf-8')
    else:
        val_str = str(val)
    
    # Try ISO format first (fastest)
    try:
        return datetime.fromisoformat(val_str)
    except (ValueError, TypeError):
        pass
    
    # Try common formats
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(val_str, fmt)
        except ValueError:
            continue
    
    # Return as string if all parsing fails
    logger.warning(f"Could not parse timestamp: {val_str}")
    return val_str

# Register SQLite adapters/converters
sqlite3.register_adapter(datetime, lambda val: val.isoformat())
sqlite3.register_converter('timestamp', _parse_sqlite_timestamp)
sqlite3.register_converter('TIMESTAMP', _parse_sqlite_timestamp)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATABASE CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DB_CONFIG = {
    "host": "localhost",
    "database": "rohit.db",
    "user": "sqlite",
    "password": "",
    "port": 5432,
}

def _get_db_path() -> str:
    """Get SQLite database file path"""
    db_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "rohit.db")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CURSOR WRAPPER - PostgreSQL to SQLite translation layer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SQLiteCursorWrapper:
    """Translates PostgreSQL SQL to SQLite-compatible syntax"""
    
    def __init__(self, sqlite_cursor: sqlite3.Cursor, cursor_factory=None):
        self._cursor = sqlite_cursor
        self.cursor_factory = cursor_factory
        self._prefetched_data = None
        self._is_returning_query = False
        self._returning_cols = None

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def execute(self, query: str, params=None):
        """Execute query with PostgreSQL syntax translation"""
        try:
            # Normalize params
            if params is not None and not isinstance(params, (list, tuple, dict)):
                params = (params,)

            # Translate PostgreSQL to SQLite syntax
            translated_query, translated_params = self._translate_query(query, params)
            
            logger.debug(f"Executing: {translated_query[:100]}... with {len(translated_params) if translated_params else 0} params")
            
            # Execute with proper parameter binding
            if translated_params:
                self._cursor.execute(translated_query, translated_params)
            else:
                self._cursor.execute(translated_query)
                
            return self
            
        except sqlite3.IntegrityError as e:
            logger.warning(f"Integrity error (expected for duplicates): {e}")
            raise
        except sqlite3.OperationalError as e:
            return self._handle_operational_error(e, query, params)
        except Exception as e:
            logger.error(f"Unexpected database error: {e}")
            raise

    def _translate_query(self, query: str, params) -> Tuple[str, tuple]:
        """Translate PostgreSQL syntax to SQLite"""
        # Basic replacements
        query = query.replace('%s', '?')
        query = query.replace('GREATEST', 'MAX')
        query = query.replace('NOW()', 'CURRENT_TIMESTAMP')
        query = query.replace('SERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT')
        query = query.replace('BIGINT PRIMARY KEY', 'INTEGER PRIMARY KEY')
        query = query.replace('BIGINT', 'INTEGER')
        
        # Handle ADD COLUMN IF NOT EXISTS
        query = re.sub(r'\bADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\b', 'ADD COLUMN', query, flags=re.IGNORECASE)

        # Handle unsupported ALTER COLUMN
        if 'ALTER' in query.upper() and 'ALTER COLUMN' in query.upper():
            logger.debug(f"Intercepted unsupported ALTER COLUMN query: {query}")
            return "SELECT 1", params

        # Handle ANY() array operations
        if params and 'ANY' in query.upper():
            query, params = self._translate_any_clause(query, params)

        return query, params

    def _translate_any_clause(self, query: str, params) -> Tuple[str, tuple]:
        """Translate PostgreSQL ANY() to SQLite IN()"""
        if not params or 'ANY' not in query.upper():
            return query, params

        params_list = list(params)
        new_query = []
        new_params = []
        param_idx = 0
        
        for match in re.finditer(r'(?:=|IN)\s*ANY\s*\(\s*\?\s*\)', query, re.IGNORECASE):
            new_query.append(query[:match.start()])
            
            if param_idx < len(params_list):
                val = params_list[param_idx]
                if isinstance(val, (list, tuple, set)):
                    val_list = list(val)
                    if val_list:
                        placeholders = ','.join(['?'] * len(val_list))
                        new_query.append(f'IN ({placeholders})')
                        new_params.extend(val_list)
                    else:
                        new_query.append('IN (NULL)')
                else:
                    new_query.append('= ?')
                    new_params.append(val)
                param_idx += 1
            
            query = query[match.end():]
        
        new_query.append(query)
        return ''.join(new_query), tuple(new_params)

    def _handle_operational_error(self, error: sqlite3.OperationalError, query: str, params):
        """Handle SQLite operational errors"""
        error_msg = str(error).lower()
        
        # Ignore "already exists" errors during schema migration
        if "duplicate column name" in error_msg or "already exists" in error_msg:
            logger.debug(f"Ignoring schema error: {error}")
            return self
        
        # Handle RETURNING clause emulation
        if "returning" in error_msg or "syntax error" in error_msg:
            if self._emulate_returning(query, params):
                return self
        
        logger.error(f"Operational error: {error}")
        raise

    def _emulate_returning(self, query: str, params) -> bool:
        """Emulate PostgreSQL RETURNING clause in SQLite"""
        # DELETE ... RETURNING
        delete_match = re.match(
            r'^\s*DELETE\s+FROM\s+(\w+)\s+WHERE\s+(.*?)\s+RETURNING\s+(.*?)\s*$',
            query,
            re.IGNORECASE | re.DOTALL
        )
        if delete_match:
            table, where_clause, returning_cols = delete_match.groups()
            self._execute_returning_delete(table, where_clause, returning_cols, params)
            return True
        
        # UPDATE ... RETURNING
        update_match = re.match(
            r'^\s*UPDATE\s+(\w+)\s+SET\s+(.*?)\s+WHERE\s+(.*?)\s+RETURNING\s+(.*?)\s*$',
            query,
            re.IGNORECASE | re.DOTALL
        )
        if update_match:
            table, set_clause, where_clause, returning_cols = update_match.groups()
            self._execute_returning_update(table, set_clause, where_clause, returning_cols, params)
            return True
        
        return False

    def _execute_returning_delete(self, table: str, where_clause: str, returning_cols: str, params):
        """Execute DELETE ... RETURNING emulation"""
        try:
            # First select the rows to return
            select_sql = f"SELECT {returning_cols} FROM {table} WHERE {where_clause}"
            if params:
                self._cursor.execute(select_sql, params)
            else:
                self._cursor.execute(select_sql)
            
            self._prefetched_data = self._cursor.fetchall()
            self._is_returning_query = True
            self._returning_cols = returning_cols
            
            # Then delete
            delete_sql = f"DELETE FROM {table} WHERE {where_clause}"
            if params:
                self._cursor.execute(delete_sql, params)
            else:
                self._cursor.execute(delete_sql)
        except Exception as e:
            logger.error(f"Error in DELETE RETURNING emulation: {e}")
            raise

    def _execute_returning_update(self, table: str, set_clause: str, where_clause: str, returning_cols: str, params):
        """Execute UPDATE ... RETURNING emulation"""
        try:
            # First update
            update_sql = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
            if params:
                self._cursor.execute(update_sql, params)
            else:
                self._cursor.execute(update_sql)
            
            # Then select updated rows
            select_sql = f"SELECT {returning_cols} FROM {table} WHERE {where_clause}"
            if params:
                self._cursor.execute(select_sql, params)
            else:
                self._cursor.execute(select_sql)
            
            self._prefetched_data = self._cursor.fetchall()
            self._is_returning_query = True
            self._returning_cols = returning_cols
        except Exception as e:
            logger.error(f"Error in UPDATE RETURNING emulation: {e}")
            raise

    def fetchone(self):
        """Fetch single row, handling RETURNING queries"""
        if self._is_returning_query and self._prefetched_data:
            row = self._prefetched_data.pop(0)
            if self.cursor_factory:
                cols = [c.strip() for c in self._returning_cols.split(',')]
                return dict(zip(cols, row))
            return row
        
        row = self._cursor.fetchone()
        if row is None:
            return None
        
        if self.cursor_factory:
            cols = [col[0] for col in self._cursor.description or []]
            return dict(zip(cols, row))
        
        return row

    def fetchall(self):
        """Fetch all rows, handling RETURNING queries"""
        if self._is_returning_query and self._prefetched_data is not None:
            rows = self._prefetched_data[:]
            self._prefetched_data = []
            
            if self.cursor_factory:
                cols = [c.strip() for c in self._returning_cols.split(',')]
                return [dict(zip(cols, r)) for r in rows]
            return rows
        
        rows = self._cursor.fetchall()
        if self.cursor_factory and self._cursor.description:
            cols = [col[0] for col in self._cursor.description]
            return [dict(zip(cols, r)) for r in rows]
        
        return rows

    def close(self):
        """Close cursor"""
        try:
            self._cursor.close()
        except Exception as e:
            logger.warning(f"Error closing cursor: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONNECTION WRAPPER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SQLiteConnectionWrapper:
    """Wraps SQLite connection with PostgreSQL-like interface"""
    
    def __init__(self, sqlite_conn: sqlite3.Connection, cursor_factory=None):
        self._conn = sqlite_conn
        self.cursor_factory = cursor_factory
        self._in_transaction = False

    @property
    def closed(self):
        return False

    @property
    def status(self):
        return 0

    def cursor(self, cursor_factory=None):
        """Create and return wrapped cursor"""
        raw_cursor = self._conn.cursor()
        factory = cursor_factory or self.cursor_factory
        return SQLiteCursorWrapper(raw_cursor, factory)

    def commit(self):
        """Commit transaction"""
        try:
            self._conn.commit()
            self._in_transaction = False
            logger.debug("Transaction committed")
        except sqlite3.OperationalError as e:
            logger.error(f"Error committing transaction: {e}")
            raise

    def rollback(self):
        """Rollback transaction"""
        try:
            self._conn.rollback()
            self._in_transaction = False
            logger.debug("Transaction rolled back")
        except sqlite3.OperationalError as e:
            logger.warning(f"Error rolling back transaction: {e}")

    def close(self):
        """Close connection"""
        try:
            self._conn.close()
        except Exception as e:
            logger.warning(f"Error closing connection: {e}")

    def _close_raw(self):
        """Close underlying connection"""
        try:
            self._conn.close()
        except Exception as e:
            logger.warning(f"Error closing raw connection: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONNECTION POOL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SQLiteThreadedPool:
    """Thread-safe SQLite connection pool"""
    
    def __init__(self, minconn: int, maxconn: int, *args, **kwargs):
        self.db_path = _get_db_path()
        self.minconn = minconn
        self.maxconn = maxconn
        self.connections = queue.Queue(maxsize=maxconn)
        self._lock = threading.Lock()
        
        # Initialize minimum connections
        for _ in range(minconn):
            conn = self._create_connection()
            self.connections.put(conn)
        
        logger.info(f"Connection pool initialized: min={minconn}, max={maxconn}, db={self.db_path}")

    def _create_connection(self) -> SQLiteConnectionWrapper:
        """Create new SQLite connection with optimizations"""
        try:
            conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                detect_types=sqlite3.PARSE_DECLTYPES,
                timeout=10.0
            )
            
            # Optimize for concurrent access
            conn.execute("PRAGMA journal_mode=WAL;")       # Write-Ahead Logging
            conn.execute("PRAGMA busy_timeout=5000;")      # Wait 5s if locked
            conn.execute("PRAGMA synchronous=NORMAL;")     # Balance safety/speed
            conn.execute("PRAGMA foreign_keys=ON;")        # Enforce foreign keys
            
            return SQLiteConnectionWrapper(conn)
        except Exception as e:
            logger.error(f"Failed to create database connection: {e}")
            raise

    def getconn(self) -> SQLiteConnectionWrapper:
        """Get connection from pool or create new one"""
        try:
            conn = self.connections.get_nowait()
            logger.debug(f"Reused pooled connection, {self.connections.qsize()} remaining")
            return conn
        except queue.Empty:
            logger.debug("Pool exhausted, creating new connection")
            return self._create_connection()

    def putconn(self, conn: SQLiteConnectionWrapper, close: bool = False):
        """Return connection to pool or close it"""
        if close:
            conn._close_raw()
            logger.debug("Connection closed")
            return
        
        try:
            # Ensure clean state before returning to pool
            raw_conn = getattr(conn, '_conn', conn)
            if raw_conn and getattr(conn, '_in_transaction', False):
                try:
                    raw_conn.rollback()
                except Exception:
                    pass
            
            self.connections.put_nowait(conn)
            logger.debug(f"Connection returned to pool, {self.connections.qsize()} available")
        except queue.Full:
            logger.warning("Pool full, closing connection instead of returning")
            conn._close_raw()

    def closeall(self):
        """Close all connections in pool"""
        closed_count = 0
        while not self.connections.empty():
            try:
                conn = self.connections.get_nowait()
                if isinstance(conn, SQLiteConnectionWrapper):
                    conn._close_raw()
                else:
                    conn.close()
                closed_count += 1
            except Exception as e:
                logger.warning(f"Error closing pooled connection: {e}")
        
        logger.info(f"Closed {closed_count} connections from pool")


def _create_sqlite_connection(*args, **kwargs):
    """Create SQLite connection (used by mocked psycopg2.connect)"""
    cursor_factory = kwargs.get("cursor_factory", None)
    conn = sqlite3.connect(
        _get_db_path(),
        check_same_thread=False,
        detect_types=sqlite3.PARSE_DECLTYPES,
        timeout=10.0
    )
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return SQLiteConnectionWrapper(conn, cursor_factory)


# Apply global monkey patches
import psycopg2
import psycopg2.pool
import psycopg2.extras
import psycopg2.extensions

psycopg2.connect = _create_sqlite_connection
psycopg2.pool.ThreadedConnectionPool = SQLiteThreadedPool
psycopg2.extras.RealDictCursor = object
psycopg2.extensions.STATUS_READY = 0

RealDictCursor = object

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GLOBAL CONNECTION POOL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_pool = SQLiteThreadedPool(1, 3)
_pool_lock = threading.Lock()


def get_db_connection() -> SQLiteConnectionWrapper:
    """Get database connection from pool"""
    return _pool.getconn()


def _release_connection(conn: SQLiteConnectionWrapper):
    """Return connection to pool"""
    if conn:
        _pool.putconn(conn)


class PooledConn:
    """Context manager for database connections"""
    
    def __init__(self):
        self.conn = None

    def __enter__(self) -> SQLiteConnectionWrapper:
        self.conn = get_db_connection()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            try:
                raw_conn = getattr(self.conn, '_conn', self.conn)
                if exc_type:
                    try:
                        raw_conn.rollback()
                    except Exception as e:
                        logger.warning(f"Error rolling back on context exit: {e}")
            finally:
                _release_connection(self.conn)
        return False


class PooledConn2:
    """Alternative pooled connection context manager"""
    pass  # Same as PooledConn


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CACHE SYSTEM - Reduce DB round-trips
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class NoOpCache(dict):
    """A dictionary subclass that never stores or returns items, forcing direct DB queries"""
    def get(self, key, default=None):
        return None
    def __getitem__(self, key):
        raise KeyError(key)
    def __setitem__(self, key, value):
        pass
    def pop(self, key, default=None):
        return None
    def clear(self):
        pass

# Cache instances (represented as standard dicts for compatibility with sub.py)
_gate_cache = NoOpCache()
_gate_cache_ttl = 30

_credits_cache = NoOpCache()
_credits_cache_ttl = 5

_premium_cache = NoOpCache()
_premium_cache_ttl = 60

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SCHEMA INITIALIZATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ensure_users_table():
    """Create users table if not exists"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    credits INTEGER DEFAULT 0,
                    is_premium INTEGER DEFAULT 0,
                    premium_expiry TIMESTAMP,
                    cc_checked INTEGER DEFAULT 0,
                    cc_charged INTEGER DEFAULT 0,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        logger.info("Users table initialized")
    except Exception as e:
        logger.error(f"Error initializing users table: {e}")
        raise


def ensure_proxy_table():
    """Create proxies table with unique constraint"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS proxies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    proxy TEXT NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, proxy)
                )
            """)
            
            # Create index for user_id lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_proxies_user_id ON proxies(user_id)
            """)
            
            # Clean up duplicates
            cursor.execute("""
                DELETE FROM proxies 
                WHERE id NOT IN (
                    SELECT MIN(id) 
                    FROM proxies 
                    GROUP BY user_id, proxy
                )
            """)
            
            # Ensure unique constraint via index (in case the table was created without UNIQUE constraint)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_proxies_user_proxy ON proxies(user_id, proxy)
            """)
            
            conn.commit()
        logger.info("Proxies table initialized")
    except Exception as e:
        logger.error(f"Error initializing proxies table: {e}")
        raise


def ensure_receipts_table():
    """Create receipts table for subscription tracking"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id TEXT UNIQUE,
                    user_id INTEGER NOT NULL,
                    plan TEXT,
                    amount_paid REAL,
                    purchased_on TIMESTAMP,
                    expires_on TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_receipts_user_id ON receipts(user_id)
            """)
            
            conn.commit()
        logger.info("Receipts table initialized")
    except Exception as e:
        logger.error(f"Error initializing receipts table: {e}")
        raise


def ensure_codes_table():
    """Create codes table for credit codes"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS codes (
                    code TEXT PRIMARY KEY,
                    credits INTEGER,
                    claimed_by INTEGER,
                    claimed_at TIMESTAMP
                )
            """)
            conn.commit()
        logger.info("Codes table initialized")
    except Exception as e:
        logger.error(f"Error initializing codes table: {e}")
        raise


def ensure_plan_keys_table():
    """Create plan keys table for subscription codes"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS plan_keys (
                    key TEXT PRIMARY KEY,
                    plan TEXT,
                    plan_name TEXT,
                    days INTEGER,
                    credits INTEGER,
                    claimed_by INTEGER,
                    claimed_at TIMESTAMP
                )
            """)
            conn.commit()
        logger.info("Plan keys table initialized")
    except Exception as e:
        logger.error(f"Error initializing plan keys table: {e}")
        raise


def ensure_gate_status_table():
    """Create gate status table"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gate_status (
                    gate TEXT PRIMARY KEY,
                    is_enabled INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        logger.info("Gate status table initialized")
    except Exception as e:
        logger.error(f"Error initializing gate status table: {e}")
        raise


def ensure_banned_users_table():
    """Create banned users table"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id INTEGER PRIMARY KEY,
                    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT
                )
            """)
            conn.commit()
        logger.info("Banned users table initialized")
    except Exception as e:
        logger.error(f"Error initializing banned users table: {e}")
        raise


def ensure_stats_table(gate_name: str):
    """Create gate-specific stats table"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            table_name = f"{gate_name}_stats"
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    status TEXT,
                    card TEXT,
                    cvv TEXT,
                    amount REAL,
                    currency TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table_name}_user ON {table_name}(user_id)
            """)
            
            conn.commit()
        logger.debug(f"Stats table {table_name} initialized")
    except Exception as e:
        logger.warning(f"Error initializing stats table {gate_name}: {e}")


def initialize_schema():
    """Initialize all database tables"""
    logger.info("Starting schema initialization...")
    try:
        ensure_users_table()
        ensure_proxy_table()
        ensure_receipts_table()
        ensure_codes_table()
        ensure_plan_keys_table()
        ensure_gate_status_table()
        ensure_banned_users_table()
        
        # Initialize stats tables for common gates
        for gate in ['ST', 'STR', 'PF', 'VBV', 'FT', 'BL', 'PP', 'AT', 'PW', 'PYU']:
            ensure_stats_table(gate)
        
        logger.info("Schema initialization completed successfully")
    except Exception as e:
        logger.error(f"Schema initialization failed: {e}")
        raise


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# USER MANAGEMENT FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Get user details"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            return cursor.fetchone()
    except Exception as e:
        logger.error(f"Error fetching user {user_id}: {e}")
        return None


def create_user(user_id: int, username: str, first_name: str = "User", initial_credits: int = 150):
    """Create new user"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO users (user_id, username, first_name, credits, joined_at)
                VALUES (%s, %s, %s, %s, %s)
            """, (user_id, username, first_name, initial_credits, datetime.now()))
            conn.commit()
        logger.info(f"Created user {user_id} ({username})")
    except Exception as e:
        logger.error(f"Error creating user {user_id}: {e}")


def get_user_credits(user_id: int) -> int:
    """Bypassed: Always return unlimited credits"""
    return 999999999


def update_credits(user_id: int, new_credits: int):
    """Bypassed: Do nothing"""
    pass


def deduct_credits_atomic(user_id: int, amount: int) -> int:
    """Bypassed: Do nothing, always return unlimited credits"""
    return 999999999


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PREMIUM/SUBSCRIPTION FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_premium_status(user_id: int) -> Tuple[bool, Optional[datetime]]:
    """Get premium status directly from database"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT is_premium, premium_expiry FROM users WHERE user_id = %s",
                (user_id,)
            )
            row = cursor.fetchone()
            
            if not row:
                return (False, None)
            
            is_premium, expiry = row
            
            # Check if premium has expired
            if is_premium and expiry:
                if datetime.now() < expiry:
                    return (True, expiry)
                else:
                    # Reset expired premium
                    cursor.execute("""
                        UPDATE users
                        SET is_premium = 0, premium_expiry = NULL, credits = 150
                        WHERE user_id = %s
                    """, (user_id,))
                    conn.commit()
                    return (False, None)
            
            return (False, None)
    except Exception as e:
        logger.error(f"Error fetching premium status for user {user_id}: {e}")
        return (False, None)


def set_premium(user_id: int, days: int) -> bool:
    """Grant premium status"""
    try:
        expiry = datetime.now() + __import__('datetime').timedelta(days=days)
        with PooledConn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users
                SET is_premium = 1, premium_expiry = %s
                WHERE user_id = %s
            """, (expiry, user_id))
            conn.commit()
        logger.info(f"Set premium for user {user_id} for {days} days")
        return True
    except Exception as e:
        logger.error(f"Error setting premium for user {user_id}: {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GATE FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def is_gate_enabled(gate: str) -> bool:
    """Check if gate is enabled directly from database"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            
            # Ensure gate exists
            cursor.execute("""
                INSERT OR IGNORE INTO gate_status (gate, is_enabled)
                VALUES (%s, 1)
            """, (gate,))
            
            # Get status
            cursor.execute("SELECT is_enabled FROM gate_status WHERE gate = %s", (gate,))
            row = cursor.fetchone()
            
            conn.commit()
            return bool(row[0]) if row else True
    except Exception as e:
        logger.error(f"Error checking gate status for {gate}: {e}")
        return True


def set_gate_status(gate: str, enabled: bool) -> bool:
    """Set gate enabled/disabled status"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO gate_status (gate, is_enabled, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (gate)
                DO UPDATE SET is_enabled = EXCLUDED.is_enabled, updated_at = CURRENT_TIMESTAMP
            """, (gate, int(enabled), datetime.now()))
            conn.commit()
        logger.info(f"Set gate {gate} to {'enabled' if enabled else 'disabled'}")
        return True
    except Exception as e:
        logger.error(f"Error setting gate {gate} status: {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STATS FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def update_user_stats(user_id: int, is_charged: bool):
    """Update user check/charge stats"""
    try:
        with PooledConn() as conn:
            cursor = conn.cursor()
            
            if is_charged:
                cursor.execute("""
                    UPDATE users
                    SET cc_checked = cc_checked + 1, cc_charged = cc_charged + 1
                    WHERE user_id = %s
                """, (user_id,))
            else:
                cursor.execute("""
                    UPDATE users
                    SET cc_checked = cc_checked + 1
                    WHERE user_id = %s
                """, (user_id,))
            
            conn.commit()
        logger.debug(f"Updated stats for user {user_id} (charged={is_charged})")
    except Exception as e:
        logger.error(f"Error updating stats for user {user_id}: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INITIALIZATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

try:
    initialize_schema()
    logger.info("Database module initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize database: {e}")
    raise
