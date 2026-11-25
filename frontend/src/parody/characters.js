export const referenceRoles={front:'Frente',profile:'Perfil',body:'Cuerpo completo',wardrobe:'Vestuario',accessory:'Accesorio',acting:'Clip de actuación',voice:'Referencia de voz'};
export const traitCategories={gesture:'Gesto facial',body:'Movimiento corporal',expression:'Expresión',rhythm:'Ritmo al hablar',pause:'Pausa',reaction:'Reacción',laugh:'Risa',catchphrase:'Muletilla',emotion:'Intención emocional'};
export const emptyCast=()=>({version:1,characters:[],appearances:[]});
export function newCharacter(id=crypto.randomUUID()){return {id,name:'Nuevo personaje',appearance:'',wardrobe:'',accessories:'',style:'realistic',references:[],traits:[]};}
export function allowedReference(asset,role){return role==='acting'?asset.kind==='video':role==='voice'?['video','audio'].includes(asset.kind):asset.kind==='image';}
export function removeCharacter(cast,id){return {...cast,characters:cast.characters.filter(c=>c.id!==id),appearances:cast.appearances.filter(a=>a.character_id!==id)};}
export function removeReference(character,id){return {...character,...(character.voice_profile?.reference_id===id?{voice_profile:null}:{}),references:character.references.filter(r=>r.id!==id),traits:character.traits.map(t=>t.evidence?.reference_id===id?{...t,evidence:null,kind:'creative',reviewed:false}:t)};}
export function proposeTraits({cast,record,window,actorIndex,characterId,newId=()=>crypto.randomUUID()}){
 const character=cast.characters.find(c=>c.id===characterId);
 if(!character)throw Error('Selecciona una ficha de personaje.');
 const ref=character.references.find(r=>r.role==='acting'&&r.asset_id===record.source.asset_id&&r.sha256===record.source.sha256);
 if(!ref)throw Error('Para llevar estos rasgos a la ficha, añade este mismo vídeo como clip de actuación del personaje y guarda primero. La escena fuente no define por sí sola la actuación del reemplazo.');
 const actor=window.observation.actors[actorIndex];if(!actor)throw Error('Observación no disponible.');
 const observation={project_id:record.project_id,analysis_id:record.id,window_index:window.index,actor_index:actorIndex};
 if(character.traits.some(t=>{const origin=t.evidence?.observation;return origin&&Object.entries(observation).every(([key,value])=>origin[key]===value);}))throw Error('Las observaciones de este sujeto e intervalo ya están en la ficha.');
 const proposals=[['body',actor.action],['expression',actor.expression]].filter(([,text])=>text?.trim()).map(([category,text])=>({id:newId(),category,text,kind:'observation',evidence:{reference_id:ref.id,start_ms:window.start_ms,end_ms:window.end_ms,observation},reviewed:false}));
 if(!proposals.length)throw Error('No hay gestos descritos para este sujeto.');
 if(character.traits.length+proposals.length>64)throw Error('La ficha admite hasta 64 rasgos.');
 return {...cast,characters:cast.characters.map(c=>c.id===characterId?{...c,traits:[...c.traits,...proposals]}:c)};
}
export function assignObservation({cast,shots,source,record,window,actorIndex,characterId,newId=()=>crypto.randomUUID()}){
 if(!source||source.sha256!==record.source.sha256)throw Error('El análisis pertenece a otra fuente. Analiza o selecciona la fuente correcta.');
 if(!cast.characters.some(c=>c.id===characterId))throw Error('Crea y elige primero un personaje de reemplazo.');
 const actor=window.observation.actors[actorIndex];if(!actor)throw Error('Observación no disponible.');
 let shot=shots.find(s=>s.start_ms===window.start_ms&&s.end_ms===window.end_ms);
 const nextShots=[...shots];if(!shot){shot={id:newId(),label:'Plano '+(window.shot+1)+' · '+window.start_ms/1000+' s',start_ms:window.start_ms,end_ms:window.end_ms};nextShots.push(shot);}
 if(cast.appearances.some(a=>a.shot_id===shot.id&&(a.character_id===characterId||a.target_label.toLowerCase()===actor.label.toLowerCase())))throw Error('Este personaje o sujeto ya tiene una asignación en el plano. Revísala en Personajes.');
 const appearance={id:newId(),shot_id:shot.id,character_id:characterId,target_label:actor.label,source_sha256:source.sha256,observation:{analysis_id:record.id,window_index:window.index,actor_index:actorIndex},reviewed:false};
 return {shots:nextShots,cast:{...cast,appearances:[...cast.appearances,appearance]}};
}
