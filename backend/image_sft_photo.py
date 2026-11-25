"""Optional photographic finishes on the unchanged SFT text-to-image sampler."""
import json
from . import image_realism as realism
from .head import digest
from .job_cancel import checkpoint

VERSION='moss-image-sft-photographic-v1'
PHASE='MOSS-Image-SFT-Photo'
FIELDS=('image_version','prompt','negative_prompt','format','quality','resolution','steps','cfg','seed','lora_enabled','lora_strength')

def selected(p):return p.get('mode')!='edit' and p.get('edit_profile','standard')=='standard' and realism.selected(p)

def bind(p):return realism.bind({**p,'image_version':VERSION})

def binding(p):return {**{key:p[key] for key in FIELDS},**realism.binding(p)}

def prepare(service,job):
    p=job['parameters'];folder=service.root/'jobs'/job['id']
    current=service.store.job(job['id']);accepted=current.get('guided_phase')==PHASE and bool(current.get('prompt_id'))
    done=(folder/(PHASE+'-complete.json')).is_file()
    context=folder/'sft-photo-context.json';workflow=folder/(PHASE+'-workflow.api.json')
    if (accepted or done) and (not context.is_file() or not workflow.is_file()):
        raise ValueError('Falta el registro de la generación SFT aceptada; no se enviará de nuevo.')
    checkpoint(service,job['id'])
    if p.get('image_version')!=VERSION or not selected(p):raise ValueError('Cambió la receta SFT del trabajo guardado.')
    realism.verify_binding(p,not (accepted or done))
    if context.is_file():
        saved=json.loads(context.read_text(encoding='utf-8'))
        if saved['binding']!=binding(p) or not workflow.is_file() or saved['graph_sha256']!=digest(workflow):
            raise ValueError('Cambió el acabado o el gráfico SFT guardado.')
        return json.loads(workflow.read_text(encoding='utf-8'))
    from .image_generation import graph
    from .image_edit import save_json
    result=graph(p,job['id']);save_json(workflow,result)
    save_json(context,{'binding':binding(p),'graph_sha256':digest(workflow)})
    return result
