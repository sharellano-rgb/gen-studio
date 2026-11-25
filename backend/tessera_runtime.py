"""Start only the pinned, loopback-only runtime installed for this adapter."""
import asyncio, json, os, subprocess, time
from pathlib import Path
from urllib.parse import urlparse
import httpx

def installation(service):
    from .tessera import cfg,runtime
    from .tessera_media import digest
    expected=cfg(service).get('installation_sha256')
    if not expected:return None  # Historical pilot and isolated unit-test settings.
    rt=runtime(service);path=rt/'installation-manifest.json'
    if not path.is_file() or digest(path)!=expected:raise ValueError('Cambió el manifiesto de instalación de Tessera.')
    record=json.loads(path.read_text(encoding='utf-8'))
    for item in record['files']:
        p=Path(item['path'])
        if not p.is_file():raise ValueError('Falta un archivo de la instalación de Tessera: '+p.name)
        stat=p.stat()
        if stat.st_size!=item['bytes'] or stat.st_mtime_ns!=item['mtime_ns']:raise ValueError('Cambió un archivo validado de Tessera: '+p.name)
    if digest(rt/'vendor-manifest.json')!=record['vendor_manifest_sha256']:raise ValueError('Cambió la versión registrada del paquete Tessera.')
    vendor=json.loads((rt/'vendor-manifest.json').read_text(encoding='utf-8'))
    for rel,sha in vendor['files'].items():
        if digest(rt/'comfy'/'custom_nodes'/'ComfyUI-Tessera'/rel)!=sha:raise ValueError('Cambió el código de Tessera: '+rel)
    return expected

async def verify(service):
    from .tessera import base,runtime
    rt=runtime(service);data=await service.get(base(service),'/system_stats')
    argv=data.get('system',{}).get('argv',[])
    if not argv or Path(argv[0]).resolve()!=(rt/'comfy'/'main.py').resolve():
        raise ValueError('El puerto de Tessera pertenece a otro entorno. No se envió ni interrumpió ningún trabajo.')
    for flag,name in [('--base-directory','comfy'),('--input-directory','input'),('--output-directory','output'),('--temp-directory','scratch')]:
        if flag not in argv or Path(argv[argv.index(flag)+1]).resolve()!=(rt/name).resolve():
            raise ValueError('Las rutas del motor no coinciden con el entorno aislado.')
    expected={'comfyui_version':'0.41.0','pytorch_version':'2.11.0+cu130'}
    if any(data.get('system',{}).get(key)!=value for key,value in expected.items()):
        raise ValueError('Cambió la versión de ComfyUI o PyTorch del entorno validado de Tessera.')
    return True

async def ensure(service):
    from .tessera import base,runtime,cfg
    await asyncio.to_thread(installation,service)
    try:await verify(service);return
    except httpx.ConnectError:pass
    rt=runtime(service);u=urlparse(base(service))
    if u.hostname!='127.0.0.1' or u.scheme!='http' or u.port!=8197:raise ValueError('El arranque automático requiere el puerto local aislado 8197.')
    python=Path(cfg(service).get('python',''))
    if not python.is_file():raise ValueError('No está configurado el Python del entorno aislado.')
    args=[str(python),'-B','-u',str(rt/'tessera_bootstrap.py'),'--base-directory',str(rt/'comfy'),'--listen','127.0.0.1','--port','8197']
    for flag,name in [('--input-directory','input'),('--output-directory','output'),('--temp-directory','scratch'),('--user-directory','user'),('--extra-model-paths-config','model_paths.yaml')]:args += [flag,str(rt/name)]
    args+=['--disable-auto-launch','--cache-none','--reserve-vram','2']
    env={**os.environ,**{k:str(rt/'scratch') for k in ('TEMP','TMP','TESSERA_SCRATCH','LTX_SCRATCH')},'PYTHONDONTWRITEBYTECODE':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONIOENCODING':'utf-8'}
    env.update({key:str(rt/'cache'/folder) for key,folder in {'HF_HOME':'huggingface','TORCH_HOME':'torch','TORCHINDUCTOR_CACHE_DIR':'inductor','TRITON_CACHE_DIR':'triton','XDG_CACHE_HOME':'general'}.items()})
    # Called under the existing cross-workspace generation lease.
    with (rt/'logs'/'server.log').open('ab') as stdout,(rt/'logs'/'server.err.log').open('ab') as stderr:
        proc=subprocess.Popen(args,cwd=rt/'comfy',env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (rt/'instance.json').write_text(json.dumps({'pid':proc.pid,'root':str(rt),'port':8197,'started':time.time()}))
    for _ in range(90):
        await asyncio.sleep(1)
        try:await verify(service);return
        except httpx.ConnectError:pass
        if proc.poll() is not None:raise ValueError('Tessera no pudo iniciar; consulta su registro de arranque.')
    raise ValueError('Tessera no respondió al arranque en 90 segundos; no se reenvió el trabajo.')

async def release_idle(service):
    """Confirm unload completion, not just acceptance of Comfy's /free flag."""
    from .tessera import cfg,base,busy
    if not cfg(service).get('enabled') or not cfg(service).get('memory_bridge'):return
    try:await verify(service)
    except httpx.ConnectError:return
    if await busy(service):raise ValueError('Tessera sigue trabajando; espera antes de cargar otro modelo.')
    await service.post(base(service),'/free',json={'unload_models':True,'free_memory':True})
    for _ in range(40):
        await asyncio.sleep(.5)
        state=await service.get(base(service),'/gen-studio/tessera-memory')
        if state.get('version')=='studio-tessera-memory-1' and state.get('loaded_models')==0:return
    raise ValueError('Tessera no confirmó la descarga de modelos. No se cargó el siguiente motor.')
