"""Conservative border guard, independent of image/runtime dependencies."""
def substantial_top_clip(foreground_pixels,face_width):
    if foreground_pixels<0 or face_width<=0:raise ValueError('Medida de borde inválida.')
    # A few antialiased cap-tip pixels can touch the first visible row; a wide
    # foreground run indicates a genuinely truncated head/cap cross-section.
    return foreground_pixels>face_width*.5
