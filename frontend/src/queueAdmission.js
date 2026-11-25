export const generationBusy=(status,activeJobs)=>!status?.queue_submissions_enabled&&activeJobs.length>0;
