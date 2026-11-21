'use strict';
const register={loading:false};const statusMessage=document.getElementById('status-message');
async function adjudicate(input){if(register.loading)return;register.loading=true;statusMessage.textContent='报告裁决中…';try{const response=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)});const data=await response.json();if(!response.ok)throw Error(data.message);location.reload();}catch(error){statusMessage.textContent=error.message+'，请修正报告';}finally{register.loading=false;}}
document.getElementById('rerun').addEventListener('click',()=>adjudicate({sample:'dirty.csv'}));document.getElementById('print').addEventListener('click',()=>print());
const source=document.getElementById('lab-source');if(source)source.addEventListener('submit',event=>{event.preventDefault();const fields=new FormData(source);const csv=fields.get('csv');adjudicate(csv?{csv}:{sample:fields.get('sample')});});
const sign=document.getElementById('sign');if(sign)sign.addEventListener('click',async()=>{try{const response=await fetch('/api/runs/'+sign.dataset.id+'/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:Number(sign.dataset.version),decision:'通过'})});const data=await response.json();if(!response.ok)throw Error(data.message);sign.dataset.version=data.review_version;statusMessage.textContent='报告已复核 / '+data.review_version;}catch(error){statusMessage.textContent=error.message;}});

async function postAmendment(payload){
  const response=await fetch('/api/m8_amend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await response.json();if(!response.ok)throw Error(data.message);
  statusMessage.textContent=data.note;location.reload();
}
const amendForm=document.getElementById('amend-form');
if(amendForm)amendForm.addEventListener('submit',async event=>{
  event.preventDefault();
  const f=new FormData(amendForm);let fields;
  try{fields=JSON.parse(f.get('fields_json'));}catch(error){statusMessage.textContent='更正字段不是合法JSON';return;}
  try{await postAmendment({run_id:f.get('run_id'),amendment_id:f.get('amendment_id'),target_id:f.get('target_id'),fields});}
  catch(error){statusMessage.textContent=error.message;}
});
document.querySelectorAll('.amend-demo-btn').forEach(btn=>btn.addEventListener('click',()=>{
  try{postAmendment({run_id:btn.dataset.run,amendment_id:btn.dataset.amend,target_id:btn.dataset.target,fields:JSON.parse(btn.dataset.fields)});}
  catch(error){statusMessage.textContent=error.message;}
}));
const recheckBtn=document.getElementById('amend-recheck');
if(recheckBtn)recheckBtn.addEventListener('click',async()=>{
  try{
    const response=await fetch('/api/m8_amend/recheck',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:recheckBtn.dataset.run})});
    const data=await response.json();if(!response.ok)throw Error(data.message);
    const box=document.getElementById('recheck-result');
    box.innerHTML=data.checks.map(c=>'<div>'+c.amendment_id+'：'+(c.applied?'采纳':'驳回')+(c.applied&&!c.match?' 指纹不符':c.applied?' 指纹一致':' 留痕一致')+'</div>').join('')
      +'<strong class="'+(data.all_match?'pass':'reject')+'">'+(data.all_match?'重新核对一致，台账可复算':'核对发现偏差，需人工复核')+'</strong>';
    statusMessage.textContent='重新核对完成：'+(data.all_match?'全部一致':'存在偏差');
  }catch(error){statusMessage.textContent=error.message;}
});
