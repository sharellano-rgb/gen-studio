"""Private revisioned metadata. Read operations never initialize a database."""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class Problem(Exception):
    def __init__(self, status, code, message, **extra):
        self.status = status
        self.detail = dict(code=code, message=message, **extra)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Repository:
    def __init__(self, workspace_root):
        root = Path(workspace_root).resolve()
        self.path = root / 'parody' / 'state.sqlite3'
        if not self.path.resolve().is_relative_to(root):
            raise Problem(409, 'INVALID_STORAGE', 'La carpeta del módulo no pertenece a este espacio.')

    def connect(self, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        else:
            db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @staticmethod
    def has_schema(db):
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise Problem(409, 'SCHEMA_VERSION', 'La base requiere otra versión del módulo.')
        return version == 1

    @staticmethod
    def initialize(db):
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise Problem(409, 'SCHEMA_VERSION', 'La base requiere otra versión del módulo.')
        db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, snapshot TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS revisions (project_id TEXT NOT NULL REFERENCES projects(id), number INTEGER NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(project_id,number))')
        db.execute('CREATE TABLE IF NOT EXISTS idempotency (scope TEXT NOT NULL, key TEXT NOT NULL, body_hash TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(scope,key))')
        db.execute('PRAGMA user_version=1')

    def read(self, project_id, revision=None):
        if self.path.is_file():
            with closing(self.connect()) as db:
                row = None
                if self.has_schema(db):
                    row = db.execute('SELECT snapshot FROM projects WHERE id=?', (project_id,)).fetchone() if revision is None else db.execute('SELECT snapshot FROM revisions WHERE project_id=? AND number=?', (project_id, revision)).fetchone()
            if row:
                return json.loads(row['snapshot'])
        raise Problem(404, 'NOT_FOUND', 'Proyecto o revisión no encontrado.')

    def guide_position(self, project_id, navigation=None):
        """Cursor changes do not invalidate approved audio, analyses or render plans."""
        current = self.read(project_id)
        if 'guide' not in current:
            if navigation:
                raise Problem(409, 'NO_GUIDE', 'Este proyecto usa el editor completo.')
            return current
        with closing(self.connect(write=navigation is not None)) as db:
            if navigation:
                db.execute('BEGIN IMMEDIATE')
                current = json.loads(db.execute('SELECT snapshot FROM projects WHERE id=?', (project_id,)).fetchone()[0])
                if current['revision'] != navigation.expected_revision or current['archived']:
                    raise Problem(409, 'GUIDE_CONFLICT', 'El proyecto cambió. Recarga su versión guardada.')
                db.execute('CREATE TABLE IF NOT EXISTS guide_cursor(project_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, step TEXT NOT NULL)')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='guide_cursor'").fetchone():
                row = db.execute('SELECT step FROM guide_cursor WHERE project_id=? AND revision=?', (project_id,current['revision'])).fetchone()
                if row:
                    current['guide']['step'] = row[0]
            if navigation:
                if current['guide']['step'] not in (navigation.previous,navigation.step):
                    raise Problem(409, 'GUIDE_CONFLICT', 'La guía avanzó en otra ventana. Recarga el proyecto.')
                db.execute('INSERT OR REPLACE INTO guide_cursor VALUES(?,?,?)', (project_id,current['revision'],navigation.step))
                db.commit()
                current['guide']['step'] = navigation.step
        return current

    def listing(self, limit=50, offset=0):
        if not self.path.is_file():
            return {'projects': [], 'next_offset': None}
        with closing(self.connect()) as db:
            if not self.has_schema(db):
                return {'projects': [], 'next_offset': None}
            rows = db.execute('SELECT snapshot FROM projects ORDER BY id LIMIT ? OFFSET ?', (limit + 1, offset)).fetchall()
        return {'projects': [json.loads(r['snapshot']) for r in rows[:limit]], 'next_offset': offset + limit if len(rows) > limit else None}

    def mutate(self, scope, key, body, transform, project_id=None):
        body_hash = hashlib.sha256(encode(body).encode()).hexdigest()
        db = self.connect(write=True)
        try:
            db.execute('BEGIN IMMEDIATE')
            self.initialize(db)
            prior = db.execute('SELECT * FROM idempotency WHERE scope=? AND key=?', (scope, key)).fetchone()
            if prior:
                if prior['body_hash'] != body_hash:
                    raise Problem(409, 'IDEMPOTENCY_CONFLICT', 'Esta clave ya se usó con otros datos.')
                return json.loads(prior['response'])
            if project_id:
                row = db.execute('SELECT snapshot FROM projects WHERE id=?', (project_id,)).fetchone()
                if not row:
                    raise Problem(404, 'NOT_FOUND', 'Proyecto no encontrado.')
                current = json.loads(row['snapshot'])
                if 'guide' in current and db.execute("SELECT 1 FROM sqlite_master WHERE name='guide_cursor' AND type='table'").fetchone():
                    cursor = db.execute('SELECT step FROM guide_cursor WHERE project_id=? AND revision=?',(project_id,current['revision'])).fetchone()
                    if cursor:
                        current['guide']['step'] = cursor[0]
                if current['revision'] != body['expected_revision']:
                    raise Problem(409, 'REVISION_CONFLICT', 'El proyecto cambió. Recarga antes de guardar.', current_revision=current['revision'])
            else:
                current = dict(id=str(uuid4()), name='', revision=0, archived=False, source=None, draft={'notes': '', 'shots': []}, created_at=datetime.now(timezone.utc).isoformat())
            result = transform(current)
            result['revision'] += 1
            result['updated_at'] = datetime.now(timezone.utc).isoformat()
            snapshot = encode(result)
            db.execute('INSERT INTO projects VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,snapshot=excluded.snapshot', (result['id'], result['revision'], snapshot))
            db.execute('INSERT INTO revisions VALUES (?,?,?)', (result['id'], result['revision'], snapshot))
            db.execute('INSERT INTO idempotency VALUES (?,?,?,?)', (scope, key, body_hash, snapshot))
            db.commit()
            return result
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()
