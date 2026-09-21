// kuro-ai/frontend/src/App.jsx — Final Form (ref-driven sizing)
import { useState, useEffect, useRef } from 'react'

// ── Inject styles once on load ────────────────────────────────
const CSS = `
html,body,#root{margin:0;padding:0;background:transparent!important;overflow:hidden;width:100vw;height:100vh;-webkit-user-select:none;user-select:none}
.k-host{position:fixed;top:0;left:0;width:100vw;height:100vh;display:flex;justify-content:center;align-items:flex-start;padding-top:2px;pointer-events:none}
.k-badges{position:fixed;top:10px;right:12px;display:flex;flex-direction:column;gap:8px;z-index:50;pointer-events:none}
.k-badge{display:flex;align-items:center;gap:7px;padding:6px 10px;border-radius:999px;font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:11px;font-weight:700;letter-spacing:.02em;backdrop-filter:blur(10px) saturate(150%);-webkit-backdrop-filter:blur(10px) saturate(150%);border:1px solid rgba(255,255,255,.2);background:rgba(8,8,16,.55);color:#f1ecff;box-shadow:0 6px 14px rgba(0,0,0,.28),inset 0 1px 0 rgba(255,255,255,.12);animation:k-badge-in .28s ease both}
.k-badge-ok{border-color:rgba(74,255,164,.45);box-shadow:0 6px 14px rgba(0,0,0,.28),0 0 12px rgba(74,255,164,.25),inset 0 1px 0 rgba(255,255,255,.12)}
.k-badge-bad{border-color:rgba(255,106,106,.5);box-shadow:0 6px 14px rgba(0,0,0,.28),0 0 12px rgba(255,106,106,.28),inset 0 1px 0 rgba(255,255,255,.12)}
.k-badge-dot{width:8px;height:8px;border-radius:50%}
.k-badge-ok .k-badge-dot{background:#4aff98;box-shadow:0 0 8px rgba(74,255,164,.7)}
.k-badge-bad .k-badge-dot{background:#ff6a6a;box-shadow:0 0 8px rgba(255,106,106,.7)}
@keyframes k-txt-in{from{opacity:0;transform:translateZ(0) translateY(4px)}to{opacity:1;transform:translateZ(0) translateY(0)}}
.k-cancel-btn{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:rgba(255,255,255,0.1);color:#fff;border:none;border-radius:50%;width:22px;height:22px;font-size:12px;font-weight:bold;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background 0.2s, transform 0.2s;pointer-events:auto;z-index:20;line-height:1}
.k-cancel-btn:hover{background:rgba(255,255,255,0.25);transform:translateY(-50%) scale(1.1)}
.k-cancel-btn:active{transform:translateY(-50%) scale(0.95)}
@keyframes k-badge-in{from{opacity:0;transform:translateY(-6px) scale(.96)}to{opacity:1;transform:translateY(0) scale(1)}}
.k-pill{position:relative;border-radius:40px;overflow:visible;pointer-events:auto;transition:width .65s cubic-bezier(.34,1.35,.64,1),height .65s cubic-bezier(.34,1.35,.64,1);border:1px solid rgba(214,155,255,.34);background:linear-gradient(180deg,rgba(32,14,50,.2) 0%,rgba(12,8,30,.15) 52%,rgba(8,6,18,.24) 100%);backdrop-filter:blur(15px) saturate(160%);-webkit-backdrop-filter:blur(15px) saturate(160%);box-shadow:inset 0 1px 0 rgba(255,255,255,.11),inset 0 -10px 16px rgba(7,5,18,.18),0 8px 20px rgba(0,0,0,.3)}
.k-clip{position:absolute;inset:0;border-radius:40px;overflow:hidden}
.k-halo{position:absolute;inset:-2px;border-radius:42px;pointer-events:none;transition:box-shadow .4s,opacity .5s;mix-blend-mode:screen}
.k-text{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;z-index:2;pointer-events:none}
.k-row{display:flex;align-items:center;gap:8px;padding:0 14px;width:100%}
.k-row-listening{justify-content:center}
.k-row-listening .k-stk{flex:0 0 auto}
.k-stk{display:flex;flex-direction:column;gap:1px;flex:1;min-width:0}
.k-lbl{font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:11.5px;font-weight:700;color:#fff;letter-spacing:.04em;white-space:nowrap;transition:text-shadow .4s}
.k-sub{font-family:-apple-system,sans-serif;font-size:9px;font-weight:500;color:rgba(255,255,255,.7);white-space:nowrap;transition:text-shadow .4s}
.k-logo{width:17px;height:17px;position:relative;flex-shrink:0;transform:translateX(-1px)}
.k-logo-img{width:100%;height:100%;object-fit:contain;filter:drop-shadow(0 0 6px rgba(191,0,255,.9));transition:filter .4s ease}
.k-wave{display:flex;align-items:center;gap:2px;height:16px;flex-shrink:0}
.k-bar{width:2px;border-radius:2px;height:3px;transition:background .4s,box-shadow .4s}
.k-ptrack{height:2px;background:rgba(148,0,211,.2);border-radius:2px;margin-top:5px;overflow:hidden;width:100%}
.k-pfill{height:100%;border-radius:2px;transition:background .4s}
.k-chk{width:13px;height:13px;border-radius:50%;border:1.5px solid currentColor;display:flex;align-items:center;justify-content:center;font-size:7px;flex-shrink:0;transition:color .4s,border-color .4s,box-shadow .4s}
@keyframes k-enter{0%{opacity:0;transform:translateY(-36px)}60%{transform:translateY(3px)}80%{transform:translateY(-1px)}100%{opacity:1;transform:translateY(0)}}
@keyframes k-exit{to{opacity:0;transform:translateY(-28px)}}
@keyframes k-blink{0%,100%{opacity:1}50%{opacity:.2}}
@keyframes k-spin{to{transform:rotate(360deg)}}
@keyframes k-pulse{0%,100%{transform:scale(1)}50%{transform:scale(.72);opacity:.35}}
@keyframes k-w1{0%,100%{height:2px}50%{height:13px}}
@keyframes k-w2{0%,100%{height:5px}50%{height:2px}}
@keyframes k-w3{0%,100%{height:9px}50%{height:3px}}
.k-entering{animation:k-enter .55s cubic-bezier(.34,1.35,.64,1) both}
.k-exiting{animation:k-exit .32s ease-in both}
.k-blink .k-logo-img{animation:k-blink 1.3s ease-in-out infinite}
.k-spin{animation:k-spin 1.4s linear infinite}
.k-pulse{animation:k-pulse .95s ease-in-out infinite}
.k-w1{animation:k-w1 .72s ease-in-out infinite}
.k-w2{animation:k-w2 .88s ease-in-out infinite}
.k-w3{animation:k-w3 .61s ease-in-out infinite}
`

// ── Sizes ─────────────────────────────────────────────────────
const SZ = {
  idle:      { w:92,  h:32 },
  listening: { w:272, h:50 },
  thinking:  { w:188, h:47 },
  acting:    { w:308, h:54 },
  done:      { w:210, h:44 },
}

const ACTIVE = new Set(['listening','thinking','acting','done'])

function normState(v){
  const s=(v||'idle').toLowerCase()
  if(s==='speaking') return 'acting'
  return SZ[s]?s:'idle'
}

// ── Color system ──────────────────────────────────────────────
const PAL=[{r:191,g:0,b:255},{r:148,g:0,b:211},{r:126,g:50,b:133}]
const CC={idx:0,blend:0,t:0}
function tickCol(dt){
  CC.t+=dt; const p=.7,ph=CC.t%p
  CC.blend=ph<.55?0:(ph-.55)/.15
  if(CC.t>=p){CC.t=CC.t%p;if(CC.blend>=.99){CC.idx=(CC.idx+1)%3;CC.blend=0}}
}
function rgb(){
  const a=PAL[CC.idx],b=PAL[(CC.idx+1)%3],t=CC.blend
  return{r:Math.round(a.r+(b.r-a.r)*t),g:Math.round(a.g+(b.g-a.g)*t),b:Math.round(a.b+(b.b-a.b)*t)}
}
function rc(a){const{r,g,b}=rgb();return`rgba(${r},${g},${b},${a})`}

function syncCSS(st){
  const{r,g,b}=rgb(),s=`rgb(${r},${g},${b})`
  const tg=`0 0 6px rgba(255,255,255,.9),0 0 14px rgba(${r},${g},${b},.75),0 0 28px rgba(${r},${g},${b},.4)`
  const sg=`0 0 4px rgba(255,255,255,.85),0 0 10px rgba(${r},${g},${b},.5),0 0 18px rgba(${r},${g},${b},.24)`
  const dg=`0 0 5px rgba(${r},${g},${b},.8),0 0 12px rgba(${r},${g},${b},.4)`
  const bg=`0 0 4px rgba(${r},${g},${b},.6)`
  document.querySelectorAll('.k-lbl').forEach(e=>e.style.textShadow=tg)
  document.querySelectorAll('.k-sub').forEach(e=>e.style.textShadow=sg)
  document.querySelectorAll('.k-logo-img').forEach(e=>{e.style.filter=`drop-shadow(0 0 6px rgba(${r},${g},${b},.9)) drop-shadow(0 0 14px rgba(${r},${g},${b},.35))`})
  document.querySelectorAll('.k-bar').forEach(e=>{e.style.background=s;e.style.boxShadow=bg})
  document.querySelectorAll('.k-chk').forEach(e=>{e.style.color=s;e.style.borderColor=s;e.style.boxShadow=dg})
  const h=document.getElementById('k-halo')
  if(h&&st!=='idle')h.style.boxShadow=`0 0 0 1px rgba(${r},${g},${b},.55),0 0 18px 4px rgba(${r},${g},${b},.22)`
  const pb=document.getElementById('k-pbar')
  if(pb)pb.style.background=rc(.65)
}

// ── Inner HTML ────────────────────────────────────────────────
const LOGO=(c='')=>`<div class="k-logo ${c}"><img src="/kuro-icon.svg" alt="Kuro" class="k-logo-img" /></div>`
function html(st,at,rt){
  if(st==='idle')    return`<div class="k-row">${LOGO()}<span class="k-lbl">Kuro</span></div>`
  if(st==='listening')return`<div class="k-row k-row-listening">${LOGO('k-blink')}<div class="k-wave"><div class="k-bar k-w1"></div><div class="k-bar k-w2"></div><div class="k-bar k-w3"></div><div class="k-bar k-w1" style="animation-delay:.09s"></div><div class="k-bar k-w2" style="animation-delay:.05s"></div><div class="k-bar k-w3" style="animation-delay:.14s"></div><div class="k-bar k-w1" style="animation-delay:.19s"></div></div><div class="k-stk"><span class="k-lbl">Listening</span><span class="k-sub">speak now</span></div></div>`
  if(st==='thinking') return`<div class="k-row">${LOGO('k-spin')}<div class="k-stk"><span class="k-lbl">Thinking...</span><span class="k-sub">processing</span></div></div>`
  if(st==='acting')   return`<div class="k-row">${LOGO('k-pulse')}<div class="k-stk" style="flex:1"><span class="k-lbl">${at}</span><div class="k-ptrack"><div id="k-pbar" class="k-pfill"></div></div></div></div>`
  if(st==='done')     return`<div class="k-row"><div class="k-chk">✓</div><div class="k-stk"><span class="k-lbl">Done</span><span class="k-sub">${rt}</span></div></div>`
  return''
}

// ── Particle factories ────────────────────────────────────────
function Disk(W,H,getT,getSt){
  let a=Math.random()*Math.PI*2,r=W*(.14+Math.random()*.28)
  let spd=(.006+Math.random()*.01)*(Math.random()<.5?1:-1)
  let tilt=.26+Math.random()*.16,sz=.53+Math.random()*1.68,life=Math.random(),ml=.5+Math.random()*.5
  const re=()=>{a=Math.random()*Math.PI*2;r=W*(.14+Math.random()*.28);spd=(.006+Math.random()*.01)*(Math.random()<.5?1:-1);tilt=.26+Math.random()*.16;sz=.53+Math.random()*1.68;life=Math.random();ml=.5+Math.random()*.5}
  return{get a(){return a},get tilt(){return tilt},
    tick(){a+=spd*(1+.7/(r/Math.max(W,1)*.4+.1));life+=.004;if(life>ml)re()},
    draw(ctx){const ex=Math.cos(a)*r,ey=Math.sin(a)*r*tilt,x=W/2+ex,y=H/2+ey,fade=Math.sin((life/ml)*Math.PI),depth=(ey/(H/2)+1)*.5,al=fade*depth*.85;if(al<.01)return;ctx.beginPath();ctx.arc(x,y,sz,0,Math.PI*2);ctx.fillStyle=rc(al);ctx.fill();ctx.beginPath();ctx.arc(x,y,sz*2,0,Math.PI*2);ctx.fillStyle=rc(al*.15);ctx.fill()}}
}
function Flow(i,n,W,H,getT,getSt){
  let ph=getT()+i*(Math.PI*2/n),life=0,ml=.8+Math.random()*.6
  const re=()=>{ph=getT()+i*(Math.PI*2/n);life=0;ml=.8+Math.random()*.6}
  return{tick(){life+=.005;if(life>ml)re()},
    draw(ctx){const st=getSt(),p=ph+getT()*.55,fade=Math.sin((life/ml)*Math.PI),x0=W/2+Math.cos(p)*W*.42,y0=H/2+Math.sin(p*.72)*H*.4,x1=W/2+Math.cos(p+1.3)*W*.36,y1=H/2+Math.sin(p*.85+.9)*H*.38,mx=W/2+Math.cos(p+.55)*W*.18,my=H/2+Math.sin(p*1.1)*H*.22,al=st==='thinking'?.1:st==='listening'?.07:.05,g=ctx.createLinearGradient(x0,y0,x1,y1);g.addColorStop(0,rc(al*fade));g.addColorStop(.5,rc(al*fade*.5));g.addColorStop(1,'transparent');ctx.beginPath();ctx.moveTo(x0,y0);ctx.quadraticCurveTo(mx,my,x1,y1);ctx.strokeStyle=g;ctx.lineWidth=.7+fade*.5;ctx.stroke()}}
}
function Dust(W,H,getT,getSt){
  let x=Math.random()*W,y=Math.random()*H,vx=(Math.random()-.5)*.25,vy=(Math.random()-.5)*.18,life=Math.random(),ml=.5+Math.random()*.5,sz=.42+Math.random()*1.68
  const re=()=>{x=Math.random()*W;y=Math.random()*H;vx=(Math.random()-.5)*.25;vy=(Math.random()-.5)*.18;life=Math.random();ml=.5+Math.random()*.5;sz=.42+Math.random()*1.68}
  return{tick(){const st=getSt();life+=.005;if(life>ml){re();return}const T=getT();x+=vx+(st==='thinking'?Math.cos(T+y*.12)*.18:0);y+=vy+(st==='listening'?Math.sin(T+x*.09)*.12:0);if(x<0)x=W;if(x>W)x=0;if(y<0)y=H;if(y>H)y=0},
    draw(ctx){const fade=Math.sin((life/ml)*Math.PI)*.72;ctx.beginPath();ctx.arc(x,y,sz,0,Math.PI*2);ctx.fillStyle=rc(fade);ctx.fill()}}
}

// ── App ───────────────────────────────────────────────────────
export default function App(){
  const[state,setState]=useState('idle')
  const[at,setAt]=useState('On it...')
  const[rt,setRt]=useState('complete')
  const[anim,setAnim]=useState('')
  const[badges,setBadges]=useState([])

  const pillRef=useRef(null)    // the pill div
  const clipRef=useRef(null)    // clipping container for canvas bitmap
  const canvasRef=useRef(null)  // the canvas element
  const textRef=useRef(null)    // text overlay div
  const haloRef=useRef(null)    // halo div
  const stRef=useRef('idle')
  const prevRef=useRef('idle')
  const rafRef=useRef(null)
  const T=useRef(0)
  const ts0=useRef(null)
  const pools=useRef({disks:[],flows:[],dusts:[]})
  const pb=useRef({w:0,dir:1})
  const dtimer=useRef(null)
  const wsRef=useRef(null)
  const wsIdxRef=useRef(0)
  const badgeTimerRef=useRef(null)

  const appLabel=(app)=>{
    const a=(app||'system').toLowerCase()
    if(a==='spotify') return 'Spotify'
    if(a==='whatsapp') return 'WhatsApp'
    if(a==='youtube') return 'YouTube'
    if(a==='browser') return 'Browser'
    return a.charAt(0).toUpperCase()+a.slice(1)
  }

  const pushBadge=(m)=>{
    const app=(m.app||'system').toLowerCase()
    const ok=!!m.success
    const text=(m.text||m.message||'Done').toString()
    const id=`${Date.now()}-${Math.random().toString(16).slice(2)}`
    const expireAt=Date.now()+3200
    const badge={id,app,ok,text,label:appLabel(app),expireAt}
    setBadges((prev)=>[badge,...prev].slice(0,5))
  }

  // Inject CSS once
  useEffect(()=>{
    if(document.getElementById('k-css'))return
    const el=document.createElement('style')
    el.id='k-css';el.textContent=CSS;document.head.appendChild(el)
  },[])

  // Badge sweep — remove badges past their expireAt time every 500ms.
  // This replaces the previous single badgeTimerRef approach which broke
  // when multiple badges arrived within the same 3.2s window.
  useEffect(()=>{
    const iv=setInterval(()=>{
      const now=Date.now()
      setBadges((prev)=>{
        const next=prev.filter((b)=>b.expireAt>now)
        return next.length===prev.length?prev:next
      })
    },500)
    return()=>clearInterval(iv)
  },[])

  function buildPools(W,H){
    const gT=()=>T.current,gS=()=>stRef.current
    pools.current={
      disks:Array.from({length:70},()=>Disk(W,H,gT,gS)),
      flows:Array.from({length:10},(_,i)=>Flow(i,10,W,H,gT,gS)),
      dusts:Array.from({length:55},()=>Dust(W,H,gT,gS)),
    }
  }

  // Resize canvas + pill immediately — called from state effect AND frame loop
  function applySize(w,h){
    const canvas=canvasRef.current
    if(!canvas)return
    // Set bitmap size
    canvas.width=w
    canvas.height=h
    // Remove ALL CSS sizing — let the bitmap define display size
    canvas.removeAttribute('style')
    canvas.style.display='block'
    canvas.style.width=w+'px'
    canvas.style.height=h+'px'

    // Resize the pill container
    const pill=pillRef.current
    if(pill){
      pill.style.width=w+'px'
      pill.style.height=h+'px'
    }

    const clip=clipRef.current
    if(clip){
      clip.style.width=w+'px'
      clip.style.height=h+'px'
    }
  }

  // State change handler
  useEffect(()=>{
    const sz=SZ[state]||SZ.idle
    stRef.current=state
    pb.current={w:0,dir:1}

    // Resize immediately
    applySize(sz.w,sz.h)
    buildPools(sz.w,sz.h)

    // Update text
    if(textRef.current){
      textRef.current.innerHTML=html(state,at,rt)
      requestAnimationFrame(()=>syncCSS(state))
    }

    // Halo
    if(haloRef.current)
      haloRef.current.style.opacity=state==='idle'?'0':'1'

    // Entry/exit anim
    const wasIdle=prevRef.current==='idle',active=state!=='idle'
    const goIdle=state==='idle'&&prevRef.current==='done'
    if(wasIdle&&active){setAnim('k-entering');setTimeout(()=>setAnim(''),600)}
    else if(goIdle){setAnim('k-exiting');setTimeout(()=>setAnim(''),400)}
    prevRef.current=state

    window.electronAPI?.setWindowSize({width:sz.w+40,height:sz.h+40})
    window.electronAPI?.setInteractive(ACTIVE.has(state))
  },[state,at,rt])

  // Canvas render loop — mount once
  useEffect(()=>{
    const canvas=canvasRef.current
    if(!canvas)return

    // Hard-set initial size
    applySize(92,32)
    buildPools(92,32)

    const ctx=canvas.getContext('2d')

    function lensRing(w,h,r,alpha){
      const t=T.current
      for(let i=0;i<3;i++){
        const rx=r*(1+Math.sin(t*.75+i*2.1)*.035),ry=r*.3*(1+Math.cos(t*.6+i*1.8)*.04)
        ctx.beginPath();ctx.ellipse(w/2,h/2,Math.max(rx,.5),Math.max(ry,.5),0,0,Math.PI*2)
        ctx.strokeStyle=rc(alpha*(.5-i*.12));ctx.lineWidth=.9-i*.2;ctx.stroke()
      }
    }
    function jet(w,h,alpha){
      const t=T.current
      for(const s of[-1,1]){
        const tx=w/2+s*w*.46,g=ctx.createLinearGradient(w/2,h/2,tx,h/2)
        g.addColorStop(0,rc(alpha*.42));g.addColorStop(1,'transparent')
        ctx.beginPath();ctx.moveTo(w/2,h/2-1.2)
        ctx.quadraticCurveTo(w/2+s*w*.22,h/2+Math.sin(t*1.1)*h*.14,tx,h/2+Math.sin(t*.85+s)*h*.04)
        ctx.lineTo(tx,h/2-Math.sin(t*.85+s)*h*.04)
        ctx.quadraticCurveTo(w/2+s*w*.22,h/2-Math.sin(t*1.1)*h*.14,w/2,h/2+1.2)
        ctx.closePath();ctx.fillStyle=g;ctx.fill()
      }
    }
    function glass(w,h){
      const t=T.current
      for(let i=0;i<5;i++){
        const spd=.08+i*.06,off=i*1.25
        const rx=w*(.08+i*.055)+Math.sin(t*spd+off)*w*.025
        const ry=h*(.14+i*.06)+Math.cos(t*spd*1.3+off)*h*.03
        ctx.beginPath();ctx.ellipse(w/2,h/2,Math.max(rx,.5),Math.max(ry,.5),t*.04+i*.62,0,Math.PI*2)
        ctx.strokeStyle=rc(.03-i*.004);ctx.lineWidth=.8;ctx.stroke()
      }
      for(let i=0;i<2;i++){
        const cx=w/2+Math.sin(t*.38+i*Math.PI)*w*.14,cy=h/2+Math.cos(t*.28+i*Math.PI*.7)*h*.18
        const cg=ctx.createRadialGradient(cx,cy,0,cx,cy,w*.18)
        cg.addColorStop(0,'rgba(255,255,255,.04)');cg.addColorStop(.5,rc(.025));cg.addColorStop(1,'transparent')
        ctx.fillStyle=cg;ctx.fillRect(0,0,w,h)
      }
      const sg=ctx.createLinearGradient(0,0,0,h*.5)
      sg.addColorStop(0,'rgba(255,255,255,.07)');sg.addColorStop(.25,'rgba(255,255,255,.022)');sg.addColorStop(.6,'rgba(255,255,255,.006)');sg.addColorStop(1,'transparent')
      ctx.fillStyle=sg;ctx.fillRect(0,0,w,h)
      const bg=ctx.createLinearGradient(0,h*.6,0,h)
      bg.addColorStop(0,'transparent');bg.addColorStop(1,'rgba(0,0,8,.35)')
      ctx.fillStyle=bg;ctx.fillRect(0,0,w,h)
      const lv=ctx.createLinearGradient(0,0,w*.28,0)
      lv.addColorStop(0,'rgba(0,0,0,.2)');lv.addColorStop(1,'transparent')
      ctx.fillStyle=lv;ctx.fillRect(0,0,w,h)
      const rv=ctx.createLinearGradient(w,0,w*.72,0)
      rv.addColorStop(0,'rgba(0,0,0,.2)');rv.addColorStop(1,'transparent')
      ctx.fillStyle=rv;ctx.fillRect(0,0,w,h)
      const gl=ctx.createRadialGradient(w*.22,h*.15,0,w*.22,h*.15,w*.18)
      gl.addColorStop(0,'rgba(255,255,255,.05)');gl.addColorStop(1,'transparent')
      ctx.fillStyle=gl;ctx.fillRect(0,0,w,h)

    }

    function frame(ts){
      if(!ts0.current)ts0.current=ts
      const dt=Math.min((ts-ts0.current)/1000,.05);ts0.current=ts
      tickCol(dt);syncCSS(stRef.current)

      const st=stRef.current
      // Ground truth: always from canvas element itself
      const w=canvas.width,h=canvas.height
      const t=T.current

      ctx.clearRect(0,0,w,h)
      ctx.fillStyle='#02010c'
      ctx.fillRect(0,0,w,h)

      if(st==='idle'){
        const g=ctx.createRadialGradient(w/2,h/2,0,w/2,h/2,h*.9)
        g.addColorStop(0,rc(.13));g.addColorStop(1,'transparent')
        ctx.fillStyle=g;ctx.fillRect(0,0,w,h)
        glass(w,h)
        ctx.beginPath();ctx.arc(w/2,h/2,2.2+Math.sin(t*.7)*.4,0,Math.PI*2);ctx.fillStyle=rc(.7);ctx.fill()
        const cg=ctx.createRadialGradient(w/2,h/2,0,w/2,h/2,10)
        cg.addColorStop(0,rc(.22));cg.addColorStop(1,'transparent');ctx.fillStyle=cg;ctx.fillRect(0,0,w,h)
        T.current+=dt;rafRef.current=requestAnimationFrame(frame);return
      }

      // Nebula
      for(let i=0;i<3;i++){
        const na=t*(.14+i*.06)+i*2.09,nx=w/2+Math.cos(na)*w*(.18+i*.07),ny=h/2+Math.sin(na*.68)*h*(.22+i*.09)
        const ng=ctx.createRadialGradient(nx,ny,0,nx,ny,w*(.28+i*.09))
        ng.addColorStop(0,rc(st==='acting'?.06:st==='thinking'?.05:st==='listening'?.035:.04));ng.addColorStop(1,'transparent')
        ctx.fillStyle=ng;ctx.fillRect(0,0,w,h)
      }

      const{disks,flows,dusts}=pools.current
      flows.forEach(f=>{f.tick();f.draw(ctx)})
      dusts.forEach(d=>{d.tick();d.draw(ctx)})
      disks.filter(p=>Math.sin(p.a)*p.tilt<0).forEach(p=>{p.tick();p.draw(ctx)})

      const lR=Math.min(w,h)*(st==='listening'?.32:st==='acting'?.35:.26)
      lensRing(w,h,lR,st==='acting'?.5:st==='thinking'?.44:.35)
      for(let i=0;i<2;i++){ctx.beginPath();ctx.ellipse(w/2,h/2,lR*(1+i*.025),lR*(.29+i*.018),0,0,Math.PI*2);ctx.strokeStyle=rc(.02);ctx.lineWidth=3-i;ctx.stroke()}

      if(st==='thinking'||st==='acting')jet(w,h,st==='acting'?.7:.35)
      if(st==='acting'){const sx=((t*28)%(w*1.6))-w*.3,sg=ctx.createLinearGradient(sx-28,0,sx+28,0);sg.addColorStop(0,'transparent');sg.addColorStop(.5,rc(.055));sg.addColorStop(1,'transparent');ctx.fillStyle=sg;ctx.fillRect(0,0,w,h)}

      const bhR=Math.min(w,h)*(st==='thinking'?.18:.15)
      const sh=ctx.createRadialGradient(w/2,h/2,bhR*.28,w/2,h/2,bhR*2.1)
      sh.addColorStop(0,'rgba(0,0,6,.97)');sh.addColorStop(.55,'rgba(0,0,4,.45)');sh.addColorStop(1,'transparent')
      ctx.fillStyle=sh;ctx.fillRect(0,0,w,h)
      ctx.beginPath();ctx.arc(w/2,h/2,bhR,0,Math.PI*2);ctx.fillStyle='#01000a';ctx.fill()
      const pr=ctx.createRadialGradient(w/2,h/2,bhR*.82,w/2,h/2,bhR*1.22)
      pr.addColorStop(0,'transparent');pr.addColorStop(.5,rc(.18));pr.addColorStop(1,'transparent')
      ctx.fillStyle=pr;ctx.fillRect(0,0,w,h)

      disks.filter(p=>Math.sin(p.a)*p.tilt>=0).forEach(p=>{p.tick();p.draw(ctx)})

      if(st==='done'){const b=ctx.createRadialGradient(w/2,h/2,0,w/2,h/2,w*.45);b.addColorStop(0,rc(.14+Math.sin(t*2.5)*.05));b.addColorStop(1,'transparent');ctx.fillStyle=b;ctx.fillRect(0,0,w,h)}

      glass(w,h)


      if(st==='acting'){const p=pb.current;p.w+=p.dir*.4;if(p.w>90)p.dir=-1;if(p.w<5)p.dir=1;const el=document.getElementById('k-pbar');if(el)el.style.width=p.w+'%'}

      T.current+=dt
      rafRef.current=requestAnimationFrame(frame)
    }

    rafRef.current=requestAnimationFrame(frame)
    return()=>{if(rafRef.current)cancelAnimationFrame(rafRef.current)}
  },[])

  // WebSocket + desktop pipeline bridge
  useEffect(()=>{
    const endpoints=['ws://127.0.0.1:8000/ws']

    const applyEvent=(m)=>{
      if(!m||typeof m!=='object') return
      if(m.type==='status'){
        const raw=(m.state||'idle').toLowerCase()
        if(raw==='shutdown'){
          setState('idle')
          if(window.electronAPI?.setWindowSize){
            window.electronAPI.setWindowSize({width:92+40,height:32+40})
          }
          return
        }
        const s=normState(raw)
        setState(s)
        if(s==='acting')setAt(m.phrase||'On it...')
      }
      if(m.type==='action_result'){
        // Truncate result text so it fits in the small pill overlay
        const raw=m.message||m.response_text||'done'
        const truncated=raw.length>40?raw.slice(0,37)+'...':raw
        setRt(truncated)
        setState('done')
        clearTimeout(dtimer.current)
        dtimer.current=setTimeout(()=>{
          setState('idle')
          window.kuro?.hide()
        },3000)
      }
      // stt_final and stt_partial: do NOT display in the pill — the pill is too
      // small for transcript text. Only action labels (phrase) are shown via status events.
      if(m.type==='app_badge') pushBadge(m)
    }

    function connect(){
      const endpoint=endpoints[wsIdxRef.current%endpoints.length]
      const ws=new WebSocket(endpoint)
      wsRef.current=ws
      ws.onmessage=(e)=>{try{applyEvent(JSON.parse(e.data))}catch(_){} }
      ws.onclose=()=>{wsIdxRef.current+=1;setTimeout(connect,1500)}
      ws.onerror=()=>ws.close()
    }

    const onDebug=(e)=>applyEvent(e?.detail)
    window.addEventListener('kuro-test',onDebug)
    if(window.kuro?.onKuroEvent){
      window.kuro.onKuroEvent((data)=>applyEvent(data))
    }

    connect()
    return()=>{
      wsRef.current?.close()
      clearTimeout(dtimer.current)
      window.removeEventListener('kuro-test',onDebug)
    }
  },[])

  return(
    <>
      <div className="k-badges">
        {badges.map((b)=>(
          <div key={b.id} className={`k-badge ${b.ok?'k-badge-ok':'k-badge-bad'}`}>
            <div className="k-badge-dot" />
            <span>{b.label}: {b.text}</span>
          </div>
        ))}
      </div>
      <div className="k-host">
        {/* Pill sized entirely by ref, NOT by React state */}
        <div
          ref={pillRef}
          className={['k-pill',anim].filter(Boolean).join(' ')}
          style={{width:92,height:32}}
        >
          <div ref={clipRef} className="k-clip">
            {/* Canvas: no CSS width/height — bitmap size IS display size */}
            <canvas ref={canvasRef} />
          </div>
          <div ref={haloRef} id="k-halo" className="k-halo" style={{opacity:0}}/>
          <div ref={textRef} className="k-text"/>
          {state !== 'idle' && (
             <button 
                className="k-cancel-btn"
                title="Cancel/Interrupt"
                onClick={(e) => {
                   e.stopPropagation();
                   fetch('http://localhost:3001/wake/stop', {method: 'POST'})
                     .catch(err => console.error(err));
                }}
             >✕</button>
          )}
        </div>
      </div>
    </>
  )
}
