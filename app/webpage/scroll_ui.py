"""Shared overflow indicators and scrollbar behavior for dashboard and settings."""

SCROLL_CSS = '''
.horizontal-scroll{max-width:100%;min-width:0;overflow-x:auto;overscroll-behavior-x:contain;padding-bottom:0;scrollbar-width:thin;scrollbar-color:#536987 #111a2d}
.horizontal-scroll.has-overflow{padding-bottom:16px}
.horizontal-scroll::-webkit-scrollbar{height:10px}
.horizontal-scroll::-webkit-scrollbar-track{background:#111a2d;border-radius:6px}
.horizontal-scroll::-webkit-scrollbar-thumb{background:#536987;border:2px solid #111a2d;border-radius:6px}
.horizontal-scroll::-webkit-scrollbar-thumb:hover{background:#8bafff}
.horizontal-scroll::-webkit-scrollbar-thumb:active{background:#b0c6ff}
.horizontal-scroll.has-overflow:hover,.horizontal-scroll.is-scrolling{scrollbar-color:#8bafff #111a2d}
.horizontal-scroll.is-scrolling::-webkit-scrollbar-thumb{background:#8bafff}
.horizontal-scroll:focus-visible{outline:2px solid var(--accent);outline-offset:4px}

.horizontal-scroll:not(.tablewrap).has-more-right{
 -webkit-mask-image:linear-gradient(to right,#000 calc(100% - 32px),rgba(0,0,0,.18));
 mask-image:linear-gradient(to right,#000 calc(100% - 32px),rgba(0,0,0,.18));
}
'''

SCROLL_SCRIPT = '''
<script data-ui-scroll-areas>
(()=>{
 const areas=[...document.querySelectorAll(".horizontal-scroll")];
 const timers=new WeakMap();
 function update(area){
  const overflow=area.clientWidth>0&&area.scrollWidth>area.clientWidth+1;
  area.classList.toggle("has-overflow",overflow);
  area.classList.toggle("has-more-right",overflow&&area.scrollLeft+area.clientWidth<area.scrollWidth-2);
 }
 const observer=new ResizeObserver(()=>areas.forEach(update));
 areas.forEach(area=>{
  observer.observe(area);
  if(area.firstElementChild)observer.observe(area.firstElementChild);
  area.addEventListener("scroll",()=>{
   update(area);
   area.classList.add("is-scrolling");
   clearTimeout(timers.get(area));
   timers.set(area,setTimeout(()=>area.classList.remove("is-scrolling"),180));
  },{passive:true});
  update(area);
 });
 document.querySelectorAll("details.panel").forEach(panel=>panel.addEventListener("toggle",()=>areas.forEach(update)));
})();
</script>
'''

SCROLL_CSS += '\ndialog[open]{overscroll-behavior:contain}\n'
SCROLL_SCRIPT += '''
<script data-ui-dialog-lock>
(()=>{
 const dialogs=[...document.querySelectorAll('dialog')];
 const body=document.body,root=document.documentElement;
 let saved=null;
 function update(){
  const open=dialogs.some(dialog=>dialog.open);
  if(open&&!saved){
   const gutter=Math.max(0,window.innerWidth-root.clientWidth);
   const padding=parseFloat(window.getComputedStyle(body).paddingRight)||0;
   saved={x:window.scrollX,y:window.scrollY,body:body.style.cssText,root:root.style.cssText};
   root.style.overflow='hidden';root.style.overscrollBehavior='none';
   body.style.position='fixed';body.style.top=-saved.y+'px';body.style.left=-saved.x+'px';
   body.style.width='100%';body.style.overflow='hidden';body.style.paddingRight=padding+gutter+'px';
  }else if(!open&&saved){
   const previous=saved;saved=null;
   body.style.cssText=previous.body;root.style.cssText=previous.root;
   const behavior=root.style.scrollBehavior;root.style.scrollBehavior='auto';
   window.scrollTo(previous.x,previous.y);root.style.scrollBehavior=behavior;
  }
 }
 const observer=new MutationObserver(update);
 dialogs.forEach(dialog=>observer.observe(dialog,{attributes:true,attributeFilter:['open']}));
 update();
})();
</script>
'''
