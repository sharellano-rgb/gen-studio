"""The original workflow's mask and inverted guide, as a private three-panel video."""
import asyncio
from uuid import UUID
from fastapi import HTTPException
from fastapi.responses import FileResponse
from .head import digest
from .guided_head import phase


def graph(parameters, video, reference, ident):
    from .mdmz_swap import graph as core_graph
    p={**parameters,'effective_prompt':parameters['prompt']}
    core=core_graph(p,video,reference,ident)
    keep={'video','components','sam3','target','detect','invert','composite','guide','size','gray'}
    result={key:value for key,value in core.items() if key in keep}
    def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    result['mask-image']=node('MaskToImage',mask=['detect',0])
    for key,source in [('source-preview',['components',0]),('mask-preview',['mask-image',0]),('guide-preview',['guide',0])]:
        result[key]=node('ImageScale',image=source,upscale_method='nearest-exact',width=320,height=0,crop='disabled')
    result['pair']=node('ImageConcanate',image1=['source-preview',0],image2=['mask-preview',0],direction='right',match_image_size=True)
    result['triptych']=node('ImageConcanate',image1=['pair',0],image2=['guide-preview',0],direction='right',match_image_size=True)
    result['diagnostics-video']=node('CreateVideo',images=['triptych',0],fps=24)
    result['diagnostics-save']=node('SaveVideo',video=['diagnostics-video',0],filename_prefix=f'gen_studio/{ident}/mdmz-diagnostics',format='mp4',codec='h264')
    return result


async def prepare(service,job,parameters,names):
    rendered=await phase(service,job,'mdmz-diagnostics',graph(parameters,names['video'],names['reference'],job['id']), 'diagnostics-save','.mp4')
    if rendered is None:return None
    record={'sha256':rendered['sha256'],'layout':['Fuente','Máscara SAM3','Guía invertida'],
            'guide_megapixels':parameters['guide_megapixels'],'preparation':parameters.get('preparation','legacy')}
    service.store.update(job['id'],mdmz_diagnostics=record)
    return record


def register(app):
    @app.get('/api/mdmz/jobs/{job_id}/diagnostics')
    async def video(job_id:UUID):
        service=app.state.jobs;job=service.store.job(str(job_id))
        if not job or job['parameters'].get('engine')!='h3_mdmz' or not job.get('mdmz_diagnostics'):
            raise HTTPException(404,'No hay una guía revisable de esta toma en tu espacio.')
        path=service.root/'jobs'/str(job_id)/'mdmz-diagnostics.mp4'
        if not path.is_file() or await asyncio.to_thread(digest,path)!=job['mdmz_diagnostics']['sha256']:
            raise HTTPException(409,'La vista previa cambió; se conserva el diagnóstico.')
        return FileResponse(path,media_type='video/mp4')
