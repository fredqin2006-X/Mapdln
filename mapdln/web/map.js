/* Selection editing shared by all official preview providers. Secrets stay in Python. */
'use strict';
(() => {
let bridge=null, map=null, provider='amap', system='GCJ02', config={}, tool='pan';
let controls=[],boundary=[],draft=[],shape='polygon',drag=null,mapReady=false,loadId=0;
let circleRadius=0;
let startupView={centers:{WGS84:[121.58901,31.2592]},zoom:15},lastView='';
let demCoverage=[];
let rectangle=null,hover=null,navigationDrag=null,pendingSubmit=null,submitFrame=0,selectionLocked=false;
const svg=document.getElementById('overlay'),status=document.getElementById('status'),hint=document.getElementById('hint');
const outline=document.getElementById('outline'),handles=document.getElementById('handles'),draftLine=document.getElementById('draft');
const mapHost=document.getElementById('map'),draftPoints=document.getElementById('draft-points');
const base=location.href.substring(0,location.href.lastIndexOf('/')+1);
window.setTheme=dark=>document.documentElement.classList.toggle('dark',dark);
function report(message,ok=false){status.textContent=message;status.classList.toggle('ok',!!ok);if(bridge)bridge.mapStatus(message,ok);}
function initialCenter(){return startupView.centers[system]||startupView.centers.WGS84;}
function leafletMap(minZoom=0){const p=initialCenter();const m=L.map('map',{doubleClickZoom:false,zoomControl:false,minZoom,maxZoom:24}).setView([p[1],p[0]],startupView.zoom);L.control.zoom({position:'bottomright'}).addTo(m);return m;}
window.currentMapView=()=>{if(!map)return null;const p=map.getCenter();return {center:[p.lng,p.lat],zoom:map.getZoom(),system};};
function publishView(){const view=JSON.stringify(window.currentMapView());if(bridge&&view!=='null'&&view!==lastView){lastView=view;bridge.mapViewChanged(view);}}
function script(url){return new Promise((resolve,reject)=>{let s=document.createElement('script');const timer=setTimeout(()=>{s.remove();reject(new Error('地图脚本加载超时，请检查网络或预览代理'));},20000);s.src=url;s.onload=()=>{clearTimeout(timer);resolve();};s.onerror=()=>{clearTimeout(timer);reject(new Error('地图脚本加载失败，请检查网络与代理'));};document.head.appendChild(s);});}
function pixel(p){
 if(!map)return {x:0,y:0};
 if(provider==='amap'){const q=map.lngLatToContainer(new AMap.LngLat(p[0],p[1]));return {x:q.x,y:q.y};}
 if(provider==='baidu'){const q=map.pointToPixel(new BMapGL.Point(p[0],p[1]));return {x:q.x,y:q.y};}
 const q=map.latLngToContainerPoint([p[1],p[0]]);return {x:q.x,y:q.y};
}
function coord(p){
 if(provider==='amap'){const q=map.containerToLngLat(new AMap.Pixel(p.x,p.y));return [q.lng,q.lat];}
 if(provider==='baidu'){const q=map.pixelToPoint(new BMapGL.Pixel(p.x,p.y));return [q.lng,q.lat];}
 const q=map.containerPointToLatLng([p.x,p.y]);return [q.lng,q.lat];
}
function setPan(on){if(!map)return;if(provider==='amap')map.setStatus({dragEnable:on,doubleClickZoom:on});else if(provider==='baidu'){on?map.enableDragging():map.disableDragging();}else{on?map.dragging.enable():map.dragging.disable();}}
function eventPoint(e){const r=svg.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top};}
function path(points,closed=false){return points.length?'M'+points.map(p=>p.x+','+p.y).join('L')+(closed?'Z':''):'';}
function svgElement(tag,attributes,parent=handles){const e=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attributes).forEach(([k,v])=>e.setAttribute(k,v));parent.appendChild(e);return e;}
// Closed centripetal Catmull-Rom, matching the engine's interpolation rule.
// Adapt in screen pixels so preview work remains bounded at any map zoom.
function curvePath(points){
 const result=[],n=points.length;
 const mix=(a,b,wa,wb)=>({x:wa*a.x+wb*b.x,y:wa*a.y+wb*b.y});
 for(let i=0;i<n;i++){
  const [p0,p1,p2,p3]=[i-1,i,i+1,i+2].map(j=>points[(j+n)%n]);
  const t0=0,t1=Math.sqrt(Math.hypot(p1.x-p0.x,p1.y-p0.y)),t2=t1+Math.sqrt(Math.hypot(p2.x-p1.x,p2.y-p1.y)),t3=t2+Math.sqrt(Math.hypot(p3.x-p2.x,p3.y-p2.y));
  const at=u=>{const t=t1+u*(t2-t1),a1=mix(p0,p1,(t1-t)/(t1-t0),(t-t0)/(t1-t0)),a2=mix(p1,p2,(t2-t)/(t2-t1),(t-t1)/(t2-t1)),a3=mix(p2,p3,(t3-t)/(t3-t2),(t-t2)/(t3-t2)),b1=mix(a1,a2,(t2-t)/(t2-t0),(t-t0)/(t2-t0)),b2=mix(a2,a3,(t3-t)/(t3-t1),(t-t1)/(t3-t1));return mix(b1,b2,(t2-t)/(t2-t1),(t-t1)/(t2-t1));};
  const segment=(a,b,pa,pb,depth)=>{
   const error=Math.max(...[.25,.5,.75].map(f=>{const q=at(a+(b-a)*f);return Math.hypot(q.x-pa.x-(pb.x-pa.x)*f,q.y-pa.y-(pb.y-pa.y)*f);}));
   if(error<=.5||depth>=10){result.push(pa);return;}
   const mid=(a+b)/2,pm=at(mid);segment(a,mid,pa,pm,depth+1);segment(mid,b,pm,pb,depth+1);
  };
  segment(0,1,p1,p2,0);
 }
 return path(result,true);
}
function paintDraft(){
 let points=draft.map(pixel),d='';
 draftPoints.replaceChildren();
 if(['polygon','curve'].includes(tool)){
  const fixed=points;
  if(hover&&points.length){const q=pixel(hover);if(points.every(p=>Math.hypot(p.x-q.x,p.y-q.y)>1))points=[...points,q];}
  d=tool==='curve'&&points.length>=3?curvePath(points):path(points,points.length>=3);
  fixed.forEach(p=>svgElement('circle',{cx:p.x,cy:p.y,r:4,class:'draft-point'},draftPoints));
 }else if(points.length===2){
  const [a,b]=points;
  if(tool==='rectangle')d=path([a,{x:b.x,y:a.y},b,{x:a.x,y:b.y}],true);
  else if(tool==='circle'){const r=Math.hypot(b.x-a.x,b.y-a.y);d=`M${a.x-r},${a.y}a${r},${r} 0 1,0 ${2*r},0a${r},${r} 0 1,0 ${-2*r},0`;}
 }
 draftLine.setAttribute('d',d);
}
function paint(){
 if(!map)return;
 publishView();
 const dem=document.getElementById('dem-coverage');if(dem)dem.setAttribute('d',path(demCoverage.map(pixel),true));
 const points=boundary.map(pixel);outline.setAttribute('d',points.length?'M'+points.map(p=>p.x+','+p.y).join('L')+'Z':'');
 handles.replaceChildren();
 controls.forEach((p,i)=>{const q=pixel(p);svgElement('circle',{cx:q.x,cy:q.y,r:6,class:'handle','data-index':i});});
 if(shape==='rectangle'&&rectangle&&boundary.length>=4){
  const center=pixel(rectangle.center),a=pixel(boundary[1]),b=pixel(boundary[2]),mid={x:(a.x+b.x)/2,y:(a.y+b.y)/2};
  const dx=mid.x-center.x,dy=mid.y-center.y,length=Math.hypot(dx,dy)||1,q={x:mid.x+32*dx/length,y:mid.y+32*dy/length};
  svgElement('line',{x1:mid.x,y1:mid.y,x2:q.x,y2:q.y,class:'rotation-stem'});
  const handle=svgElement('circle',{cx:q.x,cy:q.y,r:11,class:'rotate-handle'});
  svgElement('title',{},handle).textContent='拖动旋转矩形；按住 Shift 按 15° 对齐';
  svgElement('text',{x:q.x,y:q.y,class:'rotation-icon'}).textContent='↻';
 }
 paintDraft();
}
function setTool(t){if(selectionLocked)t='pan';tool=t;draft=[];hover=null;setPan(t==='pan');svg.classList.toggle('active',t!=='pan');outline.style.pointerEvents=t==='pan'?'visiblePainted':'none';handles.style.display=t==='pan'?'':'none';document.querySelectorAll('[data-tool]').forEach(b=>b.classList.toggle('on',b.dataset.tool===t));hint.textContent=(selectionLocked?'下载中：灰色选区已锁定；暂停期间保持锁定，取消或完成下载后解锁':t==='pan'?'拖动白点编辑、拖动选区移动；绿色 ↻ 手柄旋转矩形（Shift 对齐）；Delete 删除图形；多边形/曲线双击加点、右键白点删除':t==='rectangle'||t==='circle'?'按住左键拖动创建选区；Delete 删除图形；Esc 取消':'逐点点击；虚线预览；Enter 或“完成”闭合已点击的点；右键白点删除控制点；Delete 删除图形；Esc 取消')+' · 滚轮缩放，按住中键拖动地图';paint();}
function submit(points,kind,radius,details=null){if(!bridge||selectionLocked)return;const data={shape:kind,system,points};if(radius)data.radius_m=radius;if(kind==='rectangle')Object.assign(data,{angle_deg:rectangle?.angle_deg||0},details);bridge.selectRegion(JSON.stringify(data));}
function flushSubmit(){if(submitFrame)cancelAnimationFrame(submitFrame);submitFrame=0;if(pendingSubmit){const args=pendingSubmit;pendingSubmit=null;submit(...args);}}
function queueSubmit(...args){pendingSubmit=args;if(!submitFrame)submitFrame=requestAnimationFrame(flushSubmit);}
window.updateSelection=function(data){controls=data.controls||[];boundary=data.boundary||[];shape=data.shape||'polygon';circleRadius=data.radius_m||0;rectangle=data.rectangle||null;paint();};
window.setSelectionLocked=function(locked){
 selectionLocked=!!locked;cancelDrag();pendingSubmit=null;if(submitFrame)cancelAnimationFrame(submitFrame);submitFrame=0;
 document.body.classList.toggle('selection-locked',selectionLocked);
 document.querySelectorAll('[data-tool],#finish,#clear').forEach(b=>b.disabled=selectionLocked&&b.dataset.tool!=='pan');
 setTool('pan');
};
window.setCenter=function(p){if(!map)return;if(provider==='amap')map.setCenter(p);else if(provider==='baidu')map.panTo(new BMapGL.Point(...p));else map.setView([p[1],p[0]],map.getZoom());};
window.setDemCoverage=function(points){demCoverage=points||[];paint();};
window.fitCoverage=function(points){if(!map||points.length!==2)return;if(provider==='amap')map.setBounds(new AMap.Bounds(points[0],points[1]));else if(provider==='baidu')map.setViewport(points.map(p=>new BMapGL.Point(...p)));else map.fitBounds(points.map(p=>[p[1],p[0]]),{padding:[30,30]});};
window.previewProvider=async function(p,view){
 if(view)startupView=view;
 provider=p;mapReady=false;const id=++loadId;report('加载地图…');
 try{
  report('读取本机地图配置…');
  config=await (await fetch(base+'config')).json();
  report('准备地图底图…');
  if(map){if(map.destroy)map.destroy();else if(map.remove)map.remove();map=null;}
  document.getElementById('map').replaceChildren();
  system=p==='amap'?'GCJ02':p==='baidu'?'BD09':'WGS84';
  if(p==='amap'){
   if(!config.amap_key)throw new Error('请到账户配置页保存高德 Web 端（JS API）Key 和安全密钥；也可直接输入 WGS84 坐标');
   window._AMapSecurityConfig={serviceHost:config.amap_service};
   if(!window.AMap)await script(base+'sdk/amap.js');
   report('高德 SDK 已加载，正在创建地图…');
   if(id!==loadId)return;
   map=new AMap.Map('map',{zoom:startupView.zoom,center:initialCenter(),viewMode:'2D'});
   report('高德地图已创建，正在加载瓦片…');
   map.on('complete',()=>{const first=!mapReady;mapReady=true;report('高德地图已加载 · GCJ-02 → WGS84 转换用于下载',true);if(first&&bridge)bridge.providerReady(system);paint();});
   map.on('mapmove',paint);map.on('zoomchange',paint);map.on('resize',paint);
   AMap.plugin('AMap.Scale',()=>map.addControl(new AMap.Scale()));
  }else if(p==='baidu'){
   if(!config.baidu_ak)throw new Error('请到账户配置页保存浏览器端 AK，并按本机地址设置 Referer 白名单');
   if(!window.BMapGL)await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('百度地图初始化超时，请检查浏览器端 AK、Referer 白名单和网络')),25000);window.mapdlnBaiduReady=()=>{clearTimeout(timer);resolve();};script('https://api.map.baidu.com/api?v=1.0&type=webgl&ak='+encodeURIComponent(config.baidu_ak)+'&callback=mapdlnBaiduReady').catch(reject);});
   if(id!==loadId)return;
   map=new BMapGL.Map('map');map.centerAndZoom(new BMapGL.Point(...initialCenter()),startupView.zoom);map.enableScrollWheelZoom();map.addControl(new BMapGL.ScaleControl());
   map.addEventListener('tilesloaded',()=>{const first=!mapReady;mapReady=true;report('百度地图已加载 · BD-09 → WGS84 转换用于下载',true);if(first&&bridge)bridge.providerReady(system);paint();});
   ['moving','zoomend','resize'].forEach(e=>map.addEventListener(e,paint));
  }else if(p==='offline'){
   map=leafletMap();L.control.scale({imperial:false}).addTo(map);mapReady=true;report('离线坐标预览 · 不请求在线影像',true);map.on('move zoom resize',paint);if(bridge)bridge.providerReady(system);
  }else{
   const sourceId=p.startsWith('source:')?p.slice(7):'mapbox';
   const source=(config.sources||[]).find(s=>s.id===sourceId);if(!source)throw new Error('图源已删除，请重新选择导出影像');
   system=source.datum==='GCJ02'?'GCJ02':'WGS84';
   if(sourceId==='mapbox'&&!config.mapbox_available)throw new Error('请到账户配置页保存 Mapbox 公共 Token');
   map=leafletMap(source.min_zoom);
   const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
   const layer=L.tileLayer(base+'source-tiles/'+encodeURIComponent(sourceId)+'/{z}/{x}/{y}',{minZoom:source.min_zoom,maxZoom:24,maxNativeZoom:source.max_zoom,attribution:escape(source.attribution)}).addTo(map);
   L.control.scale({imperial:false}).addTo(map);
   let errors=0;
   layer.on('tileerror',async()=>{errors++;const s=await(await fetch(base+'source-status/'+encodeURIComponent(sourceId))).json();report(s.error||source.name+' 预览失败，请检查凭据与网络');});
   layer.on('load',()=>{if(!errors){const first=!mapReady;mapReady=true;report(source.name+' · 预览请求计入该图源本机记录',true);if(first&&bridge)bridge.providerReady(system);}paint();});
   map.on('move zoom resize',paint);
  }
  setPan(tool==='pan');
  publishView();
  setTimeout(()=>{if(id===loadId&&!mapReady)report('地图未完成加载，请检查 Key 类型、安全密钥、Referer 白名单与网络。当前选区和参数已保留。');},25000);
 }catch(e){
  report(e.message);
  // Local coordinates remain usable even when an online provider is unavailable.
  provider='offline';system='WGS84';
  map=leafletMap();L.control.scale({imperial:false}).addTo(map);map.on('move zoom resize',paint);
  setPan(tool==='pan');if(bridge)bridge.providerReady(system);paint();
 }
};
svg.addEventListener('pointerdown',e=>{
 if(!map||selectionLocked||navigationDrag||drag||e.button!==0)return;
 const p=eventPoint(e),target=e.target;
 if(target.classList.contains('rotate-handle')&&rectangle){
  const center=pixel(rectangle.center);
  drag={type:'rotate',center,rectangle:{...rectangle},startAngle:Math.atan2(p.y-center.y,p.x-center.x)};
 }else if(target.classList.contains('handle')){
  drag={type:'handle',index:Number(target.dataset.index),points:controls.map(q=>[...q]),angle:rectangle?.angle_deg||0};
 }else if(tool==='pan'&&target===outline){
  drag={type:'move',start:p,points:controls.map(pixel),rectangle:rectangle?{...rectangle}:null};
 }else if(tool==='rectangle'||tool==='circle'){
  drag={type:'draw',first:coord(p)};draft=[drag.first,drag.first];
 }else if(tool==='polygon'||tool==='curve'){
  if(draft.every(q=>{const a=pixel(q);return Math.hypot(a.x-p.x,a.y-p.y)>1;}))draft.push(coord(p));hover=null;paint();e.preventDefault();return;
 }else return;
 drag.pointerId=e.pointerId;setPan(false);svg.setPointerCapture(e.pointerId);e.preventDefault();
});
svg.addEventListener('pointermove',e=>{
 if(!map||selectionLocked||navigationDrag)return;
 const p=eventPoint(e);
 if(!drag){if(['polygon','curve'].includes(tool)){hover=coord(p);paintDraft();}return;}
 if(e.pointerId!==drag.pointerId)return;
 if(drag.type==='rotate'){
  let angle=drag.rectangle.angle_deg-(Math.atan2(p.y-drag.center.y,p.x-drag.center.x)-drag.startAngle)*180/Math.PI;
  if(e.shiftKey)angle=Math.round(angle/15)*15;
  queueSubmit([drag.rectangle.center],'rectangle',undefined,{width_m:drag.rectangle.width_m,height_m:drag.rectangle.height_m,angle_deg:(angle%360+360)%360});
 }
 if(drag.type==='handle'){controls=drag.points.map(q=>[...q]);controls[drag.index]=coord(p);queueSubmit(controls,shape,shape==='circle'&&drag.index===0?circleRadius:undefined,shape==='rectangle'?{angle_deg:drag.angle}:null);}
 if(drag.type==='move'){
  if(drag.rectangle){const q=pixel(drag.rectangle.center),center=coord({x:q.x+p.x-drag.start.x,y:q.y+p.y-drag.start.y});queueSubmit([center],'rectangle',undefined,{width_m:drag.rectangle.width_m,height_m:drag.rectangle.height_m,angle_deg:drag.rectangle.angle_deg});}
  else{controls=drag.points.map(q=>coord({x:q.x+p.x-drag.start.x,y:q.y+p.y-drag.start.y}));queueSubmit(controls,shape);}
 }
 if(drag.type==='draw'){draft=[drag.first,coord(p)];paint();}
});
svg.addEventListener('pointerup',e=>{
 if(!drag||e.pointerId!==drag.pointerId||e.button!==0)return;
 // Include the release position, even if no final pointermove was delivered.
 svg.dispatchEvent(new PointerEvent('pointermove',{clientX:e.clientX,clientY:e.clientY,pointerId:e.pointerId,shiftKey:e.shiftKey}));
 const completed=drag;drag=null;
 if(completed.type==='draw'){submit(draft,tool,undefined,tool==='rectangle'?{angle_deg:0}:null);draft=[];setTool('pan');}
 else flushSubmit();
 setPan(tool==='pan');if(svg.hasPointerCapture(e.pointerId))svg.releasePointerCapture(e.pointerId);
});
function cancelDrag(){const active=drag;drag=null;pendingSubmit=null;if(submitFrame)cancelAnimationFrame(submitFrame);submitFrame=0;if(active&&svg.hasPointerCapture(active.pointerId))svg.releasePointerCapture(active.pointerId);if(active?.type==='draw')draft=[];setPan(tool==='pan');paint();}
svg.addEventListener('pointercancel',cancelDrag);
svg.addEventListener('lostpointercapture',()=>{if(drag)cancelDrag();});
svg.addEventListener('pointerleave',()=>{if(!drag){hover=null;paintDraft();}});
function finishDrawing(){
 if(!map||selectionLocked||drag||navigationDrag||!['polygon','curve'].includes(tool))return;
 if(draft.length<3){hint.textContent='需要至少三个已点击的控制点，才能按 Enter 完成闭合';return;}
 submit(draft,tool);setTool('pan');
}
function clearSelection(){
 if(selectionLocked)return;
 cancelDrag();draft=[];hover=null;controls=[];boundary=[];rectangle=null;paint();if(bridge)bridge.clearRegion();
}
window.addEventListener('keydown',e=>{
 if(!map||e.repeat||e.isComposing||e.ctrlKey||e.altKey||e.metaKey||e.target.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])'))return;
 if(e.key==='Escape'){e.preventDefault();e.stopPropagation();cancelDrag();setTool('pan');}
 else if(!selectionLocked&&e.key==='Delete'){e.preventDefault();e.stopPropagation();clearSelection();}
 else if(!selectionLocked&&e.key==='Enter'&&['polygon','curve'].includes(tool)){e.preventDefault();e.stopPropagation();finishDrawing();}
},true);
function mapSurface(target){return target instanceof Element&&!!target.closest('#overlay,#map')&&!target.closest('.leaflet-control,.amap-control,.BMap_stdMpCtrl');}
// Right-click on the basemap (including locked selections) has no edit
// target. Suppress the browser/SDK menu without completing a draft.
window.addEventListener('contextmenu',e=>{if(mapSurface(e.target)&&!svg.contains(e.target)){e.preventDefault();e.stopPropagation();}},true);
// The SVG is a sibling of the SDK container, so intercepted wheel events
// never reach the map. AMap also rejects synthetic wheel input: use its API.
svg.addEventListener('wheel',e=>{
 if(!map||!e.deltaY)return;e.preventDefault();e.stopPropagation();
 const p=eventPoint(e),step=-Math.sign(e.deltaY);
 if(provider==='amap'){
  const oldZoom=map.getZoom(),limits=map.getZooms(),zoom=Math.max(limits[0],Math.min(limits[1],oldZoom+step)),factor=2**(zoom-oldZoom);
  const anchor=coord(p),center=map.getCenter();
  const mercatorY=lat=>Math.log(Math.tan(Math.PI/4+Math.max(-85,Math.min(85,lat))*Math.PI/360));
  const y=mercatorY(anchor[1])+(mercatorY(center.lat)-mercatorY(anchor[1]))/factor;
  map.setZoomAndCenter(zoom,[anchor[0]+(center.lng-anchor[0])/factor,(2*Math.atan(Math.exp(y))-Math.PI/2)*180/Math.PI],true);
 }else if(provider==='baidu'){
  const anchor=coord(p);map.setZoom(map.getZoom()+step,{noAnimation:true,zoomCenter:new BMapGL.Point(...anchor)});
 }else{
  // Leaflet handles wheel accumulation, trackpads and cursor anchoring.
  mapHost.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:e.clientX,clientY:e.clientY,deltaX:e.deltaX,deltaY:e.deltaY,deltaZ:e.deltaZ,deltaMode:e.deltaMode,ctrlKey:e.ctrlKey,shiftKey:e.shiftKey,altKey:e.altKey,metaKey:e.metaKey}));
 }
},{passive:false});
function panPixels(dx,dy){if(provider==='amap')map.panBy(dx,dy,0);else if(provider==='baidu')map.panBy(dx,dy,{noAnimation:true});else map.panBy([-dx,-dy],{animate:false});}
window.addEventListener('pointerdown',e=>{
 if(!map||e.button!==1||drag||navigationDrag||!mapSurface(e.target))return;
 e.preventDefault();e.stopImmediatePropagation();hover=null;navigationDrag={pointerId:e.pointerId,last:eventPoint(e)};setPan(false);mapHost.setPointerCapture(e.pointerId);document.body.classList.add('map-panning');paintDraft();
},true);
window.addEventListener('pointermove',e=>{
 if(!navigationDrag||e.pointerId!==navigationDrag.pointerId)return;
 e.preventDefault();e.stopImmediatePropagation();const p=eventPoint(e),last=navigationDrag.last;navigationDrag.last=p;panPixels(p.x-last.x,p.y-last.y);paint();
},true);
function endNavigation(e){if(!navigationDrag||(e&&e.pointerId!==undefined&&e.pointerId!==navigationDrag.pointerId))return;const id=navigationDrag.pointerId;navigationDrag=null;if(mapHost.hasPointerCapture(id))mapHost.releasePointerCapture(id);document.body.classList.remove('map-panning');setPan(tool==='pan');}
window.addEventListener('pointerup',e=>{if(navigationDrag&&e.pointerId===navigationDrag.pointerId&&e.button===1){e.preventDefault();e.stopImmediatePropagation();endNavigation(e);}},true);
window.addEventListener('pointercancel',endNavigation,true);
mapHost.addEventListener('lostpointercapture',endNavigation);
window.addEventListener('blur',()=>{endNavigation();cancelDrag();});
window.addEventListener('auxclick',e=>{if(e.button===1&&mapSurface(e.target))e.preventDefault();},true);
svg.addEventListener('contextmenu',e=>{
 e.preventDefault();e.stopPropagation();if(selectionLocked)return;
 if(['polygon','curve'].includes(tool)){
  const p=eventPoint(e);let index=-1,distance=8;
  draft.forEach((q,i)=>{const a=pixel(q),d=Math.hypot(a.x-p.x,a.y-p.y);if(d<=distance){index=i;distance=d;}});
  if(index>=0){draft.splice(index,1);hover=null;paintDraft();}return;
 }
 if(e.target.classList.contains('handle')&&['polygon','curve'].includes(shape)){
  if(controls.length<=3){hint.textContent='至少需要三个控制点';return;}controls.splice(Number(e.target.dataset.index),1);submit(controls,shape);
 }
});
svg.addEventListener('dblclick',e=>{
 if(!selectionLocked&&tool==='pan'&&e.target===outline&&['polygon','curve'].includes(shape)){
  const rect=svg.getBoundingClientRect(),p={x:e.clientX-rect.left,y:e.clientY-rect.top};
  let best=0,distance=Infinity;
  for(let i=0;i<controls.length;i++){const a=pixel(controls[i]),b=pixel(controls[(i+1)%controls.length]),dx=b.x-a.x,dy=b.y-a.y;const u=Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy||1)));const d=(p.x-a.x-u*dx)**2+(p.y-a.y-u*dy)**2;if(d<distance){distance=d;best=i;}}
  controls.splice(best+1,0,coord(p));submit(controls,shape);
 }
});
document.querySelectorAll('[data-tool]').forEach(b=>b.onclick=()=>setTool(b.dataset.tool));
document.getElementById('finish').onclick=finishDrawing;
document.getElementById('clear').onclick=clearSelection;
new QWebChannel(qt.webChannelTransport,channel=>{bridge=channel.objects.bridge;bridge.pageReady();});
window.addEventListener('error',e=>{if(e.message)report('地图服务错误：'+e.message);});
window.mapDebugInfo=()=>({provider,mapReady,hasMap:!!map,hasLeaflet:!!window.L,status:status.textContent,tool,selectionLocked,zoom:map?.getZoom(),draftPoints:draft.length,center:map?.getCenter()});
setTool('pan');
})();
