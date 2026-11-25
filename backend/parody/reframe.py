"""Fixed, explicit source-pixel crops for original Parody shots only."""
import json,subprocess
from pydantic import BaseModel,ConfigDict,Field,model_validator

VERSION='parody-original-reframe-1'
class Reframe(BaseModel):
    model_config=ConfigDict(extra='forbid')
    x:int=Field(ge=0,le=8192,strict=True)
    y:int=Field(ge=0,le=8192,strict=True)
    width:int=Field(ge=16,le=8192,strict=True)
    height:int=Field(ge=16,le=8192,strict=True)
    source_width:int=Field(ge=16,le=8192,strict=True)
    source_height:int=Field(ge=16,le=8192,strict=True)

    @model_validator(mode='after')
    def bounds(self):
        if self.x+self.width>self.source_width or self.y+self.height>self.source_height:
            raise ValueError('El reencuadre sale del vídeo original.')
        return self

def validate_source(path,crop):
    result=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_streams','-of','json',str(path)],capture_output=True,text=True,timeout=30)
    if result.returncode:raise ValueError('No se pudo comprobar el encuadre original.')
    v=json.loads(result.stdout)['streams'][0]
    rotation=float(next((s['rotation'] for s in v.get('side_data_list',[]) if 'rotation' in s),v.get('tags',{}).get('rotate',0)))
    if rotation%360 or v.get('sample_aspect_ratio','1:1') not in ('1:1','N/A','0:1'):
        raise ValueError('Este reencuadre requiere una fuente sin rotación y con píxeles cuadrados; conserva el plano completo.')
    if (v['width'],v['height'])!=(crop.source_width,crop.source_height):
        raise ValueError('Las dimensiones del recorte no coinciden con el vídeo original.')

def crop_filter(value):
    c=Reframe.model_validate(value)
    return f'crop=w={c.width}:h={c.height}:x={c.x}:y={c.y}:exact=1,'
