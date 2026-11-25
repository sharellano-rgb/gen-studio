"""Frame-exact media handling; the model never supplies the delivered audio."""
import asyncio, hashlib, json, math, os, shutil, subprocess
from fractions import Fraction
from pathlib import Path
from .tessera_graph import ladder

FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)
def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def inspect(path,max_frames=1440):
    r=subprocess.run([shutil.which('ffprobe') or 'ffprobe','-v','error','-count_frames','-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True,timeout=180,creationflags=FLAGS)
    if r.returncode:raise ValueError('No se pudo leer el vídeo.')
    d=json.loads(r.stdout);videos=[s for s in d['streams'] if s['codec_type']=='video']
    if len(videos)!=1:raise ValueError('Se requiere una sola pista de vídeo.')
    v=videos[0];fps=Fraction(v['avg_frame_rate']);rate=Fraction(v['r_frame_rate']);frames=int(v.get('nb_read_frames') or 0)
    if fps!=24 or rate!=24:raise ValueError('Tessera está validado a 24 fps constantes. Conserva tu original; no se convierte automáticamente.')
    duration=float(v.get('duration') or d['format'].get('duration') or 0)
    if not 1<=frames<=max_frames or abs(duration-frames/24)>.05:raise ValueError(f'Admite de 1 fotograma a {max_frames/24:g} segundos a 24 fps constantes; duración irregular no compatible.')
    w,h=int(v['width']),int(v['height'])
    rotation=float(next((s['rotation'] for s in v.get('side_data_list',[]) if 'rotation' in s),v.get('tags',{}).get('rotate',0)))
    if rotation%90:raise ValueError('La rotación de origen debe ser un múltiplo de 90 grados.')
    if rotation%180:w,h=h,w
    if min(w,h)<32 or max(w,h)>8192:raise ValueError('Dimensiones de origen no compatibles.')
    if v.get('color_transfer') in ('smpte2084','arib-std-b67'):raise ValueError('HDR requiere conversión de color explícita; este piloto admite SDR.')
    if v.get('sample_aspect_ratio','1:1') not in ('1:1','N/A'):raise ValueError('Se requieren píxeles cuadrados.')
    audio=[s for s in d['streams'] if s['codec_type']=='audio']
    if any(abs(float(s.get('start_time',0))-float(v.get('start_time',0)))>.05 for s in audio):
        raise ValueError('Este vídeo tiene pistas de audio desplazadas. Se necesita un ajuste explícito para conservar su sincronía.')
    # Equal nominal and average rates alone do not prove constant frame timing.
    timing=subprocess.run([shutil.which('ffprobe') or 'ffprobe','-v','error','-select_streams','v:0','-show_entries','frame=best_effort_timestamp_time','-of','json',str(path)],capture_output=True,text=True,timeout=180,creationflags=FLAGS)
    stamps=[float(f['best_effort_timestamp_time']) for f in json.loads(timing.stdout).get('frames',[]) if 'best_effort_timestamp_time' in f]
    if timing.returncode or len(stamps)!=frames or any(abs(b-a-1/24)>.001 for a,b in zip(stamps,stamps[1:])):
        raise ValueError('Los tiempos reales de los fotogramas no son constantes a 24 fps.')
    return dict(width=w,height=h,frames=frames,fps='24/1',duration=frames/24,codec=v['codec_name'],
        audio_tracks=len(audio),audio_durations=[float(s.get('duration') or duration) for s in audio],bytes=Path(path).stat().st_size,rotation=rotation)

def dimensions(info,resolution):
    short={'1080p':1080,'2k':1440,'4k':2160}[resolution]
    w,h=info['width'],info['height'];ratio=short/min(w,h)
    if ratio<=1:raise ValueError('El origen ya alcanza esta resolución. Elige un destino mayor.')
    tw,th=round(w*ratio/2)*2,round(h*ratio/2)*2
    if max(tw,th)>4096:raise ValueError('La proporción supera el límite de 4096 píxeles del perfil.')
    # Encode at half size with padding; crop only the padding after refinement.
    cw,ch=math.ceil(tw/64)*64,math.ceil(th/64)*64
    return dict(width=tw,height=th,canvas_width=cw,canvas_height=ch,crop_x=4*((cw-tw)//8),crop_y=4*((ch-th)//8))

def estimate_disk(info,target):
    # Whole decoded fp16 tensor, source, intermediate video, and safety margin.
    return int(ladder(info['frames'])*target['canvas_width']*target['canvas_height']*3*2*2+2*1024**3)

async def command(args,log,cancelled=lambda:False,progress=None):
    with Path(log).open('ab') as err:
        proc=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=err,creationflags=FLAGS)
        async def consume():
            async for line in proc.stdout:
                if progress and line.startswith(b'frame='):
                    try:progress(int(line.split(b'=',1)[1]))
                    except (ValueError,KeyError):pass
        reader=asyncio.create_task(consume())
        try:
            while proc.returncode is None:
                if cancelled():raise asyncio.CancelledError()
                try:await asyncio.wait_for(proc.wait(),.3)
                except TimeoutError:pass
            await reader
            if proc.returncode:raise ValueError('FFmpeg falló. Consulta el registro del trabajo.')
        finally:
            if proc.returncode is None:proc.kill();await proc.wait()
            await asyncio.gather(reader,return_exceptions=True)

def ffmpeg():return [shutil.which('ffmpeg') or 'ffmpeg','-nostdin','-hide_banner','-loglevel','error','-threads','2','-filter_threads','2','-n']

async def prepare(source,target,info,size,start,count,log,cancelled):
    tw,th=size['width']//2,size['height']//2;cw,ch=size['canvas_width']//2,size['canvas_height']//2
    x=size.get('crop_x',2*((cw-tw)//2))//2;y=size.get('crop_y',2*((ch-th)//2))//2
    vf=f'trim=start_frame={start}:end_frame={start+count},setpts=PTS-STARTPTS,scale={tw}:{th}:flags=lanczos,pad={cw}:{ch}:{x}:{y}'
    if cw!=tw or ch!=th:
        vf+=f',fillborders=left={x}:right={cw-tw-x}:top={y}:bottom={ch-th-y}:mode=smear'
    vf+=f',setsar=1,tpad=stop_mode=clone:stop={ladder(count)-count}'
    await command(ffmpeg()+['-i',str(source),'-vf',vf,'-frames:v',str(ladder(count)),'-c:v','libx264','-preset','fast','-crf','12','-pix_fmt','yuv420p','-an',str(target)],log,cancelled)

async def finish(raw,source,target,size,start,count,log,cancelled,progress=None):
    vf=f"crop={size['width']}:{size['height']}:{size.get('crop_x','(iw-ow)/2')}:{size.get('crop_y','(ih-oh)/2')},trim=end_frame={count},setpts=PTS-STARTPTS,setsar=1"
    await command(ffmpeg()+['-i',str(raw),'-ss',str(start/24),'-i',str(source),'-map','0:v:0','-map','1:a?',
        '-vf',vf,'-t',str(count/24),'-fps_mode','passthrough','-c:v','libx264','-preset','medium','-crf','17',
        '-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-af','asetpts=PTS-STARTPTS,apad','-movflags','+faststart',
        '-progress','pipe:1',str(target)],log,cancelled,progress)

async def assemble(source,replacement,target,size,start,count,total,log,cancelled,progress=None):
    w,h=size['width'],size['height'];filters=[];labels=[]
    for label,inp,begin,end in [('before',0,0,start),('new',1,0,count),('after',0,start+count,total)]:
        if begin==end:continue
        filters.append(f'[{inp}:v]trim=start_frame={begin}:end_frame={end},setpts=PTS-STARTPTS,scale={w}:{h}:flags=lanczos,setsar=1[{label}]');labels.append(f'[{label}]')
    filters.append(''.join(labels)+f'concat=n={len(labels)}:v=1:a=0[out]')
    await command(ffmpeg()+['-i',str(source),'-i',str(replacement),'-filter_complex_threads','2','-filter_complex',';'.join(filters),
        '-map','[out]','-map','0:a?','-t',str(total/24),'-fps_mode','passthrough','-c:v','libx264','-crf','17','-pix_fmt','yuv420p',
        '-c:a','aac','-b:a','192k','-movflags','+faststart','-progress','pipe:1',str(target)],log,cancelled,progress)

def check_output(path,source,size,count):
    got=inspect(path,max_frames=max(1440,count))
    if any(got[k]!=expected for k,expected in {'frames':count,'width':size['width'],'height':size['height'],
        'fps':'24/1','audio_tracks':source['audio_tracks']}.items()):raise ValueError('La salida no conserva duración, fotogramas, resolución o pistas de audio.')
    if any(abs(d-count/24)>.075 for d in got['audio_durations']):raise ValueError('La duración del audio no coincide con la del vídeo entregado.')
    return {**got,'sha256':digest(path)}

def edge_metric(path,frame):
    p=subprocess.run(ffmpeg()+['-i',str(path),'-vf',f'select=eq(n\\,{frame}),scale=96:54,format=gray','-frames:v','1','-f','rawvideo','pipe:1'],capture_output=True,timeout=45,creationflags=FLAGS)
    data=p.stdout
    if p.returncode or len(data)!=96*54:return None
    return {'luma':sum(data)/len(data),'sharpness':sum(abs(data[i]-data[i-1]) for i in range(1,len(data)) if i%96)/ (95*54)}

def continuity(source,replacement,start,count,total):
    out=[]
    for side,a,b in [('inicio',start-1,0),('final',start+count,count-1)]:
        if not 0<=a<total:continue
        x,y=edge_metric(source,a),edge_metric(replacement,b)
        if x and y:out.append({'boundary':side,'luma_delta':round(abs(x['luma']-y['luma']),2),
            'sharpness_ratio':round((y['sharpness']+.01)/(x['sharpness']+.01),3)})
    return out
