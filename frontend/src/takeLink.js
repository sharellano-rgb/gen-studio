export function takeFromSearch(search){
  const id=new URLSearchParams(search).get('take');
  return id&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)?id.toLowerCase():'';
}
