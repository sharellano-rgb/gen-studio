"""Head region geometry; no GPU or image dependencies."""
import math


def head_roi(boxes, width, height):
    if not boxes:
        raise ValueError('No se pudo seguir la cabeza del sujeto.')
    bounds = []
    for x0,y0,x1,y1 in boxes:
        fw,fh = x1-x0,y1-y0
        if fw <= 0 or fh <= 0:
            raise ValueError('La detección de cabeza tiene dimensiones inválidas.')
        bounds.append((x0-.6*fw,y0-.75*fh,x1+.6*fw,y1+1.25*fh))
    left,top = min(b[0] for b in bounds),min(b[1] for b in bounds)
    right,bottom = max(b[2] for b in bounds),max(b[3] for b in bounds)
    side = math.ceil(max(right-left,bottom-top,min(width,height)*.4)/32)*32
    if side > min(width,height) or side >= 448:
        raise ValueError('La cabeza recorre una zona demasiado amplia para este afinado. Usa una toma más corta.')
    x = max(0,min(width-side,round((left+right-side)/2)))
    y = max(0,min(height-side,round((top+bottom-side)/2)))
    # Clipping at the image edge is allowed; clipping an interior head is not.
    if x > max(0,left)+1 or y > max(0,top)+1 or x+side < min(width,right)-1 or y+side < min(height,bottom)-1:
        raise ValueError('No se puede incluir toda la cabeza en un recorte seguro.')
    return [x,y,side,side]


def aligned(base, mapped):
    fw,fh=base[2]-base[0],base[3]-base[1]
    distance=math.hypot((base[0]+base[2]-mapped[0]-mapped[2])/2,
                        (base[1]+base[3]-mapped[1]-mapped[3])/2)
    scale=(mapped[3]-mapped[1])/max(1,fh)
    return distance <= max(12,.35*math.hypot(fw,fh)) and .6 <= scale <= 1.6, distance, scale
