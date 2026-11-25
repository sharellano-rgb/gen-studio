import React from 'react';
import {canReadSonnyGallery} from './sonnyGallery.js';

export default function SonnyGalleryLinks({workspaceId,imagesOnly=false,targetId='sonny',targetName='Sonny'}){
 if(!canReadSonnyGallery(workspaceId))return null;
 return <nav className="sonny-gallery-links" aria-label={'Historial de '+targetName}>{!imagesOnly&&<a className="sonny-gallery-link" href={'/?view='+targetId}>Vídeos de {targetName} ↗</a>}<a className="sonny-gallery-link" href={'/?view='+targetId+'&media=images'}>Imágenes de {targetName} ↗</a></nav>;
}
