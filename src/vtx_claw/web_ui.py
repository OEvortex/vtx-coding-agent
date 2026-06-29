from __future__ import annotations

from aiohttp import web

CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VTX Claw</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;height:100vh;display:flex;flex-direction:column}
.header{padding:12px 20px;background:#161b22;border-bottom:1px solid #30363d;display:flex;align-items:center;gap:10px}
.header h1{font-size:16px;font-weight:600}
.header .dot{width:8px;height:8px;border-radius:50%;background:#3fb950}
#chat{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}
.msg{max-width:70%;padding:10px 14px;border-radius:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
.msg.user{align-self:flex-end;background:#238636;color:#fff;border-bottom-right-radius:4px}
.msg.assistant{align-self:flex-start;background:#21262d;border:1px solid #30363d;border-bottom-left-radius:4px}
.msg.system{align-self:center;background:#1c2128;color:#8b949e;font-size:13px;border:1px solid #30363d}
#input-bar{padding:12px 20px;background:#161b22;border-top:1px solid #30363d;display:flex;gap:10px}
#input{flex:1;padding:10px 14px;background:#0d1117;border:1px solid #30363d;border-radius:8px;color:#c9d1d9;font-size:14px;resize:none;outline:none;font-family:inherit}
#input:focus{border-color:#58a6ff}
#send{padding:10px 20px;background:#238636;color:#fff;border:none;border-radius:8px;cursor:pointer;font-weight:600}
#send:hover{background:#2ea043}
#send:disabled{opacity:0.5;cursor:not-allowed}
.typing{display:none;align-self:flex-start;padding:10px 14px;background:#21262d;border:1px solid #30363d;border-radius:12px}
.typing.show{display:flex;gap:4px}
.typing span{width:6px;height:6px;background:#8b949e;border-radius:50%;animation:bounce .6s infinite alternate}
.typing span:nth-child(2){animation-delay:.2s}
.typing span:nth-child(3){animation-delay:.4s}
@keyframes bounce{to{opacity:.3;transform:translateY(-4px)}}
</style>
</head>
<body>
<div class="header"><div class="dot"></div><h1>VTX Claw</h1></div>
<div id="chat">
<div class="msg system">Connected to VTX Claw gateway. Send a message to start.</div>
</div>
<div class="typing" id="typing"><span></span><span></span><span></span></div>
<div id="input-bar">
<textarea id="input" rows="1" placeholder="Type a message..." autofocus></textarea>
<button id="send" onclick="send()">Send</button>
</div>
<script>
const chat=document.getElementById('chat'),input=document.getElementById('input'),typing=document.getElementById('typing');
let ws,sessionId='web-'+Date.now();
function connect(){
ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws`);
ws.onopen=()=>{ws.send(JSON.stringify({method:'connect',id:1,params:{protocol:1}}))};
ws.onmessage=(e)=>{
const d=JSON.parse(e.data);
if(d.type==='event'&&d.event==='agent'){
const data=d.data?.data||d.data;
if(data?.text){addMsg('assistant',data.text);typing.classList.remove('show')}
if(data?.phase==='end')typing.classList.remove('show');
}
};
}
connect();
function addMsg(role,content){
const div=document.createElement('div');div.className='msg '+role;div.textContent=content;
chat.appendChild(div);chat.scrollTop=chat.scrollHeight;
}
function send(){
const text=input.value.trim();if(!text)return;
addMsg('user',text);input.value='';typing.classList.add('show');
ws.send(JSON.stringify({method:'chat',id:Date.now(),params:{text,session_id:sessionId}}));
}
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}});
input.addEventListener('input',()=>{input.style.height='auto';input.style.height=Math.min(input.scrollHeight,120)+'px'});
</script>
</body>
</html>"""


def register_web_ui_routes(app: web.Application) -> None:
    async def index(request: web.Request) -> web.Response:
        return web.Response(text=CHAT_HTML, content_type="text/html")

    app.router.add_get("/", index)
    app.router.add_get("/ui", index)
