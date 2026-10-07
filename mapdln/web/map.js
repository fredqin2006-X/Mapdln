/* Selection editing shared by all official preview providers. Secrets stay in Python. */
'use strict';
(() => {
let bridge=null, map=null, provider='amap', system='GCJ02', config={}, tool='pan';
let controls=[],boundary=[],draft=[],shape='polygon',drag=null,mapReady=false,loadId=0;
let circleRadius=0;
const svg=document.getElementById('overlay'),status=document.getElementById('status'),hint=document.getElementById('hint');
const outline=document.getElementById('outline'),handles=document.getElementById('handles'),draftLine=document.getElementById('draft');
const base=location.href.substring(0,location.href.lastIndexOf('/')+1);
window.setTheme=dark=>document.documentElement.classList.toggle('dark',dark);
function report(message,ok=false){status.textContent=message;if(bridge)bridge.mapStatus(message,ok);}
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
function paint(){
 if(!map)return;
 const points=boundary.map(pixel);outline.setAttribute('d',points.length?'M'+points.map(p=>p.x+','+p.y).join('L')+'Z':'');
 handles.replaceChildren();
 controls.forEach((p,i)=>{const q=pixel(p),e=document.createElementNS('http://www.w3.org/2000/svg','circle');e.setAttribute('cx',q.x);e.setAttribute('cy',q.y);e.setAttribute('r',6);e.setAttribute('class','handle');e.dataset.index=i;handles.appendChild(e);});
 draftLine.setAttribute('points',draft.map(pixel).map(p=>p.x+','+p.y).join(' '));
}
function setTool(t){tool=t;draft=[];setPan(t==='pan');svg.classList.toggle('active',t!=='pan');outline.style.pointerEvents=t==='pan'?'visiblePainted':'none';handles.style.display=t==='pan'?'':'none';document.querySelectorAll('[data-tool]').forEach(b=>b.classList.toggle('on',b.dataset.tool===t));hint.textContent=t==='pan'?'拖动白色控制点编辑；拖动选区移动；多边形/曲线双击边界加点，右键控制点删除':t==='rectangle'||t==='circle'?'按住并拖动创建选区':'逐点点击；点击“完成”闭合选区';paint();}
function submit(points,kind,radius){if(!bridge)return;const data={shape:kind,system,points};if(radius)data.radius_m=radius;bridge.selectRegion(JSON.stringify(data));}
window.updateSelection=function(data){controls=data.controls||[];boundary=data.boundary||[];shape=data.shape||'polygon';circleRadius=data.radius_m||0;paint();};
window.setCenter=function(p){if(!map)return;if(provider==='amap')map.setCenter(p);else if(provider==='baidu')map.panTo(new BMapGL.Point(...p));else map.setView([p[1],p[0]],map.getZoom());};
window.previewProvider=async function(p){
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
   map=new AMap.Map('map',{zoom:15,center:[121.5928,31.2573],viewMode:'2D'});
   report('高德地图已创建，正在加载瓦片…');
   map.on('complete',()=>{const first=!mapReady;mapReady=true;report('高德地图已加载 · GCJ-02 → WGS84 转换用于下载',true);if(first&&bridge)bridge.providerReady(system);paint();});
   map.on('mapmove',paint);map.on('zoomchange',paint);map.on('resize',paint);
   AMap.plugin('AMap.Scale',()=>map.addControl(new AMap.Scale()));
  }else if(p==='baidu'){
   if(!config.baidu_ak)throw new Error('请到账户配置页保存浏览器端 AK，并按本机地址设置 Referer 白名单');
   if(!window.BMapGL)await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('百度地图初始化超时，请检查浏览器端 AK、Referer 白名单和网络')),25000);window.mapdlnBaiduReady=()=>{clearTimeout(timer);resolve();};script('https://api.map.baidu.com/api?v=1.0&type=webgl&ak='+encodeURIComponent(config.baidu_ak)+'&callback=mapdlnBaiduReady').catch(reject);});
   if(id!==loadId)return;
   map=new BMapGL.Map('map');map.centerAndZoom(new BMapGL.Point(121.5993,31.2633),15);map.enableScrollWheelZoom();map.addControl(new BMapGL.ScaleControl());
   map.addEventListener('tilesloaded',()=>{const first=!mapReady;mapReady=true;report('百度地图已加载 · BD-09 → WGS84 转换用于下载',true);if(first&&bridge)bridge.providerReady(system);paint();});
   ['moving','zoomend','resize'].forEach(e=>map.addEventListener(e,paint));
  }else{
   if(!config.mapbox_available)throw new Error('请到账户配置页保存 Mapbox 公共 Token');
   map=L.map('map',{doubleClickZoom:false}).setView([31.2592,121.58901],15);
   const layer=L.tileLayer(base+'tiles/{z}/{x}/{y}.jpg',{maxZoom:22,attribution:'© Mapbox © Maxar © OpenStreetMap'}).addTo(map);
   L.control.scale({imperial:false}).addTo(map);
   let errors=0;
   layer.on('tileerror',async()=>{errors++;const s=await(await fetch(base+'mapbox-status')).json();report(s.error||'Mapbox 预览瓦片加载失败，请检查 Token 或下载代理');});
   layer.on('load',()=>{if(!errors){const first=!mapReady;mapReady=true;report('Mapbox 卫星预览 · WGS84；预览请求计入本机记录',true);if(first&&bridge)bridge.providerReady(system);}paint();});
   map.on('move zoom resize',paint);
  }
  setPan(tool==='pan');
  setTimeout(()=>{if(id===loadId&&!mapReady)report('地图未完成加载，请检查 Key 类型、安全密钥、Referer 白名单与网络。当前选区和参数已保留。');},25000);
 }catch(e){
  report(e.message);
  // Local coordinates remain usable even when an online provider is unavailable.
  provider='offline';system='WGS84';
  map=L.map('map',{doubleClickZoom:false}).setView([31.2592,121.58901],15);L.control.scale({imperial:false}).addTo(map);map.on('move zoom resize',paint);
  if(bridge)bridge.providerReady(system);paint();
 }
};
svg.addEventListener('pointerdown',e=>{
 if(!map||e.button!==0)return;
 const rect=svg.getBoundingClientRect(),p={x:e.clientX-rect.left,y:e.clientY-rect.top},target=e.target;
 if(target.classList.contains('handle')){
  drag={type:'handle',index:Number(target.dataset.index)};
 }else if(tool==='pan'&&target===outline){
  drag={type:'move',start:p,points:controls.map(pixel)};
 }else if(tool==='rectangle'||tool==='circle'){
  drag={type:'draw',first:coord(p)};draft=[drag.first,drag.first];
 }else if(tool==='polygon'||tool==='curve'){
  draft.push(coord(p));paint();return;
 }else return;
 setPan(false);svg.setPointerCapture(e.pointerId);e.preventDefault();
});
svg.addEventListener('pointermove',e=>{
 if(!drag)return;
 const rect=svg.getBoundingClientRect(),p={x:e.clientX-rect.left,y:e.clientY-rect.top};
 if(drag.type==='handle'){controls[drag.index]=coord(p);submit(controls,shape,shape==='circle'&&drag.index===0?circleRadius:undefined);}
 if(drag.type==='move'){controls=drag.points.map(q=>coord({x:q.x+p.x-drag.start.x,y:q.y+p.y-drag.start.y}));submit(controls,shape);}
 if(drag.type==='draw'){draft=[drag.first,coord(p)];paint();}
});
svg.addEventListener('pointerup',e=>{
 if(!drag)return;
 const completed=drag;drag=null;
 if(completed.type==='draw'){submit(draft,tool);draft=[];setTool('pan');}
 else submit(controls,shape,shape==='circle'&&completed.type==='handle'&&completed.index===0?circleRadius:undefined);
 setPan(tool==='pan');if(svg.hasPointerCapture(e.pointerId))svg.releasePointerCapture(e.pointerId);
});
svg.addEventListener('contextmenu',e=>{
 e.preventDefault();if(e.target.classList.contains('handle')&&['polygon','curve'].includes(shape)){
  if(controls.length<=3){hint.textContent='至少需要三个控制点';return;}controls.splice(Number(e.target.dataset.index),1);submit(controls,shape);
 }
});
svg.addEventListener('dblclick',e=>{
 if(tool==='pan'&&e.target===outline&&['polygon','curve'].includes(shape)){
  const rect=svg.getBoundingClientRect(),p={x:e.clientX-rect.left,y:e.clientY-rect.top};
  let best=0,distance=Infinity;
  for(let i=0;i<controls.length;i++){const a=pixel(controls[i]),b=pixel(controls[(i+1)%controls.length]),dx=b.x-a.x,dy=b.y-a.y;const u=Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy||1)));const d=(p.x-a.x-u*dx)**2+(p.y-a.y-u*dy)**2;if(d<distance){distance=d;best=i;}}
  controls.splice(best+1,0,coord(p));submit(controls,shape);
 }
});
document.querySelectorAll('[data-tool]').forEach(b=>b.onclick=()=>setTool(b.dataset.tool));
document.getElementById('finish').onclick=()=>{if(draft.length>=3){submit(draft,tool);setTool('pan');}else hint.textContent='需要至少三个控制点';};
document.getElementById('clear').onclick=()=>{draft=[];controls=[];boundary=[];paint();if(bridge)bridge.clearRegion();};
new QWebChannel(qt.webChannelTransport,channel=>{bridge=channel.objects.bridge;bridge.pageReady();});
window.addEventListener('error',e=>{if(e.message)report('地图服务错误：'+e.message);});
window.mapDebugInfo=()=>({provider,mapReady,hasMap:!!map,hasLeaflet:!!window.L,status:status.textContent});
})();
