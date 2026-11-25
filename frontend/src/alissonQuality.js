// Match backend/alisson_swap.py. The model generates at this size; no export-only resize.
export const ALISSON_QUALITIES=[
  {id:'preview',label:'Rápida',megapixels:0.3,maxSide:1024,upscale:false},
  {id:'balanced',label:'Equilibrada',megapixels:0.5,maxSide:1536,upscale:true},
  {id:'high',label:'Alta',megapixels:1,maxSide:1536,upscale:true},
];
export const validAlissonQuality=value=>ALISSON_QUALITIES.some(p=>p.id===value);
export const effectiveAlissonQuality=value=>value===undefined?'preview':value;
function legacyRound(value){const floor=Math.floor(value);return value-floor===0.5?floor+floor%2:Math.round(value);}
export function alissonGeometry(video,quality='preview'){
  const width=Number(video?.display_width??video?.width),height=Number(video?.display_height??video?.height);
  const preset=ALISSON_QUALITIES.find(p=>p.id===quality);
  if(!preset||!Number.isFinite(width)||!Number.isFinite(height)||!(width>0&&height>0))return null;
  let scale=Math.min(Math.sqrt(preset.megapixels*1000000/(width*height)),preset.maxSide/Math.max(width,height));
  if(!preset.upscale)scale=Math.min(1,scale);
  const round=quality==='preview'?legacyRound:Math.round;
  return [Math.max(32,round(width*scale/32)*32),Math.max(32,round(height*scale/32)*32)];
}
export function alissonQualityLabel(preset,video){
  const geometry=alissonGeometry(video,preset.id);
  return `${preset.label} · ${geometry?geometry.join(' × ')+' px':'≈ '+String(preset.megapixels).replace('.',',')+' MP'}`;
}
