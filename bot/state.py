import hashlib
import sqlite3
from pathlib import Path


class State:
    """Persistent deduplication stores only hashed URL/video pairs, never text."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute('CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY)')
        self.conn.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
        self.pending = set()

    @staticmethod
    def key(url, video_id):
        return hashlib.sha256(f'{url}\n{video_id}'.encode()).hexdigest()

    def seen(self, url, video_id):
        key = self.key(url, video_id)
        return key in self.pending or bool(self.conn.execute('SELECT 1 FROM seen WHERE key=?', (key,)).fetchone())

    def stage(self, url, video_id):
        self.pending.add(self.key(url, video_id))

    def commit(self):
        with self.conn:
            self.conn.executemany('INSERT OR IGNORE INTO seen VALUES(?)', [(k,) for k in self.pending])
        self.pending.clear()

    def close(self):
        self.conn.close()

    def get(self, key):
        row = self.conn.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def set(self, key, value):
        with self.conn:
            self.conn.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', (key, str(value)))
