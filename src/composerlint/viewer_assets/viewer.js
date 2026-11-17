'use strict';
(async () => {
  const $ = id => document.getElementById(id);
  const status = (text, attention=false) => { $('status').textContent = text; $('viewer-warning').textContent=attention?text:''; };
  const request = async (path, options) => {
    const response = await fetch(path, options);
    if (!response.ok) throw new Error(response.status === 410 ? 'View expired. Ask Codex to reopen this score.' : response.status === 409 ? 'Selection changed in another copy. Reload this view.' : 'Request failed. Retry the page or reopen the score; M1 text review remains available.');
    return response;
  };
  try {
    let manifest = await (await request('manifest.json')).json();
    const originalManifest=manifest; let assetPrefix='';
    const scrollPositions=new Map(),pageCache=new Map();
    const scope = manifest.scope;
    const sourceBound=manifest.viewer_schema_version===2;
    let activeReference=scope.reference_part_id;
    const byId = new Map(manifest.measures.map(m => [m.id,m]));
    let reference = manifest.measures.filter(m => m.part_id === activeReference);
    const setReference=part=>{activeReference=part;reference=originalManifest.measures.filter(m=>m.part_id===part);};
    const labels = index => reference.find(m => m.ordinal === index);
    const label = index => { const m=labels(index); return `Measure ${m.printed_label ?? 'unlabeled'} · source ${index} · occurrence ${m.label_occurrence}`; };
    $('title').textContent = manifest.title || 'Sheet music';
    const names = [...new Set(manifest.measures.map(m => m.part_label || m.part_id))];
    $('scope').textContent = `${names.join(' · ')} · all staves · written pitch · ${manifest.mode === 'whole_score' ? 'complete score' : 'optional focused passage'}`;
    let anchor=null, end=null, revision=0, selected=null, pending=Promise.resolve(), failure=null;
    let currentPage=1, svg=null, controls=[], magnification=1, fitting=true, fitMode='width', manualScale=null, focusRange=null, displayRevision=0;
    let comparison={revision:0,A:null,B:null};
    const readComparison=async()=>{
      const response=await fetch('comparison');const data=await response.json();
      if(!response.ok)throw new Error(data.error?.reason||'A/B passages unavailable. Reopen the score.');
      return data;
    };
    const refresh = () => {
      const low=Math.min(anchor,end), high=Math.max(anchor,end);
      for(const c of controls){
        const index=sourceBound&&c.members ? c.members.find(m=>m.part_id===activeReference)?.ordinal : c.index;
        const active=anchor!==null && index!==undefined && index>=low && index<=high;
        c.element.classList.toggle('selected',active);
        const linked=anchor!==null && manifest.findings.some(f=>f.category!=='analysis_limitation' && f.measure_ids.some(id=>{const m=byId.get(id);return m && m.ordinal>=low && m.ordinal<=high;}) && f.measure_ids.some(id=>byId.get(id)?.ordinal===c.index));
        c.element.classList.toggle('context',linked && !active);
        c.element.setAttribute('aria-pressed',String(active));
        let bookmark='';
        for(const slot of ['A','B']){
          const s=comparison[slot]?.selection,on=!assetPrefix&&s&&c.index>=s.start_measure_index&&c.index<=s.end_measure_index;
          c.element.classList.toggle(`passage-${slot.toLowerCase()}`,Boolean(on));
          if(on)bookmark=slot;
        }
        c.element.querySelector('.ab-marker')?.remove();
        if(bookmark&&c.box){
          const tag=document.createElementNS('http://www.w3.org/2000/svg','text');
          tag.setAttribute('class','ab-marker');tag.setAttribute('x',String(c.box.x));tag.setAttribute('y',String(c.box.y-22));tag.textContent=bookmark;
          c.element.append(tag);
        }
        if(c.box){
          const sourceMeasure=sourceBound&&index===undefined?c.members?.[0]:null;
          const caption=sourceMeasure?`Measure ${sourceMeasure.printed_label??'unlabeled'} · ${sourceMeasure.part_label} · source ${sourceMeasure.ordinal}`:label(sourceBound?index:c.index);
          c.element.setAttribute('aria-label',`${caption}${bookmark?`; Passage ${bookmark}`:''}; all shown parts`);
        }
      }
      const printed=i=>labels(i)?.printed_label ?? i;
      $('selection-summary').textContent=anchor===null ? 'No measures selected' : `Measures ${printed(low)}${low===high?'':`–${printed(high)}`} · ${names.join(', ')}`;
      $('copy').disabled=anchor===null;
      $('ab-summary').textContent=['A','B'].map(slot=>{const s=comparison[slot]?.selection;return `${slot}: ${s?`${printed(s.start_measure_index)}–${printed(s.end_measure_index)}`:'—'}`;}).join(' · ');
      for(const slot of ['a','b']){
        $(`set-${slot}`).disabled=Boolean(assetPrefix);
        $(`go-${slot}`).disabled=Boolean(assetPrefix)||!comparison[slot.toUpperCase()];
      }
      const originalNotice='A/B refers to Original. Switch to Original to compare passages.';
      if(assetPrefix)$('ab-status').textContent=originalNotice;
      else if($('ab-status').textContent===originalNotice)$('ab-status').textContent='';
    };
    const select = (index, extend=false, part=activeReference) => {
      const samePart=part===activeReference;
      const nextAnchor=extend && anchor!==null && samePart ? anchor : index;
      if(index!==null && Math.abs(index-nextAnchor)>7){
        status('Select at most eight contiguous source measures. Your previous selection and complete page are preserved.',true);
        return;
      }
      if(sourceBound)setReference(part);
      anchor=nextAnchor; end=index; refresh(); selected=null; $('reference').value=''; status('Synchronizing selection…');
      const start=index===null ? null : Math.min(anchor,end), finish=index===null ? null : Math.max(anchor,end);
      const selectedPart=activeReference;
      pending=pending.then(async () => {
        if(failure) throw failure;
        const body={start,end:finish,revision:revision+1};if(sourceBound)body.reference_part_id=selectedPart;
        const response=await request('selection',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const result=await response.json(); revision=result.revision; selected=result.selection ? result : null;
        $('reference').value=result.selection_ref || '';
        status(start===null ? 'Selection cleared. Select a measure before asking Codex.' : 'Selection ready. Ask Codex about these measures in this conversation.');
      }).catch(error => { failure=error; selected=null; $('reference').value=''; status(error.message,true); });
    };
    const clear = () => {select(null);$('clear').focus();};
    const fit = () => {
      if(!svg) return;
      const viewport=$('viewport');
      const available=Math.max(180,window.innerHeight-viewport.getBoundingClientRect().top-18);
      viewport.style.height=`${available}px`;
      const box=svg.viewBox.baseVal;
      const width=Math.max(100,fitMode==='width' ? viewport.clientWidth-30 : Math.min(viewport.clientWidth-30,(viewport.clientHeight-30)*box.width/box.height));
      svg.style.width=`${manualScale===null ? width : box.width*manualScale}px`;
      $('zoom-label').textContent=manualScale===null ? (fitMode==='width'?'Fit width':'Fit page') : `${Math.round(manualScale*100)}%`;

    };
    const keyboard = (event,index,part=activeReference) => {
      if(event.key==='Enter'||event.key===' '){event.preventDefault();select(index,event.shiftKey,part);}
      if(event.shiftKey && ['ArrowLeft','ArrowRight'].includes(event.key)){
        event.preventDefault();
        if(sourceBound&&part!==activeReference){select(index,false,part);return;}
        const next=Math.max(scope.start_measure_index,Math.min(sourceBound?reference.length:scope.end_measure_index,(end??index)+(event.key==='ArrowRight'?1:-1)));
        if(anchor!==null && Math.abs(next-anchor)>7){select(next,true);return;}
        select(next,true);
        if(!focusRange){
          const m=labels(next), page=manifest.pages.find(p=>p.measure_ids.includes(m.id));
          if(page.number!==currentPage) showPage(page.number).then(()=>controls.find(c=>c.index===next)?.element.focus()).catch(e=>status(e.message,true));
        }
      }
      if(event.key==='Escape'){event.preventDefault();clear();}
    };
    const display = (svgText,groups,ids) => {
      const parsed=new DOMParser().parseFromString(svgText,'image/svg+xml');
      if(parsed.querySelector('parsererror')) throw new Error('Notation could not be displayed. Retry this page.');
      svg=document.importNode(parsed.documentElement,true);
      $('score').replaceChildren(svg); $('measures').replaceChildren(); controls=[];
      const displayed=new Set(ids);
      for(const group of groups){
        const element=svg.querySelector(`[id="${group.svg_id}"]`);
        if(!element) throw new Error('Mapped measure is missing. Reopen this score.');
        const m=group.measure_ids.map(id=>byId.get(id)).find(m=>m.part_id===scope.reference_part_id) || byId.get(group.measure_ids[0]);
        const findings=manifest.findings.filter(f=>f.category!=='analysis_limitation' && f.measure_ids.some(id=>group.measure_ids.includes(id)));
        element.classList.toggle('finding',findings.length>0);
        element.setAttribute('tabindex','0'); element.setAttribute('role','button'); element.setAttribute('aria-label',`${label(m.ordinal)}; ${findings.length} findings; all shown parts`);
        const box=element.getBBox(),rect=document.createElementNS('http://www.w3.org/2000/svg','rect');
        for(const [k,v] of Object.entries({x:box.x-20,y:box.y-60,width:box.width+40,height:box.height+120})) rect.setAttribute(k,String(v));
        rect.setAttribute('class','hitbox'); element.insertBefore(rect,element.firstChild);
        element.addEventListener('click',e=>{
          let target=m;
          if(sourceBound){let node=e.target;while(node&&node!==element){const id=manifest.note_measure_ids?.[node.id];if(id){target=byId.get(id);break;}node=node.parentElement;}}
          select(target.ordinal,e.shiftKey,sourceBound?target.part_id:activeReference);
        });
        element.addEventListener('keydown',e=>{const target=sourceBound?(group.measure_ids.map(id=>byId.get(id)).find(m=>m.part_id===activeReference)||m):m;keyboard(e,target.ordinal,target.part_id);});
        controls.push({element,index:m.ordinal,box,members:group.measure_ids.map(id=>byId.get(id))});
      }
      for(const m of reference.filter(m=>displayed.has(m.id))){
        const b=document.createElement('button'); b.textContent=label(m.ordinal);
        b.addEventListener('click',e=>select(m.ordinal,e.shiftKey,sourceBound?m.part_id:activeReference));
        b.addEventListener('keydown',e=>{if(!['Enter',' '].includes(e.key)) keyboard(e,m.ordinal,sourceBound?m.part_id:activeReference);});
        $('measures').append(b); controls.push({element:b,index:m.ordinal,members:[m]});
      }
      for(const id of [...(manifest.changed_note_ids || []),...(manifest.changed_marking_ids || [])]){const n=svg.querySelector(`[id="${id}"]`);if(n){n.classList.add('changed-note');const b=n.getBBox(),r=document.createElementNS('http://www.w3.org/2000/svg','rect');for(const [k,v] of Object.entries({x:b.x-16,y:b.y-16,width:Math.max(40,b.width+32),height:b.height+32}))r.setAttribute(k,String(v));r.setAttribute('class','changed-marker');r.setAttribute('pointer-events','none');n.insertBefore(r,n.firstChild);}}
      for(const group of groups){if(group.measure_ids.some(id=>(manifest.affected_measure_ids || []).includes(id)))svg.querySelector(`[id="${group.svg_id}"]`)?.classList.add('affected-measure');}
      fit(); refresh(); $('viewport').scrollTo(0,0);
    };
    const showPage = async number => {
      if(number<1 || number>manifest.page_count) return;
      const ticket=++displayRevision;
      status(`Loading complete page ${number}…`);
      const cacheKey=`${assetPrefix}page-${number}.svg`;
      const text=pageCache.get(cacheKey) ?? await (await request(cacheKey)).text();
      pageCache.set(cacheKey,text);
      try{comparison=await readComparison();}catch(e){comparison={revision:0,A:null,B:null};$('ab-status').textContent=e.message;}
      if(ticket!==displayRevision) return;
      const page=manifest.pages[number-1];
      display(text,page.groups,page.measure_ids); currentPage=number; focusRange=null;
      $('page-label').textContent=`Page ${number} of ${manifest.page_count}`;
      $('previous').disabled=number===1; $('next').disabled=number===manifest.page_count;
      $('static').href=`${assetPrefix}page-${number}.svg`; $('static').hidden=false;
      status(`Complete ${assetPrefix?'Proposed':'Original'} page ready. Select any measure, then ask Codex.`);
    };
    $('previous').addEventListener('click',()=>showPage(currentPage-1).catch(e=>status(e.message,true)));
    $('next').addEventListener('click',()=>showPage(currentPage+1).catch(e=>status(e.message,true)));
    $('clear').addEventListener('click',clear);
    $('copy').addEventListener('click',async()=>{
      await pending; if(!selected) return status('Select a measure first.',true);
      try{await navigator.clipboard.writeText(selected.selection_ref); status('Selection copied. Paste it into Codex with your question.');}
      catch{$('reference').closest('details').open=true; $('reference').focus(); $('reference').select(); status('Clipboard unavailable. Copy the selected reference below.',true);}
    });
    $('fit').addEventListener('click',()=>{fitting=true;fitMode='page';manualScale=null;magnification=1;fit();$('viewport').scrollTo(0,0);});
    $('fit-width').addEventListener('click',()=>{fitting=true;fitMode='width';manualScale=null;fit();});
    for(const [id,factor] of [['smaller',1/1.25],['larger',1.25]]) $(id).addEventListener('click',()=>{fitting=false;manualScale=Math.max(.15,Math.min(5,(manualScale ?? svg.getBoundingClientRect().width/svg.viewBox.baseVal.width)*factor));fit();});
    new ResizeObserver(()=>{if(fitting) fit();}).observe($('viewport'));
    window.addEventListener('resize',fit);
    for(const n of manifest.notices){const p=document.createElement('p');p.textContent=n;$('notices').append(p);}
    const initial=await (await request('selection')).json(); revision=initial.revision;
    if(initial.selection){if(sourceBound)setReference(initial.selection.reference_part_id);selected=initial;anchor=initial.selection.start_measure_index;end=initial.selection.end_measure_index;$('reference').value=initial.selection_ref;}
    const requested=Number(location.hash.match(/^#page=(\d+)$/)?.[1] || 1);
    await showPage(requested>=1 && requested<=manifest.page_count ? requested : 1);
    const readySelection=async()=>{
      const settling=pending;await settling;if(failure)throw failure;
      const current=await (await request('selection')).json();
      if(settling!==pending || current.revision!==revision)throw new Error('Selection changed while reading context or in another copy. Retrieve fresh selection; reload if the conflict persists.');
      return current;
    };
    const capture=async slot=>{
      try{
        if(assetPrefix)throw new Error('Switch to Original before setting A/B.');
        const live=await readySelection();
        const response=await fetch('comparison',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slot,selection_revision:live.revision,pair_revision:comparison.revision})});
        const result=await response.json();
        if(!response.ok)throw new Error(result.error?.reason||'A/B capture failed.');
        comparison=result;refresh();fit();
        $('ab-status').textContent=slot==='clear'?'A/B cleared.':`Passage ${slot} captured from Original.`;
      }catch(e){$('ab-status').textContent=e.message;}
    };
    $('set-a').addEventListener('click',()=>capture('A'));
    $('set-b').addEventListener('click',()=>capture('B'));
    $('clear-ab').addEventListener('click',()=>capture('clear'));
    $('ab-controls').addEventListener('toggle',fit);
    for(const slot of ['A','B'])$(`go-${slot.toLowerCase()}`).addEventListener('click',async()=>{
      try{
        if(assetPrefix)throw new Error('Switch to Original to locate A/B.');
        comparison=await readComparison();refresh();
        const s=comparison[slot]?.selection;if(s)await window.composerViewer.locateMeasure(s.start_measure_index,s.reference_part_id);
      }catch(e){$('ab-status').textContent=e.message;}
    });
    window.composerViewer={clear:()=>{$('score').replaceChildren();},originalManifest,get selection(){return selected;},get version(){return assetPrefix?'proposed':'original';},
      setVersion:async(next,prefix='')=>{await pending;const oldVersion=assetPrefix?'proposed':'original';scrollPositions.set(oldVersion,{left:$('viewport').scrollLeft,top:$('viewport').scrollTop});const m=anchor===null ? manifest.pages[currentPage-1]?.measure_ids.map(id=>byId.get(id)).find(Boolean)?.ordinal : Math.min(anchor,end);for(const key of pageCache.keys())if(key.startsWith('p-') && !key.startsWith(prefix || 'NO_PROPOSAL'))pageCache.delete(key);manifest=next;assetPrefix=prefix;const match=manifest.pages.find(p=>p.measure_ids.some(id=>byId.get(id)?.ordinal===m));await showPage(match?.number || 1);const scroll=scrollPositions.get(prefix?'proposed':'original');if(scroll)$('viewport').scrollTo(scroll.left,scroll.top);},
      locateMeasure:async(index,part=activeReference)=>{const m=originalManifest.measures.find(m=>m.ordinal===index&&m.part_id===part),page=manifest.pages.find(p=>p.measure_ids.includes(m?.id));if(page){await showPage(page.number);controls.find(c=>c.index===index)?.element.scrollIntoView({block:'center',inline:'nearest'});}},
      get page(){return currentPage;},
      readySelection,
      readyContext:async read=>{const before=await readySelection();const context=await read();const after=await readySelection();if(context.selection.revision!==before.revision||after.revision!==before.revision)throw new Error('Selection changed while reading context. Retrieve fresh context.');return context;}};
    window.dispatchEvent(new Event('composerlint-viewer-ready'));
    if(document.modelContext?.registerTool){
      document.modelContext.registerTool({name:'composerlint_current_comparison',description:'Read current explicitly captured Original Passage A and B with source/view/pair revision. A/B retain requested first/second order across pages. Resolve both source-bound selections without single-live-selection revision; call existing M3 comparison. No analysis, edit or chat publication.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true},execute:async()=>{
        if(sourceBound)throw new Error('This source-bound view supports one live passage. Explicit whole-score comparison remains available through the legacy workflow.');
        if(assetPrefix)throw new Error('A/B compares Original passages. Switch to Original first.');
        await readySelection();const first=await readComparison();await readySelection();const last=await readComparison();
        if(first.revision!==last.revision)throw new Error('A/B changed while reading. Retrieve fresh context.');
        comparison=last;refresh();
        if(!last.A||!last.B)throw new Error('Set both Passage A and Passage B, or name both ranges in Codex.');
        return {...last,display_version:'original',notice:'Captured A/B passages, not the single live selection. Resolve canonical A and B against this base snapshot, preserving A=first and B=second; re-read pair revision before analysis.'};
      }});
      document.modelContext.registerTool({name:'composerlint_current_selection',description:'For requests such as Analyze these measures, automatically read the current live ComposerLint selection in this view, even when off the visible page. Manual copying is only a fallback. Resolve with resolve_score_selection and the original source path before musical claims. Does not edit music.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true},execute:async()=>{
        const current=await readySelection();
        return {...current,display_version:assetPrefix?'proposed':'original'};
      }});
    }else status('Live selection bridge is unavailable in this browser. Tell Codex the measure numbers and instrument.',true);
  }catch(error){status(error.message,true);}
})();
