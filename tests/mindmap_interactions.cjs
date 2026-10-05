// Exercise the exported controller with a small DOM model; this is not browser visual verification.
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const assert = require('node:assert/strict');
class Element {
  constructor() {
    this.listeners={}; this.style={}; this.dataset={}; this.attrs={}; this.children=[];
    this.hidden=false; this.textContent=''; this.classes=new Set();
    this.classList={add:v=>this.classes.add(v),remove:v=>this.classes.delete(v)};
  }
  addEventListener(k,f){this.listeners[k]=f;}
  setAttribute(k,v){this.attrs[k]=v;}
  hasAttribute(k){return k in this.attrs;}
  replaceChildren(){this.children=[];}
  append(n){this.children.push(n);}
  focus(){this.focused=true;}
  closest(s){return s==='.map-node' && this.dataset.id ? this : null;}
}
const ids=Object.fromEntries(['map-data','viewport','canvas','zoom','note','note-title','note-time',
  'note-key','note-body','note-link','fit','plus','minus','close','focus','status','png'].map(id=>[id,new Element()]));
const plan={nodes:[{id:'root',x:500,y:500,width:244,height:84,depth:0,title:'Root',body:'',time:'',takeaway:''},
  {id:'n-1',x:810,y:400,width:244,height:74,depth:1,chapter:1,title:'Chapter',body:'Intro',time:'00:01:00',takeaway:'Key'},
  {id:'n-1-1',x:1120,y:400,width:244,height:74,depth:2,chapter:1,title:'Topic',body:'<img>\n\nSecond paragraph',time:'00:01:02',takeaway:'Conclusion'}]};
ids['map-data'].textContent=JSON.stringify(plan);
ids.viewport.clientWidth=1200; ids.viewport.clientHeight=800;
ids.viewport.getBoundingClientRect=()=>({left:0,top:0}); ids.viewport.setPointerCapture=()=>{};
const svg=new Element(), elements=plan.nodes.map(n=>{
  const e=new Element(); e.dataset={id:n.id,depth:n.depth}; e.attrs.tabindex='0'; return e;
});
svg.querySelectorAll=s=>s==='.selected' ? elements.filter(e=>e.classes.has('selected')) : elements;
ids.canvas.querySelector=()=>svg;
const document={listeners:{},getElementById:id=>ids[id],createElement:()=>new Element(),addEventListener(k,f){this.listeners[k]=f;}};
const window={addEventListener(){}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../assets/mindmap.js'),'utf8'),{document,window});
const click=(id)=>ids[id].listeners.click({currentTarget:ids[id]});
const original=ids.canvas.style.transform;
click('plus'); assert.notEqual(ids.canvas.style.transform,original);
click('fit'); assert.equal(ids.canvas.style.transform,original);
svg.listeners.click({target:elements[2]});
assert.equal(ids.note.hidden,false); assert.equal(ids['note-title'].textContent,'Topic');
assert.equal(ids['note-body'].children.length,2);
assert.equal(ids['note-body'].children[0].textContent,'<img>');
assert.equal(ids['note-link'].href,'reading.html#chapter-1');
click('close'); assert.equal(ids.note.hidden,true); assert.equal(elements[2].focused,true);
click('focus'); assert.equal(elements[2].style.display,'none'); assert.equal(elements[2].attrs.tabindex,'-1');
assert.equal(elements[1].style.display,''); assert.equal(ids.focus.textContent,'展开全部');
click('focus'); assert.equal(elements[2].style.display,'');
svg.listeners.keydown({target:elements[2],key:'Enter',preventDefault(){}});
assert.equal(ids.note.hidden,false);
document.listeners.keydown({key:'Escape'}); assert.equal(ids.note.hidden,true);
const blank=new Element();
ids.viewport.listeners.pointerdown({button:0,target:blank,clientX:100,clientY:100,pointerId:1});
ids.viewport.listeners.pointermove({clientX:130,clientY:120});
assert.notEqual(ids.canvas.style.transform,original); ids.viewport.listeners.pointerup();
// A fresh click after dragging must work immediately.
ids.viewport.listeners.pointerdown({button:0,target:elements[2]});
svg.listeners.click({target:elements[2]}); assert.equal(ids.note.hidden,false);
click('close');
ids.png.listeners.click().then(()=>{
  assert.match(ids.status.textContent,/SVG/); assert.equal(ids.png.disabled,false);
  console.log('Mind-map interaction checks passed: zoom, fit, drag, keyboard, notes, main branches, PNG error fallback.');
});
