/* Injected into a built page by browser.test.js. Reads a list of steps from
   location.hash — each step a list of [action, selector, value] — applies
   them, and after every step appends a snapshot of what the page shows to
   <pre id="probe">. The test parses that back out of --dump-dom. */
(function(){
  const snaps=[], err=[];
  window.addEventListener("error",e=>err.push(String(e.message)));
  const $=s=>document.querySelector(s);
  const txt=s=>{const e=$(s);return e?e.textContent.trim():null;};
  const pts=p=>(p.getAttribute("d").match(/[ML]/g)||[]).length;
  const snapshot=()=>{
    const sels={};
    for(const id of ["boardsel","basesel","basewin","winsel","thrsel"]){
      const s=$("#"+id); sels[id]={value:s.value, disabled:s.disabled, options:[...s.options].map(o=>o.value), labels:[...s.options].map(o=>o.textContent)};
    }
    const facts={}; document.querySelectorAll("#tr-facts > div").forEach(d=>facts[d.querySelector(".lbl").textContent]=d.querySelector("b").textContent);
    const vd={}; document.querySelectorAll("#vd-stats > div").forEach(d=>vd[d.querySelector(".lbl").textContent]=d.querySelector("b").textContent);
    const chips={}; document.querySelectorAll("#chips button").forEach(b=>chips[b.textContent.replace(/[\d,]+$/,"").trim()]={n:b.querySelector(".n").textContent,title:b.title});
    const ov=$("#ov");
    const spark=$(".vrow svg path");
    let json=null; try{ json=JSON.parse(txt("#vd-json")); }catch(e){}
    return {
      sels,
      ro:{run:txt("#ro-run"), report:txt("#ro-report"), suite:txt("#ro-suite")},
      roLabels:[...document.querySelectorAll(".readout .lbl")].map(l=>l.textContent),
      plotLabels:[...$("#plot").querySelectorAll("text")].map(t=>t.textContent),
      build:txt(".mast .build"),
      vdLabels:[...document.querySelectorAll(".verdict .lbl")].map(l=>l.textContent),
      ovsub:txt("#ov-sub"),
      bars:[...ov.querySelectorAll("rect")].filter(r=>/--crit|--good/.test(r.getAttribute("fill")||"")).length,
      ovDates:[...ov.querySelectorAll("text")].filter(t=>/^[A-Z][a-z]{2} \d/.test(t.textContent)).length,
      ovBand:[...ov.querySelectorAll("text")].filter(t=>t.textContent==="baseline window").length,
      plotPaths:[...$("#plot").querySelectorAll("path")].map(pts),
      // a NaN in a path or a rect is not an error to the browser — it just draws nothing
      nan:["#plot path","#plot circle","#plot text","#bplot path","#bplot circle","#bplot text","#ov rect","#ov text","#ovheat rect"].filter(q=>
        [...document.querySelectorAll(q)].some(e=>[...e.attributes].some(at=>/NaN|Infinity/.test(at.value)))),
      plotBand:[...$("#plot").querySelectorAll("text")].filter(t=>t.textContent==="BASELINE WINDOW").length,
      boardPaths:[...$("#bplot").querySelectorAll("path")].map(pts),
      tableRows:document.querySelectorAll("#tablewrap tbody tr").length,
      tableLatest:(r=>r?[...r.children].map(td=>td.textContent):null)($("#tablewrap tbody tr")),
      tableDates:[...document.querySelectorAll("#tablewrap tbody tr > td:first-child")].map(td=>td.textContent),
      sparkPts:spark?pts(spark):null,
      catCount:txt("#cat-count"),
      chips,
      tr:{name:txt("#tr-name"), sub:txt("#tr-sub"), metric:txt("#tr-metric"), now:txt("#tr-now"), delta:txt("#tr-delta"), fam:txt("#tr-fam")},
      facts, vd, json,
      verdict:(txt("#vd-head")||"").replace(/\s+/g," "),
      // the tooltip last shown by a hover step
      tip:(txt("#tip")||"").replace(/\s+/g," "),
      legend:[...document.querySelectorAll("#legend span")].map(s=>s.textContent),
      view:["chart","boards","table"].find(v=>$("#v-"+v).getAttribute("aria-pressed")==="true"),
      // what is actually displayed, not what is pressed: the plots are <svg>,
      // and an svg's `hidden` property is not the attribute
      shown:["plot","bplot","tablewrap"].filter(id=>getComputedStyle($("#"+id)).display!=="none")
    };
  };
  const act=([what,sel,val])=>{
    const e=$(sel); if(!e) throw new Error("no element "+sel);
    if(what==="select"){ e.value=val; e.dispatchEvent(new Event("change")); }
    else if(what==="click"){ e.click(); }
    else if(what==="input"){ e.value=val; e.dispatchEvent(new Event("input")); }
    // a pointer at a fraction of the element's width (0 = left edge, 1 = right), mid-height
    else if(what==="hover"){ const bb=e.getBoundingClientRect();
      e.dispatchEvent(new PointerEvent("pointermove",{clientX:bb.left+bb.width*(val===undefined?1:+val),clientY:bb.top+bb.height/2,bubbles:true})); }
    else throw new Error("unknown action "+what);
  };
  const done=()=>{ const pre=document.createElement("pre"); pre.id="probe";
    pre.textContent=JSON.stringify({snaps, errors:err}); document.body.appendChild(pre); };
  // steps run with a pause between them: the search box debounces its input
  // by 120 ms, and --virtual-time-budget lets those timers fire
  let steps=[];
  try{ steps=JSON.parse(decodeURIComponent(location.hash.slice(1)||"%5B%5B%5D%5D")); }
  catch(e){ err.push("THROWN: "+e.message); }
  let i=0;
  const next=()=>{
    if(i>=steps.length) return done();
    try{ for(const a of steps[i]) act(a); }catch(e){ err.push("THROWN: "+e.message); return done(); }
    i++;
    setTimeout(()=>{ try{ snaps.push(snapshot()); }catch(e){ err.push("THROWN: "+e.message); } next(); }, 250);
  };
  next();
})();
