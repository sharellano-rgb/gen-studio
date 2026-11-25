"""Explicit edit settings, reusable character cards and CPU-only shot review."""
import asyncio
import copy
import json
import shutil
import subprocess
from typing import Annotated, Literal
from uuid import UUID, uuid4
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_serializer, model_validator
from .guided_composition import local_path
from .head import digest
from .store import now
from .long_plan import SCENE_CUT_MAX_FRAMES, reviewed_segments
from .scene_detection import detect_cuts, parse_metrics

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180, pattern=r'^[^<>]+$')]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=700, pattern=r'^[^<>]*$')]
CharacterId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')]

class EditorModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class EditPolicy(EditorModel):
    version: Literal[1] = 1
    scope: Literal['character','face'] = 'character'
    clothing: Literal['reference','source'] = 'reference'
    background: Literal['source','replace'] = 'source'
    background_description: Description = ''

    @model_validator(mode='after')
    def consistent(self):
        if self.scope=='face' and self.clothing!='source':
            raise ValueError('El cambio sólo de rostro conserva la ropa del vídeo.')
        if self.background=='replace' and not self.background_description:
            raise ValueError('Describe el fondo que quieres usar.')
        if self.background=='source' and self.background_description:
            raise ValueError('Elige cambiar el fondo para añadir una descripción nueva.')
        return self

class CharacterCard(EditorModel):
    name: Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=80)]
    target: ShortText
    reference_ids: list[UUID] = Field(min_length=1,max_length=9)
    views: list[Annotated[str,StringConstraints(strip_whitespace=True,max_length=60)]] = Field(default_factory=list,max_length=9)

    @model_validator(mode='after')
    def ordered_views(self):
        if self.views and len(self.views)!=len(self.reference_ids):raise ValueError('Describe cada vista en el mismo orden que las imágenes.')
        if len(set(self.reference_ids))!=len(self.reference_ids):raise ValueError('No repitas una imagen en la misma ficha.')
        return self

class CharacterBinding(CharacterCard):
    target_selection_id: UUID | None = None

    @model_serializer(mode='wrap')
    def legacy_shape(self, handler):
        result=handler(self)
        if not self.target_selection_id:result.pop('target_selection_id',None)
        return result

    id: CharacterId

class ShotTargetSelection(EditorModel):
    character_id: CharacterId
    selection_id: UUID

class ShotEdit(EditorModel):
    target_selections: list[ShotTargetSelection] = Field(default_factory=list,max_length=9)
    disabled_character_ids: list[CharacterId] = Field(default_factory=list, max_length=9)
    index: int = Field(ge=0,strict=True)
    passthrough: bool = Field(default=False,strict=True)
    targets: list[ShortText] = Field(default_factory=list,max_length=9)
    background: Literal['inherit','source','replace'] = 'inherit'
    background_description: Description = ''

    @model_serializer(mode='wrap')
    def legacy_shape(self, handler):
        result=handler(self)
        if not self.target_selections:result.pop('target_selections',None)
        if not self.disabled_character_ids:result.pop('disabled_character_ids',None)
        return result

    @model_validator(mode='after')
    def consistent(self):
        if self.background=='replace' and not self.background_description:raise ValueError('Describe el fondo nuevo de este plano.')
        if self.background!='replace' and self.background_description:raise ValueError('La descripción requiere cambiar el fondo del plano.')
        if len(set(self.disabled_character_ids)) != len(self.disabled_character_ids):
            raise ValueError('Una ficha se desactivó más de una vez en este plano.')
        return self

class ShotPlanRequest(EditorModel):
    video_id: UUID
    frames: int = Field(ge=3,le=SCENE_CUT_MAX_FRAMES,strict=True)

def effective_policy(policy=None,shot_edit=None):
    if policy is None and (not shot_edit or shot_edit.get('background','inherit')=='inherit'):return None
    result=EditPolicy.model_validate(policy or {}).model_dump()
    if shot_edit and shot_edit.get('background','inherit')!='inherit':
        edit=ShotEdit.model_validate(shot_edit)
        result.update(background=edit.background,background_description=edit.background_description)
    return EditPolicy.model_validate(result).model_dump()

def compile_policy_lines(policy,source_background=None):
    p=EditPolicy.model_validate(policy)
    lines=['These explicit edit settings take precedence over free-text descriptions of appearance or environment.']
    if p.scope=='face':
        lines.append('Replace ONLY the assigned facial identity using the reference facial proportions and distinctive features. Keep source hair, body, clothing, accessories and hands unchanged. Follow the source head position, orientation, expression and timing exactly.')
    else:
        lines.append('Replace every instance of the assigned source subjects using their reference identities, facial features, hair and distinctive anatomy. Preserve source positions, motion, expression timing and contact with objects.')
    if p.clothing=='source':lines.append('Keep the clothing and accessories from the source video unchanged. Ignore reference clothing when rendering the outfit.')
    else:lines.append('Use the clothing and accessories from each assigned character reference consistently throughout the video.')
    if p.background=='replace':lines.append('Replace the environment background consistently with: '+p.background_description+'. Preserve source camera motion, foreground objects, placement and interactions. Do not restore the original background colors.')
    elif source_background:lines.append(f"Preserve this shot's source background: {source_background['setting']}. Keep its observed color, brightness and variations: {source_background['color']}. Reference backgrounds never change the environment.")
    else:lines.append('Preserve the source environment, its colors, brightness, shadows and background objects throughout. Reference backgrounds never change the environment.')
    return lines

def shot_reference_indices(payload,edit):
    disabled=set(edit.get('disabled_character_ids',[]))
    if not disabled:return list(range(len(payload['direct_reference_ids'])))
    characters=payload.get('direct_characters') or []
    if disabled.difference(c['id'] for c in characters):
        raise ValueError('Una ficha desactivada ya no pertenece a esta toma.')
    slots=[c['id'] for c in characters for _ in c['reference_ids']]
    active=[i for i,character_id in enumerate(slots) if character_id not in disabled]
    if not active and not edit.get('passthrough'):
        raise ValueError('Activa un personaje en este plano o marca «Conservar este plano sin cambios».')
    return active


def shot_payload(payload,segment,*,scoped_cast=False):
    result=copy.deepcopy(payload);edit=segment.get('shot_edit')
    if not edit:return result
    edit=ShotEdit.model_validate(edit).model_dump(mode='json')
    if scoped_cast or edit.get('disabled_character_ids'):
        indices=shot_reference_indices(payload,edit)
        targets=edit['targets'] or payload['direct_targets']
        disabled=set(edit.get('disabled_character_ids',[]))
        refs=[payload['direct_reference_ids'][i] for i in indices]
        characters=[]
        offset=0
        for card in payload.get('direct_characters',[]):
            if card['id'] not in disabled:
                current={**card,'target':targets[offset]}
                current.pop('target_selection_id',None)
                characters.append(current)
            offset+=len(card['reference_ids'])
        result.update(direct_targets=[targets[i] for i in indices],direct_reference_ids=refs,
                      direct_characters=characters,reference_id=refs[min(1,len(refs)-1)],
                      face_reference_id=refs[0],direct_shot_cast_scoped=True)
        if 'direct_reference_hashes' in payload:
            result['direct_reference_hashes']=[payload['direct_reference_hashes'][i] for i in indices]
        if 'direct_visual_target_proofs' in payload:
            selected={selection['selection_id'] for selection in edit.get('target_selections',[])}
            result['direct_visual_target_proofs']=[v for v in payload['direct_visual_target_proofs']
                if v['character_id'] not in disabled and v['id'] in selected]
    elif edit['targets']:
        result['direct_targets']=edit['targets']
        if result.get('direct_binding_mode')=='assigned' and result.get('direct_characters'):
            offset=0
            for card in result['direct_characters']:
                card['target']=edit['targets'][offset]
                card.pop('target_selection_id',None)
                offset+=len(card['reference_ids'])
        else:
            result.pop('direct_characters',None)
    policy=effective_policy(payload.get('direct_edit_policy'),edit)
    if policy is not None:result['direct_edit_policy']=policy
    if result.get('direct_shot_cast_scoped') or not payload.get('direct_dynamic_prompts'):
        from .character_bindings import compile_prompt
        direction='' if result.get('direct_shot_cast_scoped') else result.get('direct_direction','')
        result['prompt']=compile_prompt(result['direct_targets'],len(result['direct_reference_ids']),direction,edit_policy=policy,characters=result.get('direct_characters'))
        if not result.get('direct_characters'):result['direct_binding_mode']='manual'
    return result

def _save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.'+str(uuid4())+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)

def _source(service,video_id):
    asset=service.store.asset(str(video_id))
    if not asset or asset.get('kind')!='video':raise ValueError('Selecciona un vídeo disponible.')
    path=local_path(service.root,service.root/'inputs'/asset['filename'])
    if not path.is_file():raise ValueError('El vídeo fuente ya no está disponible.')
    return path

def _references(service,refs):
    for ref in refs:
        asset=service.store.asset(str(ref))
        if not asset or asset.get('kind')!='image' or not local_path(service.root,service.root/'inputs'/asset['filename']).is_file():raise ValueError('Una imagen de la ficha ya no está disponible.')

def _plan_file(service,plan_id):return service.root/'studio-plans'/str(UUID(str(plan_id)))/'plan.json'
def _load_plan(service,plan_id):
    path=_plan_file(service,plan_id)
    if not path.is_file():raise ValueError('La revisión de planos no está disponible. Analiza de nuevo el vídeo.')
    return json.loads(path.read_text(encoding='utf-8'))


def reviewed_plan(service,payload):
    plan_id=payload.get('direct_shot_plan_id');edits=payload.get('direct_shot_edits') or []
    if not plan_id:
        if edits:raise ValueError('Revisa los planos antes de asignar cambios por plano.')
        return None
    if payload.get('engine')!='h3_direct' or not payload.get('direct_scene_cuts') or payload.get('direct_start_seconds',0)!=0:raise ValueError('La revisión requiere H3 directo por cortes desde el inicio.')
    record=_load_plan(service,plan_id)
    if record.get('status')!='completed' or record.get('id')!=str(plan_id):raise ValueError('Espera a que termine la revisión de planos.')
    if record.get('video_id')!=str(payload['video_id']) or record.get('frames')!=payload['frames']:raise ValueError('Cambió el vídeo o la duración. Revisa de nuevo los planos.')
    source_sha=digest(_source(service,payload['video_id']))
    if source_sha!=record.get('source_sha256') or payload.get('direct_source_sha256',source_sha)!=source_sha:raise ValueError('Cambió el archivo fuente. Revisa de nuevo los planos.')
    valid=[ShotEdit.model_validate(e).model_dump(mode='json') for e in edits];indexes=[e['index'] for e in valid]
    if len(indexes)!=len(set(indexes)):raise ValueError('Un plano tiene más de una edición.')
    segments=reviewed_segments(record['frames'],record['cuts'],[e['index'] for e in valid if e['passthrough']])
    if len(record.get('segments',[]))!=len(segments) or any(any(s.get(k)!=e[k] for k in ('index','start','keep')) for s,e in zip(record.get('segments',[]),segments)):raise ValueError('Los límites guardados cambiaron. Revisa de nuevo el vídeo.')
    for edit in valid:
        if edit['index']>=len(segments):raise ValueError('La edición indica un plano inexistente.')
        if edit['targets'] and len(edit['targets'])!=len(payload.get('direct_reference_ids',[])):raise ValueError('Asigna todas las referencias en el mismo orden en cada plano.')
        if edit.get('disabled_character_ids'):shot_reference_indices(payload,edit)
    return record

def validate_editor_payload(service,payload):
    if payload.get('direct_edit_policy') is not None:EditPolicy.model_validate(payload['direct_edit_policy'])
    characters=[CharacterBinding.model_validate(c) for c in payload.get('direct_characters',[])]
    if len({c.id for c in characters})!=len(characters):raise ValueError('Cada personaje debe tener una ficha distinta.')
    editor=any(payload.get(k) for k in ('direct_edit_policy','direct_characters','direct_shot_plan_id','direct_shot_edits'))
    if editor and (payload.get('engine')!='h3_direct' or payload.get('direct_binding_mode')!='assigned'):raise ValueError('El editor requiere H3 directo con referencias asignadas.')
    if characters:
        refs=[str(r) for c in characters for r in c.reference_ids];targets=[c.target for c in characters for _ in c.reference_ids]
        if len(refs)>9 or refs!=list(map(str,payload.get('direct_reference_ids',[]))) or targets!=payload.get('direct_targets'):raise ValueError('Las fichas, referencias y asignaciones de la toma no coinciden.')
        _references(service,refs)
    reviewed=reviewed_plan(service,payload)
    from .visual_targets import validate_bindings
    validate_bindings(service,payload,reviewed)
    return reviewed

def apply_reviewed_plan(service,payload,plan):
    record=reviewed_plan(service,payload)
    if record is None:return plan
    if plan.get('source_sha256')!=record['source_sha256'] or plan.get('frames')!=record['frames']:raise ValueError('La preparación no coincide con el vídeo revisado.')
    edits={e.index:e.model_dump(mode='json') for e in map(ShotEdit.model_validate,payload.get('direct_shot_edits') or [])}
    expected=reviewed_segments(record['frames'],record['cuts'],[i for i,e in edits.items() if e['passthrough']])
    if plan.get('cuts')!=record['cuts'] or len(plan.get('segments',[]))!=len(expected):raise ValueError('Los cortes preparados no coinciden con la revisión.')
    result=copy.deepcopy(plan)
    for s,e in zip(result['segments'],expected):
        if any(s.get(k)!=e[k] for k in ('index','start','keep','render_frames')):raise ValueError('Un plano preparado no coincide con la revisión.')
        s['passthrough']=e['passthrough'];s['shot_edit']=edits.get(s['index'],ShotEdit(index=s['index']).model_dump())
    result['reviewed_plan_id']=record['id'];return result

def _card_table(store):
    with store.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS studio_characters (id TEXT PRIMARY KEY, card TEXT NOT NULL)')

def _register_cards(app):
    @app.get('/api/characters')
    async def characters():
        service=app.state.jobs;_card_table(service.store)
        with service.store.connect() as db:return {'characters':[json.loads(row[0]) for row in db.execute('SELECT card FROM studio_characters ORDER BY rowid DESC')]}
    async def save_card(body,card_id=None):
        service=app.state.jobs
        async with service.lock:
            try:
                _references(service,body.reference_ids);_card_table(service.store)
                with service.store.connect() as db:
                    previous=db.execute('SELECT card FROM studio_characters WHERE id=?',(str(card_id),)).fetchone() if card_id else None
                    if card_id and not previous:raise HTTPException(404,'Ficha no encontrada.')
                    card=body.model_dump(mode='json')|{'id':str(card_id or uuid4()),'updated_at':now(),'created_at':json.loads(previous[0])['created_at'] if previous else now()}
                    db.execute('INSERT INTO studio_characters VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET card=excluded.card',(card['id'],json.dumps(card,ensure_ascii=False)));return card
            except ValueError as error:raise HTTPException(409,str(error)) from error
    @app.post('/api/characters',status_code=201)
    async def create_character(body:CharacterCard):return await save_card(body)
    @app.put('/api/characters/{character_id}')
    async def update_character(character_id:UUID,body:CharacterCard):return await save_card(body,character_id)
    @app.delete('/api/characters/{character_id}')
    async def delete_character(character_id:UUID):
        service=app.state.jobs
        async with service.lock:
            _card_table(service.store)
            with service.store.connect() as db:
                if db.execute('DELETE FROM studio_characters WHERE id=?',(str(character_id),)).rowcount!=1:raise HTTPException(404,'Ficha no encontrada.')
        return {'deleted':str(character_id)}

def _run(args,timeout=180):
    result=subprocess.run(args,capture_output=True,timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('No se pudo preparar la vista de planos: '+result.stderr.decode('utf-8',errors='replace')[-300:])
    return result.stdout+result.stderr

def _analyze_plan(source,folder,record):
    ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:raise ValueError('No está disponible el lector de vídeo para revisar planos.')
    if digest(source)!=record['source_sha256']:raise ValueError('El vídeo cambió antes del análisis.')
    normalized=folder/'preview.mp4';count=record['frames']
    duration=float(_run([ffprobe,'-v','error','-show_entries','format=duration','-of','csv=p=0',str(source)]))
    if count>round(duration*24)+1:raise ValueError('La duración elegida supera la del archivo fuente.')
    # Match the render timeline when the last video frame ends before the audio.
    _run([ffmpeg,'-v','error','-n','-i',str(source),'-an','-vf',f'fps=24:start_time=0,scale=960:960:force_original_aspect_ratio=decrease:force_divisible_by=2,tpad=stop_mode=clone:stop={count},trim=end_frame={count},setpts=PTS-STARTPTS','-frames:v',str(count),'-c:v','libx264','-crf','18','-pix_fmt','yuv420p',str(normalized)])
    probe=json.loads(_run([ffprobe,'-v','error','-select_streams','v:0','-count_frames','-show_entries','stream=nb_read_frames','-of','json',str(normalized)]))
    if int(probe['streams'][0]['nb_read_frames'])!=count:raise ValueError('La duración elegida supera los fotogramas disponibles.')
    metrics=_run([ffmpeg,'-hide_banner','-i',str(normalized),'-vf','scdet=t=0,metadata=print','-an','-f','null','-'])
    detection=detect_cuts(parse_metrics(metrics),count);bounds=[0,*detection['cuts'],count];segments=[]
    for index,(start,end) in enumerate(zip(bounds,bounds[1:])):
        thumbnail=folder/f'shot-{index}.jpg'
        _run([ffmpeg,'-v','error','-n','-i',str(normalized),'-vf',f'select=eq(n\\,{start}),scale=480:-2','-frames:v','1','-q:v','3',str(thumbnail)],timeout=60)
        if not thumbnail.is_file():raise ValueError('No se pudo leer la miniatura de un plano.')
        segments.append({'index':index,'start':start,'keep':end-start,'too_long':end-start>345,'thumbnail_url':f"/api/direct/shot-plan/{record['id']}/thumbnail/{index}",'thumbnail_sha256':digest(thumbnail)})
    if digest(source)!=record['source_sha256']:raise ValueError('El vídeo cambió durante la revisión.')
    return {**record,'status':'completed','error':None,'cuts':detection['cuts'],'segments':segments,'detector':detection.get('detector','scene-detection'),'updated_at':now()}

def _register_plans(app):
    @app.post('/api/direct/shot-plan',status_code=202)
    async def create_plan(body:ShotPlanRequest):
        service=app.state.jobs
        async with service.lock:
            tasks=getattr(service,'studio_plan_tasks',{});service.studio_plan_tasks=tasks
            if any(not task.done() for task in tasks.values()):raise HTTPException(409,'Espera a que termine la revisión de planos en curso.')
            try:
                source=_source(service,body.video_id)
                record={'id':str(uuid4()),'status':'queued','video_id':str(body.video_id),'frames':body.frames,'source_sha256':digest(source),'segments':[],'cuts':[],'error':None,'created_at':now()}
                path=_plan_file(service,record['id']);_save(path,record)
            except ValueError as error:raise HTTPException(409,str(error)) from error
            async def work():
                running={**record,'status':'running'};_save(path,running)
                try:result=await asyncio.to_thread(_analyze_plan,source,path.parent,running)
                except Exception as error:result={**running,'status':'failed','error':str(error),'updated_at':now()}
                _save(path,result)
            tasks[record['id']]=asyncio.create_task(work());return record
    @app.get('/api/direct/shot-plan/{plan_id}')
    async def get_plan(plan_id:UUID):
        service=app.state.jobs
        try:
            record=_load_plan(service,plan_id)
            if record['status'] in ('queued','running') and str(plan_id) not in getattr(service,'studio_plan_tasks',{}):return {**record,'status':'failed','error':'La revisión se interrumpió al reiniciar. Revisa de nuevo los planos.'}
            return record
        except ValueError as error:raise HTTPException(404,str(error)) from error
    @app.get('/api/direct/shot-plan/{plan_id}/thumbnail/{index}')
    async def thumbnail(plan_id:UUID,index:int):
        service=app.state.jobs
        try:
            record=_load_plan(service,plan_id)
            if record['status']!='completed' or index<0 or index>=len(record['segments']):raise ValueError('Miniatura no disponible.')
            path=_plan_file(service,plan_id).parent/f'shot-{index}.jpg'
            if not path.is_file() or digest(path)!=record['segments'][index]['thumbnail_sha256']:raise ValueError('La miniatura cambió. Revisa de nuevo los planos.')
            return FileResponse(path,media_type='image/jpeg')
        except ValueError as error:raise HTTPException(404,str(error)) from error

def register(app,settings):
    _register_cards(app)
    _register_plans(app)
    from .visual_targets import register as register_visual_targets
    register_visual_targets(app,settings)
