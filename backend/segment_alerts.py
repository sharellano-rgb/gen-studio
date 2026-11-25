"""Private device subscriptions and transactional delivery of segment-ready events."""
from contextlib import contextmanager, closing
import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field

HELPER = Path(__file__).resolve().parents[1]/'integrations/segment-push/send.cjs'


@contextmanager
def transaction(store):
    with closing(store.connect()) as db, db:
        yield db


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS segment_push_keys (id INTEGER PRIMARY KEY, keys TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS segment_devices (endpoint TEXT PRIMARY KEY, subscription TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS segment_alerts (endpoint TEXT, job TEXT, number INTEGER, due INTEGER, created INTEGER, PRIMARY KEY(endpoint,job))')


def enqueue(db,state,changes):
    p=state.get('parameters',{})
    if not p.get('sequence_parent_id') or type(p.get('sequence_index')) is not int:
        return
    if state.get('human_segment_review') or state.get('result_available'):
        return
    if not (changes.get('human_segment_review') or changes.get('result_available') and changes.get('status')=='completed'):
        return
    stamp=int(time.time())
    db.execute('INSERT OR IGNORE INTO segment_alerts SELECT endpoint,?,?,?,? FROM segment_devices',
               (state['id'],p['sequence_index']+1,stamp,stamp))


def helper(body):
    out=subprocess.run(['node',str(HELPER)],input=json.dumps(body),capture_output=True,text=True,
                       timeout=20,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return json.loads(out.stdout)


def keys(store):
    with transaction(store) as db:
        row=db.execute('SELECT keys FROM segment_push_keys WHERE id=1').fetchone()
        if not row:
            db.execute('INSERT OR IGNORE INTO segment_push_keys VALUES (1,?)',(json.dumps(helper({})),))
            row=db.execute('SELECT keys FROM segment_push_keys WHERE id=1').fetchone()
        return json.loads(row[0])


class Device(BaseModel):
    endpoint:str=Field(max_length=3000)
    keys:dict[str,str]


def valid_endpoint(endpoint):
    u=urlsplit(endpoint)
    return (u.scheme=='https' and u.netloc in ('fcm.googleapis.com','updates.push.services.mozilla.com',
            'web.push.apple.com','push.services.mozilla.com') and len(u.path)>1 and not u.fragment)


def register(app):
    @app.get('/api/segment-alerts/key')
    async def public_key():
        return {'publicKey':(await asyncio.to_thread(keys,app.state.jobs.current.store))['publicKey']}

    @app.post('/api/segment-alerts/device')
    def subscribe(device:Device):
        import base64
        try:
            if not valid_endpoint(device.endpoint):raise ValueError()
            for name,size in [('p256dh',65),('auth',16)]:
                value=device.keys[name]
                if len(value)>100 or len(base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True))!=size:raise ValueError()
        except (KeyError,ValueError):
            raise HTTPException(422,'Suscripción push no compatible.')
        with transaction(app.state.jobs.current.store) as db:
            db.execute('INSERT OR REPLACE INTO segment_devices VALUES (?,?)',(device.endpoint,device.model_dump_json()))
        return {'enabled':True}

    @app.post('/api/segment-alerts/status')
    def status(device:Device):
        with transaction(app.state.jobs.current.store) as db:
            enabled=bool(db.execute('SELECT 1 FROM segment_devices WHERE endpoint=?',(device.endpoint,)).fetchone())
        return {'enabled':enabled}

    @app.post('/api/segment-alerts/disable')
    def disable(device:Device):
        with transaction(app.state.jobs.current.store) as db:
            remove(db,device.endpoint)
        return {'enabled':False}


def remove(db,endpoint):
    db.execute('DELETE FROM segment_devices WHERE endpoint=?',(endpoint,))
    db.execute('DELETE FROM segment_alerts WHERE endpoint=?',(endpoint,))


def deliver(store):
    stamp=int(time.time())
    with transaction(store) as db:
        db.execute('DELETE FROM segment_alerts WHERE created<?',(stamp-86400,))
        row=db.execute('SELECT a.endpoint,job,number,subscription FROM segment_alerts a JOIN segment_devices d USING(endpoint) WHERE due<=? ORDER BY due LIMIT 1',(stamp,)).fetchone()
    if not row:return
    endpoint,job,number,subscription=row
    status=helper({'subscription':json.loads(subscription),'keys':keys(store),'payload':{'job':job,'number':number}})['status']
    with transaction(store) as db:
        if status in (404,410):remove(db,endpoint)
        elif 200<=status<300:db.execute('DELETE FROM segment_alerts WHERE endpoint=? AND job=?',(endpoint,job))
        else:db.execute('UPDATE segment_alerts SET due=? WHERE endpoint=? AND job=?',(stamp+60,endpoint,job))


async def worker(services):
    while True:
        for service in services.values():
            try:await asyncio.to_thread(deliver,service.store)
            except Exception:logging.getLogger('gen_studio').warning('No se pudo procesar un aviso de tramo.')
        await asyncio.sleep(5)
