import re
ONBOARD=re.compile(r'(risque suas tarefas|conectar seu calend|instalar todoist|adicionar se[cç][oõ]es|bem-vind|experimente|aplicativo m[oó]vel|atalho|modelo|template|explore|dica:|organize sua|use etiquetas|filtros e etiquetas|adicione sua primeira|todoist para|descobrir os layouts|revise sua entrada|visualiza[cç][oõ]es hoje)',re.I)
HEAD=re.compile(r'^(?:☀️|🌤️|🌙|🕑|🕐|🕒|🌅|🌆)\s*\*Resumo d[oa] (?:dia|manhã|tarde)\s*—\s*([^*]+)\*')
def edition_header(text):
    first,_,rest=text.partition('\n');m=HEAD.match(first)
    if not m:return text
    import datetime as _d
    from zoneinfo import ZoneInfo
    h=_d.datetime.now(ZoneInfo('America/Sao_Paulo')).hour
    label='☀️ *Resumo da manhã' if h<12 else '🌤️ *Resumo da tarde' if h<20 else '🌙 *Resumo do dia'
    return label+' — '+m.group(1).strip()+'*'+('\n'+rest if rest else '')
def polish(text):
    text=edition_header(text)
    lines=text.split('\n');out=[]
    # drop Todoist onboarding bullets
    lines=[l for l in lines if not (l.startswith('•') and ONBOARD.search(l))]
    has_todo=any(l.strip().startswith('*✅ Todoist') for l in lines)
    skip=False
    for l in lines:
        s=l.strip()
        if s.startswith('*Pendências prioritárias*') and has_todo: skip=True; continue
        if skip:
            if s.startswith('•') : continue
            skip=False
        if l.startswith('•'):
            l=re.sub(r'\.{2,}','.',l); l=l.replace('.;',';')
            for _ in range(4):
                l=re.sub(r'\s+(?:à|a|o|ao|de|da|do|para|em|no|na|com|e|um|uma|que|iria|ia|vai|seria|sobre)([.;])',r'\1',l)
            l=re.sub(r'^(• (?:Pra?\.\s)?[^\s;:]+(?: [A-Z][^\s;:]+)?) ([A-ZÁÉÍÓÚ])([a-zçãéíóú]+(?:ou|eu|iu))\b',lambda m:m.group(1)+' '+m.group(2).lower()+m.group(3),l)
            # style 04/11: capitalized verb right after a name (Kelly Avisou que -> Kelly avisou que)
            l=re.sub(r'(?<=\w) ([A-ZÁÉÍÓÚ])([a-zçãéíóú]{3,}(?:ou|eu|iu))(?= [a-zà-ú])',lambda m:' '+m.group(1).lower()+m.group(2),l)
            l=re.sub(r';\s*Resposta registrada:\s*',', e você respondeu: ',l)
            l=re.sub(r'\s*Mensagem recebida:\s*',' escreveu: ',l)
            l=re.sub(r';\s*Filipe (respondeu|enviou|disse|confirmou|pediu|agradeceu|explicou|orientou)\b',r', e você \1',l)
            l=re.sub(r'\bque (?:o )?Filipe\b','que você',l)
        if s.startswith('⚠️ Cobertura:') and len(s)>180:
            parts=[p.strip() for p in s[len('⚠️ Cobertura:'):].split(';') if p.strip()]
            notes=[p for p in parts if re.search(r'indispon|pausad|parcial|sem conteúdo',p,re.I)]
            l='⚠️ Cobertura parcial: '+('YouTube sem legendas; ' if any('YouTube' in p for p in notes) else '')+('algumas mídias não analisadas; ' if any('mídia' in p or 'vídeo' in p for p in notes) else '')+'detalhes no painel.'
        out.append(l)
    # remove empty section headers (header followed by blank/header/end)
    res=[]
    for i,l in enumerate(out):
        s=l.strip()
        if re.fullmatch(r'\*[^*]+\*',s) and not s.startswith('*🌙') and not s.startswith('*☀') :
            nxt=next((x.strip() for x in out[i+1:] if x.strip()),'')
            if not nxt or re.fullmatch(r'\*[^*]+\*',nxt) and not nxt.startswith('_') or nxt.startswith('📊') or nxt.startswith('⚠️ Cobertura'):
                if not (nxt.startswith('_')): continue
        res.append(l)
    text='\n'.join(res)
    return re.sub(r'\n{3,}','\n\n',text)
if __name__=='__main__':
    import json,sys;d=json.load(open(sys.argv[1]));print(polish(d['text']))
