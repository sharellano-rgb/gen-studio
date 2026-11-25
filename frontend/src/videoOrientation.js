export function videoOrientation(video){
  const width=Number(video?.display_width??video?.width),height=Number(video?.display_height??video?.height);
  if(!(width>0&&height>0))return null;
  return height>width?'portrait':width>height?'landscape':'square';
}
export function sourceResolution(video,resolution='1344x768',direct=true){
  const orientation=videoOrientation(video);if(!orientation)return resolution;
  const [w,h]=resolution.split('x').map(Number);
  if(orientation==='square')return direct?'512x512':resolution;
  if(['640x1152','1152x640'].includes(resolution))return orientation==='portrait'?'640x1152':'1152x640';
  if(orientation==='portrait')return h>w?resolution:direct&&Math.max(w,h)>=1000?'768x1344':'480x864';
  return w>h?resolution:direct&&Math.max(w,h)>=1000?'1344x768':'864x480';
}

export function directSourceResolution(video,resolution='1344x768'){
  const width=Number(video?.display_width??video?.width),height=Number(video?.display_height??video?.height);
  if(width>0&&height>0&&width/height>=Math.sqrt(4/7)&&width/height<=Math.sqrt(7/4)){
    return Math.max(...resolution.split('x').map(Number))>=1000?'1024x1024':'512x512';
  }
  return sourceResolution(video,resolution);
}
