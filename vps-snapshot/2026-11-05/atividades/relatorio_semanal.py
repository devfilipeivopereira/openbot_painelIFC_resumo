#!/usr/bin/env python3
"""Relatório semanal de produtividade (pt-BR) via melhor modelo gratuito do LiteLLM; salva em /opt/atividades/relatorios e no Drive/Produtividade.
Uso: relatorio_semanal.py all | last | week AAAA-Www  [--force]"""
import sys, json, re, os, time, datetime as dt, urllib.request
sys.path.insert(0, '/opt/atividades')
from atividades import sb, lit, llm_key, mask, log, TZ
BASE = '/opt/atividades'; OUT = BASE + '/relatorios'
ACC = 'googledrive_rager-helver'
PREF = ('gpt-oss-120b', 'nemotron-3-ultra', 'gemini-3.8-flash')
def chat(prompt, system):
    """melhor modelo gratuito: gpt-oss-120b (Groq/Cerebras) > nemotron ultra (OpenRouter free) > Gemini flash; nunca o 'tudo' (Codex)."""
    fallback = None
    for m in ['groq', 'cerebras', 'openrouter-free', 'gemini-flash']:
        for att in range(6):
            try:
                body = {'model': m, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}], 'temperature': 0.4, 'max_tokens': 4000}
                q = urllib.request.Request('http://172.18.0.1:4000/v1/chat/completions', data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + llm_key(), 'Content-Type': 'application/json'})
                with urllib.request.urlopen(q, timeout=240) as r: d = json.loads(r.read()); real = r.headers.get('x-litellm-model-name') or m
                txt = re.sub(r'(?s)<think>.*?</think>', '', d['choices'][0]['message'].get('content') or '').strip()
                u = d.get('usage') or {}
                sb(f"insert into atividades.uso_llm(modelo,chamadas,tokens_in,tokens_out,motivo) values ({lit(real)},1,{int(u.get('prompt_tokens',0))},{int(u.get('completion_tokens',0))},'relatorio_semanal');")
                if len(txt) > 400 and txt.lstrip().startswith('#'):
                    if any(p in real for p in PREF): return txt, real
                    fallback = fallback or (txt, real)
                time.sleep(3)
            except Exception as e:
                log('relatorio llm', m, att, type(e).__name__); time.sleep(10 * (att + 1))
    if fallback: return fallback
    raise RuntimeError('nenhum modelo gratuito respondeu')
def first(n):
    n = (n or '').strip()
    if not n or n.startswith('Contato') or re.search(r'\d{4}', n): return None
    w = re.sub(r'^(pr\.?|pastor|pra\.?|bispo|dr\.?|dra\.?)\s+', '', n, flags=re.I).split()
    return (w[0].capitalize() if w else None)
def week_data(mon):
    sun = mon + dt.timedelta(days=6); a, b = mon.isoformat(), sun.isoformat()
    def metr(x, y):
        return sb(f"""select count(*) total, count(*) filter (where area='Igreja') igreja, count(*) filter (where area='Família') familia, count(*) filter (where area='Pessoal') pessoal,
          count(*) filter (where fonte='whatsapp') conversas, coalesce(sum(n_out) filter (where fonte='whatsapp'),0) msgs_enviadas, coalesce(sum(n_in) filter (where fonte='whatsapp'),0) msgs_recebidas,
          count(*) filter (where pedido) pedidos, count(*) filter (where pedido and status='respondido') pedidos_respondidos, count(*) filter (where pedido and status='pendente') pedidos_pendentes,
          count(*) filter (where fonte='todoist') tarefas_concluidas, count(*) filter (where fonte like 'agenda%%') eventos, round(coalesce(sum(duracao_min) filter (where fonte like 'agenda%%'),0)/60.0,1) horas_agenda,
          count(*) filter (where tipo='e-mail') emails_enviados, round(coalesce(sum(duracao_min),0)/60.0,1) horas_estimadas, count(distinct data) dias_ativos
          from atividades.atividade where data between '{x}' and '{y}'""", rows=True)[0]
    cur = metr(a, b); prev = metr((mon - dt.timedelta(days=7)).isoformat(), (mon - dt.timedelta(days=1)).isoformat())
    porDia = sb(f"select to_char(data,'Dy DD/MM') d, area, count(*) n from atividades.atividade where data between '{a}' and '{b}' group by data,1,2 order by data", rows=True)
    contatos = sb(f"select com_quem, area, count(*) dias, sum(n_out) env, sum(n_in) rec from atividades.atividade where fonte='whatsapp' and data between '{a}' and '{b}' group by 1,2 order by 3 desc, 4 desc limit 15", rows=True)
    peds = sb(f"select to_char(data,'DD/MM') d, com_quem, area, status, resumo, round(extract(epoch from now()-pendente_desde)/3600) h from atividades.atividade where pedido and data between '{a}' and '{b}' order by data limit 60", rows=True)
    outros = sb(f"select to_char(data,'DD/MM') d, fonte, tipo, area, resumo, duracao_min from atividades.atividade where fonte<>'whatsapp' and data between '{a}' and '{b}' order by data limit 60", rows=True)
    grupos = sb(f"select com_quem, area, count(*) dias, sum(n_out) env from atividades.atividade where tipo='conversa (grupo)' and data between '{a}' and '{b}' group by 1,2 order by 4 desc limit 8", rows=True)
    def anon(s): return mask(re.sub(r'\b[A-ZÁÉÍÓÚ][a-záéíóúãõç]+ [A-ZÁÉÍÓÚ][a-záéíóúãõç]+( [A-ZÁÉÍÓÚ][a-záéíóúãõç]+)*', lambda m: m.group().split()[0], s or ''))[:110]
    return {
        'semana': f"{mon:%d/%m} a {sun:%d/%m/%Y}", 'parcial': sun >= dt.datetime.now(TZ).date(),
        'numeros_semana': cur, 'numeros_semana_anterior': prev,
        'atividades_por_dia_e_area': porDia,
        'principais_contatos_whatsapp': [{'nome': first(c['com_quem']) or 'contato sem nome', 'area': c['area'], 'dias_com_conversa': c['dias'], 'msgs_enviadas': c['env'], 'msgs_recebidas': c['rec']} for c in contatos],
        'grupos_onde_participou': [{'grupo': anon(g['com_quem'])[:40], 'area': g['area'], 'dias': g['dias'], 'msgs_enviadas': g['env']} for g in grupos],
        'pedidos_recebidos': [{'dia': p['d'], 'de': first(p['com_quem']) or 'contato', 'area': p['area'], 'status': p['status'], 'resumo_do_pedido': anon(p['resumo']), 'horas_pendente': p['h'] if p['status'] == 'pendente' else None} for p in peds],
        'tarefas_eventos_emails': [{'dia': o['d'], 'fonte': o['fonte'].split(':')[0], 'tipo': o['tipo'], 'area': o['area'], 'titulo': anon(o['resumo'])[:80], 'min': o['duracao_min']} for o in outros],
    }
SYSTEM = ("Você é um mentor de produtividade experiente, cristão, que conhece o contexto pastoral. Escreve para o Pastor Filipe (pastor da Igreja da Família Cristã - IFC, "
 "casado com Renata, pai de Daniel e Amanda) uma análise semanal em pt-BR, calorosa, honesta, encorajadora e nunca julgadora. Considere que ministério envolve cuidado de pessoas, "
 "que tempo com a família é prioridade legítima e que descanso (sabático) é saudável. Use só primeiros nomes. Nunca cite telefones, CPF, e-mails ou trechos literais de mensagens privadas. "
 "Baseie-se só nos dados; quando algo não puder ser medido (ex.: tempo exato de resposta, oração, estudo, visitas presenciais), diga que é estimativa ou que não aparece nas fontes. "
 "As fontes são WhatsApp (3 números), agenda Google, Todoist, e-mails enviados. 'horas_estimadas' é aproximação.")
def prompt(d):
    return ("Dados agregados da semana (JSON):\n" + json.dumps(d, ensure_ascii=False, default=str) + "\n\n"
     f"Escreva em markdown, começando exatamente com '# Produtividade — semana {d['semana'][:5]} a {d['semana'][8:13]}'" + (' (semana parcial)' if d['parcial'] else '') + ", com as seções:\n"
     "## Resumo em 3 linhas (exatamente 3 linhas)\n## Números-chave (tabela: métrica | esta semana | semana anterior | variação)\n"
     "## Igreja × Família × Pessoal (distribuição e avaliação do equilíbrio)\n## Pedidos e tempo de resposta (quantos, quantos respondidos, o que ficou pendente — descreva os pendentes de forma genérica)\n"
     "## Pontos fortes\n## Pontos de atenção\n## 3 sugestões práticas para a próxima semana (numeradas, concretas)\n"
     "Máximo ~600 palavras. Não invente números.")
def upload(name, text):
    from cx import CX, objs
    c = CX(); folder = json.load(open(BASE + '/drive.json'))['folder_id']
    r = c.run('GOOGLEDRIVE_CREATE_FILE_FROM_TEXT', ACC, {'file_name': name, 'mime_type': 'text/markdown', 'parent_id': folder, 'text_content': text})
    fid = next((o['id'] for o in objs(r) if isinstance(o.get('id'), str) and len(o['id']) > 20), None)
    if not fid: raise RuntimeError('upload falhou ' + json.dumps(r)[:200])
    return fid
def delete_drive(fid):
    from cx import CX
    CX().run('GOOGLEDRIVE_MOVE_FILE', ACC, {'file_id': fid, 'remove_parents': json.load(open(BASE + '/drive.json'))['folder_id'], 'add_parents': 'root'}) if False else None
def gen(mon, force=False):
    y, w, _ = mon.isocalendar(); name = f'produtividade-{y}-W{w:02d}.md'; st_path = BASE + '/relatorios/estado.json'
    os.makedirs(OUT, exist_ok=True); st = json.load(open(st_path)) if os.path.exists(st_path) else {}
    d = week_data(mon)
    if d['numeros_semana']['total'] == 0: log('relatorio', name, 'sem dados'); return
    if name in st and not force and not st[name].get('parcial'): log('relatorio', name, 'já existe'); return
    if name in st and st[name].get('parcial') and not force and not d['parcial']: pass  # semana completou: regenera
    elif name in st and not force: log('relatorio', name, 'já existe (parcial)'); return
    txt, model = chat(prompt(d), SYSTEM)
    txt = re.sub(r'\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b', '[cpf]', txt); txt = re.sub(r'\+?\d[\d\s().-]{7,}\d', '[número]', txt)
    txt += f"\n\n---\n_Gerado em {dt.datetime.now(TZ):%d/%m/%Y %H:%M} (BRT) pelo modelo gratuito {model}, a partir da planilha Atividades — Filipe._\n"
    open(f'{OUT}/{name}', 'w').write(txt)
    if name in st and st[name].get('drive_id'):
        from cx import CX
        fid = st[name]['drive_id']; CX().run('GOOGLEDRIVE_EDIT_FILE', ACC, {'file_id': fid, 'content': txt, 'mime_type': 'text/markdown'})
    else: fid = upload(name, txt)
    st[name] = {'drive_id': fid, 'modelo': model, 'parcial': d['parcial'], 'gerado': dt.datetime.now(TZ).isoformat()}
    json.dump(st, open(st_path, 'w'), indent=1); log('relatorio', name, 'ok', model)
if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'last'; force = '--force' in sys.argv
    today = dt.datetime.now(TZ).date(); this_mon = today - dt.timedelta(days=today.weekday())
    if cmd == 'all':
        first_day = dt.date.fromisoformat(sb("select min(data) d from atividades.atividade", rows=True)[0]['d'])
        m = first_day - dt.timedelta(days=first_day.weekday())
        while m <= this_mon:
            try: gen(m, force)
            except Exception as e: log('relatorio ERRO', m, type(e).__name__, str(e)[:200])
            m += dt.timedelta(days=7)
    elif cmd == 'last': gen(this_mon - dt.timedelta(days=7), force)
    elif cmd == 'week':
        y, w = sys.argv[2].split('-W'); gen(dt.date.fromisocalendar(int(y), int(w), 1), force)
