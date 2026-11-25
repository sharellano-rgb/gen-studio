"""Private, lazily loaded conversation records, separate from history payloads."""
import json


def initialize(store):
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS qwen_director (job_id TEXT PRIMARY KEY, state TEXT NOT NULL)')


def read(store, ident):
    with store.connect() as db:
        row=db.execute('SELECT state FROM qwen_director WHERE job_id=?',(ident,)).fetchone()
    return json.loads(row[0]) if row else None


def save(store, ident, state):
    with store.connect() as db:
        save_in_transaction(db, ident, state)


def save_in_transaction(db, ident, state):
    db.execute('INSERT INTO qwen_director VALUES (?,?) ON CONFLICT(job_id) DO UPDATE SET state=excluded.state',
        (ident,json.dumps(state,ensure_ascii=False)))
