# -*- coding: utf-8 -*-
"""Página do painel (HTML único, sem dependências externas) e exportação em CSV."""
import csv
import io
import time

PAINEL_HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel do Roteador</title>
<style>
:root{--bg:#f6f6f3;--card:#fff;--ink:#1c1c1a;--mut:#6b6b66;--line:#e3e2dc;--ok:#1f7a4d;--warn:#b26a00;--bad:#b3261e;--acc:#2a62d9;--bar:#e9e8e2}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecebe6;--mut:#9a9992;--line:#30302d;--ok:#4cc38a;--warn:#e0a13a;--bad:#ef7b72;--acc:#7aa2f7;--bar:#2b2b28}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px 24px;align-items:center;padding:14px 20px;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:5}
h1{font-size:17px;margin:0}
.kpi{display:flex;gap:20px;flex-wrap:wrap;margin-left:auto;color:var(--mut)}
.kpi b{color:var(--ink);font-size:16px}
main{max-width:1180px;margin:0 auto;padding:16px 16px 60px;display:grid;gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card h2{font-size:14px;margin:0 0 10px;letter-spacing:.2px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mut{color:var(--mut)}
.bar{height:8px;background:var(--bar);border-radius:6px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--acc)}
.alert{padding:8px 12px;border-radius:8px;border:1px solid var(--line);margin-bottom:6px}
.alert.critico{border-color:var(--bad);color:var(--bad)}.alert.aviso{border-color:var(--warn);color:var(--warn)}
button{font:inherit;padding:4px 10px;border-radius:7px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button:hover{border-color:var(--acc)}
.row{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.scroll{overflow:auto;max-height:420px}
a{color:var(--acc)}
</style>
</head>
<body>
<header>
  <h1>Painel do Roteador de IA</h1>
  <span class="mut" id="hora"></span>
  <div class="kpi">
    <span>Pedidos hoje <b id="k-ped">0</b></span>
    <span>Tokens hoje <b id="k-tok">0</b></span>
    <span id="k-eco"></span>
    <label><input type="checkbox" id="som"> som de alerta</label>
  </div>
</header>
<main>
  <section class="card" id="alertas"></section>
  <section class="grid" id="provedores"></section>
  <div class="grid">
    <section class="card"><h2>Modelos usados hoje</h2><div id="modelos"></div></section>
    <section class="card"><h2>Peso médio de cada pedido (última hora, em tokens)</h2><div id="pesos"></div>
      <p class="mut" style="margin:8px 0 0">Mostra onde os tokens vão. Se "ferramentas" e "histórico" dominam, ligue a economia no config.yaml.</p></section>
  </div>
  <div class="grid">
    <section class="card"><h2>Computador e modelo local</h2><div id="local"></div></section>
    <section class="card"><h2>Privacidade</h2><div id="priv"></div></section>
  </div>
  <section class="card" id="sug-card" hidden><h2>Sugestões para aprovar</h2><div id="sug"></div></section>
  <section class="card" id="aus-card" hidden><h2>Modelos que não existem mais</h2><div id="aus"></div></section>
  <section class="card"><h2>Problemas por modelo (desde o início)</h2><div id="prob"></div></section>
  <section class="card" id="ava-card" hidden><h2>Avaliação por tarefas (python avaliar.py)</h2><div id="ava"></div></section>
  <section class="card">
    <div class="row"><h2>Últimas decisões</h2><a href="/api/historico.csv">baixar CSV (sem texto dos pedidos)</a></div>
    <div class="scroll" id="hist"></div>
  </section>
</main>
<script>
const $=s=>document.querySelector(s);
const el=(t,a={},...k)=>{const e=document.createElement(t);for(const[x,v]of Object.entries(a)){if(x==='class')e.className=v;else if(x.startsWith('on'))e.addEventListener(x.slice(2),v);else e.setAttribute(x,v)}for(const c of k.flat()){if(c==null)continue;e.append(c.nodeType?c:document.createTextNode(String(c)))}return e};
const fmt=n=>new Intl.NumberFormat('pt-BR').format(n||0);
async function post(url,body){await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});load()}
function tabela(cols,rows){const t=el('table');t.append(el('tr',{},cols.map(c=>el('th',{},c))));for(const r of rows)t.append(el('tr',{},r.map(c=>el('td',{},c))));return t}
let criticosAntes=0;
function beep(){try{const a=new(window.AudioContext||window.webkitAudioContext)();const o=a.createOscillator();o.frequency.value=660;o.connect(a.destination);o.start();setTimeout(()=>{o.stop();a.close()},350)}catch(e){}}
function render(d){
  $('#hora').textContent='atualizado às '+d.agora;
  $('#k-ped').textContent=fmt(d.pedidos_hoje);
  $('#k-tok').textContent=fmt(d.tokens_hoje.in+d.tokens_hoje.out);
  $('#k-eco').textContent='';
  if(d.economia)$('#k-eco').append('Economia estimada hoje ',el('b',{},'US$ '+d.economia.poupado_usd));
  const al=$('#alertas');al.replaceChildren(el('h2',{},'Alertas'));
  if(!d.alertas.length)al.append(el('div',{class:'ok'},'Tudo certo por enquanto.'));
  for(const a of d.alertas)al.append(el('div',{class:'alert '+a.nivel},a.msg));
  const crit=d.alertas.filter(a=>a.nivel==='critico').length;
  if(crit>criticosAntes&&$('#som').checked)beep();criticosAntes=crit;
  const pv=$('#provedores');pv.replaceChildren();
  for(const p of d.provedores){
    const c=el('div',{class:'card'});
    c.append(el('div',{class:'row'},el('h2',{},p.nome+(p.pago?' (pago)':'')),
      el('button',{onclick:()=>post('/api/pausar',{provedor:p.nome,pausar:!p.pausado})},p.pausado?'Retomar':'Pausar')));
    if(p.pausado)c.append(el('div',{class:'warn'},'Pausado por você: fora do rodízio.'));
    const frac=1-p.pressao;
    c.append(el('div',{class:'mut'},'Cota diária usada: '+Math.round(frac*100)+'%'+(p.horas_restantes!=null?' · restam ~'+p.horas_restantes+' h no ritmo de hoje':'')));
    c.append(el('div',{class:'bar'},el('i',{style:'width:'+Math.round(frac*100)+'%'})));
    c.append(tabela(['Chave','Estado','Hoje'],p.chaves.map(k=>[k.n+' ('+k.mascara+')',
      el('span',{class:k.estado==='ativa'?'ok':k.estado==='em pausa'?'warn':'bad'},k.estado+(k.segundos?' ('+k.segundos+'s)':'')),
      fmt(k.usado)+(k.limite?' / '+fmt(k.limite):'')])));
    if(p.limite_dia||p.limite_mes){
      c.append(el('div',{class:'mut'},'Gasto: hoje US$ '+p.gasto_dia+(p.limite_dia?' de '+p.limite_dia:'')+' · mês US$ '+p.gasto_mes+(p.limite_mes?' de '+p.limite_mes:'')));
      c.append(el('button',{onclick:()=>post('/api/liberar_gasto',{provedor:p.nome})},'Liberar mais gasto hoje'));
    }
    pv.append(c);
  }
  $('#modelos').replaceChildren(d.modelos.length?tabela(['Modelo','Pedidos','Entrada','Saída'],d.modelos.map(m=>[m.modelo,fmt(m.pedidos),fmt(m.tin),fmt(m.tout)])):el('span',{class:'mut'},'Nenhum pedido ainda.'));
  const mx=Math.max(1,...Object.values(d.pesos_medios));
  $('#pesos').replaceChildren(...Object.entries(d.pesos_medios).map(([k,v])=>el('div',{},el('div',{class:'row'},el('span',{},k),el('span',{},fmt(v))),el('div',{class:'bar'},el('i',{style:'width:'+Math.round(v/mx*100)+'%'})))));
  const L=d.local;
  const lc=[];
  if(L.hardware){lc.push(el('div',{},'RAM: '+(L.hardware.ram_gb||'?')+' GB · Placa de vídeo: '+(L.hardware.gpu?L.hardware.gpu+' ('+L.hardware.vram_gb+' GB)':'não detectada')));}
  if(L.recomendacao)lc.push(el('div',{class:'mut'},L.recomendacao));
  lc.push(el('div',{},'Modelo local: '+(L.ativo?('estado '+L.estado+(L.modelo?' · '+L.modelo:'')):'desligado (local.ativo no config.yaml)')));
  if(L.erro)lc.push(el('div',{class:'bad'},L.erro));
  $('#local').replaceChildren(...lc);
  const P=d.privacidade;
  $('#priv').replaceChildren(el('div',{},'Detector: '+(P.ativo?'ligado':'DESLIGADO')),
    el('div',{},'Pedidos com dado sensível detectado hoje: '+P.com_deteccao_hoje),
    el('div',{},'Barrados (nada saiu): '+P.barrados_hoje),
    el('div',{class:P.externos_sensiveis_hoje?'bad':'ok'},'Sensíveis que saíram para a internet hoje: '+P.externos_sensiveis_hoje));
  $('#sug-card').hidden=!d.sugestoes.length;
  $('#sug').replaceChildren(...d.sugestoes.map(s=>el('div',{class:'row'},
    el('span',{},'Nível '+s.nivel+': colocar '+s.mover+' antes de '+s.antes_de+' (falhas '+Math.round(s.taxa_atual*100)+'% contra '+Math.round(s.taxa_nova*100)+'%)'),
    el('button',{onclick:()=>post('/api/aprovar_ordem',{nivel:s.nivel})},'Aprovar'))));
  $('#aus-card').hidden=!d.ausentes.length;
  $('#aus').replaceChildren(...d.ausentes.map(a=>el('div',{},a.provedor+'/'+a.modelo+' (nível '+a.nivel+')'+(a.sugestoes.length?' · parecidos: '+a.sugestoes.join(', '):''))));
  const pr=Object.entries(d.problemas);
  $('#prob').replaceChildren(pr.length?tabela(['Modelo','Ocorrências'],pr.map(([m,v])=>[m,Object.entries(v).map(([k,n])=>k+': '+n).join(' · ')])):el('span',{class:'mut'},'Nenhum problema registrado.'));
  const av=Object.entries(d.avaliacao||{});
  $('#ava-card').hidden=!av.length;
  $('#ava').replaceChildren(av.length?tabela(['Modelo','Nível','Acertos','Quando'],av.map(([m,v])=>[m,v.nivel,v.acertos+'/'+v.total+' ('+Math.round(100*v.acertos/v.total)+'%)',v.quando])):'');
  $('#hist').replaceChildren(tabela(['Hora','Nível','Motivo','Modelo','Chave','Tokens','Tempo','Situação'],d.historico.map(h=>[
    new Date(h.t*1000).toLocaleTimeString('pt-BR'),h.nivel||'',h.motivo||'',(h.prov||'-')+'/'+(h.modelo||'-'),h.chave||'',
    h.tin!=null?fmt(h.tin)+' / '+fmt(h.tout):'',h.ms!=null?(h.ms/1000).toFixed(1)+'s':'',
    el('span',{class:h.status==='ok'?'ok':'bad'},(h.status||'')+(h.trocas?' · '+h.trocas+' troca(s)':'')+(h.privado?' · privado':'')+(h.externo_sensivel?' · SENSÍVEL EXTERNO':''))])));
}
async function load(){try{const r=await fetch('/api/painel');if(r.status===401){document.body.textContent='Acesso negado. Abra o endereço completo mostrado no terminal do roteador.';return}render(await r.json())}catch(e){$('#hora').textContent='sem conexão com o roteador'}}
load();setInterval(load,3000);
</script>
</body>
</html>
"""


def historico_csv(historico):
    """Histórico em CSV. Só metadados: nunca o texto dos pedidos."""
    saida = io.StringIO()
    w = csv.writer(saida)
    w.writerow(["hora", "nivel", "motivo", "provedor", "modelo", "chave", "tokens_entrada", "tokens_saida",
                "tempo_ms", "situacao", "trocas", "privado", "dados_detectados", "sensivel_externo"])
    for h in historico:
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(h.get("t", 0))), h.get("nivel", ""),
                    h.get("motivo", ""), h.get("prov", ""), h.get("modelo", ""), h.get("chave", ""),
                    h.get("tin", ""), h.get("tout", ""), h.get("ms", ""), h.get("status", ""),
                    h.get("trocas", ""), h.get("privado", ""), "+".join(h.get("detectado", [])),
                    h.get("externo_sensivel", "")])
    return saida.getvalue()
