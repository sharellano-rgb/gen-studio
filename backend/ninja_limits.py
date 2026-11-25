"""Duration limits for a single continuous AI Ninja generation at 24 fps."""
FPS=24
MAX_FRAMES=30*FPS

def render_frames(frames):
    if type(frames) is not int or not 5<=frames<=MAX_FRAMES:
        raise ValueError('AI Ninja admite de 5 fotogramas a 30 segundos a 24 fps.')
    return frames+(5-frames)%17

MAX_MODEL_FRAMES=render_frames(MAX_FRAMES)
