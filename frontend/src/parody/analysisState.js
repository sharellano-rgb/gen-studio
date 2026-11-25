// A terminal observation is not ready until the model has actually unloaded.
export const analysisBusy = record => Boolean(record && (
 ['running','cancelling'].includes(record.status) ||
 (!record.vram_released && !['cleanup_failed','interrupted'].includes(record.status))
));
