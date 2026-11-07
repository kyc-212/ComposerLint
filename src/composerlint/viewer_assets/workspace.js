'use strict';
(() => {
  const start=async()=>{
    const $=id=>document.getElementById(id),viewer=window.composerViewer;
    if(!viewer)return;
    // Source-bound viewing does not create or impersonate an M1 edit workspace.
    if(viewer.originalManifest.viewer_schema_version===2)return;
    let state=null,lastAutoProposal=null,detailProposal=null,busy=false,closed=false,failures=0,updating=false,previewError='',originalHighlights=[];
    const text=(id,value)=>{$(id).textContent=value;};
    const human=value=>String(value??'unknown').replaceAll('_',' ');
    const pitch=p=>p ? `${p.step}${p.alteration===-2?'𝄫':p.alteration===-1?'♭':p.alteration===1?'♯':p.alteration===2?'𝄪':''}${p.octave}` : 'unknown';
    const get=async path=>{const r=await fetch(path);if(!r.ok){const e=new Error(r.status===410?'View or proposal expired. Reopen the score if needed.':r.status===409?'Source or proposal changed. Retrieve fresh context.':'Connection interrupted. Reconnecting; preview actions are paused.');e.status=r.status;throw e;}return r.json();};
    const paragraph=(parent,value,tag='p')=>{const e=document.createElement(tag);e.textContent=value;parent.append(e);return e;};
    const details=(parent,label)=>{const e=document.createElement('details');paragraph(e,label,'summary');parent.append(e);return e;};
    const measureLabel=i=>viewer.originalManifest.measures.find(m=>m.part_id===viewer.originalManifest.scope.reference_part_id && m.ordinal===i)?.printed_label ?? i;
    const valid=()=>state?.proposal?.preview_status==='ready'&&['verified','preparing'].includes(state.state)&&!failures;
    const buttons=()=>{const proposed=viewer.version==='proposed';$('approve-export').disabled=busy||!valid()||!state?.export_binding||!proposed;$('view-original').setAttribute('aria-pressed',String(!proposed));$('view-proposed').setAttribute('aria-pressed',String(proposed));$('view-proposed').disabled=busy||!valid();$('discard-proposal').disabled=busy||!state?.proposal||state.state==='preparing'||Boolean(failures);text('version-badge',proposed?'Proposed':'Original');$('version-badge').classList.toggle('proposed',proposed);};
    const version=async proposed=>{
      if(busy)return;busy=true;buttons();
      try{
        const p=state?.proposal;if(proposed&&!valid())throw new Error('A current verified, rendered proposal is required.');
        const next=proposed?await get(`p-${p.proposal_id}-manifest.json`):{...viewer.originalManifest,changed_note_ids:p?originalHighlights:[]};
        if(proposed)originalHighlights=next.original_changed_note_ids||[];
        await viewer.setVersion(next,proposed?`p-${p.proposal_id}-`:'');previewError='';
      }catch(e){
        previewError=`Preview unavailable. ${e.message}`;text('edit-status',previewError);
        try{await viewer.setVersion(viewer.originalManifest);}catch{viewer.clear();text('edit-status','Notation unavailable. Reopen the score.');}
      }finally{busy=false;buttons();}
    };
    $('view-original').addEventListener('click',()=>version(false));$('view-proposed').addEventListener('click',()=>version(true));
    $('discard-proposal').addEventListener('click',async()=>{
      if(!state?.proposal||busy||failures)return;
      busy=true;buttons();
      try{
        const r=await fetch('discard',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({proposal_id:state.proposal.proposal_id,expected_workspace_revision:state.workspace_revision,nonce:state.csrf_nonce})});
        if(!r.ok)throw new Error('Proposal changed. Retrieve fresh context before discarding.');
        busy=false;await version(false);await update();$('clear').focus();
      }catch(e){text('edit-status',e.message);}finally{busy=false;buttons();}
    });
    $('approve-export').addEventListener('click',async()=>{
      if(busy||!valid()||!state?.export_binding||viewer.version!=='proposed')return;
      $('proposal-details').open=true;await loadDetails();
      busy=true;buttons();text('edit-status','Review the native confirmation dialog. Only your approval saves this candidate.');
      try{
        const r=await fetch('approve-export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({nonce:state.csrf_nonce,binding:state.export_binding})});
        const value=await r.json();if(!r.ok)throw new Error(value.error?.reason||'Export failed; no successful save confirmed.');
        text('edit-status',`Saved and reopened MusicXML: ${value.output_path}`);
      }catch(e){text('edit-status',e.message);}finally{busy=false;buttons();}
    });
    const changePage=async(offset=0)=>{
      const p=state?.proposal;if(!p||!valid())return;
      const data=await get(`p-${p.proposal_id}-changes-${offset}.json`);if(state?.proposal?.proposal_id!==p.proposal_id)return;
      if(!offset)$('note-changes').replaceChildren();
      for(const n of data.items){const row=document.createElement('div');$('note-changes').append(row);paragraph(row,n.kind?`Measure ${n.printed_label}: ${human(n.kind)} · ${n.before||'unmarked'} → ${n.after||'removed'}`:`Measure ${n.printed_label}: ${pitch(n.before_written)} → ${pitch(n.after_written)} written`);if(n.kind==='rhythm'||n.kind==='note_creation')paragraph(row,`Onset ${n.original_onset} → ${n.proposed_onsets.join(', ')} · ${human(n.form||n.operation)}`);const extra=details(row,'Musical & source details');paragraph(extra,n.kind?`${n.scope||'Exact source attack'} · source measure ${n.measure_index}${n.source_note_ordinal?', note '+n.source_note_ordinal:''}${n.absolute_offset?' · quarter offset '+n.absolute_offset:''}`:`${pitch(n.before_sounding)} → ${pitch(n.after_sounding)} sounding · source measure ${n.measure_index}, note ${n.source_note_ordinal}`);const button=paragraph(row,`Show measure ${measureLabel(n.measure_index)}`,'button');button.addEventListener('click',()=>viewer.locateMeasure(n.measure_index).catch(e=>text('edit-status',e.message)));}
      if(data.next_offset!==null){const b=paragraph($('note-changes'),`More changes (${data.next_offset} of ${data.total_items} shown)`,'button');b.addEventListener('click',()=>{b.remove();changePage(data.next_offset).catch(e=>text('edit-status',e.message));});}
    };
    const loadDetails=async()=>{
      const p=state?.proposal;if(!$('proposal-details').open||!valid()||detailProposal===p.proposal_id)return;
      try{
        const receipt=await get(`p-${p.proposal_id}-verification.json`);
        if(state?.proposal?.proposal_id!==receipt.proposal_id||p.verification_sha256!==receipt.verification_sha256||p.candidate_sha256!==receipt.candidate_sha256)throw new Error('Verification identity changed. Retrieve fresh context.');
        const gates=Object.entries(receipt.gates),passed=gates.filter(([,v])=>v===true).length;
        text('verification-rows',`${passed} of ${gates.length} supported checks passed. Exact transformation verified; musical improvement is not assessed.`);
        const box=$('verification-details');box.replaceChildren();for(const [gate,value] of gates)paragraph(box,`${value===true?'✓':'Incomplete'} ${human(gate)}`);
        paragraph(box,receipt.notation.limitation);paragraph(box,receipt.analysis.notice);
        for(const [label,rows] of [['Coverage changes',receipt.analysis.coverage],['New findings',receipt.analysis.new_findings],['Resolved findings',receipt.analysis.resolved_findings]]){const more=details(box,`${label} (${rows.length})`);for(const row of rows)paragraph(more,row.explanation||JSON.stringify(row,null,2),'pre');}
        if(receipt.analysis.coverage_details){const more=details(box,'Complete coverage evidence');for(const row of receipt.analysis.coverage_details)paragraph(more,JSON.stringify(row,null,2),'pre');}
        const ids=details(box,'Receipt identities');paragraph(ids,`Proposal ${receipt.proposal_id}\nCandidate ${receipt.candidate_sha256}\nVerification ${receipt.verification_sha256}`,'pre');
        await changePage();if(state?.proposal?.proposal_id===p.proposal_id)detailProposal=p.proposal_id;
      }catch(e){text('verification-rows',e.message);}
    };
    $('proposal-details').addEventListener('toggle',()=>{if($('proposal-details').open)loadDetails();else if($('proposal-details').contains(document.activeElement))$('proposal-details').querySelector('summary').focus();});
    const resetDetails=()=>{detailProposal=null;$('proposal-details').open=false;for(const id of ['note-changes','verification-rows','verification-details'])text(id,'');};
    const update=async()=>{
      if(closed||updating||busy)return;updating=true;
      try{
        const next=await get('workspace.json');const previousId=state?.proposal?.proposal_id;state=next;failures=0;
        const p=state.proposal;if(previousId!==p?.proposal_id){resetDetails();previewError='';originalHighlights=[];}
        $('proposal-strip').hidden=!p;
        text('edit-status',state.state==='stale'?'Source changed. Original snapshot shown; reopen for current notation.':state.state==='preparing'?`${p?'Prior verified proposal retained. ':''}Preparing notation preview…`:state.last_error?`${p?'Prior proposal retained. New request failed: ':''}${state.last_error.reason}`:(state.export_receipt?`Saved and reopened MusicXML: ${state.export_receipt.output_path}`:previewError)||(!['verified','no_proposal','discarded'].includes(state.state)?human(state.state):''));
        if(!valid()){
          if(viewer.version==='proposed')await version(false);
          lastAutoProposal=null;resetDetails();
          if(p)text('proposal-summary','Preview unavailable; Original snapshot shown.');
        }else{
          const t=p.target,count=t.rhythm_node_count??t.pitched_segment_count,unit=t.rhythm_node_count?'note/rest results':(count===1?'note':'notes');text('proposal-summary',`${t.part_label||t.part_id} · measures ${measureLabel(t.start_measure_index)}–${measureLabel(t.end_measure_index)} · ${count} ${unit} · ${p.operation.policy==='v02_m6_bounded_note_creation_v1'?'melodic continuation':p.operation.policy==='v02_m6_rhythmic_edit_v1'?'rhythmic alternative':p.operation.kind==='expressive_edit_batch'?'creative alternative':p.operation.kind==='exact_pitch_batch'?'exact pitch changes':'octave '+p.operation.direction}${state.state==='preparing'?' · prior proposal':''}`);
          if(lastAutoProposal!==p.proposal_id){lastAutoProposal=p.proposal_id;await version(true);}
          if($('proposal-details').open)await loadDetails();
        }
        if(!p&&previousId&&!state.workspace_id)text('edit-status','Temporary preview expired. Original snapshot shown.');
        if(!p&&$('proposal-strip').contains(document.activeElement))$('clear').focus();
        text('connection-status','');buttons();
      }catch(e){
        failures++;text('connection-status',e.message);buttons();
        if(e.status===410){closed=true;state=null;await version(false);$('proposal-strip').hidden=true;text('edit-status','View expired. Original snapshot only; reopen the score.');}
        if(e.status===409){await version(false);text('edit-status','Source or preview changed. Original snapshot only; reopen the score.');}
      }finally{updating=false;}
    };
    // Registration is independent of proposal existence and polling success.
    if(document.modelContext?.registerTool)document.modelContext.registerTool({name:'composerlint_current_edit_context',description:'Fresh live source/workspace/revisions and selection binding with displayed version. No edit, approval or export.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true},execute:async()=>{const context=await viewer.readyContext(()=>get('workspace.json'));delete context.csrf_nonce;delete context.export_binding;return {...context,display_version:viewer.version};}});
    await update();
    const poll=async()=>{if(!closed&&!document.hidden)await update();if(!closed)setTimeout(poll,Math.min(30000,failures?3000*2**Math.min(failures,3):state?.state==='preparing'?1000:3000));};setTimeout(poll,3000);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!closed)update();});
  };
  window.addEventListener('composerlint-viewer-ready',start,{once:true});
})();
