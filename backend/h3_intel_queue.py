"""Run prepared H3 language work independently of the Ryzen generation lease."""
import asyncio
import json
import logging
from uuid import uuid4

from . import qwen_director_store as records, qwen_runtime
from .store import now

logger = logging.getLogger("gen_studio")


def enqueue(service, job, state, owners):
    attempt = state.get("intel_attempt") or {}
    ids = [owner["id"] for owner in owners]
    state["intel_attempt"] = {
        "id": str(uuid4()), "status": "queued", "owners": ids, "queued_at": now(),
        "priority": int(attempt.get("status") == "retry_queued"),
    }
    with service.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        saved = db.execute("SELECT state FROM qwen_director WHERE job_id=?", (job["id"],)).fetchone()
        current = json.loads(saved[0]) if saved else {}
        if current.get("plan") or current.get("intel_attempt") != (attempt or None):
            raise ValueError("El plan o su intento ya cambiaron. Actualiza la toma.")
        for ident in ids:
            row = db.execute("SELECT state FROM jobs WHERE id=?", (ident,)).fetchone()
            owner = json.loads(row[0]) if row else {}
            if owner.get("status") not in {"queued","preparing","running"} or owner.get("cancel_requested"):
                raise ValueError("La toma dejó de estar disponible para planificar.")
            service.store.update_in_transaction(db, ident, status="queued", qwen_waiting=True,
                qwen_question_job_id=job["id"], intel_plan_pending=job["id"],
                stage="Indicaciones H3 en cola en Intel", progress=None)
        records.save_in_transaction(db, job["id"], state)
    service.intel_wake.set()


def pending(service):
    with service.store.connect() as db:
        return [row[0] for row in db.execute("""
            SELECT q.job_id FROM qwen_director q JOIN jobs j ON j.id=q.job_id
            WHERE json_extract(j.state,'$.intel_plan_pending')=j.id
              AND json_extract(j.state,'$.status')='queued'
              AND json_extract(q.state,'$.intel_attempt.status') IN ('queued','sending')
            ORDER BY json_extract(q.state,'$.intel_attempt.priority') DESC,
                     json_extract(q.state,'$.intel_attempt.queued_at'), q.job_id
        """)]


def transition(service, ident, attempt_id, expected, new_status, *, error=None, plan=None):
    """Claim and publish only while every owner is waiting for this exact child."""
    with service.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state FROM qwen_director WHERE job_id=?", (ident,)).fetchone()
        state = json.loads(row[0]) if row else {}
        attempt = state.get("intel_attempt", {})
        if attempt.get("id") != attempt_id or attempt.get("status") != expected:
            return False
        for owner in attempt["owners"]:
            row = db.execute("SELECT state FROM jobs WHERE id=?", (owner,)).fetchone()
            current = json.loads(row[0]) if row else {}
            if (current.get("status") != "queued" or current.get("cancel_requested")
                    or current.get("intel_plan_pending") != ident):
                return False
        attempt.update(status=new_status)
        if new_status == "sending":
            attempt["started_at"] = now()
            stage = "Intel está preparando las indicaciones H3"
        else:
            attempt["finished_at"] = now()
            stage = "Indicaciones de Intel listas para H3"
        if error:
            attempt["error"] = error
            stage = "Intel en pausa · Abre el asistente para reintentar"
        if plan is not None:
            attempt["result"] = plan
        records.save_in_transaction(db, ident, state)
        for owner in attempt["owners"]:
            service.store.update_in_transaction(db, owner, stage=stage, qwen_waiting=new_status != "planned",
                intel_plan_pending=ident if new_status == "sending" else None,
                qwen_question_job_id=None if new_status == "planned" else ident)
    service.wake.set()
    return True


async def execute(service, ident):
    from .qwen_director import compose
    state = records.read(service.store, ident)
    attempt = state["intel_attempt"]
    key = attempt["id"]
    if attempt["status"] == "sending":
        transition(service, ident, key, "sending", "paused",
                   error="El servicio se reinició durante el envío a Intel. Verifica el puente antes de reintentar.")
        return
    if not transition(service, ident, key, "queued", "sending"):
        return
    try:
        with qwen_runtime.scope(service.store.job(ident), service):
            task = asyncio.create_task(compose(service, state, "Prepare this take using the selected settings."))
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=1)
                    owner = service.store.job(ident)
                    if not owner or owner.get("cancel_requested") or owner["status"] != "queued":
                        return
                plan = task.result()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    except Exception as exc:
        transition(service, ident, key, "sending", "paused", error=str(exc)[:600] or "Falló la planificación en Intel.")
    else:
        transition(service, ident, key, "sending", "planned", plan=plan)


async def work(service):
    while True:
        service.intel_wake.clear()
        try:
            if service.coordinator and service.coordinator.maintenance:
                await asyncio.sleep(1)
                continue
            for ident in pending(service):
                await execute(service, ident)
            await service.intel_wake.wait()
        except Exception:
            logger.exception("Error al supervisar la cola de planificación Intel")
            await asyncio.sleep(2)
