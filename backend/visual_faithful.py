"""Opt-in spatial ROI adapter for the separate Faithful Object Swap method.

No model, sampler, seed, audio lock, prompt, timing or reference-image role is
changed here. Qwen's caller must describe the tracked working crop, not apply
full-frame coordinates a second time inside it. Existing graphs are untouched.
"""
from copy import deepcopy
from uuid import UUID

VERSION = 'visual-faithful-roi-1'
REQUIRED_NODES = (
    'MVEx_SubjectCropAdvanced', 'MVEx_SubjectUncrop', 'GetImageSize', 'ImageScale',
    'GenStudioFielPrepareMask', 'GenStudioFielROIGuide', 'GenStudioFielAudit',
    'GenStudioFielCheckUncrop', 'GenStudioFielGuideToROI',
)


def node(kind, **inputs):
    return {'class_type': kind, 'inputs': inputs}


def adapt_graph(graph, p, ident):
    if p.get('background') != 'video':
        raise ValueError('El método Fiel requiere Object Swap y el escenario original.')
    UUID(ident)
    count = p.get('render_frames', p.get('frames'))
    if type(count) is not int or count < 5 or (count - 5) % 17:
        raise ValueError('Fiel requiere un tramo completo en la cuadrícula de fotogramas de H3.')
    for key in ('width', 'height'):
        if type(p.get(key)) is not int or p[key] < 32 or p[key] % 32:
            raise ValueError('Resolución final Fiel no válida.')
    expected = {'components':'GetVideoComponents','guide':'SECoursesH3GenjutsuMotionGuide',
        'condition':'MiniMaxH3ReferenceToVideo','sample':'SamplerCustomAdvanced',
        'decode':'VAEDecode','output':'CreateVideo','save':'SaveVideo'}
    for key, kind in expected.items():
        if graph.get(key, {}).get('class_type') != kind:
            raise ValueError('Workflow Fiel incompatible: ' + key)
    if graph['condition']['inputs'].get('ref_videos.ref_video_0') != ['guide', 0]:
        raise ValueError('La guía visual original no ocupa el papel esperado.')
    for key in ('fiel-mask', 'crop', 'fiel-roi-guide', 'fiel-audit', 'fiel-check', 'fiel-uncrop', 'fiel-output'):
        if key in graph:
            raise ValueError('No se puede adaptar dos veces el workflow Fiel.')
    # Same requested working pixel budget as the selected SECourses quality.
    # Negative MVEx target permits downscaling too; positive only sets a floor.
    target_mp = p['width'] * p['height'] / (1024 * 1024)
    margin, feather = p.get('fiel_composite_margin', 2), p.get('fiel_composite_feather', 1)
    if type(margin) is not int or not 0 <= margin <= 8 or type(feather) is not int or not 0 <= feather <= 4:
        raise ValueError('Margen de composición Fiel no válido.')
    grow = p.get('mask_grow', 0)
    if type(grow) is not int or not 0 <= grow <= 64:
        raise ValueError('Ampliación de máscara Fiel no válida.')
    g = deepcopy(graph)
    g['fiel-mask'] = node('GenStudioFielPrepareMask', images=['components', 0], masks=['guide', 1],
        expected=count, grow_pixels=grow)
    g['crop'] = node('MVEx_SubjectCropAdvanced', original_images=['components', 0], masks=['fiel-mask', 0],
        mode='zoomed', mask_threshold=.1, divisible_by=32, upscale_megapixels=-target_mp, resize_method='auto',
        **{'mode.crop_scale':1.5, 'mode.min_padding_allowed':1., 'mode.min_padding_allowed_window':1,
           'mode.pad_deficit_tol':1, 'mode.pad_surplus_tol':4, 'mode.resize_cost':1.,
           'mode.movement_cost':1., 'mode.center_pull':.0001, 'mode.end_tightening':1.,
           'mode.end_tightening_window':0, 'mode.zoom_step':1., 'mode.max_zoom_rate':0.,
           'mode.aspect_ratio':0., 'mode.seamless_loop':False})
    g['fiel-crop-size'] = node('GetImageSize', image=['crop', 0])
    g['fiel-roi-guide'] = node('GenStudioFielROIGuide', images=['crop', 0], masks=['crop', 1],
        expected=count, composite_margin=margin, feather_inside=feather, guide_megapixels=p.get('guide_megapixels',1.))
    # Audit is a dependency of H3 conditioning: actual guide/mask/frame contracts
    # are persisted and validated before the expensive video generation begins.
    g['fiel-audit'] = node('GenStudioFielAudit', source_images=['components', 0], source_masks=['fiel-mask', 0],
        roi_images=['crop', 0], roi_masks=['crop', 1], guides=['fiel-roi-guide', 0],
        hard_masks=['fiel-roi-guide', 2], bboxes=['crop', 2], crop_debug=['crop', 3],
        expected=count, run_id=ident, source_sha256=p.get('source_sha256', ''),
        render_sha256=p.get('visual_faithful_render_sha256',''),
        composite_margin=margin, feather_inside=feather, mask_grow=grow)
    g['condition']['inputs'].update(width=['fiel-crop-size', 0], height=['fiel-crop-size', 1],
        **{'ref_videos.ref_video_0':['fiel-audit', 0]})
    g['fiel-check'] = node('GenStudioFielCheckUncrop', images=['decode', 0], source_images=['components', 0],
        roi_images=['crop', 0], masks=['fiel-roi-guide', 1], bboxes=['crop', 2], expected=count)
    g['fiel-uncrop'] = node('MVEx_SubjectUncrop', cropped_images=['fiel-check', 0],
        original_images=['components', 0], bboxes=['crop', 2], cropped_masks=['fiel-roi-guide', 1],
        feather=0, resize_method='auto')
    g['fiel-output'] = node('ImageScale', image=['fiel-uncrop', 0], upscale_method='lanczos',
        width=p['width'], height=p['height'], crop='disabled')
    g['output']['inputs']['images'] = ['fiel-output', 0]
    return g
