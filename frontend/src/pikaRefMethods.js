export const PIKA_DETAILED='pika_ref2va_detailed';
export const PIKA_DETAIL_POLICY='pika-full-detail-1';
export const PIKA_DETAIL_LABEL='Pika · Análisis visual detallado';
export const isPikaRefMethod=value=>['pika_ref2va',PIKA_DETAILED].includes(value);
export function pikaRefMethod(value){const p=value?.parameters||value;return p?.engine==='pika_ref2va'?(p.pika_analysis_policy===PIKA_DETAIL_POLICY?PIKA_DETAILED:'pika_ref2va'):null;}
export const pikaRefLabel=value=>pikaRefMethod(value)===PIKA_DETAILED?PIKA_DETAIL_LABEL:'Pika H3 Flex';
export function pikaRefEndpoint(method='pika_ref2va'){if(!isPikaRefMethod(method))throw Error('Método Pika desconocido.');return method===PIKA_DETAILED?'pika-ref2va-detailed':'pika-ref2va';}
export const detailedOption={id:PIKA_DETAILED,label:PIKA_DETAIL_LABEL,detail:'Experimental · Encuadre completo + detalles · Qwen por instante'};
