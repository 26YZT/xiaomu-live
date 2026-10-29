// Execute the real inline stage script with deterministic browser/RTC fakes. No network or model calls.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const scripts = [...fs.readFileSync('static/stage.html','utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
const flush = async()=>{for(let n=0;n<16;n++) await Promise.resolve();};

function fixture(query='?obs=1&role=obs&layer=avatar') {
  const elements = new Map(), events = new Map(), engines=[], recorders=[], sockets=[], posts=[];
  class Element {
    constructor(){this.style={};this.dataset={};this.children=[];this.events={};this.readyState=0;this.videoWidth=0;this.videoHeight=0;}
    set innerHTML(v){this.children=[];} get innerHTML(){return '';}
    append(...els){this.children.push(...els);}
    replaceChildren(...els){this.children=els;}
    querySelector(s){return this.children.find(x=>s==='video'||s.includes(x.dataset.uid))||null;}
    addEventListener(e,f){this.events[e]=f;} remove(){this.removed=true;}
    play(){return Promise.resolve();}
    requestVideoFrameCallback(f){this.frameCallback=f;}
  }
  class Engine {
    constructor(){this.handlers={};this.destroyed=false;engines.push(this);}
    on(e,f){(this.handlers[e]??=[]).push(f);}
    emit(e,...args){for(const f of this.handlers[e]||[])f(...args);}
    setRemoteViewConfig(){} setChannelProfile(){} setDefaultPublishLocalAudioStream(){}
    setDefaultPublishLocalVideoStream(){} setDefaultSubscribeAllRemoteAudioStreams(){} setDefaultSubscribeAllRemoteVideoStreams(){}
    joinChannel(){return Promise.resolve();} leaveChannel(){return Promise.resolve();} destroy(){this.destroyed=true;}
  }
  class Socket {constructor(){this.readyState=0;sockets.push(this);}close(){this.readyState=3;}send(){} }
  class Recorder {
    static isTypeSupported(){return true;}
    constructor(stream){this.stream=stream;this.state='inactive';recorders.push(this);}
    start(){this.state='recording';}stop(){this.state='inactive';}
  }
  const env={console,URLSearchParams,CSS:{escape:s=>s},crypto:{randomUUID:()=> 'test-client'},
    location:{search:query,origin:'http://localhost:28900'},innerWidth:1296,innerHeight:1080,
    navigator:{sendBeacon(){}},setInterval(){},setTimeout(){return 1;},clearTimeout(){},
    CustomEvent:class {constructor(type,{detail}){this.type=type;this.detail=detail;}},
    document:{documentElement:{dataset:{}},getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element(),createTextNode:s=>s,querySelectorAll:()=>[]},
    EventSource:class{addEventListener(){} close(){}},WebSocket:Socket,MediaRecorder:Recorder,
    MediaStream:class{constructor(tracks){this.tracks=tracks;}},
    support:Promise.resolve({support:true}),
    fetch:async(url,options)=>{if(options?.body)posts.push({url,...JSON.parse(options.body)});return {ok:url.includes('/api/'),json:async()=>({rtc:{provider:'artc',token:'fake',user_id:'fake'},live_id:'one'})};},
    addEventListener:(e,f)=>{(events.get(e)||events.set(e,[]).get(e)).push(f);},
  };
  env.window=env;
  env.AliRtcEngine={isSupported:()=>env.support,setLogLevel(){},getInstance:()=>new Engine()};
  env.dispatchEvent=e=>{for(const f of events.get(e.type)||[])f(e);};
  let now=10000; env.Date=class extends Date {static now(){return now;}};
  vm.createContext(env);for(const s of scripts)vm.runInContext(s,env);
  return {env,elements,engines,recorders,sockets,posts,run:s=>vm.runInContext(s,env),advance:n=>{now+=n;}};
}
const live={status:'live',mode:'vidu',live_id:'one',rtc_ready:true,profile:'test',queue:[],output_mode:'obs'};

test('stopping during asynchronous support check cannot join a retired session',async()=>{
  const f=fixture();let release;f.env.support=new Promise(r=>release=r);
  f.env.input=live; f.run('render(input)'); await flush();
  f.env.input={...live,status:'ended',rtc_ready:false};f.run('render(input)');
  release({support:true});await flush();
  assert.equal(f.engines.length,0);
  assert.equal(f.posts.filter(p=>p.event==='joined').length,0);
});

test('subscription acknowledgement is not reported as a received video frame',async()=>{
  const f=fixture();f.env.input=live;f.run('render(input)');await flush();
  f.engines[0].emit('videoSubscribeStateChanged','avatar',1,3);await flush();
  assert.equal(f.posts.filter(p=>p.event==='remote_video').length,0);
  const video=f.elements.get('videoBox').children[0];video.videoWidth=720;video.readyState=2;
  if(video.frameCallback)video.frameCallback(0,{});else video.events.loadeddata?.();
  await flush();
  const frame=f.posts.find(p=>p.event==='remote_video');assert.ok(frame);
  assert.equal(frame.live_id,'one');assert.equal(frame.client_id,'test-client');
});

test('audio arriving after video is added by restarting the recording segment',async()=>{
  const f=fixture('?broadcast=1');
  const track=kind=>({kind,readyState:'live',getSettings:()=>({width:720,height:1280}),addEventListener(){}});
  f.env.v=track('video');f.env.a=track('audio');
  f.run("state={status:'live',live_id:'one',broadcast:{target:'file'}};joinedLive='one';window.__remote.video=v;maybeBroadcast()");
  f.sockets[0].readyState=1;f.sockets[0].onopen();
  f.run('window.__remote.audio=a;maybeBroadcast()');f.advance(5000);f.run('maybeBroadcast()');
  assert.equal(f.recorders.at(-1).stream.tracks.length,2);
  assert.equal(f.recorders[0].state,'inactive');
});

test('a late websocket open cannot restart a cancelled recorder',()=>{
  const f=fixture('?broadcast=1');
  f.env.v={kind:'video',readyState:'live',getSettings:()=>({}),addEventListener(){}};
  f.run("state={status:'live',live_id:'one',broadcast:{target:'file'}};joinedLive='one';window.__remote.video=v;maybeBroadcast();stopPush('stop')");
  f.sockets[0].readyState=1;f.sockets[0].onopen();
  assert.equal(f.recorders[0].state,'inactive');
});
