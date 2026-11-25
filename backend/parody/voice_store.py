"""Private, immutable-input voice previews with idempotent admission."""
import hashlib
import json
from contextlib import closing
from uuid import uuid4
from .analysis_store import AnalysisStore, now
from .repository import Repository, Problem, encode


class VoiceStore(AnalysisStore):
    def __init__(self,root):
        self.folder=Repository(root).path.parent/'voices'
        self.path=self.folder/'tasks.sqlite3'
        if not self.folder.resolve().is_relative_to(Repository(root).path.parent.resolve()):
            raise Problem(409,'INVALID_STORAGE','La carpeta de voces no pertenece al espacio.')

    def create(self,project,key,body,binding,input_hash):
        with closing(self.connect(True)) as db:
            db.execute('BEGIN IMMEDIATE')
            prior=self.prior(project['id'],key,body)
            if prior:return prior
            record=dict(id=str(uuid4()),project_id=project['id'],project_revision=project['revision'],
                        line_id=binding['line']['id'],input_hash=input_hash,binding=binding,
                        status='running',stage='Preparando vista previa de voz',created_at=now(),updated_at=now(),
                        cancel_requested=False,resources_released=False,artifact=None,error=None,
                        synthetic_label='PARODIA SINTÉTICA · vista previa',review_required=True)
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?)',(record['id'],project['id'],key,
                       hashlib.sha256(encode(body).encode()).hexdigest(),encode(record)))
            db.commit()
        return record
