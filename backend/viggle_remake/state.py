"""Immutable per-window receipt; workspace/job identity never comes from an LLM."""
import hashlib,json,re
from pathlib import Path
from uuid import UUID

def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def graph_hash(graph):return hashlib.sha256(json.dumps(graph,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def atomic(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8');tmp.replace(path)

class Receipt:
    def __init__(self,workspace_root,workspace,job_id):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',workspace):raise ValueError('Identificador de espacio inválido.')
        self.workspace=workspace;self.job_id=str(UUID(str(job_id)));self.root=Path(workspace_root).resolve()
        self.folder=(self.root/'jobs'/self.job_id).resolve()
        if not self.folder.is_relative_to(self.root):raise ValueError('La toma escapa del espacio privado.')
        self.folder.mkdir(parents=True,exist_ok=True);self.path=self.folder/'viggle-receipt-v1.json'
        self.workflow=self.folder/'viggle-workflow-v1.json'
    def read(self):
        if not self.path.exists():return None
        state=json.loads(self.path.read_text(encoding='utf-8'))
        if state.get('workspace')!=self.workspace or state.get('job_id')!=self.job_id:raise ValueError('El registro pertenece a otro espacio o toma.')
        if not self.workflow.is_file() or graph_hash(json.loads(self.workflow.read_text(encoding='utf-8')))!=state.get('graph_sha256'):raise ValueError('El workflow aceptado cambió; no se reenviará.')
        return state
    def accept(self,graph):
        old=self.read()
        sha=graph_hash(graph)
        if old:
            if old['graph_sha256']!=sha:raise ValueError('Esta toma ya tiene otro workflow aceptado.')
            return old
        # Exclusive reservation across processes. Crash here leaves a malformed
        # receipt which deliberately blocks automatic resubmission.
        with self.path.open('x',encoding='utf-8') as f:f.write('{}')
        atomic(self.workflow,graph)
        state={'workspace':self.workspace,'job_id':self.job_id,'graph_sha256':sha,'status':'prepared','attempted':False,'prompt_id':None}
        atomic(self.path,state);return state
    def update(self,**changes):
        state=self.read()
        if any(k in changes for k in ('workspace','job_id','graph_sha256')):raise ValueError('La vinculación aceptada es inmutable.')
        state.update(changes);atomic(self.path,state);return state
