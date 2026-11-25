export function imageHistoryPath(historyView,selected){
 return historyView?'/images/history':selected?'/jobs/'+encodeURIComponent(selected):null;
}
const terminal=new Set(['completed','failed','cancelled','submission_unknown']);
export function imageHistoryDelay(jobs,hidden=false){
 return !hidden&&jobs.some(job=>!terminal.has(job.status))?3000:30000;
}
