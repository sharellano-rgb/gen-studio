// The UI option represents both analysis policies. Historical requests without
// these flags must keep exactly their previous payload shape.
export function detailedVisualAnalysisAvailable(form) {
  return form.auto_describe === true && !/subject[ _]definitions[\s*_`]*:/i.test(form.prompt || '');
}

export function detailedVisualAnalysisEnabled(form) {
  return detailedVisualAnalysisAvailable(form) && form.qwen_adaptive_zoom === true && form.qwen_motion_grounding === true;
}

export function withDetailedVisualAnalysis(form, enabled) {
  const next = {...form};
  delete next.qwen_adaptive_zoom;
  delete next.qwen_motion_grounding;
  if (enabled === true && detailedVisualAnalysisAvailable(form)) {
    next.qwen_adaptive_zoom = true;
    next.qwen_motion_grounding = true;
  }
  return next;
}

export function secoursesSubmission(form, pending, makeRequestKey) {
  // An uncertain submission is retried verbatim, including historical options.
  if (pending) return pending;
  return {...withDetailedVisualAnalysis(form, detailedVisualAnalysisEnabled(form)), request_key: makeRequestKey()};
}
