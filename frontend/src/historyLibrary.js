import {PIKA_DETAILED,pikaRefMethod} from './pikaRefMethods.js';
import {VISUAL_ENGINES,visualEngineFor} from './secoursesVisualMethods.js';
import {takeLabel} from './sceneResults.js';

const activeStatuses = new Set(['queued', 'preparing', 'submitting', 'running', 'refining', 'downloading', 'cancelling']);
const preparationOperations = new Set(['motion_anchor', 'ninja_prepare', 'ninja_shots_plan', 'ninja_shots_prepare']);
const methodOrder = ['viggle_remake','tessera','pika_ref2va',PIKA_DETAILED,'pika_h3_flex','h3_i2va','h3_direct', 'h3_secourses',...VISUAL_ENGINES.map(method=>method.id),'h3_alisson', 'h3_ninja', 'bunny', 'tara', 'motion_layers', 'archive'];

const createdAt = job => {
  const timestamp = Date.parse(job?.created_at || '');
  return Number.isFinite(timestamp) ? timestamp : 0;
};
const newestFirst = jobs => [...jobs].sort((a, b) => createdAt(b) - createdAt(a));
const completedResult = job => job?.status === 'completed' && job.result_available === true;
const normalized = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase();
const basename = value => typeof value === 'string' ? value.trim().split(/[\\/]/).pop() : '';
const label = job => takeLabel({...job, parameters:job?.parameters || {}});

function internalTake(job) {
  const p = job?.parameters || {};
  return p.tessera_segment_index!=null || preparationOperations.has(p.operation) ||
    Number.isInteger(p.long_segment_index) || Number.isInteger(p.ninja_shot_index) ||
    Boolean(p.sequence_parent_id || p.ninja_sequence_owner || p.hybrid_parent_id);
}
function baseTake(group) {
  return group.root || [...(group.jobs || [])].sort((a, b) => createdAt(a) - createdAt(b)).find(job => !internalTake(job)) ||
    [...(group.jobs || [])].sort((a, b) => createdAt(a) - createdAt(b))[0];
}
function assetName(asset) {
  return basename(asset?.name) || basename(asset?.original_name);
}
function namedSource(job, assets, includeInternal = false) {
  const p = job?.parameters || {};
  const ids = [p.source_video_id, ...(!internalTake(job) || includeInternal ? [p.video_id] : [])].filter(Boolean);
  for (const id of ids) {
    const name = assetName(assets.find(asset => asset.id === id));
    if (name) return name;
  }
  return '';
}

/** A user name, the original video name, or a distinguishable take label. */
export function sceneName(group, assets = [], metadata = {}) {
  const stored = metadata[group.id]?.name;
  if (typeof stored === 'string' && stored.trim()) return stored.trim();
  const base = baseTake(group);
  const original = namedSource(base, assets, Boolean(group.root));
  if (original) return original;
  const shortId = String(group.id || base?.id || '').slice(0, 8);
  return [label(base), shortId].filter(Boolean).join(' · ');
}

/** Explicit selection only; null/invalid server selections override the old local choice. */
export function sceneChosenId(group, metadata = {}, legacySelected = {}) {
  const entry = metadata[group.id];
  const chosen = entry && Object.hasOwn(entry, 'selected_job_id') ? entry.selected_job_id : legacySelected[group.id];
  return (group.jobs || []).some(job => job.id === chosen && completedResult(job) && job.parameters?.tessera_segment_index==null) ? chosen : null;
}

export function sceneRepresentative(group, metadata = {}, legacySelected = {}) {
  const jobs = newestFirst(group.jobs || []);
  const chosen = sceneChosenId(group, metadata, legacySelected);
  return jobs.find(job => job.id === chosen) ||
    jobs.find(job => completedResult(job) && !internalTake(job)) || jobs[0] || null;
}

/** UI method families: retired motion methods remain identifiable as archive. */
export function sceneMethod(job) {
  const p = job?.parameters || {};
  if(p.engine==='viggle_remake')return 'viggle_remake';
  if(p.engine==='tessera')return 'tessera';
  if(pikaRefMethod(p))return pikaRefMethod(p);
  if(p.engine==='h3_i2va')return 'h3_i2va';
  if(p.engine==='pika_h3_flex')return 'pika_h3_flex';
  if (p.transfer_method === 'tara') return 'tara';
  if (p.operation === 'motion_layers') return 'motion_layers';
  if (p.operation === 'motion_bunny' || p.transfer_method === 'bunny') return 'bunny';
  if (p.engine === 'viggle_motion' || p.hybrid_parent_id || p.motion_scene_recipe ||
      ['motion_hybrid', 'motion_anchor', 'motion_scene', 'motion_transfer'].includes(p.operation) ||
      ['h3_multiview', 'h3_scene_direct'].includes(p.transfer_method)) return 'archive';
  if (p.operation === 'h3_face_refine' || p.engine === 'h3_ninja') return 'h3_ninja';
  if(p.engine==='h3_secourses')return visualEngineFor(p)||'h3_secourses';
  return ['h3_direct','h3_alisson'].includes(p.engine) ? p.engine : 'archive';
}

export function sceneMethods(group) {
  const present = new Set((group.jobs || []).map(sceneMethod));
  return methodOrder.filter(method => present.has(method));
}

/** Filter whole scene groups. Method and status must describe the same take. */
export function filterScenes(groups, {query = '', method = 'all', status = 'all'} = {}, assets = [], metadata = {}, legacySelected = {}) {
  const search = normalized(query).trim();
  return groups.filter(group => {
    const jobs = group.jobs || [];
    const chosen = sceneChosenId(group, metadata, legacySelected);
    const hasTake = jobs.some(job => {
      if (method !== 'all' && sceneMethod(job) !== method) return false;
      if (status === 'all') return true;
      if (status === 'chosen') return job.id === chosen;
      if (status === 'active') return activeStatuses.has(job.status);
      if (status === 'failed') return job.status === 'failed' || job.status === 'submission_unknown';
      return job.status === status;
    });
    if (!hasTake) return false;
    if (!search) return true;
    const text = [sceneName(group, assets, metadata), ...jobs.flatMap(job => [
      label(job), namedSource(job, assets), namedSource(job, assets, true),
    ])].join(' ');
    return normalized(text).includes(search);
  }).sort((a, b) => {
    const latest = group => Math.max(0, ...(group.jobs || []).map(createdAt));
    return latest(b) - latest(a);
  });
}

/** H3 output uses 24 fps; long_keep_frames excludes padding/context frames. */
export function sceneDuration(job) {
  const p = job?.parameters || {};
  const frames = Number(p.long_keep_frames ?? p.frames);
  return Number.isFinite(frames) && frames > 0 ? `${Number((frames / 24).toFixed(2))} s` : '';
}

export function sceneResolution(job) {
  const resolution = job?.scene_resolution || job?.parameters?.resolution || '';
  const match = String(resolution).match(/^(\d+)\s*[x×]\s*(\d+)$/i);
  return match && Number(match[1]) > 0 && Number(match[2]) > 0 ? `${Number(match[1])} × ${Number(match[2])}` : '';
}
