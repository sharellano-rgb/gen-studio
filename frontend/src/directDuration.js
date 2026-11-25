export const DIRECT_CHUNK_THRESHOLD=240;
export const DIRECT_CHUNK_FRAMES=168;
export const directMaxFrames=settings=>settings?.direct_preset==='hands'&&!settings.direct_scene_cuts?2880:1440;
export function limitDirectDuration(settings){return {...settings,frames:Math.min(settings.frames,directMaxFrames(settings))};}
export const continuousChunks=settings=>!settings?.direct_scene_cuts&&Number.isInteger(settings?.frames)&&settings.frames>DIRECT_CHUNK_THRESHOLD;
export const continuityAvailable=settings=>continuousChunks(settings)&&settings.direct_preset==='hands'&&settings.direct_character_version==='v1.5';
export const continuityEnabled=settings=>Boolean(settings?.direct_continuity)&&continuityAvailable(settings);
export function directDurationHint(settings={}){const seconds=settings.direct_preset==='fasth3_omni'?5:DIRECT_CHUNK_FRAMES/24;return `Hasta ${directMaxFrames(settings)/24} s en tramos de máximo ${seconds} s. El último fotograma enlaza los tramos del mismo plano; los cambios de plano reinician la continuidad. Se conservan la duración y el audio originales.`;}
