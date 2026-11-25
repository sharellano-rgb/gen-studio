"""Private analysis tasks; separate database, no migration of project revisions."""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from uuid import UUID, uuid4
from .repository import Repository, Problem, encode

ACTIVE = {'running', 'cancelling'}


def now():
    return datetime.now(timezone.utc).isoformat()


class AnalysisStore:
    def __init__(self, root):
        self.folder = Repository(root).path.parent / 'analyses'
        self.path = self.folder / 'tasks.sqlite3'
        if not self.path.resolve().is_relative_to(Repository(root).path.parent.resolve()):
            raise Problem(409, 'INVALID_STORAGE', 'La carpeta de análisis no pertenece a este espacio.')

    def connect(self, write=False):
        if write:
            self.folder.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            db.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, project TEXT NOT NULL, request_key TEXT NOT NULL, body_hash TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(project,request_key))')
            return db
        return sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True)

    def prior(self, project, key, body):
        if not self.path.exists(): return None
        with closing(self.connect()) as db:
            row = db.execute('SELECT body_hash,record FROM tasks WHERE project=? AND request_key=?', (project, key)).fetchone()
        if not row: return None
        if row[0] != hashlib.sha256(encode(body).encode()).hexdigest():
            raise Problem(409, 'IDEMPOTENCY_CONFLICT', 'Esta clave de análisis ya se usó con otros datos.')
        return json.loads(row[1])

    def create(self, project, key, body, binding, source):
        with closing(self.connect(True)) as db:
            db.execute('BEGIN IMMEDIATE')
            previous = self.prior(project['id'], key, body)
            if previous: return previous
            record = dict(id=str(uuid4()), project_id=project['id'], project_revision=project['revision'],
                          source=source, qwen_model_binding=binding, version='parody-analysis-1',
                          status='running', stage='Preparando evidencia visual', created_at=now(), updated_at=now(),
                          cancel_requested=False, vram_released=False, windows=[], error=None, plan=None,
                          review_required=True, audio_analysis='not_available')
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?)', (record['id'], project['id'], key,
                       hashlib.sha256(encode(body).encode()).hexdigest(), encode(record)))
            db.commit()
        return record

    def read(self, project, ident):
        if self.path.exists():
            with closing(self.connect()) as db:
                row = db.execute('SELECT record FROM tasks WHERE project=? AND id=?', (project, ident)).fetchone()
            if row: return json.loads(row[0])
        raise Problem(404, 'NOT_FOUND', 'Análisis no encontrado en este proyecto.')

    def listing(self, project):
        if not self.path.exists(): return []
        with closing(self.connect()) as db:
            rows = db.execute('SELECT record FROM tasks WHERE project=? ORDER BY rowid DESC LIMIT 30', (project,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def update(self, project, ident, **patch):
        with closing(self.connect(True)) as db:
            db.execute('BEGIN IMMEDIATE')
            record = self.read(project, ident)
            record.update(patch, updated_at=now())
            db.execute('UPDATE tasks SET record=? WHERE project=? AND id=?', (encode(record), project, ident))
            db.commit()
        return record

    def directory(self, ident):
        folder = (self.folder / str(UUID(ident))).resolve()
        if not folder.is_relative_to(self.folder.resolve()):
            raise Problem(409, 'INVALID_STORAGE', 'La evidencia no pertenece al análisis.')
        return folder
