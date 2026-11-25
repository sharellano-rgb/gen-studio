"""Workspace-private, read-only progress snapshots for the current job."""
import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException

from .job_estimates import estimate_for_job


def register_job_activity(app):
    @app.get('/api/jobs/{job_id}/activity')
    async def job_activity(job_id: UUID):
        store = app.state.jobs.store
        job = store.job(str(job_id))
        if not job:
            raise HTTPException(404, 'Trabajo no encontrado.')
        observed = datetime.now(timezone.utc)
        estimate = await asyncio.to_thread(estimate_for_job, store, job, observed)
        return dict(job_id=job['id'], status=job['status'], stage=job.get('stage'),
                    progress=job.get('progress'), activity=job.get('activity'),
                    progress_source=job.get('progress_source'), estimate=estimate,
                    created_at=job.get('created_at'), updated_at=job.get('updated_at'),
                    observed_at=observed.isoformat())
