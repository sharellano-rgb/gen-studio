"""Versioned native audio/video recipe, independent of source-motion H3 plans."""
import copy
import re
import wave
from ..media import probe
from .repository import Problem
from .pacing import POLICY as PACING_POLICY
from .speech import fingerprint, voice_path
from .voice_store import VoiceStore
from .voice_runtime import local_input
from .script import line_binding
from .analysis_media import sha256
from .. import qwen_runtime as qr

VERSION='parody-pika-ref2va-1'
ENGINE='pika_ref2va_native'
FRAMES=124
SIZE=(1344,768)


def is_native(plan):return plan.get('version')==VERSION


def graph(image,voice,prompt,seed,prefix):
    # Pinned to the successful native-audio experiment on 2026-11-17.
    def node(kind,**inputs):return dict(class_type=kind,inputs=inputs)
    return dict(
        dit=node('UNETLoader',unet_name='minimax_h3_ref2va_pruned_int8_convrot.safetensors',weight_dtype='default'),
        lora=node('LoraLoaderModelOnly',model=['dit',0],lora_name='pika_h3_flex_ref2va_v1_bf16.safetensors',strength_model=1.0),
        encoder=node('CLIPLoader',clip_name='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',type='minimax',device='default'),
        video_vae=node('VAELoader',vae_name='minimax_h3_video_vae_int8_convrot.safetensors'),
        audio_vae=node('VAELoader',vae_name='minimax_h3_audio_vae_fp32.safetensors'),
        image=node('LoadImage',image=image),speech_audio=node('LoadAudio',audio=voice),
        conditioning=node('MiniMaxH3ReferenceToVideo',clip=['encoder',0],vae=['video_vae',0],audio_vae=['audio_vae',0],
            prompt=prompt,width=SIZE[0],height=SIZE[1],length=FRAMES,ref_image_size='match',
            **{'ref_images.ref_image_0':['image',0],'ref_audios.ref_audio_0':['speech_audio',0]}),
        guider=node('BasicGuider',model=['lora',0],conditioning=['conditioning',0]),
        noise=node('RandomNoise',noise_seed=seed),sampler=node('KSamplerSelect',sampler_name='lcm'),
        sigmas=node('ManualSigmas',sigmas='1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0'),
        sampling=node('SamplerCustomAdvanced',noise=['noise',0],guider=['guider',0],sampler=['sampler',0],sigmas=['sigmas',0],latent_image=['conditioning',1]),
        video_decode=node('VAEDecode',samples=['sampling',0],vae=['video_vae',0]),
        audio_decode=node('VAEDecodeAudio',samples=['sampling',0],vae=['audio_vae',0]),
        mux=node('CreateVideo',images=['video_decode',0],audio=['audio_decode',0],fps=24.0,bit_depth=8,color_space='sRGB',codec='none'),
        save=node('SaveVideo',video=['mux',0],filename_prefix=prefix,format='mp4',
            **{'format.codec':'h264','format.codec.encoding':'re-encode','format.codec.encoding.crf':18.0}))


def scene_reference(service,asset_id):
    asset=service.store.asset(str(asset_id))
    if not asset or asset.get('kind')!='image':raise Problem(404,'SCENE_REQUIRED','Selecciona una imagen de escena de este espacio.')
    root=(service.root/'inputs').resolve();path=(root/asset['filename']).resolve()
    if not root.is_relative_to(service.root.resolve()) or not path.is_relative_to(root) or not path.is_file():
        raise Problem(404,'SCENE_REQUIRED','La imagen de escena no está disponible en este espacio.')
    info=probe(path);w,h=info['width'],info['height']
    if info.get('codec') not in ('png','mjpeg','webp') or w<512 or h<288 or w*h>20_000_000 or abs(w/h-SIZE[0]/SIZE[1])>.025:
        raise Problem(422,'SCENE_FORMAT','Este piloto requiere un fotograma completo horizontal con proporción 7:4 (por ejemplo 1344×768). Prepara el encuadre antes de generar.')
    return dict(asset_id=str(asset_id),sha256=sha256(path),name=asset.get('name',path.name),role='prepared_scene',width=w,height=h)


def compile_native(service,project,body,base,accepted=None):
    if service.settings.get('parody_ref2va_enabled') is not True and accepted is None:
        raise Problem(503,'REF2VA_DISABLED','Ref2VA nativo todavía no está habilitado para generar.')
    if not body.scene_asset_id or not body.scene_reviewed or not body.voice_reviewed or body.performance!='new' or not body.voice_preview_id:
        raise Problem(422,'REF2VA_REVIEW','Revisa el fotograma completo con el personaje ya reemplazado y escucha la voz antes de preparar Ref2VA.')
    if len(body.reference_ids)!=1 or body.quality!='768p' or body.clothing!='reference':
        raise Problem(422,'REF2VA_PROFILE','Este piloto usa una ficha de identidad, la escena ya preparada y 768p. El vestuario debe estar resuelto en el fotograma.')
    scene=scene_reference(service,body.scene_asset_id)
    voice=VoiceStore(service.root).read(project['id'],str(body.voice_preview_id))
    binding,digest=line_binding(project,voice['line_id']);line=binding['line']
    if voice['status']!='review_pending' or not voice.get('artifact') or digest!=voice['input_hash']:
        raise Problem(409,'VOICE_STALE','Regenera la frase modificada antes de usar Ref2VA.')
    if line['kind']!='speech' or line['shot_id']!=base['shot']['id'] or line['character_id']!=base['character_id']:
        raise Problem(422,'VOICE_ASSIGNMENT','La voz debe pertenecer al personaje y plano seleccionados.')
    if line.get('voice',{}).get('mode')=='clone' and not line['voice'].get('authorized'):
        raise Problem(422,'VOICE_PERMISSION','La muestra clonada requiere autorización registrada.')
    with wave.open(str(voice_path(service,voice['id'],voice['artifact']['sha256'])),'rb') as wav:
        if (wav.getframerate(),wav.getnchannels(),wav.getsampwidth())!=(24000,1,2):raise ValueError('Se requiere la voz preparada PCM mono de 24 kHz.')
        samples=wav.getnframes()*2
    if not 96000<=samples<=FRAMES*2000:
        raise Problem(422,'REF2VA_VOICE_LENGTH','Usa una voz limpia de 2 a 5,17 segundos. No se recorta, acelera ni rellena para forzar la compatibilidad.')
    recipe=graph('scene.png','voice.wav','PARODY_REF2VA_PENDING',body.seed,'parody-ref2va')
    model=qr.checked_binding(accepted[qr.FIELD]) if accepted else qr.selected_binding(service)
    result=copy.deepcopy(base)
    result.pop('native_payload')
    result.update(version=VERSION,request=body.model_dump(mode='json'),scene=scene,
        source_timing=base['timing'],timing=dict(start_frame=0,end_frame_exclusive=FRAMES,frames=FRAMES,fps=24,start_ms=0,end_ms=FRAMES*1000/24),
        resolution='1344x768',audio='native_generated',performance='reference_acting',
        voice=dict(id=voice['id'],input_hash=digest,sha256=voice['artifact']['sha256'],samples_48k=samples,offset_samples=0,line=copy.deepcopy(line)),
        speech=dict(internal_frames=FRAMES,recipe=recipe,recipe_sha256=fingerprint(recipe),
                    director_policy=accepted.get('director_policy',VERSION) if accepted else 'parody-ref2va-language-2',**{qr.FIELD:model}),
        warnings=['Imagen de escena ya preparada + voz como referencia. No recibe el vídeo original como guía de movimiento.',
            'Genera vídeo y audio juntos: 124 fotogramas, 24 fps, 1344×768 (5,167 s). El intervalo fuente es contexto, no la duración final.',
            'No promete copiar exactamente timbre, pronunciación, duración de la voz ni píxeles del escenario.',
            'Revisa texto hablado, timbre, identidad, naturalidad y sincronización antes del montaje.',
            'El montaje conserva el audio generado. Cambiar diálogo o voz requiere una nueva generación, no sustituir el WAV.'])
    if accepted is None:
        result['speech']['pacing_policy'] = PACING_POLICY
    elif 'pacing_policy' in accepted:
        result['speech']['pacing_policy'] = accepted['pacing_policy']
    if accepted is None or accepted.get('look_policy')=='scene-continuity-1':
        result['speech']['look_policy']='scene-continuity-1'
    # Only bind observations for a catchphrase actually present in this line.
    # Accepted historical plans keep the evidence snapshot they started with.
    if accepted is not None:
        cues = accepted.get('delivery_evidence', [])
    else:
        speaker = next(c for c in project['cast']['characters'] if c['id'] == line['character_id'])
        words = ' ' + ' '.join(re.findall(r'\w+', line['text'].casefold())) + ' '
        cues = []
        for trait in speaker.get('traits', []):
            if trait['category'] != 'catchphrase' or not trait['reviewed'] or not trait.get('evidence'):
                continue
            quoted = re.findall(r'[«“"]([^»”"]+)[»”"]', trait['text'])
            phrases = [' '.join(re.findall(r'\w+', q.casefold())) for q in quoted]
            if any(q and ' ' + q + ' ' in words for q in phrases):
                cues.append(dict(text=trait['text'], evidence=trait['evidence']))
    if accepted is not None:
        if accepted.get('phrase_clip'):
            result['speech']['phrase_clip']=copy.deepcopy(accepted['phrase_clip'])
    else:
        from .script import phrase_line
        from .phrase_reference import bind
        rule=project.get('script',{}).get('required_phrase')
        if rule and rule.get('reference_id') and phrase_line(rule,line):
            result['speech']['phrase_clip']=bind(service,project,rule)
            cues=[cue for cue in cues if cue['evidence']['reference_id']==rule['reference_id']
                  and cue['evidence']['start_ms']<rule['end_ms']
                  and cue['evidence']['end_ms']>rule['start_ms']]
    if cues:
        result['speech']['delivery_evidence'] = copy.deepcopy(cues[:4])
    return result


def validate_montage(selection):
    if selection.audio!='source' or selection.voice_preview_ids or selection.lipsync!='none' or selection.timing is not None:
        raise Problem(422,'NATIVE_AUDIO_REQUIRED','Ref2VA conserva su audio nativo. Cambiar la voz, aplicar lip sync o desplazar audio requiere revisar otra generación.')
