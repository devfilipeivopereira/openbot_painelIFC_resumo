"""Export private, sanitized current collection artifacts for OpenBot routines."""
import collections,json,os,re,tempfile,unicodedata
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
from zoneinfo import ZoneInfo
STAGING=Path('/var/lib/docker/volumes/ifc-mcp-state/_data/data/staging')
OUTPUT=Path('/var/lib/docker/volumes/openbot-data/_data/ifc/painel/raw')
PATTERNS=[re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),re.compile(r"[\w.-]+@(?:s\.whatsapp\.net|g\.us|lid)",re.I),re.compile(r"(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)"),re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),re.compile(r"(?<!\w)@\d{10,}(?!\d)")]
DELIVERY_SOURCE_KEYS=('drive_secretaria','instagram','evolution_grupos','evolution_mulheres','mensagens_diretas','email_secretaria')
def clean(value):
 text=re.sub(r"[\r\n]+"," ",str(value or ''))
 for pattern in PATTERNS:text=pattern.sub('[removido]',text)
 return text
def event_day(row):
 if not isinstance(row,dict):return None
 value=row.get('date') or row.get('start') or row.get('startTime')
 if isinstance(value,dict):value=value.get('date') or value.get('dateTime')
 if not isinstance(value,str) or len(value)<10:return None
 try:
  if len(value)==10:return date.fromisoformat(value)
  parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
  return parsed.astimezone(ZoneInfo('America/Sao_Paulo')).date() if parsed.tzinfo else parsed.date()
 except ValueError:return None
def summary_context():
 state=json.loads((STAGING.parent.parent/'state.json').read_text())
 context={};now=datetime.now(timezone.utc)
 allowed={'youtube':('coverage_24h_complete','caption_coverage','limitations'),'instagram':('coverage_24h_complete','content_coverage_complete','analysis_scope','posts_without_caption','limitations'),'agenda':('events','window_start','window_end'),'todoist':('tasks','coverage_complete','total_open_tasks'),'financeiro':('mensal','max_data','ult_import','sem_data','saldo_inicial','por_ano_ate_mes','categorias'),'visitantes':('total','ultimos_30_dias','ultimos_7_dias','enviadas_sem_resposta_registrada','nota'),'n8n':('workflows',),'email_secretaria':('coverage_24h_complete','body_coverage_complete','attachment_coverage_complete','attachment_unknown_records','attachment_pending_reasons')}
 def sanitize(value):
  if isinstance(value,str):return clean(value)
  if isinstance(value,list):return [sanitize(x) for x in value]
  if isinstance(value,dict):return {k:sanitize(v) for k,v in value.items() if not re.search(r'id$|token|secret|password|senha|telefone|phone|cpf|email|url|contato|recipient',k,re.I)}
  return value if value is None or isinstance(value,(bool,int,float)) else None
 for source,fields in allowed.items():
  if state.get('steps',{}).get(source,{}).get('status')!='succeeded':
   context[source]={'status':'not_current'};continue
  try:
   payload=json.loads((STAGING/(source+'.json')).read_text())
   stamp=payload.get('generated_at',payload.get('checked_at'));parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
   if parsed.tzinfo is None or not -300<=(now-parsed.astimezone(timezone.utc)).total_seconds()<=86400:raise ValueError('stale')
   data=sanitize({k:payload[k] for k in fields if k in payload})
   if source=='instagram':
    posts=payload.get('messages')
    data['publications_24h']=len(posts) if isinstance(posts,list) else None
   if source=='youtube':
    recent=payload.get('recent_24h')
    data['publications_24h']=len(recent) if isinstance(recent,list) else None
   if source=='email_secretaria':
    mailbox_counts=payload.get('mailboxes') if isinstance(payload.get('mailboxes'),dict) else {}
    data['mailbox_counts']={'dev_kern':mailbox_counts.get('secretaria_dev_kern'),'hotmail':mailbox_counts.get('secretaria_hotmail')}
   if source=='visitantes':
    recent=payload.get('recentes') if isinstance(payload.get('recentes'),list) else []
    def marked(value):return str(value or '').strip().casefold() in {'true','1','sim','yes','enviado','sent','respondido','replied'}
    pending=[]
    for row in recent[:100]:
     if not isinstance(row,dict):continue
     status=str(row.get('status') or '').casefold()
     sent=marked(row.get('mensagem_enviada')) or bool(re.search(r'enviad|sent|disparad',status))
     replied=marked(row.get('resposta_registrada')) or bool(re.search(r'respondid|responded|reply|retorno recebido',status))
     if sent and not replied:
      item={key:row.get(key) for key in ('nome','data','mensagem_enviada','resposta_registrada','status') if key in row}
      pending.append(sanitize(item))
    table_total=data.get('total') if type(data.get('total')) is int else None
    data['acompanhamentos_sem_resposta_registrada']={
     'total_registrado_na_tabela':data.get('enviadas_sem_resposta_registrada'),
     'registros_analisados':min(len(recent),100),
     'total_registros_tabela':table_total,
     'registros_fora_da_amostra':max(0,table_total-min(len(recent),100)) if table_total is not None else None,
     'identificados_nos_registros_analisados':len(pending),
     'pessoas':pending,
    }
   if source=='agenda' and isinstance(data.get('events'),list):
    today=datetime.now(ZoneInfo('America/Sao_Paulo')).date();last=today+timedelta(days=7)
    last_30=today+timedelta(days=30)
    calendar_events=data['events']
    data['events']=[event for event in calendar_events if event_day(event) is not None and today<=event_day(event)<=last]
    data['events_7d']=data['events']
    data['events_8_30']=[event for event in calendar_events if event_day(event) is not None and last<event_day(event)<=last_30]
    data['events_date_unresolved']=[event for event in calendar_events if event_day(event) is None]
    data['window']='today_through_30_days; split_into_7_day_and_8_30_day_windows; unresolved_dates_retained'
   if source=='financeiro':
    dated=data.get('max_data')
    yearly=data.get('por_ano_ate_mes')
    monthly=data.get('mensal')
    year=dated[:4] if isinstance(dated,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',dated) else None
    if year and isinstance(yearly,list):
     rows=[row for row in yearly if isinstance(row,dict) and str(row.get('ano'))==year and isinstance(row.get('res'),(int,float))]
     if rows:
      through=max(str(row.get('mes')) for row in rows)
      current_months=[row for row in monthly if isinstance(row,dict) and str(row.get('mes','')).startswith(year)] if isinstance(monthly,list) else []
      data['year_to_date']={'ano':year,'ate_data':dated,'ate_mes':through,'movimento_liquido_registrado':round(sum(float(row['res']) for row in rows),2),'fluxo_neutro_bruto':round(sum(float(row.get('neutras_valor_bruto',0)) for row in current_months),2),'meses_com_registros':len(rows),'lancamentos_receita':sum(int(row.get('entradas_n',0)) for row in rows),'lancamentos_despesa':sum(int(row.get('saidas_n',0)) for row in rows),'lancamentos_neutros':sum(int(row.get('neutras_n',0)) for row in rows),'lancamentos_sem_classificacao':sum(int(row.get('sem_classificacao_n',0)) for row in rows),'valor_sem_classificacao':round(sum(float(row.get('sem_classificacao_valor',0)) for row in rows),2)}
     else:data['year_to_date']=None
    else:data['year_to_date']=None
   context[source]={'status':'current','generated_at':stamp,'data':data}
  except (OSError,ValueError,TypeError,KeyError):context[source]={'status':'not_current'}
 media_coverage={}
 for source in ('evolution_grupos','evolution_mulheres','mensagens_diretas'):
  if state.get('steps',{}).get(source,{}).get('status')!='succeeded':continue
  try:
   payload=json.loads((STAGING/(source+'.json')).read_text())
   stamp=payload.get('generated_at')
   if not isinstance(stamp,str):continue
   parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
   if parsed.tzinfo is None or not -300<=(now-parsed.astimezone(timezone.utc)).total_seconds()<=86400:continue
   records=payload.get('messages')
   if not isinstance(records,list):continue
   unavailable=0;transcribed=0;unread=0;partial=0;ocr_text=0;document_text=0;visual_ai=0;video_partial=0
   for row in records:
    if not isinstance(row,dict):continue
    kind=str(row.get('type','')).casefold();body=str(row.get('text',''))
    is_audio=any(token in kind for token in ('audio','ptt','voice'))
    if is_audio:
     if '[áudio transcrito]' in body:transcribed+=1
     elif body.startswith('[Áudio indisponível'):unavailable+=1
    else:
     normalized=body.casefold()
     if any(marker in normalized for marker in ('vídeo não analisado: visual resumido por amostragem','vídeo analisado parcialmente: visual por amostragem','conteúdo visual analisado por amostragem, não integralmente')):video_partial+=1
     elif re.search(r'\bnão analisad[oa](?::|\])',normalized):
      if 'conteúdo visual ou do arquivo indisponível' in normalized:unread+=1
      else:partial+=1
     if not is_audio and '[áudio transcrito]' in body:transcribed+=1
     if 'ocr da imagem' in normalized or '[ocr;' in normalized:ocr_text+=1
     if '[descrição visual por ia' in normalized:visual_ai+=1
     if 'conteúdo textual extraído' in normalized:document_text+=1
   media_coverage[source]={'records':len(records),'audio_transcribed':transcribed,'audio_unavailable':unavailable,'ocr_text_extracted':ocr_text,'document_text_extracted':document_text,'media_content_unavailable':unread,'media_analysis_partial':partial,'visual_ai_descriptions':visual_ai,'video_sampled_partial':video_partial}
   if source=='evolution_grupos':
    groups=payload.get('groups') if isinstance(payload.get('groups'),list) else []
    media_coverage[source]['groups_monitored']=sum(1 for row in groups if isinstance(row,dict))
  except (OSError,ValueError,TypeError,KeyError):continue
 context['media_coverage']=media_coverage
 context['unavailable_sources']=[name for name,value in state.get('steps',{}).items() if isinstance(value,dict) and value.get('status') in {'failed','not_configured','executor_not_implemented'} and name!='ata']
 return context

def finance_digest_block(supporting_context):
 entry=supporting_context.get('financeiro') if isinstance(supporting_context,dict) else None
 if not isinstance(entry,dict) or entry.get('status')!='current':return ''
 data=entry.get('data')
 if not isinstance(data,dict):return ''
 rows=data.get('mensal') if isinstance(data.get('mensal'),list) else []
 rows=[row for row in rows if isinstance(row,dict) and isinstance(row.get('mes'),str) and re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',row['mes'])]
 if not rows:return ''
 latest=max(rows,key=lambda row:row['mes'])
 def brl(value):
  if type(value) not in (int,float):return 'não informado'
  amount=f'{abs(float(value)):,.2f}'.replace(',','_').replace('.',',').replace('_','.')
  return ('-R$ ' if value<0 else 'R$ ')+amount
 month='{:02d}/{}'.format(int(latest['mes'][5:]),latest['mes'][:4])
 values=(('entradas',latest.get('entradas')),('saídas',latest.get('saidas')))
 parts=[f'{label} {brl(value)}' for label,value in values]
 ent,sai=latest.get('entradas'),latest.get('saidas')
 result=round(float(ent)-float(sai),2) if type(ent) in (int,float) and type(sai) in (int,float) else latest.get('saldo')
 parts.append(f'resultado do mês {brl(result)}')
 line='*💰 Finanças*\nFinanceiro '+month+': '+'; '.join(parts)
 alerts=[]
 if type(result) in (int,float) and result<0:alerts.append('⚠️ Resultado negativo.')
 stamp=data.get('max_data')
 if isinstance(stamp,str):
  try:
   age=(datetime.now(ZoneInfo('America/Sao_Paulo')).date()-date.fromisoformat(stamp[:10])).days
   if age>10:alerts.append('⚠️ Dados desatualizados há mais de 10 dias.')
  except ValueError:pass
 return line+(chr(10)+chr(10).join(alerts[:1]) if alerts else '')

def todoist_digest_block(supporting_context):
 entry=supporting_context.get('todoist') if isinstance(supporting_context,dict) else None
 if not isinstance(entry,dict) or entry.get('status')!='current':return ''
 data=entry.get('data')
 if not isinstance(data,dict) or data.get('coverage_complete') is not True:return ''
 tasks=data.get('tasks') if isinstance(data.get('tasks'),list) else []
 tasks=[task for task in tasks if isinstance(task,dict) and not _todoist_onboarding(task)]
 if not tasks:return ''
 labels={'atrasada':'atrasadas','hoje':'para hoje','futura':'futuras','sem_prazo':'sem prazo','prazo_nao_interpretado':'prazo a conferir'}
 counts=collections.Counter(str(task.get('status','prazo_nao_interpretado')) for task in tasks)
 parts=[f'{count} {labels.get(state,"prazo a conferir")}' for state,count in counts.items() if count]
 total=len(tasks)
 detail='; '.join(parts) if parts else 'nenhuma pendência'
 no_due=sorted((task for task in tasks if task.get('status')=='sem_prazo'),key=lambda task:(-(task.get('priority') if type(task.get('priority')) is int else 0),str(task.get('content','')).casefold()))
 no_due_extra=max(0,len(no_due)-3)
 shown='; '.join(str(task.get('content','')) for task in no_due[:3])
 tail=(f'; sem prazo: {shown}' if shown else '')+(f'; +{no_due_extra} sem prazo' if no_due_extra else '')
 urgent=[]
 for task in tasks:
  state=str(task.get('status',''))
  due=task.get('due_date')
  include=state in {'atrasada','hoje'} or (state=='prazo_nao_interpretado' and (task.get('priority') or 0)>=4)
  if state=='futura' and isinstance(due,str):
   try:include=datetime.fromisoformat(due[:10]).date()<=datetime.now(ZoneInfo('America/Sao_Paulo')).date()+timedelta(days=7)
   except ValueError:include=False
  if include:urgent.append(task)
 urgent.extend(task for task in no_due[:3] if task not in urgent)
 urgent.sort(key=lambda task:(0 if task.get('status')=='atrasada' else 1 if task.get('status')=='hoje' else 2,-(task.get('priority') if type(task.get('priority')) is int else 0),str(task.get('content','')).casefold()))
 names=[]
 for task in urgent[:8]:
  title=clean(task.get('content','')).strip()
  if title:
   due=task.get('due_date')
   due_label=''
   if isinstance(due,str):
    try:due_label=' — '+datetime.fromisoformat(due[:10]).strftime('%d/%m')
    except ValueError:pass
   if task.get('status')=='atrasada':due_label=' — atrasada'
   elif task.get('status')=='hoje':due_label=' — hoje'
   elif task.get('status')=='prazo_nao_interpretado':due_label=' — prazo a conferir'
   names.append('• '+title+due_label)
 if no_due_extra:names.append(f'• +{no_due_extra} sem prazo')
 return '*✅ Todoist*'+(('\n'+'\n'.join(names)) if names else '')

def _todoist_onboarding(task):
 title=str(task.get('content','')).casefold()
 project=str(task.get('project','') or task.get('project_name','')).casefold()
 words=('adicionar secoes','instalar todoist','explore','experimente','baixe','conecte','conectar seu calendario','getting started','bem-vindo','bem vindo','dica','risque suas tarefas','todoist para','aplicativo movel','use etiquetas','filtros e etiquetas','adicione sua primeira','organize sua','arraste','atalho','modelo de projeto','inbox zero','caixa de entrada zero')
 normalized=unicodedata.normalize('NFKD',title+' '+project).encode('ascii','ignore').decode()
 return any(word in normalized for word in words)

def visitors_digest_block(supporting_context):
 entry=supporting_context.get('visitantes') if isinstance(supporting_context,dict) else None
 if not isinstance(entry,dict) or entry.get('status')!='current':return ''
 data=entry.get('data') if isinstance(entry.get('data'),dict) else {}
 follow=data.get('acompanhamentos_sem_resposta_registrada')
 count=data.get('ultimos_7_dias')
 pending=follow.get('identificados_nos_registros_analisados') if isinstance(follow,dict) else 0
 if not pending and isinstance(data.get('enviadas_sem_resposta_registrada'),int):pending=data['enviadas_sem_resposta_registrada']
 if type(count) is not int or count<0 or type(pending) is not int or pending<0 or not (count or pending):return ''
 return f'🤝 Visitantes: {count} novos nesta semana; {pending} aguardando contato'

def agenda_digest_block(supporting_context):
 entry=supporting_context.get('agenda') if isinstance(supporting_context,dict) else None
 if not isinstance(entry,dict) or entry.get('status')!='current':return ''
 data=entry.get('data') if isinstance(entry.get('data'),dict) else {}
 zone=ZoneInfo('America/Sao_Paulo')
 def clock(value):
  if not isinstance(value,str) or len(value)<16:return None,None
  try:
   parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
   local=parsed.astimezone(zone) if parsed.tzinfo else parsed
   return local.date(),local.strftime('%Hh%M')
  except ValueError:return None,None
 def render(event):
  day=event_day(event);date_label=day.strftime('%d/%m') if day else 'data a confirmar'
  title=clean(event.get('title') or 'Evento sem título')
  start_day,start_time=clock(event.get('date'))
  end_day,end_time=clock(event.get('end'))
  if event.get('all_day') is True:time_label='dia inteiro'
  elif start_time:
   time_label=start_time
   if end_time and end_day==start_day:time_label+='–'+end_time
  else:time_label='horário não informado'
  status=str(event.get('status') or '').casefold()
  labels={'tentative':'provisório','confirmed':'confirmado'}
  parts=[date_label,time_label,title]
  location=clean(event.get('location'))
  if location:parts.append(location)
  if status in labels:parts.append(labels[status])
  return '• '+' · '.join(parts)
 events7=data.get('events_7d') if isinstance(data.get('events_7d'),list) else data.get('events') if isinstance(data.get('events'),list) else []
 events830=[]
 unresolved=data.get('events_date_unresolved') if isinstance(data.get('events_date_unresolved'),list) else []
 lines=['*📅 Agenda 7 dias*']
 events7=[row for row in events7 if isinstance(row,dict) and str(row.get('status','')).casefold()!='cancelled']
 events830=[row for row in events830 if isinstance(row,dict) and str(row.get('status','')).casefold()!='cancelled']
 unresolved=[row for row in unresolved if isinstance(row,dict) and str(row.get('status','')).casefold()!='cancelled']
 lines.extend(render(row) for row in events7 if isinstance(row,dict))
 if unresolved:
  lines.extend('• data a confirmar · '+clean(row.get('title') or 'Evento sem título') for row in unresolved)
 if not events7 and not unresolved:return ''
 return '\n'.join(lines)

def delivery_source_snapshot(source_timestamps,now=None):
 if not isinstance(source_timestamps,dict):return None
 moment=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
 filenames={'drive_secretaria':'drive_secretaria.json','instagram':'instagram.json','evolution_grupos':'evolution_grupos.json','evolution_mulheres':'evolution_mulheres.json','mensagens_diretas':'mensagens_diretas.json','email_secretaria':'email_secretaria.json'}
 try:state=json.loads((STAGING.parent.parent/'state.json').read_text())
 except (OSError,ValueError,TypeError):return None
 snapshot={}
 for source in DELIVERY_SOURCE_KEYS:
  stamp=source_timestamps.get(source)
  if not isinstance(stamp,str) or state.get('steps',{}).get(source,{}).get('status')!='succeeded':return None
  try:parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
  except ValueError:return None
  try:payload=json.loads((STAGING/filenames[source]).read_text())
  except (OSError,ValueError,TypeError,KeyError):return None
  if payload.get('generated_at')!=stamp or parsed.tzinfo is None:return None
  age=(moment-parsed.astimezone(timezone.utc)).total_seconds()
  if not -300<=age<=86400:return None
  snapshot[source]={'status':'current','generated_at':stamp}
 return snapshot

def export():
 state=json.loads((STAGING.parent.parent/'state.json').read_text())
 for key in ('evolution_grupos','evolution_mulheres','email_secretaria','agenda'):
  if state.get('steps',{}).get(key,{}).get('status')!='succeeded':raise ValueError('artifact_collection_not_succeeded')
 now=datetime.now(timezone.utc);day=now.astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%Y%m%d')
 specs=[('evolution_grupos.json','resumo_grupos_'+day+'.txt','messages',('group','at','sender','text')),('evolution_mulheres.json','mulheres_ultimas24h.txt','messages',('at','sender','text')),('email_secretaria.json','email_secretaria_'+day+'.txt','messages',('received_at','mailbox','sender','subject','type','text','snippet')),('agenda.json','agenda_7d_'+day+'.txt','events',('date','title','location','end'))]
 outputs={};stamps={}
 for source,name,key,fields in specs:
  payload=json.loads((STAGING/source).read_text());stamp=datetime.fromisoformat(payload['generated_at'].replace('Z','+00:00'))
  age=(now-stamp.astimezone(timezone.utc)).total_seconds()
  if stamp.tzinfo is None or not -300<=age<=86400:raise ValueError('artifact_source_not_current')
  rows=payload.get(key)
  if not isinstance(rows,list):raise ValueError('artifact_source_invalid')
  if source=='agenda':
   today=now.astimezone(ZoneInfo('America/Sao_Paulo')).date();last=today+timedelta(days=7)
   rows=[row for row in rows if event_day(row) is None or today<=event_day(row)<=last]
  lines=[]
  for row in rows:
   if not isinstance(row,dict):raise ValueError('artifact_record_invalid')
   if any(x in str(row.get('type','')).lower() for x in ('audio','ptt','voice')) and not str(row.get('text','')).startswith(('[áudio transcrito]', '[Áudio indisponível')):raise ValueError('artifact_audio_missing')
   prefix='DATA NÃO IDENTIFICADA | ' if source=='agenda' and event_day(row) is None else ''
   line=prefix+' | '.join(clean(row.get(field)) for field in fields if row.get(field) not in (None,''))
   if source=='agenda':
    extras=[]
    if row.get('description'):extras.append('Detalhes: '+clean(row.get('description')))
    if row.get('organizer'):extras.append('Responsável: '+clean(row.get('organizer')))
    attendees=row.get('attendees') if isinstance(row.get('attendees'),list) else []
    labels={'accepted':'confirmado','tentative':'talvez','declined':'recusou','needsAction':'sem resposta'}
    if row.get('attendee_count'):
     names=['{} ({})'.format(clean(item.get('name')),labels.get(item.get('response_status'),'estado não informado')) for item in attendees[:20] if isinstance(item,dict) and item.get('name')]
     extras.append('Participantes ({}){}'.format(row.get('attendee_count'),': '+', '.join(names) if names else ': nomes não disponíveis'))
     if row.get('attendees_omitted') or len(attendees)<int(row.get('attendee_count') or 0) or len(attendees)>20:extras.append('Lista de participantes parcial')
    rsvp=row.get('attendee_rsvp_counts')
    if isinstance(rsvp,dict) and any(isinstance(n,int) and n>0 for n in rsvp.values()):
     extras.append('Respostas: '+'; '.join(labels.get(k,k)+': '+str(n) for k,n in rsvp.items() if isinstance(n,int) and n>0))
    if row.get('all_day') is True:extras.append('Dia inteiro')
    if row.get('status') not in (None,'','confirmed'):extras.append('Confirmação: '+clean(row.get('status')))
    if row.get('recurring') is True:extras.append('Recorrente')
    if extras:line+=' | '+' | '.join(extras)
   lines.append(line)
  outputs[name]='\n'.join(lines)+'\n';stamps[source]=payload['generated_at']
 scale_path=Path('/opt/ifc-painel/box/ifc/painel/escala_domingo.json')
 scale=json.loads(scale_path.read_text(encoding='utf-8'))
 if not isinstance(scale,dict) or not isinstance(scale.get('escala'),list):raise ValueError('manual_schedule_invalid')
 today=now.astimezone(ZoneInfo('America/Sao_Paulo')).date();schedule=[]
 for row in scale['escala']:
  if not isinstance(row,dict):raise ValueError('manual_schedule_row_invalid')
  scheduled=date.fromisoformat(row['data'])
  if today<=scheduled<=today+timedelta(days=7):
   schedule.append(' | '.join(clean(row.get(field)) for field in ('data','abertura','ministro_louvor','pregacao','obs')))
 modified=datetime.fromtimestamp(scale_path.stat().st_mtime,timezone.utc).isoformat()
 outputs['agenda_7d_'+day+'.txt']+='\nEscala manual â€” campos: data | abertura | louvor | pregaÃ§Ã£o | observaÃ§Ãµes. HorÃ¡rios nÃ£o inferidos. Fonte modificada em '+modified+'\n'+'\n'.join(schedule)+'\n'
 OUTPUT.mkdir(parents=True,exist_ok=True,mode=0o700);os.chown(OUTPUT,1000,1000);os.chmod(OUTPUT,0o700)
 for name,content in outputs.items():
  fd,tmp=tempfile.mkstemp(prefix='.artifact-',dir=OUTPUT)
  try:
   os.fchmod(fd,0o600);os.fchown(fd,1000,1000)
   with os.fdopen(fd,'w',encoding='utf-8') as out:out.write(content);out.flush();os.fsync(out.fileno())
   os.replace(tmp,OUTPUT/name)
  finally:
   if os.path.exists(tmp):os.unlink(tmp)
 return {'status':'ok','files':len(outputs),'day':day,'source_timestamps':stamps,'family_file_generated':False,'escala_included':True,'schedule_entries':len(schedule)}
def curation_source_coverage_block(record_coverage,supporting_context):
 if not isinstance(record_coverage,dict):return ''
 media=supporting_context.get('media_coverage',{}) if isinstance(supporting_context,dict) else {}
 groups=media.get('evolution_grupos',{}) if isinstance(media,dict) else {}
 labels=(('grupos','Grupos WhatsApp','mensagens'),('mensagens_diretas','DMs','mensagens'),('mulheres','Mulheres IFC','mensagens'),('drive_secretaria','Drive','trechos'),('instagram','Instagram','publicações'),('youtube','YouTube','vídeos novos'))
 parts=[]
 for source,label,unit in labels:
  row=record_coverage.get(source)
  if isinstance(row,list):
   if any(type(index) is not int or index<0 for index in row) or row!=list(range(len(row))):continue
   covered=total=len(row)
  elif isinstance(row,dict) and type(row.get('covered')) is int and type(row.get('total')) is int:
   covered,total=row['covered'],row['total']
  else:continue
  if covered<0 or total<0 or covered>total:continue
  if source=='youtube':
   entry=supporting_context.get('youtube') if isinstance(supporting_context,dict) else None
   data=entry.get('data',{}) if isinstance(entry,dict) and entry.get('status')=='current' else {}
   if not isinstance(data,dict) or data.get('coverage_24h_complete') is not True:continue
  elif source=='instagram':
   entry=supporting_context.get('instagram') if isinstance(supporting_context,dict) else None
   data=entry.get('data',{}) if isinstance(entry,dict) and entry.get('status')=='current' else {}
   if not isinstance(data,dict) or data.get('coverage_24h_complete') is not True:continue
  value=str(total) if covered==total else f'{covered}/{total} lidos'
  suffix=''
  if source=='grupos' and isinstance(groups,dict) and type(groups.get('groups_monitored')) is int:
   suffix=f"/{groups['groups_monitored']} grupos"
  if source=='youtube' and total==0:suffix=' sem novidades; feed completo'
  if source=='instagram' and total==0:suffix=' sem publicações; janela completa'
  parts.append(f'{label} {value} {unit}{suffix}')
 if not parts:return ''
 return '🔎 Cobertura da curadoria · últimas 24h: '+' · '.join(parts)+'.'

def prepare_delivery(text,source_timestamps,supporting_context,agenda_validator=None,source_record_coverage=None):
 if not isinstance(text,str) or not text.strip():raise ValueError('delivery_text_missing')
 source_snapshot=delivery_source_snapshot(source_timestamps)
 if not isinstance(source_snapshot,dict) or len(source_snapshot)!=len(DELIVERY_SOURCE_KEYS):raise ValueError('delivery_source_snapshot_invalid')
 url='https://openclaw.filipeivopereira.com/ifc-painel/'
 footer='📊 Painel IFC: '+url
 text=text.strip()
 if text.endswith(footer):text=text[:-len(footer)].rstrip()
 elif text.endswith(url):text=text[:-len(url)].rstrip()
 now=datetime.now(ZoneInfo('America/Sao_Paulo'))
 edition=os.environ.get('IFC_DIGEST_EDITION','matinal' if now.hour<12 else 'noturno')
 if edition not in ('matinal','tarde','noturno'):raise ValueError('digest_edition_invalid')
 weekdays=('segunda-feira','terça-feira','quarta-feira','quinta-feira','sexta-feira','sábado','domingo')
 weekday=weekdays[now.weekday()]
 labels={'matinal':f'☀️ *Resumo da manhã — {weekday}, {now:%d/%m}*','tarde':f'🕑 *Resumo da tarde — {weekday}, {now:%d/%m}*','noturno':f'🌙 *Resumo do dia — {weekday}, {now:%d/%m}*'}
 text=re.sub(r'(?im)^\s*(?:☀️|🕑|🌙)?\s*[*_]*\s*(?:resumo (?:do dia|da manhã|da tarde)|painel ifc)[^\n]*\n?','',text).strip()
 text=re.sub(r'(?im)^\s*✨\s*[*_]Destaque do dia[*_]\s*\n?','',text).strip()
 text=labels[edition]+'\n✨ *Destaque do dia*'+('\n'+text if text else '')
 agenda=agenda_digest_block(supporting_context)
 if agenda:text+='\n\n'+agenda
 finance=finance_digest_block(supporting_context)
 finance_to_append=''
 if finance:
  data=(supporting_context.get('financeiro') or {}).get('data',{})
  monthly=data.get('mensal') if isinstance(data.get('mensal'),list) else []
  latest=max((row for row in monthly if isinstance(row,dict) and isinstance(row.get('mes'),str)),key=lambda row:row['mes'],default=None)
  expected=[latest.get(key) for key in ('entradas','saidas','saldo')] if latest else []
  month=latest.get('mes','') if latest else ''
  amounts=[]
  for value in expected:
   if type(value) in (int,float):
    amounts.append(f'{abs(float(value)):,.2f}'.replace(',','_').replace('.',',').replace('_','.'))
  text=re.sub(r'(?im)^\*💰 Finanças\*\s*$', '', text)
  text=re.sub(r'(?im)^.*financeiro.*$', '', text)
  text=re.sub(r'(?im)^⚠️ Dados desatualizados.*$|^⚠️ Resultado negativo\.?\s*$', '', text).strip()
  finance_to_append=finance
 todoist=todoist_digest_block(supporting_context)
 if todoist:text+='\n\n'+todoist
 if finance_to_append:
  text+='\n\n'+finance_to_append
  details_present=('financeiro '+month[5:7]+'/'+month[:4] in text.casefold()
                   and all(value in text for value in amounts[:2]))
  # 04/11: the block shows entradas-saidas as result (not the sheet 'saldo'), so only check entradas/saidas;
  # never abort the whole edition for this self-check: drop the finance block instead.
  if not details_present:
   text=text.replace('\n\n'+finance_to_append,'')
 visitors=visitors_digest_block(supporting_context)
 if visitors:text+='\n\n'+visitors
 source_labels={'youtube':'YouTube','agenda':'Agenda','todoist':'Todoist','financeiro':'Financeiro','visitantes':'Visitantes','n8n':'n8n','notion':'Notion','planilhas':'Planilhas IFC','drive_secretaria':'Drive da secretaria','instagram':'Instagram','evolution_grupos':'Grupos WhatsApp','evolution_mulheres':'Mulheres IFC','mensagens_diretas':'Mensagens diretas','email_secretaria':'E-mail da secretaria'}
 unavailable=set()
 for source,label in source_labels.items():
  entry=supporting_context.get(source)
  if isinstance(entry,dict) and entry.get('status')!='current':unavailable.add(source)
 for source in supporting_context.get('unavailable_sources',[]):
  if isinstance(source,str) and source in source_labels:unavailable.add(source)
 warning_parts=[]
 source_coverage=curation_source_coverage_block(source_record_coverage,supporting_context)
 if source_coverage:warning_parts.append(source_coverage.removeprefix('🔎 Cobertura da curadoria · últimas 24h: ').rstrip('.'))
 if unavailable:
  order=list(source_labels)
  labels=', '.join(source_labels[name] for name in order if name in unavailable)
  warning_parts.append('fontes não verificadas: '+labels)
 youtube=supporting_context.get('youtube')
 if isinstance(youtube,dict) and youtube.get('status')=='current':
  youtube_data=youtube.get('data') if isinstance(youtube.get('data'),dict) else {}
  caption_coverage=youtube_data.get('caption_coverage') if isinstance(youtube_data.get('caption_coverage'),dict) else {}
  unavailable_captions=caption_coverage.get('unavailable_after_three_attempts')
  max_attempts=caption_coverage.get('attempts_per_video_max')
  coverage_notes=[]
  if type(unavailable_captions) is int and unavailable_captions>0:
   caption_label='legenda indisponível' if unavailable_captions==1 else 'legendas indisponíveis'
   attempts=(f'após até {max_attempts} tentativa' if max_attempts==1 else f'após até {max_attempts} tentativas') if type(max_attempts) is int and max_attempts>0 else 'após as tentativas de recuperação'
   coverage_notes.append(f'⚠️ YouTube: {unavailable_captions} {caption_label} {attempts}; conteúdo não analisado.')
  retry_after=caption_coverage.get('authenticated_fallback_retry_after')
  if caption_coverage.get('authenticated_fallback_status')=='temporarily_paused' and isinstance(retry_after,str):
   try:paused_until=datetime.fromisoformat(retry_after.replace('Z','+00:00')).astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m às %H:%M')
   except ValueError:paused_until='após renovação da cota'
   coverage_notes.append(f'⚠️ YouTube: legendas autenticadas pausadas por cota até {paused_until}; conteúdo sem legenda não foi analisado.')
  if youtube_data.get('coverage_24h_complete') is False:
   coverage_notes.append('⚠️ YouTube: a cobertura da janela de 24 horas está incompleta; atividade recente pode não estar toda representada.')
  if coverage_notes:warning_parts.extend(note.removeprefix('⚠️ ') for note in coverage_notes)
 coverage=supporting_context.get('media_coverage',{})
 skipped=[];unread=[];partial_media=[];partial_video=[]
 if isinstance(coverage,dict):
  names={'evolution_grupos':'Grupos WhatsApp','evolution_mulheres':'Mulheres IFC','mensagens_diretas':'Mensagens diretas'}
  for source,label in names.items():
   entry=coverage.get(source)
   if not isinstance(entry,dict):continue
   if type(entry.get('audio_unavailable')) is int and entry['audio_unavailable']>0:skipped.append(f"{label} {entry['audio_unavailable']}")
   if type(entry.get('media_content_unavailable')) is int and entry['media_content_unavailable']>0:unread.append(f"{label} {entry['media_content_unavailable']}")
   if type(entry.get('media_analysis_partial')) is int and entry['media_analysis_partial']>0:partial_media.append(f"{label} {entry['media_analysis_partial']}")
   if type(entry.get('video_sampled_partial')) is int and entry['video_sampled_partial']>0:partial_video.append(f"{label} {entry['video_sampled_partial']}")
 if skipped or unread or partial_media or partial_video:
  gaps=[]
  if skipped:gaps.append('áudio(s) ignorado(s) após três tentativas: '+', '.join(skipped)+'; conteúdo não inferido')
  if unread:gaps.append('mídia(s) sem conteúdo interpretável: '+', '.join(unread))
  if partial_media:gaps.append('mídia(s) com análise parcial por OCR ou extração textual; conteúdo visual não interpretado por completo: '+', '.join(partial_media))
  if partial_video:gaps.append('vídeo(s) com análise parcial por amostragem, não integral: '+', '.join(partial_video))
  warning_parts.extend(gaps)
 if warning_parts:
  short='⚠️ Cobertura: '+'; '.join(dict.fromkeys(warning_parts))+'.'
  text+='\n\n'+short
 text+='\n\n'+footer
 if callable(agenda_validator):agenda_validator(text,supporting_context)
 if len(text)>4000 or any(pattern.search(text) for pattern in PATTERNS):raise ValueError('delivery_text_invalid')
 day=now.date().isoformat()
 suffix={'matinal':'','tarde':'.tarde','noturno':'.noturno'}[edition]
 manifest_support={k:{field:v.get(field) for field in ('status','generated_at')} for k,v in supporting_context.items() if isinstance(v,dict)};manifest_support.update(source_snapshot)
 manifest={'edition':edition,'window_hours':24,'version':1,'status':'prepared_not_sent','day':day,'generated_at':now.isoformat(),'source_timestamps':source_timestamps,'supporting_sources':manifest_support,'source_snapshot_validated':True,'text':text}
 root=Path('/opt/ifc-painel/mcp/delivery');root.mkdir(parents=True,exist_ok=True,mode=0o700)
 for path,content,owner in [(root/(day+suffix+'.json'),json.dumps(manifest,ensure_ascii=False),0),(OUTPUT/('resumo_'+edition+'_'+day.replace('-','')+'.txt'),text+'\n',1000)]:
  fd,tmp=tempfile.mkstemp(prefix='.summary-',dir=path.parent)
  try:
   os.fchmod(fd,0o600);os.fchown(fd,owner,owner)
   with os.fdopen(fd,'w',encoding='utf-8') as out:out.write(content);out.flush();os.fsync(out.fileno())
   os.replace(tmp,path)
  finally:
   if os.path.exists(tmp):os.unlink(tmp)
 return {'status':'prepared_not_sent','chars':len(text)}
if __name__=='__main__':
 try:print(json.dumps(export()))
 except Exception as error:print(json.dumps({'status':'failed','reason':str(error) if isinstance(error,ValueError) else type(error).__name__}));raise SystemExit(1)
