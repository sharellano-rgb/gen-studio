export const NEW_CHARACTER_VERSION='v1.5';
export function characterVersion(parameters){return parameters?.direct_character_version??'v1';}
export function initialCharacterVersion(prior){return prior?characterVersion(prior):NEW_CHARACTER_VERSION;}
export function characterVersionLabel(parameters){return 'Character Swap '+characterVersion(parameters)+(parameters?.direct_vsa?' · VSA':'');}
export function characterStrength(preset,version,current=1){return preset==='hands'?(version==='v1.5'?1:1.35):preset==='realism'?1:current;}
export function updateCharacterConfiguration(settings,name,value){
  const next={...settings,[name]:value};
  if(name==='direct_character_version'||name==='direct_preset')
    next.lora_strength=characterStrength(next.direct_preset,characterVersion(next),next.lora_strength);
  if(name==='direct_preset'&&value==='fasth3_omni')Object.assign(next,{frames:Math.max(48,Math.min(next.frames,1440)),direct_continuity:false,direct_crop_face:false,lora_strength:0});
  if(name==='direct_preset'&&settings.direct_preset==='fasth3_omni'&&value!=='fasth3_omni')next.lora_strength=characterStrength(value,characterVersion(next),1);
  if(next.direct_vsa&&(next.direct_preset!=='hands'||characterVersion(next)!=='v1.5'))next.direct_vsa=false;
  return next;
}
export function matchesCharacterPreflight(check,version){return !!check&&check.character_version===version;}
export function createCharacterPreflight(fetcher,onResult,onError){
  let sequence=0;
  return {
    async run(version){
      const request=++sequence;onResult(null);
      try{
        const result=await fetcher('/workflows/h3-direct/preflight?character_version='+encodeURIComponent(version));
        if(request!==sequence)return;
        if(!matchesCharacterPreflight(result,version))throw Error('La comprobaci\u00f3n corresponde a otra versi\u00f3n. Vuelve a comprobar.');
        onResult(result);
      }catch(error){if(request===sequence){onResult(null);onError(error.message);}}
    },
    invalidate(){sequence++;}
  };
}

export const characterVsaReady=(settings,check)=>!settings.direct_vsa||(settings.direct_preset==='hands'&&settings.direct_character_version==='v1.5'&&matchesCharacterPreflight(check,'v1.5')&&check.vsa?.ok===true);
