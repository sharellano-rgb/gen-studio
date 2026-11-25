export const NOTE_CATEGORIES=[
  ['face','Identidad y rostro'],['clothing','Ropa y accesorios'],['hands','Manos y contactos'],
  ['background','Fondo y objetos'],['motion','Movimiento'],['join','Unión de tramos'],['other','Otro detalle'],
];
export const categoryLabel=category=>NOTE_CATEGORIES.find(([value])=>value===category)?.[1]||'Otro detalle';
export function segmentAtFrame(segments,frame){
  if(!Number.isInteger(frame)||frame<0)return null;
  return (segments||[]).find(s=>frame>=s.start&&frame<s.start+s.keep)||null;
}
export function noteBody(review,note){
  if(!review?.result_sha256||!Number.isInteger(review.revision))throw Error('Actualiza las marcas antes de guardar.');
  return {expected_revision:review.revision,result_sha256:review.result_sha256,frame:note.frame,
    category:note.category,subject:note.subject||'',comment:note.comment||'',status:note.status||'open'};
}
export const openNotesForSegment=(notes,segment)=>(notes||[]).filter(n=>n.status==='open'&&segmentAtFrame([segment],n.frame));
