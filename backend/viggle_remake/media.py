"""CPU-only exact window extraction/assembly. All paths are supplied by trusted backend code."""
import json,subprocess
from pathlib import Path
from fractions import Fraction
from .state import digest,atomic
from .planner import validate

def run(args):
    result=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-xerror','-n',*map(str,args)],capture_output=True,timeout=300,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('FFmpeg: '+result.stderr.decode(errors='replace')[-1500:])

def probe(path):
    r=subprocess.run(['ffprobe','-v','error','-count_frames','-show_streams','-show_format','-of','json',str(path)],capture_output=True,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if r.returncode:raise ValueError('No se puede leer el vídeo generado.')
    return json.loads(r.stdout)

def validate_video(path,frames,width,height):
    info=probe(path);v=next(s for s in info['streams'] if s['codec_type']=='video')
    if (int(v['nb_read_frames']),v['width'],v['height'],Fraction(v['avg_frame_rate']))!=(frames,width,height,Fraction(24)):
        raise ValueError('El vídeo no conserva fotogramas, resolución o FPS.')
    run(['-i',path,'-f','null','-'])
    return {'frames':frames,'width':width,'height':height,'fps':24,'video_seconds':frames/24,'audio':any(s['codec_type']=='audio' for s in info['streams'])}

def prepare_window(source,window,target,width,height,expected_sha,full_frame=False):
    if digest(source)!=expected_sha:raise ValueError('Cambió la fuente normalizada.')
    a,b=window['source'];nf=window['render_frames'];pad=window['padding_frames']
    if b-a+pad!=nf or a<0 or b<=a:raise ValueError('Ventana fuente inválida.')
    info=probe(source);audio=any(s['codec_type']=='audio' for s in info['streams'])
    video=next(s for s in info['streams'] if s['codec_type']=='video')
    near_aspect=abs((video['width']/video['height'])/(width/height)-1)<=.03
    fit=(f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2' if full_frame else f'scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height}')
    if full_frame and near_aspect:fit=f'scale={width}:{height}:flags=lanczos'
    vf=f'trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS,{fit},setsar=1,tpad=stop_mode=clone:stop={pad}'
    filters='[0:v]'+vf+'[v]';args=['-i',source]
    if audio:filters+=f';[0:a]atrim=start={a/24}:end={b/24},asetpts=PTS-STARTPTS,apad=whole_dur={nf/24},atrim=end={nf/24}[a]'
    args+=['-filter_complex',filters,'-map','[v]']
    args+=['-map','[a]','-c:a','aac','-b:a','192k'] if audio else ['-an']
    run([*args,'-frames:v',nf,'-r',24,'-c:v','libx264','-crf',12,'-pix_fmt','yuv420p',target])
    result=validate_video(target,nf,width,height)
    if digest(source)!=expected_sha:raise ValueError('Cambió la fuente durante la extracción.')
    return {**result,'path':str(target),'sha256':digest(target)}

def prefix(previous,window,prior,target,width,height,expected_sha):
    if (window.get('prefix_of')!=prior['index'] or window['index']!=prior['index']+1 or window['shot']!=prior['shot']
      or window['context_frames']!=17 or window['trim_start']!=17
      or window['render'][0]!=prior['render'][0]+window.get('prefix_at',-1)
      or window['own'][0]!=prior['own'][1] or window['render'][0]+17!=window['own'][0]):raise ValueError('El prefijo no pertenece al mismo plano y tramo anterior.')
    a=window['prefix_at']
    if a<0 or a+17>prior['render_frames'] or digest(previous)!=expected_sha:raise ValueError('Cambió el resultado que guía la continuidad.')
    run(['-i',previous,'-vf',f'trim=start_frame={a}:end_frame={a+17},setpts=PTS-STARTPTS','-an','-c:v','libx264','-crf',0,'-pix_fmt','yuv420p',target])
    validate_video(target,17,width,height)
    return {'path':str(target),'sha256':digest(target),'previous_sha256':expected_sha,'frame_start':a,'frames':17}

def join(record,results,normalized,target,width,height,source_sha):
    validate(record)
    if len(results)!=len(record['windows']) or digest(normalized)!=source_sha:raise ValueError('No coincide el manifiesto de ensamblaje.')
    folder=Path(target).parent/'join';folder.mkdir(exist_ok=False)
    for w,item in zip(record['windows'],results):
        if item.get('index')!=w['index'] or digest(item['path'])!=item['sha256']:raise ValueError('Falta o cambió una ventana aprobada.')
        part=folder/f"part-{w['index']:04}.mp4";start=w['trim_start']
        run(['-i',item['path'],'-vf',f"trim=start_frame={start}:end_frame={start+w['keep']},setpts=PTS-STARTPTS",'-an','-c:v','libx264','-crf',16,'-pix_fmt','yuv420p',part])
        validate_video(part,w['keep'],width,height)
    manifest=folder/'concat.txt';manifest.write_text(''.join(f"file 'part-{w['index']:04}.mp4'\n" for w in record['windows']),encoding='utf-8')
    run(['-f','concat','-safe',1,'-i',manifest,'-i',normalized,'-map','0:v:0','-map','1:a:0?','-c','copy','-movflags','+faststart',target])
    media=validate_video(target,record['frames'],width,height)
    if digest(normalized)!=source_sha:raise ValueError('Cambió el audio fuente durante el ensamblaje.')
    result={**media,'path':str(target),'sha256':digest(target),'source_sha256':source_sha,'windows':results,'transition':'none','overlap':'trim exactly once'}
    atomic(folder/'receipt.json',result);return result
