"""Independent local geometry gate; model coordinates are checked numerically."""
import json,subprocess
from pathlib import Path
from .state import digest,atomic
VERSION='dwpose-source-roi-1'

def inventory(settings):
 python=Path(settings['face_python']).resolve();aux=python.parent.parent/'ComfyUI/custom_nodes/comfyui_controlnet_aux'
 paths={'det':aux/'ckpts/yolox_l_dynamic_batch_opset_17_sim.onnx','pose':aux/'ckpts/dw-ll_ucoco_384_bs5.torchscript.pt'}
 if not python.is_file() or any(not f.is_file() for f in paths.values()):raise ValueError('Falta el entorno local de comprobación de poses; no se descargarán modelos.')
 return python,aux,{k:{'path':str(v),'sha256':digest(v)} for k,v in paths.items()}

def accepted(report,indices):
 if report.get('version')!=VERSION or [f['frame'] for f in report['frames']]!=indices:raise ValueError('La comprobación de pose no corresponde a los fotogramas solicitados.')
 return all(f['padding_changed_fraction']<=.01 and 0<f['detected_people']<=12 and len(f['people'])==f['detected_people'] and all(v['valid_joints']>=4 and v['median_error'] is not None and v['median_error']<=.06 and v['max_error']<=.16 for v in f['people']) for f in report['frames'])

def check(service,job,source,result,indices,name,still):
 python,aux,models=inventory(service.settings);folder=service.root/'jobs'/job['id'];output=folder/(name+'-geometry.json')
 key={'source_sha256':digest(source),'result_sha256':digest(result),'frames':indices,'still':still,'models':models}
 if output.exists():
  report=json.loads(output.read_text(encoding='utf-8'))
  if report['binding']!=key:raise ValueError('Cambió la evidencia geométrica guardada.')
 else:
  manifest=folder/(name+'-geometry-input.json');atomic(manifest,{**key,'aux':str(aux),'source':str(source),'result':str(result),'output':str(output)})
  r=subprocess.run([str(python),'-B',str(Path(__file__).with_name('pose_worker.py')),str(manifest)],capture_output=True,timeout=180,creationflags=subprocess.CREATE_NO_WINDOW)
  if r.returncode:raise ValueError('No se pudo verificar la pose local: '+r.stderr.decode(errors='replace')[-1200:])
  report=json.loads(output.read_text(encoding='utf-8'))
  if report['binding']!=key:raise ValueError('El comprobador de poses devolvió otra entrada.')
 accepted(report,indices);return report

def correction(report):
 rows=[]
 for f in report['frames']:
  if f['padding_changed_fraction']>.01:rows.append('Restore the unchanged source canvas border; do not expand or reframe the scene.')
  for i,v in enumerate(f['people'],1):
   if v['valid_joints']<4 or v['median_error'] is None:rows.append(f'Pose of source person at box {v["source_box"]} is not verifiable; preserve the original visible body exactly.')
   elif v['median_error']>.06 or v['max_error']>.16:rows.append(f'Source person ROI {v["source_box"]}: excessive pose/position shift (median {v["median_error"]:.3f}, max {v["max_error"]:.3f} of canvas). Restore source shoulders, elbows, wrists and visible hips at their original positions. Keep the original close crop; never reveal feet or full body if cropped in the source.')
 return ' '.join(rows)[:2400]
