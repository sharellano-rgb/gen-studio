"""Runs with the isolated Python's existing psutil, never imports Torch."""
import argparse
import json
import pathlib
import subprocess
import sys
import psutil

p=argparse.ArgumentParser();p.add_argument('action',choices=['capture','inspect','stop','resources']);p.add_argument('--record');p.add_argument('--pid',type=int);p.add_argument('--nonce');a=p.parse_args()
base=pathlib.Path(sys.executable).resolve().parent.parent
if not (base/'ComfyUI/main.py').is_file():raise RuntimeError('Unexpected isolated interpreter')

def owned(record):
    if record['executable']!=str(base/'python/python.exe'):raise RuntimeError('Unexpected executable binding')
    try:process=psutil.Process(record['pid'])
    except psutil.NoSuchProcess:return None
    if (abs(process.create_time()-record['create_time'])>.01 or pathlib.Path(process.exe()).resolve()!=base/'python/python.exe'
            or process.cmdline()!=record['argv']):raise RuntimeError('PID ownership changed; no process stopped')
    return process

if a.action=='resources':
    raw=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,memory.free,utilization.gpu','--format=csv,noheader,nounits'],text=True,timeout=8,creationflags=subprocess.CREATE_NO_WINDOW)
    used,free,util=[int(s.strip()) for s in raw.strip().split(',')]
    result={'gpu_used_mib':used,'gpu_free_mib':free,'gpu_utilization_percent':util,'host_available_bytes':psutil.virtual_memory().available}
elif a.action=='capture':
    process=psutil.Process(a.pid);args=process.cmdline()
    if pathlib.Path(process.exe()).resolve()!=base/'python/python.exe' or str(base/'ComfyUI/main.py') not in args or not any(a.nonce in s for s in args):
        raise RuntimeError('New process does not match isolated runtime and nonce')
    result={'pid':a.pid,'create_time':process.create_time(),'executable':str(base/'python/python.exe'),'argv':args}
else:
    path=pathlib.Path(a.record).resolve()
    if not path.is_relative_to(base/'runtime'):raise RuntimeError('Process record outside private runtime')
    record=json.loads(path.read_text(encoding='utf-8'));process=owned(record)
    if a.action=='stop' and process:
        # Recheck immediately before termination; do not enumerate arbitrary PIDs.
        descendants=process.children(recursive=True)
        for child in descendants:
            try:child.terminate()
            except psutil.NoSuchProcess:pass
        process.terminate()
        _,alive=psutil.wait_procs([process,*descendants],timeout=15)
        for child in alive:
            if child==process:owned(record)
            child.kill()
        _,alive=psutil.wait_procs(alive,timeout=10)
        if alive:raise RuntimeError('Owned experimental processes did not exit')
        process=None
    result={'running':process is not None,'pid':record['pid']}
    if process:
        info=process.memory_info();result.update(rss_bytes=info.rss,private_bytes=info.private)
print(json.dumps(result))
