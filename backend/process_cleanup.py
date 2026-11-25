"""Stop a tracked Alisson CPU helper and its own media subprocesses."""
import asyncio
import os
import subprocess

async def stop_process_tree(process, timeout=5):
    if process.returncode is not None:return
    if os.name == 'nt':
        killer=await asyncio.create_subprocess_exec('taskkill.exe','/PID',str(process.pid),'/T','/F',
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            _,error=await asyncio.wait_for(killer.communicate(),timeout=max(10,timeout))
        except TimeoutError as failure:
            if killer.returncode is None:killer.kill();await killer.wait()
            raise RuntimeError('No se confirmó la detención de la preparación y sus procesos hijos.') from failure
        if killer.returncode:
            if process.returncode is not None:return
            try:await asyncio.wait_for(process.wait(),timeout=timeout);return
            except TimeoutError as failure:
                raise RuntimeError('No se pudo detener la preparación: '+error.decode(errors='replace')[-1000:]) from failure
        try:await asyncio.wait_for(process.wait(),timeout=timeout)
        except TimeoutError as failure:raise RuntimeError('El proceso de preparación no confirmó su salida.') from failure
        return
    process.terminate()
    try:await asyncio.wait_for(process.wait(),timeout=timeout)
    except TimeoutError:process.kill();await process.wait()
