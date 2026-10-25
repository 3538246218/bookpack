"use strict";
(() => {
  const catalog=[
    {id:"ocean",name:"海岸晨光",detail:"细浪 · 暖光",mode:"day"},
    {id:"forest",name:"林间微雨",detail:"护眼绿 · 雨丝与流萤",mode:"green"},
    {id:"stars",name:"星夜远山",detail:"黑夜 · 星光与云雾",mode:"night"},
    {id:"rain",name:"雨夜窗景",detail:"深蓝 · 窗雨与灯火",mode:"night"}
  ];
  const canvas=document.createElement("canvas");canvas.id="landscape-canvas";canvas.setAttribute("aria-hidden","true");
  document.getElementById("app-scene").replaceChildren(canvas);
  const context=canvas.getContext("2d",{alpha:false});
  let width=1,height=1,settings={},playing=false,frame=0,last=0,time=0,timer=0,frames=0,reason="",active="ocean";
  const random=n=>{const x=Math.sin(n*127.1+311.7)*43758.5453;return x-Math.floor(x);};
  const gradient=(top,bottom)=>{const g=context.createLinearGradient(0,0,0,height);g.addColorStop(0,top);g.addColorStop(1,bottom);context.fillStyle=g;context.fillRect(0,0,width,height);};
  const glow=(x,y,r,color)=>{const g=context.createRadialGradient(x,y,0,x,y,r);g.addColorStop(0,color);g.addColorStop(1,"transparent");context.fillStyle=g;context.fillRect(x-r,y-r,r*2,r*2);};
  function hills(color,base,seed,amplitude){context.fillStyle=color;context.beginPath();context.moveTo(0,height);for(let x=0;x<=width+12;x+=12){const y=height*(base+Math.sin(x/width*4+seed)*amplitude+Math.sin(x/width*10+seed*3)*amplitude*.35);context.lineTo(x,y);}context.lineTo(width,height);context.fill();}
  function sea(t){
    const sunset=settings.preset==="sunset",morning=settings.preset==="morning";
    gradient(sunset?"#d7b4a3":morning?"#c4dce0":"#c6e0e8",sunset?"#579392":"#61a4b0");
    const horizon=height*.39,sunX=width*.75,sunY=height*.27;
    glow(sunX,sunY,width*.54,sunset?"#ffd4a177":"#fff9d955");
    context.fillStyle=sunset?"#fce0b8":"#f9f3d9";context.beginPath();context.arc(sunX,sunY,width*.055,0,Math.PI*2);context.fill();
    hills(sunset?"#c3a79888":"#8aaebc66",.4,3,.018);
    const water=context.createLinearGradient(0,horizon,0,height);water.addColorStop(0,sunset?"#759fa0":"#75afb9");water.addColorStop(1,sunset?"#397b79":"#2c8190");context.fillStyle=water;context.fillRect(0,horizon,width,height-horizon);
    const strength=Number(settings.waves??56)/100;
    for(let row=0;row<75;row++){
      const depth=row/74,y=horizon+Math.pow(depth,1.65)*(height-horizon);
      context.beginPath();for(let x=-12;x<=width+12;x+=12){const phase=x/(35+depth*50)+t*(.7+depth*.5)+row*2;const yy=y+Math.sin(phase)*(1+depth*5)*strength+Math.sin(phase*.43+t*.6)*depth*3*strength;x===-12?context.moveTo(x,yy):context.lineTo(x,yy);}
      context.strokeStyle=row%3===0?`rgba(227,248,240,${.13+depth*.16})`:`rgba(20,91,107,${.08+depth*.12})`;context.lineWidth=.6+depth;context.stroke();
      if(row%3===0){const x=sunX+Math.sin(row*43+t*.4)*width*.09*depth;context.fillStyle=`rgba(255,240,199,${.12+depth*.06})`;context.fillRect(x-width*depth*.08,y,width*depth*.16,1+depth);}
    }
  }
  function forest(t){
    gradient("#193b32","#789b75");glow(width*.68,height*.19,width*.75,"#d2e4ab44");
    hills("#315b45",.44,1,.07);hills("#244e3c",.54,2,.06);
    for(let i=0;i<28;i++){
      const depth=random(i+2),x=random(i+44)*width,h=height*(.22+depth*.43),base=height*(.64+depth*.33),sway=Math.sin(t*.5+i)*2;
      context.fillStyle=depth>.5?"#183f32":"#315b43";context.fillRect(x-2,base-h,4,h);
      for(let branch=0;branch<7;branch++){const by=base-h+branch*h*.11,bw=(branch+1)*h*.047;context.beginPath();context.moveTo(x+sway,by-h*.1);context.lineTo(x-bw,by+h*.18);context.quadraticCurveTo(x,by+h*.13,x+bw,by+h*.18);context.closePath();context.fill();}
    }
    for(let i=0;i<32;i++){const x=(random(i+130)*width+t*9)%width,y=(random(i+290)*height+t*(38+random(i+40)*25))%height;context.strokeStyle="#d2e9cf35";context.lineWidth=.7;context.beginPath();context.moveTo(x,y);context.lineTo(x-2,y+14);context.stroke();}
    for(let i=0;i<10;i++){const x=random(i+80)*width+Math.sin(t*.7+i)*8,y=height*(.57+random(i+83)*.35)+Math.cos(t*.6+i)*7;glow(x,y,5,`rgba(218,243,163,${.25+Math.sin(t+i)*.15})`);}
    const fog=context.createLinearGradient(0,height*.5,0,height);fog.addColorStop(0,"#b1d3ae00");fog.addColorStop(1,"#91b7a655");context.fillStyle=fog;context.fillRect(0,height*.5,width,height*.5);
  }
  function stars(t){
    gradient("#070e1d","#23344a");glow(width*.73,height*.23,width*.32,"#a4c3d01c");
    context.fillStyle="#d7dfc9";context.beginPath();context.arc(width*.73,height*.23,16,0,Math.PI*2);context.fill();
    for(let i=0;i<90;i++){const x=random(i+3)*width,y=random(i+104)*height*.6;context.fillStyle=`rgba(217,230,238,${.18+(.5+Math.sin(t*(.3+random(i)) + i)*.5)*.55})`;context.beginPath();context.arc(x,y,.4+random(i+62)*1.1,0,Math.PI*2);context.fill();}
    hills("#1a2c40",.62,2,.08);hills("#142335",.72,4,.08);hills("#0b1927",.85,6,.07);
    for(let i=0;i<5;i++){const y=height*(.58+i*.055),x=((t*4+i*width*.3)%(width*1.6))-width*.3;glow(x,y,width*.45,"#90aeb90b");}
  }
  function rain(t){
    gradient("#101c2c","#243b48");
    for(let i=0;i<17;i++){const x=random(i+33)*width,y=height*.45,h=height*(.15+random(i+122)*.25);context.fillStyle=i%2?"#162634":"#0b1928";context.fillRect(x,y,width*.1,h+height*.3);for(let k=0;k<6;k++){context.fillStyle=k%3?"#e4b97855":"#7eacbd44";context.fillRect(x+3+(k%2)*12,y+10+Math.floor(k/2)*19,4,7);}}
    glow(width*.23,height*.65,width*.25,"#f2ba5e23");glow(width*.76,height*.54,width*.22,"#96b8d01b");
    for(let i=0;i<80;i++){const speed=25+random(i+22)*65,x=(random(i+800)*width+Math.sin(i)*t*2+width)%width,y=(random(i+30)*height+t*speed)%height;context.strokeStyle=i%4?"#bdd6e33c":"#d4e7ee66";context.lineWidth=i%5===0?1.6:.7;context.beginPath();context.moveTo(x,y);context.lineTo(x-2,y+8+random(i+7)*25);context.stroke();}
    context.fillStyle="#050d1580";context.fillRect(width*.09,0,7,height);context.fillRect(width*.86,0,7,height);context.fillRect(0,height*.67,width,6);
  }
  function draw(){if(!context)return;({ocean:sea,forest,stars,rain}[active]||sea)(time);frames++;canvas.dataset.frames=String(frames);}
  function resize(){const box=canvas.parentElement.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,1.5);width=Math.max(1,box.width);height=Math.max(1,box.height);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);context?.setTransform(dpr,0,0,dpr,0,0);draw();}
  function tick(now){if(!playing)return;if(now-last>=1000/24){const delta=Math.min(.15,(now-last)/1000);last=now;time+=delta*(.25+Number(settings.waves??56)/100*1.25);draw();}frame=requestAnimationFrame(tick);}
  function apply(next,systemReduced=false){settings=next;active=catalog.some(row=>row.id===next.scene)?next.scene:"ocean";canvas.style.filter=`brightness(${.82+Number(next.daylight??55)*.004})`;const still=!next.motion||Number(next.waves)===0||systemReduced||document.hidden;reason=!next.motion?"动态景色已关闭":systemReduced?"系统已减少动态效果，景色静止":Number(next.waves)===0?"动态强度为 0，景色静止":document.hidden?"应用在后台，动态已暂停":"动态景色正在播放";const run=!still;if(playing!==run){playing=run;cancelAnimationFrame(frame);if(run){last=performance.now();frame=requestAnimationFrame(tick);}}canvas.dataset.playing=String(run);canvas.dataset.scene=active;draw();return {playing,reason,scene:active,mode:catalog.find(row=>row.id===active).mode,frames};}
  addEventListener("resize",()=>{clearTimeout(timer);timer=setTimeout(resize,120);});resize();
  window.BookpackScenes={catalog,apply,diagnostics:()=>({playing,reason,active,frames,width,height})};
})();
