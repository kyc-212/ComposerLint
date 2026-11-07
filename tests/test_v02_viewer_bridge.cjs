// Exercise the real viewer/controller scripts with an asynchronous local transport.
// The DOM stub deliberately has no retired sidebar, focus controls or findings list.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
 constructor(id=''){this.id=id;this.handlers={};this.children=[];this.style={};this.attrs={};this.hidden=false;this.open=false;this.textContent='';this.clientWidth=800;this.clientHeight=700;this.scrollLeft=this.scrollTop=0;this.viewBox={baseVal:{width:1000,height:1200}};this.classList={toggle(){},add(){}};}
 addEventListener(name,fn){this.handlers[name]=fn;}setAttribute(k,v){this.attrs[k]=v;}append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}getBoundingClientRect(){return {top:100,width:800};}scrollTo(){}focus(){}contains(){return false;}querySelector(){return null;}
}
const html=fs.readFileSync('src/composerlint/viewer_assets/viewer.html','utf8');
const elements=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(x=>[x[1],new Element(x[1])]));
const document={getElementById:id=>elements[id]||null,createElement:()=>new Element(),importNode:()=>new Element(),addEventListener(){},modelContext:{registerTool:t=>tools[t.name]=t}};
const tools={},listeners={},timers=[];let selection={selection:null,revision:0},workspace={workspace_id:null,state:'no_proposal',selection,proposal:null};let postGate=null,contextGate=null;
const manifest={scope:{reference_part_id:'p1',start_measure_index:1,end_measure_index:2},measures:[1,2].map(n=>({id:'m'+n,ordinal:n,part_id:'p1',printed_label:String(n),part_label:'Piano'})),pages:[{number:1,groups:[],measure_ids:['m1','m2']}],findings:[],notices:[],page_count:1,mode:'whole_score'};
const clone=x=>JSON.parse(JSON.stringify(x));
const fetch=async(path,options)=>{
 let data;
 if(path==='selection'){
  if(options){if(postGate)await postGate;const b=JSON.parse(options.body);selection={revision:b.revision,selection:b.start===null?null:{start_measure_index:b.start,end_measure_index:b.end}};}
  data=clone(selection);
 }else if(path==='workspace.json'){data=clone({...workspace,selection});if(contextGate)await contextGate;}
 else if(path==='manifest.json'||path.endsWith('-manifest.json'))data=manifest;
 else if(path.endsWith('.svg'))data='<svg/>';
 else throw Error('Unexpected or retired route: '+path);
 return {ok:true,json:async()=>clone(data),text:async()=>data};
};
const window={innerHeight:900,addEventListener:(n,f)=>listeners[n]=f,dispatchEvent:e=>listeners[e.type]?.()};
const context=vm.createContext({window,document,fetch,location:{hash:''},DOMParser:class{parseFromString(){return {querySelector:()=>null,documentElement:{}};}},ResizeObserver:class{observe(){}},Event:class{constructor(type){this.type=type;}},setTimeout:f=>timers.push(f)});
vm.runInContext(fs.readFileSync('src/composerlint/viewer_assets/workspace.js','utf8'),context);
vm.runInContext(fs.readFileSync('src/composerlint/viewer_assets/viewer.js','utf8'),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 for(let n=0;n<15&&!tools.composerlint_current_selection;n++)await flush();
 assert.deepEqual(Object.keys(tools).sort(),['composerlint_current_comparison','composerlint_current_edit_context','composerlint_current_selection']);
 const read=()=>tools.composerlint_current_edit_context.execute();
 assert.equal((await read()).workspace_id,null);
 // Pending click must settle before a combined context can return.
 let release;postGate=new Promise(r=>release=r);elements.measures.children[0].handlers.click({shiftKey:false});
 let done=false;const pending=read().then(x=>{done=true;return x;});await flush();assert.equal(done,false);release();postGate=null;
 assert.equal((await pending).selection.selection.start_measure_index,1);
 // Concurrent click between selection and context reads must refuse stale context.
 contextGate=new Promise(r=>release=r);const changing=read();await flush();elements.measures.children[1].handlers.click({shiftKey:false});release();contextGate=null;
 await assert.rejects(changing,/Selection changed/);
 selection.revision++;await assert.rejects(read(),/Selection changed/);await assert.rejects(tools.composerlint_current_selection.execute(),/Selection changed/);selection.revision--;
 console.log('PASS: all three existing tools register without retired DOM; pending clicks settle; concurrent and remote revisions refuse stale targets.');
 // A verified preview expiring restores Original, removes controls and says why.
 workspace={workspace_id:'w1',state:'verified',proposal:{proposal_id:'test',preview_status:'ready',operation:{direction:'up'},target:{part_id:'p1',start_measure_index:1,end_measure_index:2,pitched_segment_count:2}}};
 await timers.shift()();assert.equal(window.composerViewer.version,'proposed');assert.equal(elements['proposal-strip'].hidden,false);
 workspace={workspace_id:null,state:'no_proposal',proposal:null};await timers.shift()();
 assert.equal(window.composerViewer.version,'original');assert.equal(elements['proposal-strip'].hidden,true);assert.match(elements['edit-status'].textContent,/expired/);
 console.log('PASS: proposal expiry restores Original, hides controls and announces expiry.');
})().catch(error=>{console.error(error);process.exitCode=1;});
