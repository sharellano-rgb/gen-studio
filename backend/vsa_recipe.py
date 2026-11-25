"""Optional, fixed-policy VSA attention for Character Swap1.5 Hands; native is unchanged."""
import hashlib,json,struct
from functools import lru_cache
from pathlib import Path
NODE='GenStudioRef2VAVSAGatePatch'
GATE='fasth3_vsa_gate.safetensors'
GATE_SHA='bf95408e335c4b0d6a1a44946a428a3e12849e6eab2de9566ecedae4a0043420'
GATE_SIZE=3853522616
ENGINE=Path.home()/'AppData/Roaming/simpligen/engine'
GATE_PATH=ENGINE/'models/loras'/GATE
NODE_PATH=ENGINE/'ComfyUI/custom_nodes/GenStudio-Ref2VA-VSA/nodes.py'
NODE_SHA='030f9bce605b35d9594db4eb3787a544fd8b623d3542cd84c57438fb7339a7cf'
POLICY='ref2va-vsa50-v1'
@lru_cache(maxsize=8)
def _verified(gate_path,gate_size,gate_time,node_path,node_size,node_time):
 gate=Path(gate_path);node=Path(node_path)
 if gate_size!=GATE_SIZE:raise ValueError('El tamaño de los pesos VSA no coincide.')
 def sha(p):
  h=hashlib.sha256()
  with p.open('rb') as f:
   for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
  return h.hexdigest()
 if sha(gate)!=GATE_SHA or sha(node)!=NODE_SHA:raise ValueError('Cambió el nodo o los pesos VSA verificados.')
 with gate.open('rb') as f:header=json.loads(f.read(struct.unpack('<Q',f.read(8))[0]))
 tensors=[v for k,v in header.items() if k!='__metadata__']
 if len(tensors)!=50 or any(v['shape']!=[7168,5376] for v in tensors):raise ValueError('Dimensiones VSA incompatibles.')
 return {'policy':POLICY,'gate_sha256':GATE_SHA,'node_sha256':NODE_SHA,'sparsity':.5,'steps':8}
def verify_assets():
 a,b=GATE_PATH.stat(),NODE_PATH.stat()
 return dict(_verified(str(GATE_PATH),a.st_size,a.st_mtime_ns,str(NODE_PATH),b.st_size,b.st_mtime_ns))
def apply(graph,parameters):
 if not parameters.get('direct_vsa'):return graph
 if parameters.get('direct_preset')!='hands' or parameters.get('direct_character_version')!='v1.5':raise ValueError('VSA requiere Realismo Manos v1.5.')
 if parameters.get('frames',0)>360 or graph['19']['inputs']['length']>362:
  raise ValueError('VSA admite hasta 15 segundos. Reduce la duración o desactiva Aceleración VSA para un vídeo más largo con cortes.')
 verify_assets()
 if graph['21']['inputs']['steps']!=8 or graph['21']['inputs']['scheduler']!='beta':raise ValueError('VSA conserva la receta de ocho pasos beta/Euler.')
 if graph['10']['inputs']['model']!=['8',0]:raise ValueError('La cadena de modelo VSA no coincide con la receta verificada.')
 graph['vsa-experimental']={'class_type':NODE,'inputs':{'model':['8',0],'gate_file':GATE,'sparsity':.5}}
 graph['10']['inputs']['model']=['vsa-experimental',0]
 return graph
