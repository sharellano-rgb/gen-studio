# GEN STUDIO

Interfaz local en español para organizar trabajos de imagen y vídeo con React, FastAPI y motores externos. Esta primera distribución para Windows incluye la interfaz, el servidor y dos plantillas API de ComfyUI para texto a vídeo y reemplazo de personaje.

## Inicio rápido en Windows

Necesitas Git, Python 3.14 y Node.js 22.12 o posterior con npm. En PowerShell:

```powershell
git clone https://github.com/sharellano-rgb/gen-studio.git
cd gen-studio
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
Copy-Item backend/config.example.json backend/config.json
npm.cmd ci --prefix frontend
npm.cmd run build --prefix frontend
.\start.cmd
```

Abre http://127.0.0.1:8790 en esa PC. El puerto debe estar libre. Puedes comprobar el arranque en http://127.0.0.1:8790/api/health. Los datos de trabajo se guardan en la carpeta local `data`.

## Generación y motores opcionales

El primer arranque usa el modo de auditoría. Puedes abrir la interfaz, consultar el estado y revisar las plantillas. Para renderizar necesitas una instalación compatible de ComfyUI con los nodos y pesos utilizados por el workflow. Los JSON en `workflows/` enumeran los tipos de nodo y modelos. Ajusta `comfy_url` en `backend/config.json` y consulta `/api/workflows/minimax-h3/preflight` para comprobar la instalación. Activa `generation_enabled` y reinicia el servidor cuando el motor esté preparado.

Para las funciones de análisis, `ollama_url` señala al servicio Ollama local. La aplicación verifica las versiones e identidades de los modelos que reconoce. Los métodos avanzados de Object Swap, Parody Maker, voz, Intel y upscaling dependen de herramientas, adaptadores y archivos adicionales. Esta distribución inicial se comprobó en modo local de auditoría. La instalación completa de esos métodos y el render en una PC nueva quedan pendientes de validación.

### Comprobar las dos plantillas

Con GEN STUDIO y ComfyUI en marcha, ejecuta en PowerShell:

```powershell
$studio = 'http://127.0.0.1:8790'
Invoke-RestMethod "$studio/api/workflows/minimax-h3/preflight"
Invoke-RestMethod "$studio/api/workflows/character-replacement/preflight"
```

Busca `ok: True` y una lista de errores vacía para la plantilla que usarás. Si el servicio no responde, comprueba que `comfy_url` coincide con el puerto de tu instalación de ComfyUI. Si falta un nodo o un archivo, el campo `errors` indica cuál. Estas consultas leen el catálogo del motor y la integridad del grafo sin iniciar un render.

| Archivo de modelo que espera ComfyUI | Plantilla |
|---|---|
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | Texto a vídeo |
| `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | Reemplazo |
| `h3_character_swap_pro4500_1000.safetensors` | Reemplazo |
| `sam3.1_multiplex_fp16.safetensors` | Reemplazo |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | Ambas |
| `minimax_h3_video_vae_fp16.safetensors` | Ambas |
| `minimax_h3_audio_vae_fp32.safetensors` | Ambas |

La plantilla de reemplazo también requiere nodos de lectura de vídeo, seguimiento SAM3 y conversión de máscaras. El vídeo y la imagen de referencia se comprueban al preparar cada trabajo.

## Licencias

El código propio de GEN STUDIO se distribuye con la [licencia MIT](LICENSE). Las partes derivadas de Viggle conservan su [licencia Apache 2.0](licenses/Viggle-Apache-2.0.txt) y su [aviso de atribución](licenses/Viggle-NOTICE.txt). Los archivos de tipografía y sus avisos se encuentran en [studio-fonts](frontend/public/studio-fonts). Cada dependencia, modelo y nodo externo conserva sus condiciones de uso y redistribución.
