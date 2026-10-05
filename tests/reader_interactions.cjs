const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
class Element {
  constructor(tag, ownText = '', classes = []) { this.tagName=tag; this.ownText=ownText; this.classes=classes; this.children=[]; this.attrs={}; this.hidden=false; this.open=false; this.listeners={}; }
  add(child) { child.parentElement=this; this.children.push(child); return child; }
  get textContent() { return this.ownText + this.children.map(x=>x.textContent).join(''); }
  set textContent(value) { this.ownText=value; }
  querySelectorAll(selector) { const descendants=this.children.flatMap(c=>[c,...c.querySelectorAll('*')]); return descendants.filter(c=>selector==='*'||selector==='details'&&c.tagName==='DETAILS'||selector.startsWith('.')&&c.classes.includes(selector.slice(1))); }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  setAttribute(k,v) { this.attrs[k]=v; }
  addEventListener(k,f) { this.listeners[k]=f; }
}
const notes=new Element('DIV'); notes.id='notes';
const timeline=new Element('DIV'); timeline.id='timeline'; timeline.hidden=true;
const chapter=notes.add(new Element('SECTION','',['chapter'])); chapter.id='chapter-1';
const intro=chapter.add(new Element('DIV','Chapter heading',['chapter-overview']));
const guide=intro.add(new Element('DETAILS','Intro keyword'));
const topic=chapter.add(new Element('ARTICLE','Economic explanation',['search-item']));
const supplement=topic.add(new Element('DETAILS','整理补充 Qualification'));
const background=chapter.add(new Element('DETAILS','Background'));
const secondary=background.add(new Element('ARTICLE','Hidden context',['search-item']));
const timelineItem=timeline.add(new Element('ARTICLE','Chronology',['search-item']));
const search=new Element('INPUT'); search.value='';
const empty=new Element('P'); empty.hidden=true;
const toc=new Element('NAV');
const link=toc.add(new Element('A')); link.hash='#chapter-1';
const label=new Element('H2');
const buttons=['notes','timeline'].map(view=>{const b=new Element('BUTTON');b.dataset={view};return b;});
const ids={notes,timeline,search,empty,toc,'toc-label':label};
const document={getElementById:id=>ids[id],querySelectorAll:selector=>{
  if(selector==='[data-view]')return buttons;
  if(selector==='#toc a')return [link];
  const [id,sub]=selector.split(' '); return ids[id.slice(1)].querySelectorAll(sub);
}};
const window={listeners:{},addEventListener(k,f){this.listeners[k]=f;}};
const location={hash:'#chapter-1'};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../assets/reader.js'),'utf8'),{document,window,location});
assert.equal(buttons[0].attrs['aria-pressed'],'true');
buttons[1].listeners.click(); assert.equal(notes.hidden,true); assert.equal(timeline.hidden,false); assert.equal(toc.hidden,true);
search.value='Chronology'; search.listeners.input(); assert.equal(timelineItem.hidden,false); assert.equal(empty.hidden,true);
buttons[0].listeners.click(); search.value='Hidden context'; search.listeners.input(); assert.equal(background.open,true); assert.equal(secondary.hidden,false); assert.equal(topic.hidden,true);
search.value=''; search.listeners.input(); assert.equal(background.open,false); assert.equal(topic.hidden,false);
search.value='Qualification'; search.listeners.input(); assert.equal(supplement.open,true);
search.value='Intro keyword'; search.listeners.input(); assert.equal(supplement.open,false); assert.equal(guide.open,true); assert.equal(topic.hidden,false);
search.value='not present'; search.listeners.input(); assert.equal(empty.hidden,false); assert.equal(chapter.hidden,true);
search.value=''; search.listeners.input(); assert.equal(empty.hidden,true); assert.equal(chapter.hidden,false);
background.open=true; search.value='Hidden context'; search.listeners.input(); search.value=''; search.listeners.input(); assert.equal(background.open,true);
location.hash='#segment-1'; window.listeners.hashchange(); assert.equal(timeline.hidden,false);
console.log('Reader interaction checks passed: view switches, search, folded content, restored open state, deep links.');
