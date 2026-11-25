import {VISUAL_ENGINES} from './secoursesVisualMethods.js';
const KEY='gen-history-filters-v1';
const methods=['all','tessera','h3_direct','bunny','h3_secourses',...VISUAL_ENGINES.map(method=>method.id),'h3_alisson','h3_ninja','archive'],statuses=['all','completed','active','failed','cancelled','chosen'];
export function historyFilters(value){return {query:typeof value?.query==='string'?value.query.slice(0,200):'',method:methods.includes(value?.method)?value.method:'all',status:statuses.includes(value?.status)?value.status:'all'};}
export function readHistoryFilters(storage){try{const value=JSON.parse(storage.getItem(KEY));return historyFilters(value?.version===1?value:null);}catch{return historyFilters(null);}}
export function saveHistoryFilters(storage,value){try{storage.setItem(KEY,JSON.stringify({version:1,...historyFilters(value)}));return true;}catch{return false;}}
