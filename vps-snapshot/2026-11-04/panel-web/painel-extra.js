/* Painel IFC · seções extras da VPS (painel_ifc, gerado por panel_enrich.py). Sem LLM, sem telefones. */
(function(){
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const brl=v=>Number(v||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
  const det=(id,title,sub,body,open)=>`<details class="px-sec" id="sec-${id}"${open?' open':''}><summary><b>${esc(title)}</b> <small>${esc(sub)}</small></summary><div class="px-body">${body}</div></details>`;
  function grupos(P){
    const ok=P.filter(g=>!g.erro).sort((a,b)=>(b.t||0)-(a.t||0));
    const tot=ok.reduce((s,g)=>s+(g.t||0),0);
    return det('px-participantes','Grupos e participantes',`${ok.length} grupos · ${tot} participações`,
      ok.map(g=>`<details class="px-g"><summary>${esc(g.label||g.g)} <small>${esc(g.c)} · ${g.t} membros · ${g.a} admins${g.s?` · ${g.s} sem nome`:''}</small></summary>
      <p><b>Admins:</b> ${esc((g.admins||[]).join(', ')||'—')}</p><p>${esc((g.membros||[]).join(', ')||'—')}</p></details>`).join('')
      +(P.some(g=>g.erro)?`<p class="px-mut">Sem acesso: ${esc(P.filter(g=>g.erro).map(g=>g.g).join(', '))}</p>`:''));
  }
  function visit(v){
    const t=v.totais||{},i=v.interacao_totais||{};
    return det('px-visitantes','Visitantes · interação',`${t.total||0} no total · ${t.d30||0} em 30 dias · ${t.d7||0} em 7 dias`,
      `<p>Mensagens enviadas: ${i.enviadas||0} · responderam: ${i.responderam||0} · sem mensagem: ${i.sem_mensagem||0} · taxa: ${i.taxa||0}%</p>
      <table class="px-t"><tr><th>Data</th><th>Nome</th><th>Vez</th></tr>${(v.lista||[]).map(x=>`<tr><td>${esc(x.data)}</td><td>${esc(x.nome)}</td><td>${esc(x.vez)}</td></tr>`).join('')}</table><p class="px-mut">${esc(v.fonte)}</p>`);
  }
  function fin(f){
    const ms=Object.keys(f.categorias||{}).sort().reverse();
    return det('px-financeiro','Financeiro · categorias e maiores lançamentos',`${ms.length} meses`,
      ms.map((m,k)=>{const c=f.categorias[m]||{};return `<details class="px-g"${k===0?' open':''}><summary>${esc(m)}</summary><div class="px-cols">${['RECEITA','DESPESA'].map(tp=>`<div><b>${tp==='RECEITA'?'Entradas':'Saídas'}</b><ul>${(c[tp]||[]).map(([n,v])=>`<li>${esc(n)}: ${brl(v)}</li>`).join('')}</ul></div>`).join('')}</div>
      <b>Maiores</b><ul>${((f.maiores||{})[m]||[]).map(x=>`<li>${esc(x.data)} · ${esc(x.descricao||x.categoria)}: ${brl(x.valor)}</li>`).join('')}</ul></details>`}).join(''));
  }
  function louvor(L){return det('px-louvor','Escala do louvor (grupo do ministério)',`${L.length} registros`,
    `<table class="px-t"><tr><th>Data</th><th>Ministro</th><th>Vocais</th><th>Instrumentos</th><th>Funções</th></tr>${L.map(x=>`<tr><td>${esc(x.data)}</td><td>${esc(x.ministro)}</td><td>${esc(x.vocais)}</td><td>${esc(x.instrumentos)}</td><td>${esc(x.funcoes)}</td></tr>`).join('')}</table>`);}
  function base(b){return det('px-base','Contatos fora dos grupos',`${b.homens_fora} homens · ${b.agenda_fora} da agenda · total IFC ${b.total_ifc_estimado} (${b.total_ifc_estimado_tipo||'estimado'})`,`<p class="px-mut">${esc(b.fonte)}. Base: ${b.base_homens} homens, ${b.base_agenda} contatos.</p>`);}
  function tec(p){
    const n=p.n8n||[],st=p.status||{};
    return det('px-tecnico','Técnico · automações e coleta',`${n.filter(x=>x.status!=='ok').length} com atenção · gerado ${p.gerado_em||''}`,
      `<table class="px-t"><tr><th>Fluxo</th><th>Status</th><th>Última</th><th>OK 7d</th><th>Erros 7d</th></tr>${n.map(x=>`<tr><td>${esc(x.nome)}</td><td>${esc(x.status)}</td><td>${esc(x.ultima)}</td><td>${x.ok7}</td><td>${x.err7}</td></tr>`).join('')}</table>
      <p>${Object.entries(st).map(([k,v])=>`${esc(k)}: ${esc(v)}`).join(' · ')}</p>`);
  }
  async function run(){
    try{
      const r=await fetch('api/painel',{credentials:'same-origin',cache:'no-store'});if(!r.ok)return;
      const d=await r.json(),p=d.painel_ifc;if(!p)return;
      let box=document.getElementById('px-extra');
      if(!box){box=document.createElement('section');box.id='px-extra';box.className='px-extra';const s=document.getElementById('sections');s.parentNode.insertBefore(box,s.nextSibling);}
      box.innerHTML=`<h2 class="px-h">Detalhes da VPS</h2>`+[p.participantes&&grupos(p.participantes),p.visitantes&&visit(p.visitantes),p.financeiro_detalhe&&fin(p.financeiro_detalhe),
        (p.louvor||[]).length&&louvor(p.louvor),p.base_contatos&&base(p.base_contatos),tec(p)].filter(Boolean).join('');
      const sel=document.getElementById('section-jump');
      if(sel){sel.querySelectorAll('option[data-px]').forEach(o=>o.remove());box.querySelectorAll('details.px-sec').forEach(x=>{const o=document.createElement('option');o.value=x.id;o.dataset.px='1';o.textContent='VPS · '+x.querySelector('summary b').textContent;sel.appendChild(o);});
        if(!sel.dataset.pxBound){sel.dataset.pxBound='1';sel.addEventListener('change',e=>{const el=document.getElementById(e.target.value);if(el&&el.classList.contains('px-sec')){el.open=true;el.scrollIntoView({behavior:'smooth',block:'start'});}});}}
    }catch(e){}
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',run);else run();
  document.addEventListener('click',e=>{if(e.target.closest('#refresh'))setTimeout(run,1500);});
})();
