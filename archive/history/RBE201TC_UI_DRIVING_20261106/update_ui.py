from pathlib import Path
import math
root=Path('work/device-console-v2/frontend')
p=root/'index.html';s=p.read_text().replace('<title>小车控制台</title>','<title>CYBERTRUCK</title>').replace('<h1>小车控制台</h1>','<h1>CYBERTRUCK</h1>')
s=s.replace('<div class="drive-body">','''<div class="mode-switch" role="group" aria-label="驾驶模式"><button id="mode-manual" type="button" aria-pressed="true" disabled>手动遥控</button><button id="mode-auto" type="button" aria-pressed="false" disabled>自动驾驶</button></div><div id="manual-controls" class="drive-body">''')
s=s.replace('<p id="drive-message"','''<div id="auto-controls" class="auto-controls" hidden><svg aria-hidden="true" viewBox="0 0 32 32"><circle cx="16" cy="16" r="12"/><circle cx="16" cy="16" r="4"/><path d="m5 12 7 3m8 0 7-3M16 20v8"/></svg><span id="autonomy-status" role="status">尚未就绪</span><div class="auto-actions"><button id="auto-start" type="button" disabled>开始自动驾驶</button><button id="auto-stop" type="button" aria-label="停止自动驾驶">停止</button></div></div><p id="drive-message"''')
start=s.index('<article id="camera-panel"');end=s.index('</article>',start)+len('</article>');camera=s[start:end]
s=s[:start]+s[end:]
camera=camera.replace('<h2>相机</h2>','<h2 id="camera-title">驾驶实时画面</h2>')
camera=camera.replace('成功采集后，这里会显示真实画面。','等待相机连接')
s=s.replace('</div></aside></div>','</div>'+camera+'</aside></div>',1)
# Render the unmodified photograph through a fixed silhouette and headlight mask.
# Wheel rotations are contained in fixed, calibrated wheel-plane masks.
def rotor(name,x,y,ratio,tilt,r):
 a=ratio*math.cos(tilt);b=ratio*math.sin(tilt);c=-math.sin(tilt);d=math.cos(tilt);det=a*d-b*c
 inv=(d/det,-b/det,-c/det,a/det,(c*y-d*x)/det,(b*x-a*y)/det)
 return f'''<g class="wheel-plane" data-wheel="{name}" transform="matrix({a:.9f} {b:.9f} {c:.9f} {d:.9f} {x} {y})"><defs><radialGradient id="{name}-edge"><stop offset="0.91" stop-color="white"/><stop offset="1" stop-color="black"/></radialGradient><mask id="{name}-rim-mask" maskUnits="userSpaceOnUse" x="-{r}" y="-{r}" width="{r*2}" height="{r*2}"><circle r="{r}" fill="url(#{name}-edge)"/><circle r="4.5" fill="black"/></mask></defs><g mask="url(#{name}-rim-mask)"><g id="wheel-{name}" transform="rotate(0)"><image href="/cybertruck-photo.jpg" width="1320" height="643" transform="matrix({' '.join(f'{v:.9f}' for v in inv)})"/></g></g></g>'''
photo_start=s.index('<img src="/cybertruck-photo.jpg"');photo_end=s.index('</svg></div></div>',photo_start)+len('</svg>')
scene='''<svg class="vehicle-scene" viewBox="0 0 1320 643" role="img" aria-label="Cybertruck，轮毂随行驶指令转动"><defs><linearGradient id="beam-fade" x1="450" y1="410" x2="20" y2="595" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="white"/><stop offset="1" stop-color="black"/></linearGradient><mask id="vehicle-cutout" maskUnits="userSpaceOnUse" x="0" y="0" width="1320" height="643"><path fill="white" d="M273 334 333 291 549 143 711 114 846 100 900 93 1093 148 1090 250 1042 275 1040 346 Q1034 397 987 400L946 395 926 372 665 472 653 520 Q641 567 595 574 Q540 577 521 523L298 465 272 437Z"/><path fill="url(#beam-fade)" d="M274 347 510 414 198 643H0V436Z"/></mask></defs><g mask="url(#vehicle-cutout)"><image href="/cybertruck-photo.jpg" width="1320" height="643"/>'''
scene+=rotor('front',596.5,498.5,.65,math.radians(16),54)
scene+=rotor('rear',998.5,333.5,.61,math.radians(14),48)
scene+='</g></svg>'
s=s[:photo_start]+scene+s[photo_end:]
p.write_text(s)
# Shorter camera rate text is already supplied by app.js; leave polling untouched.
p=root/'vehicle.js';v=p.read_text().replace('if(now-last<1000/30)','if(now-last<1000/60)').replace('*dt*210','*dt*150');p.write_text(v)
# Add mode switching while preserving the existing heartbeat protocol.
p=root/'control.js';c=p.read_text()
c=c.replace('  async start(direction,speed) {','''  async setMode(mode) {
    if(this.switching||mode===this.state.driving_mode)return;
    this.switching=true;this.cancelTimers();const generation=++this.generation;
    this.sessionEpoch++;this.session=null;this.emit({state:'switching',error:null});
    try {
      if(!this.key){const state=await this.request();this.key=state.control_key;}
      const state=await this.request({action:'set_mode',mode});
      if(generation===this.generation)this.emit({...state,error:null});
    } catch(error) {
      if(generation===this.generation)this.emit({state:'offline',armed:false,error:error.message,outputs:[0,0,0,0]});
    } finally {this.switching=false;if(generation===this.generation)this.emit({});}
  }
  async start(direction,speed) {
    if(this.switching||this.state.driving_mode==='automatic')return;''')
c=c.replace('if(this.refreshing||this.active||this.arming)return;','if(this.refreshing||this.active||this.arming||this.switching)return;')
c=c.replace("stopped:'就绪',armed:'就绪'","switching:'切换中',stopped:'就绪',armed:'就绪'")
c=c.replace("['stopping','offline','error','fault'].includes(state.state)","['switching','stopping','offline','error','fault'].includes(state.state)")
c=c.replace("    const owned=state.armed&&!!control.session;","""    const automatic=state.driving_mode==='automatic';
    if(automatic)clearHeld();
    const modeAvailable=!control.switching&&!['loading','offline','switching','stopping'].includes(state.state);
    $('mode-manual').disabled=$('mode-auto').disabled=!modeAvailable;
    $('mode-manual').setAttribute('aria-pressed',String(!automatic));
    $('mode-auto').setAttribute('aria-pressed',String(automatic));
    $('manual-controls').hidden=automatic;
    $('auto-controls').hidden=!automatic;
    $('camera-title').textContent=automatic?'自动驾驶实时画面':'驾驶实时画面';
    $('autonomy-status').textContent=state.autonomy?.ready?'准备就绪':'尚未就绪';
    $('auto-start').disabled=true;
    $('drive-speed').disabled=automatic||control.switching;
    const owned=state.armed&&!!control.session;""")
c=c.replace("state.armed&&!owned?'其他端控制中':labels[state.state]||'等待控制'","automatic?'自动模式':state.armed&&!owned?'其他端控制中':labels[state.state]||'等待控制'")
c=c.replace("const available=state.enabled&&!['fault','offline','loading','stopping'].includes(state.state);","const available=!automatic&&!control.switching&&state.enabled&&!['fault','offline','loading','stopping','switching'].includes(state.state);")
c=c.replace("  $('drive-stop').addEventListener", """  $('mode-manual').addEventListener('click',()=>{clearHeld();control.setMode('manual');});
  $('mode-auto').addEventListener('click',()=>{clearHeld();control.setMode('automatic');});
  $('auto-stop').addEventListener('click',()=>{clearHeld();control.stop();});
  $('drive-stop').addEventListener""")
c=c.replace("if(e.button!==0||heldPointer!==null||heldKeys.size)return;","if(button.disabled||control.switching||control.state.driving_mode==='automatic'||e.button!==0||heldPointer!==null||heldKeys.size)return;")
c=c.replace("if(!DRIVE_KEYS[e.code]||!control.state.enabled", "if(control.switching||control.state.driving_mode==='automatic'||!DRIVE_KEYS[e.code]||!control.state.enabled")
p.write_text(c)
