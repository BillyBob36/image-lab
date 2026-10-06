"use strict";
const Q={token:'',jobs:[],state:null,polling:false,selected:null,shown:'',submitting:false};
function definitions(){return S.models[S.model]?.caps.definitions||DEFINITIONS}
function rememberModel(){
 if(!S.model)return;
 const fields=['sizeStrategy','ratio','def','quality','format','fidelity','moderation'];
 const draft=Object.fromEntries(fields.map(k=>[k,S[k]]));
 for(const id of ['sizeW','sizeH','nImages','compression','qwenTitle','qwenSeed','qwenSteps','qwenGuidance','qwenNegative'])draft[id]=$(id)?.value;
 draft.transparent=$('transparent').checked;
 localStorage.setItem('imagelab-settings-'+S.model,JSON.stringify(draft));
}
function restoreModel(id){
 let d={};try{d=JSON.parse(localStorage.getItem('imagelab-settings-'+id)||'{}')}catch{}
 const initial={sizeStrategy:'combo',ratio:'1:1',def:'1k',quality:S.models[id].caps.defaultQuality,format:'png',fidelity:'low',moderation:'auto'};
 for(const k of Object.keys(initial))S[k]=d[k]??initial[k];
 for(const [k,v] of Object.entries({sizeW:1024,sizeH:1024,nImages:1,compression:80,qwenTitle:'',qwenSeed:'',qwenSteps:40,qwenGuidance:1,qwenNegative:''}))if($(k))$(k).value=d[k]??v;
 $('transparent').checked=!!d.transparent;$('nVal').textContent=$('nImages').value;$('compressionVal').textContent=$('compression').value;
}
function applyQwenCaps(caps){
 const q=!!caps.queue;
 if($('incompatibleSettings')){for(const [id,marker] of Object.entries(Q.optionAnchors)){if(q)$('incompatibleSettings').append($(id));else marker.after($(id));}$('incompatibleSettings').classList.toggle('hidden',!q);}

 document.body.classList.toggle('qwen-selected',q);
 $('qwenCompute').classList.toggle('hidden',!q);
 $('qwenQueue').classList.toggle('hidden',!q||S.tab==='gallery');
 $('qwenOptions').classList.toggle('hidden',!q);
 $('editSources').classList.toggle('hidden',S.tab!=='edit'&&!q);
 $('maskEditor').classList.toggle('hidden',!caps.mask||!S.sources.length);
 $('maskUnavailable').classList.toggle('hidden',!q);
 $('maskEnabled').disabled=!caps.mask;
 $('maskPaint').style.pointerEvents=caps.mask?'auto':'none';
 $('prompt').maxLength=caps.maxPromptChars;
 $('prompt').placeholder=S.tab==='edit'?'Décrivez la modification à apporter…':`Décrivez l’image à créer… (${caps.maxPromptChars.toLocaleString('fr-FR')} caractères maximum)`;
 const constraints=caps.freeSize;
 for(const id of ['sizeW','sizeH']){const e=$(id);e.step=constraints?.edgeMultiple||16;e.min=constraints?.minEdge||512;e.max=constraints?.maxLongEdge||1536;}
 $('nImages').value=Math.min(+$('nImages').value,caps.maxImages);$('nVal').textContent=$('nImages').value;
 toggleField('fQuality',caps.qualities.length>0);$('fQuality').title=q?'Qwen utilise les étapes et le guidage ci-dessous.':'';
 toggleField('fModeration',caps.moderation.length>0);$('fModeration').title=q?'Le prompt est transmis tel quel, sans filtre ajouté par Image Lab.':'';
 toggleField('fFidelity',S.tab==='edit'&&caps.inputFidelity);$('fFidelity').title=caps.inputFidelity?'Disponible en édition':'Ce réglage est propre aux modèles GPT-image.';
 const trans=caps.nativeTransparency||(S.tab==='generate'&&caps.transparencyFallback);
 toggleField('fTransparent',trans);if(!trans){$('transHint').textContent='Non pris en charge';$('transLabel').textContent=q?'Qwen produit une image avec fond.':'Indisponible dans ce mode.';}
 toggleField('fCompression',S.format==='jpeg'&&caps.jpegCompression&&!$('transparent').checked);
 toggleField('fFormat',!$('transparent').checked&&caps.formats.length>1);
 $('fFormat').title=q?'Qwen génère un PNG natif.':'';
 $('qwenNegative').disabled=+$('qwenGuidance').value<=1;
 $('qwenNegativeHint').textContent=$('qwenNegative').disabled?'Actif lorsque le guidage dépasse 1.':'Transmis au prompt négatif de Qwen.';
 $('capabilitySummary').replaceChildren();
 const badges=[['Références multiples',caps.multiImage],['Pinceau / masque',caps.mask],['Transparence native',caps.nativeTransparency],['4K native',constraints?.maxLongEdge>=3840],['Seed',caps.seed],['File GPU',caps.queue]];
 for(const [label,on] of badges){const span=document.createElement('span');span.className=on?'capability available':'capability unavailable';span.textContent=(on?'✓ ':'')+label;span.title=on?'Disponible avec ce modèle':'Non pris en charge par ce modèle';$('capabilitySummary').append(span);}
 document.querySelectorAll('.model-btn').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.model===S.model)));
 if(q)renderQwen();
}
async function qapi(path,options={},retry=true){
 const headers={...(options.headers||{})};
 if(options.method&&options.method!=='GET')headers['X-Qwen-Token']=Q.token;
 const r=await api('/api/qwen'+path,{...options,headers});
 if(r.status===403&&retry){Q.token=(await (await api('/api/qwen/session')).json()).token;return qapi(path,options,false)}
 const data=await r.json().catch(()=>({}));
 if(!r.ok){const detail=Array.isArray(data.detail)?data.detail.map(v=>v.msg).join(' · '):data.detail;throw new Error(detail||'Le service Qwen ne répond pas.')}
 return data;
}
function bindQwen(){
 const extras=document.createElement('details');extras.id='incompatibleSettings';extras.innerHTML='<summary>Fonctions indisponibles avec ce modèle</summary>';extras.className='incompatible-settings hidden';$('optionsPanel').append(extras);
 Q.optionAnchors={};for(const id of ['fQuality','fTransparent','fCompression','fFidelity','fModeration']){const marker=document.createComment(id);$(id).before(marker);Q.optionAnchors[id]=marker;}
 $('fN').after($('qwenOptions'));

 Object.assign(I18N.fr,{tagline_qwen:'Références, seed et liberté du prompt',avail_A100:'Votre GPU',tab_edit:'Édition et références'});
 Object.assign(I18N.en,{tagline_qwen:'References, seed and direct prompts',avail_A100:'Your GPU',tab_edit:'Editing & references'});
 $('a100Start').onclick=async()=>{try{await qapi('/gpu/a100/start',{method:'POST'});await pollQwen()}catch(e){showErr(e.message)}};
 $('a100Stop').onclick=async()=>{try{await qapi('/gpu/a100/stop',{method:'POST'});await pollQwen()}catch(e){showErr(e.message)}};
 $('qwenIdle').onchange=async()=>{try{await qapi('/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({idleMinutes:+$('qwenIdle').value})});pollQwen()}catch(e){showErr(e.message)}};
 $('qwenGuidance').oninput=()=>{applyQwenCaps(S.models[S.model].caps);rememberModel()};
 $('optionsPanel').addEventListener('change',rememberModel);
 window.addEventListener('beforeunload',rememberModel);
 document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();run()}});
 pollQwen();setInterval(pollQwen,4000);
}
async function pollQwen(){
 if(Q.polling)return;Q.polling=true;
 try{
  if(!Q.token)Q.token=(await qapi('/session')).token;
  const [state,data]=await Promise.all([qapi('/state'),qapi('/jobs')]);Q.state=state;Q.jobs=data.jobs;if(!Q.selected)Q.selected=Q.jobs.find(j=>j.results.length||!['complete','failed','cancelled','imported'].includes(j.status))?.id||null;$('qwenConnection').textContent='';renderQwen();
 }catch(e){$('qwenConnection').textContent='Connexion Qwen interrompue. Reprise automatique du suivi…';}
 finally{Q.polling=false;}
}
function renderQwen(){
 if(!Q.state)return;
 const {a10,a100,settings,queueLength}=Q.state;
 $('a10Dot').className='gpu-dot '+(a10.running?'on':'');$('a100Dot').className='gpu-dot '+(a100.running?'on':['starting','preparing'].includes(a100.status)?'waiting':'');
 $('a10Label').textContent=a10.running?'Démarrée · Qwen non préparé':a10.power==='deallocated'?'Éteinte':a10.power==='unknown'?'État indisponible':a10.power;
 $('a10Reason').textContent=a10.reason;$('a10Start').title=a10.reason;$('a10Start').disabled=!a10.supported;
 $('a100Label').textContent=a100.label||'Vérification…';$('a100Start').disabled=!!a100.wakeRequested||['starting','preparing','generating','ready'].includes(a100.status);
 $('a100Start').textContent=a100.wakeRequested?'Démarrage demandé':a100.status==='ready'?'A100 prête':'Démarrer A100';
 $('a100Stop').disabled=!a100.owned||queueLength>0||a100.status==='generating';
 $('qwenPrepareProgress').hidden=a100.status!=='preparing';$('qwenPrepareProgress').value=a100.progress||0;
 if(document.activeElement!==$('qwenIdle'))$('qwenIdle').value=settings.idleMinutes;
 $('qwenQueueCount').textContent=queueLength?`${queueLength} en cours ou en attente`:'';
 const wrap=$('qwenJobs');const hash=JSON.stringify(Q.jobs.map(j=>[j.id,j.status,j.message,j.progress,j.results.length,j.position]));
 if(wrap.dataset.hash!==hash){wrap.dataset.hash=hash;wrap.replaceChildren();
  if(!Q.jobs.length){const empty=document.createElement('p');empty.className='queue-empty';empty.textContent='Votre première demande apparaîtra ici. Le GPU se réveillera si nécessaire.';wrap.append(empty);}
  for(const j of Q.jobs.slice(0,30)){
   const row=document.createElement('article');row.className='queue-job';
   const text=document.createElement('div');text.className='queue-job-text';const title=document.createElement('b');title.textContent=j.title||j.prompt.slice(0,90);const message=document.createElement('p');
   const statuses={queued:'En attente',preparing:'Préparation',running:'Génération',complete:'Terminée',cancelled:'Annulée',failed:'Échec'};
   message.textContent=`${statuses[j.status]||j.status}${j.position?' · position '+j.position:''} · ${j.results.length}/${j.count} images${j.message?' · '+j.message:''}`;
   text.append(title,message);row.append(text);
   const actions=document.createElement('div');actions.className='queue-actions';
   const button=(label,fn)=>{const b=document.createElement('button');b.className='btn-ghost';b.textContent=label;b.onclick=fn;actions.append(b)};
   const active=!['complete','cancelled','failed','imported'].includes(j.status);
   if(active){const p=document.createElement('progress');p.max=100;p.value=j.progress||0;text.append(p);button(j.cancelRequested?'Annulation demandée':'Annuler',async()=>{try{await qapi(`/jobs/${j.id}/cancel`,{method:'POST'});pollQwen()}catch(e){showErr(e.message)}});}
   if(j.results.length){button('Voir',()=>{Q.selected=j.id;Q.shown='';showQwenResult(j);$('results').scrollIntoView({behavior:'smooth',block:'center'})});const a=document.createElement('a');a.className='btn-ghost';a.href=`/api/qwen/jobs/${j.id}/download`;a.textContent='PNG + réglages';actions.append(a);}
   if(j.canResume)button('Reprendre le suivi',async()=>{try{await qapi(`/jobs/${j.id}/resume`,{method:'POST'});pollQwen()}catch(e){showErr(e.message)}});
   button('Réutiliser',()=>reuseQwen(j));row.append(actions);wrap.append(row);
  }
 }
 const selected=Q.jobs.find(j=>j.id===Q.selected);if(selected?.results.length&&S.model==='qwen-image-2.1'&&S.tab!=='gallery')showQwenResult(selected);
}
function showQwenResult(job){
 const hash=job.id+':'+job.results.length;if(Q.shown===hash)return;Q.shown=hash;
 const saved=job.results.map(r=>r.gallery).filter(Boolean);
 renderResults({images:job.results.map(r=>r.gallery?.url||r.url),saved_images:saved,model:'qwen-image-2.1',format:'png'});
}
async function runQwen(prompt){
 if(Q.submitting)return;
 const caps=S.models[S.model].caps;
 if(S.sources.length>caps.maxReferences){showErr(`Qwen accepte jusqu’à ${caps.maxReferences} références.`);return;}
 const [width,height]=currentSize().split('x').map(Number);
 if(width%32||height%32||Math.min(width,height)<256||Math.max(width,height)>2048){showErr('Qwen : dimensions de 256 à 2048 px, par multiples de 32.');return;}
 const steps=+$('qwenSteps').value,guidance=+$('qwenGuidance').value,seed=$('qwenSeed').value===''?null:+$('qwenSeed').value;
 if(!Number.isInteger(steps)||steps<4||steps>60||guidance<1||guidance>7||(seed!==null&&(!Number.isInteger(seed)||seed<0||seed>2147483647))){showErr('Vérifiez les étapes, le guidage et la seed.');return;}
 const body={prompt,width,height,count:+$('nImages').value,steps,guidance,seed,title:$('qwenTitle').value,negativePrompt:$('qwenNegative').value,references:[]};
 Q.submitting=true;setBusy(true);showErr('');rememberModel();
 try{
  for(const s of [...S.sources]){
   if(!s.qwenId){const fd=new FormData();fd.append('file',s.file,s.file.name);s.qwenId=(await qapi('/uploads',{method:'POST',body:fd})).id;}
   body.references.push(s.qwenId);
  }
  let pending;try{pending=JSON.parse(localStorage.getItem('imagelab-qwen-send')||'null')}catch{}
  if(!pending||JSON.stringify(pending.body)!==JSON.stringify(body))pending={body,key:crypto.randomUUID()};
  localStorage.setItem('imagelab-qwen-send',JSON.stringify(pending));
  const job=await qapi('/jobs',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':pending.key},body:JSON.stringify(body)});
  localStorage.removeItem('imagelab-qwen-send');Q.selected=job.id;Q.shown='';await pollQwen();$('qwenQueue').scrollIntoView({behavior:'smooth',block:'nearest'});
 }catch(e){showErr(e.message)}finally{Q.submitting=false;setBusy(false);}
}
async function reuseQwen(job){
 try{
  selectModel('qwen-image-2.1');selectTab(job.references.length?'edit':'generate');
  $('prompt').value=job.prompt;$('promptCount').textContent=job.prompt.length;localStorage.setItem('imagelab-prompt',job.prompt);
  $('qwenTitle').value=job.title||'';$('qwenNegative').value=job.negativePrompt||'';
  for(const [id,k] of [['qwenSeed','seed'],['qwenSteps','steps'],['qwenGuidance','guidance'],['nImages','count'],['sizeW','width'],['sizeH','height']])$(id).value=job[k];
  S.sizeStrategy='custom';const sources=[];
  for(const ref of job.references){const r=await api(ref.url);if(!r.ok)throw new Error('Référence indisponible.');const blob=await r.blob();sources.push({file:new File([blob],ref.name,{type:'image/png'}),url:URL.createObjectURL(blob),qwenId:ref.id});}
  S.sources.forEach(s=>URL.revokeObjectURL(s.url));S.sources=sources;afterSourceChange();applyCaps();rememberModel();$('prompt').focus();
 }catch(e){showErr(e.message)}
}
