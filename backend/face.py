import asyncio
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def available(settings):
    model_root = Path(settings.get('face_models', ''))
    return bool(settings.get('face_python')) and all(path.is_file() for path in [
        Path(settings['face_python']), model_root / 'inswapper_128.onnx',
        model_root / 'models/buffalo_l/det_10g.onnx', model_root / 'models/buffalo_l/w600k_r50.onnx'])


async def refine(service, job, validate_only=False):
    if not available(service.settings):
        raise ValueError('Faltan las rutas locales del procesamiento facial; no se instala ningún componente.')
    job_id = job['id']
    payload = job['parameters']
    folder = service.root / 'jobs' / job_id
    folder.mkdir(parents=True, exist_ok=True)
    run_root = folder / ('face-run-' + str(uuid4()))
    run_root.mkdir()
    video = service.store.asset(payload['video_id'])
    reference = service.store.asset(payload['face_reference_id'])
    manifest = {'model_root': service.settings['face_models'], 'parent_pid': os.getpid(), 'reference': str(service.root / 'inputs' / reference['filename']),
                'source': str(service.root / 'inputs' / video['filename']),
                'generated': str(service.root / 'results' / (job_id + '.mp4')),
                'mask': str(folder / 'subject-mask.mp4'), 'frames': payload['frames'],
                'target': str(service.root / 'results' / (job_id + '.face.mp4')),
                'run_root': str(run_root), 'validate_only': validate_only,
                'forced_cut_frames': job.get('head_plan', {}).get('scene_cuts', [])}
    manifest_path = run_root / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    process = None
    with (run_root / 'stderr.log').open('wb') as error_log:
        try:
            process = await asyncio.create_subprocess_exec(
                service.settings['face_python'], '-u', str(ROOT / 'scripts/refine_face.py'), str(manifest_path),
                stdout=asyncio.subprocess.PIPE, stderr=error_log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'PYTHONIOENCODING': 'utf-8'})
            while line := await process.stdout.readline():
                try:
                    event = json.loads(line.decode('utf-8'))
                    if event.get('type') == 'progress' and not validate_only:
                        service.store.update(job_id, stage='Corrigiendo el rostro del sujeto',
                                             progress={'value': event['frame'], 'max': payload['frames']})
                except (UnicodeDecodeError, ValueError):
                    continue
            if await process.wait():
                detail = (run_root / 'error.json')
                error = json.loads(detail.read_text())['error'] if detail.is_file() else 'El proceso facial terminó con error. Consulta el registro del trabajo.'
                raise ValueError(error)
        finally:
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
    report = json.loads((run_root / 'report.json').read_text(encoding='utf-8'))
    if not validate_only:
        (folder / 'face-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
