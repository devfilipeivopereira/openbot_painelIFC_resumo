"""Verify the private dashboard using its mounted secret without exporting it."""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import paramiko

env = {}
for line in Path(sys.argv[1]).read_text(encoding="utf-8-sig").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")

# The diagnostic runs inside the serving container. The secret stays there;
# only named pass/fail checks and the panel timestamp return over SSH.
diagnostic = r'''from pathlib import Path
import base64, json, re, os, stat, unicodedata, urllib.request, urllib.error
from datetime import datetime, date
from zoneinfo import ZoneInfo
a=dict(x.split("=",1) for x in Path("/run/secrets/ifc-panel-auth").read_text().splitlines() if "=" in x)
h=base64.b64encode((a["IFC_PANEL_USER"]+":"+a["IFC_PANEL_PASSWORD"]).encode()).decode()
base="https://openclaw.filipeivopereira.com/ifc-painel/"
def get(path="",auth=True):
 headers={"Authorization":"Basic "+h} if auth else {}
 req=urllib.request.Request(base+path,headers=headers)
 try:
  with urllib.request.urlopen(req,timeout=15) as r: return r.status,r.headers.get("Content-Type",""),r.read()
 except urllib.error.HTTPError as e: return e.code,e.headers.get("Content-Type",""),b""
anon=get(auth=False)[0]
page=get(); css=get("painel.css"); js=get("painel.js"); panel=get("api/painel"); ata=get("api/ata"); todo=get("api/todoist"); history=get("api/history"); curation_status=get("api/curation-status")
today=datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat(); history_today=get("api/history/"+today)
checks={"anonymous_401":anon==401,"html_200":page[0]==200 and "text/html" in page[1] and b"Painel IFC" in page[2],"css_200":css[0]==200,"js_200":js[0]==200,"history_sidebar":page[0]==200 and b"history-list" in page[2],"history_index_200":history[0]==200 and "application/json" in history[1],"history_today_200":history_today[0]==200 and "application/json" in history_today[1],"todoist_section":js[0]==200 and "Pendências · Todoist" in js[2].decode("utf-8","replace"),"question_review_table_hook":js[0]==200 and b"revisao_perguntas_pedidos" in js[2] and b"trecho original" in js[2].lower(),"panel_200":panel[0]==200 and "application/json" in panel[1],"ata_200":ata[0]==200 and "application/json" in ata[1],"todoist_api_200":todo[0]==200 and "application/json" in todo[1],"curation_status_api_200":curation_status[0]==200 and "application/json" in curation_status[1],"curation_notice_ui":b"curation-status" in page[2] and b"api/curation-status" in js[2]}
try:
 status_data=json.loads(curation_status[2]);checks["curation_status_shape"]=status_data.get("status") in {"curation_not_ready","curation_ready","published","unknown"} and set(status_data).issubset({"status","checked_at","reason_code"}) and (status_data.get("reason_code") is None or status_data.get("reason_code") in {"curation_stale_or_model_invalid","sections_missing","source_snapshot_mismatch","record_coverage_incomplete","summary_incomplete","section_incomplete","evidence_incomplete","question_request_review_incomplete","validation_error","direct_message_coverage_incomplete","todoist_coverage_incomplete","agenda_coverage_incomplete","family_schedule_incomplete","prayer_updates_invalid","digest_validation_failed","source_snapshot_changed","model_output_invalid","curation_validation_failed"})
 marker=json.loads(Path("/data/curation-status.json").read_text(encoding="utf-8"))
 checks["curation_status_matches_private_marker"]=status_data=={"status":marker.get("status"),"checked_at":marker.get("checked_at"),"reason_code":marker.get("reason_code")}
except Exception:
 checks["curation_status_shape"]=False;checks["curation_status_matches_private_marker"]=False
try:
 hd=json.loads(history[2]); checks["history_today_listed"]=today in hd.get("dates",[])
except Exception: checks["history_today_listed"]=False
try:
 d=json.loads(panel[2]); checks["panel_shape"]=isinstance(d,dict) and "atencao" in d
 source_rows=d.get('fontes',[]) if isinstance(d,dict) else []
 source_index={str(row.get('fonte')):row for row in source_rows if isinstance(row,dict)}
 editorial_sources=('drive_secretaria','instagram','evolution_grupos','evolution_mulheres','mensagens_diretas','email_secretaria','youtube')
 coverage_counts={}
 for source in editorial_sources:
  detail=str(source_index.get(source,{}).get('detalhe',''))
  match=re.search(r'Índices de entrada da curadoria validados: (\d+)/(\d+)\.',detail)
  if match:coverage_counts[source]=(int(match.group(1)),int(match.group(2)))
 checks['editorial_record_coverage_all_sources']=len(coverage_counts)==len(editorial_sources) and all(covered==total for covered,total in coverage_counts.values())
 notion=source_index.get('notion',{});sheets=source_index.get('planilhas',{});finance=source_index.get('financeiro',{})
 checks['notion_excluded_per_schedule']=notion.get('status')=='Fora da rotina diária' and 'não é consultado diariamente' in str(notion.get('detalhe',''))
 checks['sheets_specialized_coverage']=sheets.get('status')=='Cobertura por coletores especializados' and 'Batizandos IFC' in str(sheets.get('detalhe',''))
 checks['finance_dashboard_source_labelled']='dashboard financeiro IFC no Supabase' in str(finance.get('detalhe',''))
 visitor=d.get('visitantes',{}) if isinstance(d,dict) else {}
 visitor_rows=visitor.get('recentes',[]) if isinstance(visitor,dict) else []
 visitor_source=source_index.get('visitantes',{})
 checks['visitor_rows_from_n8n_baserow']=isinstance(visitor_rows,list) and bool(visitor_rows) and all(isinstance(row,dict) and {'nome','data','status'}.issubset(row) for row in visitor_rows) and 'Baserow' in str(visitor_source.get('detalhe','')) and 'n8n' in str(visitor_source.get('detalhe',''))
 checks['visitor_ui_table']='Cadastros recentes · Baserow / n8n' in js[2].decode('utf-8','replace') and 'visitantes' in js[2].decode('utf-8','replace')
 email_rows=d.get('email_mensagens',[]) if isinstance(d,dict) else []
 drive_rows=d.get('drive_documentos',[]) if isinstance(d,dict) else []
 checks['email_evidence_rows']=isinstance(email_rows,list) and bool(email_rows) and all(isinstance(row,dict) and {'mailbox','received_at','sender','subject','type'}.issubset(row) for row in email_rows)
 checks['drive_evidence_rows']=isinstance(drive_rows,list) and bool(drive_rows) and all(isinstance(row,dict) and {'name','mimeType','modifiedTime','url'}.issubset(row) and (not row['url'] or str(row['url']).startswith(('https://drive.google.com/','https://docs.google.com/'))) for row in drive_rows) and any(bool(row.get('url')) for row in drive_rows if isinstance(row,dict))
 ui_text=js[2].decode('utf-8','replace')
 checks['email_drive_evidence_tables']=all(marker in ui_text for marker in ('E-mails considerados na curadoria','Arquivos consultados · janela de 24 horas e referências'))
 def exact_worship_day(value):
  raw=str(value or '')[:10]
  for fmt in ('%Y-%m-%d','%d/%m/%Y'):
   try:return datetime.strptime(raw,fmt).date()
   except ValueError:pass
  return None
 active_scales=d.get('escala_louvor');past_scales=d.get('escala_louvor_historico')
 checks['worship_history_separated']=isinstance(active_scales,list) and isinstance(past_scales,list) and 'Histórico arquivado por mês' in js[2].decode('utf-8','replace') and 'renderScaleHistory' in js[2].decode('utf-8','replace')
 ui_js=js[2].decode('utf-8','replace')
 checks['all_scale_sections_archive_past_and_ambiguous']=all(marker in ui_js for marker in ('partitionScaleRows','otherScaleData','escala_pregacao','escala_recepcao','escalas_ministerios')) and 'Histórico arquivado por mês' in ui_js
 checks['scale_history_view_is_pure_and_renders_objects']='Keep view-only history local to this render' in ui_js and 'function renderScaleData' in ui_js and 'Responsáveis e detalhes' in ui_js
 checks['agenda_cancellation_overlap_and_event_links']=all(marker in ui_js for marker in ('const overlapsDay=','CANCELADO','Abrir no Google Calendar','event_url'))
 active_days=[exact_worship_day(row.get('data')) for row in active_scales if isinstance(row,dict)] if isinstance(active_scales,list) else []
 checks['active_worship_has_no_past_or_ambiguous_dates']=len(active_days)==len(active_scales or []) and all(day is not None and day>=datetime.now(ZoneInfo('America/Sao_Paulo')).date() for day in active_days)
 youtube=d.get('youtube_publico') if isinstance(d,dict) else None
 video_rows=(youtube.get('videos',[]) if isinstance(youtube,dict) else [])+(youtube.get('recent_24h',[]) if isinstance(youtube,dict) else [])
 checks['youtube_metadata_without_raw_transcripts']=isinstance(youtube,dict) and all(isinstance(row,dict) and 'transcript' not in row for row in video_rows)
 checks['youtube_curation_when_current']=isinstance(youtube,dict) and (not youtube.get('curation_current') or isinstance(d.get('youtube_curadoria'),dict))
 checks['youtube_curation_ui']=js[0]==200 and all(marker in js[2].decode('utf-8','replace') for marker in ('Curadoria dos vídeos recentes','Quando a legenda não é recuperada','coverage_24h_complete','limitations','caption_status'))
 book=d.get("livro_oracao",{}); prayers=book.get("pedidos",[]) if isinstance(book,dict) else []
 checks["prayer_book_shape"]=isinstance(prayers,list) and len(prayers)>=116 and all(isinstance(r,dict) and "pedido" in r and "id" not in r and "provenance" not in r for r in prayers)
 checks["prayer_book_ui"]=b"prayer-search" in js[2]
 checks["readable_section_tables"]=all(marker in js[2] for marker in (b"renderStructured",b"dataTable",b"section-description",b"scope=",b"table-scroll-hint"))
 checks["responsive_table_styles"]=all(marker in css[2] for marker in (b".number",b".negative",b".data-table thead",b".table-scroll-hint"))
 baptisms=d.get("batismo",{}); baptism_rows=baptisms.get("registros",[]) if isinstance(baptisms,dict) else []
 checks["baptism_sheet_records"]=len(baptism_rows)>=192 and all(isinstance(r,dict) and set(r).issubset({"nome","data_batismo","ano","idade_no_batismo","situacao"}) for r in baptism_rows)
 baptism_links=baptisms.get("links",{})
 checks["baptism_sheet_links"]=len(baptism_links)>=1 and all(re.match(r"^https://docs\.google\.com/spreadsheets/d/[^/]+/edit$",str(v)) for v in baptism_links.values())
 checks["baptism_chart_ui"]=all(x in js[2] for x in (b"renderBaptismChart",b"baptism-search",b"baptism-year",b"filterBaptisms"))
 checks["ata_excluded_from_panel"]="__ATA2026__" not in panel[2].decode() and "atividade_whatsapp_7d" not in d
 pii=re.compile(rb"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|@[^\s\"']+?(?:s\.whatsapp\.net|g\.us|lid)|(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)",re.I)
 def pii_safe_without_authorized_ifc_directory(value):
  if not isinstance(value,dict):return False,False
  clean=dict(value);directory=clean.get('participantes_grupos');roster_ok=directory is None;roster_counts={}
  if isinstance(directory,dict) and directory.get('status')=='current' and directory.get('scope')=='IFC' and isinstance(directory.get('contatos'),list):
   groups=directory.get('groups',[]);people=directory['contatos']
   normalize=lambda x:' '.join(unicodedata.normalize('NFKD',str(x)).encode('ascii','ignore').decode().casefold().split())
   group_names={normalize(g.get('g')) for g in groups if isinstance(g,dict) and isinstance(g.get('g'),str)} if isinstance(groups,list) else set()
   identities=[];non_ifc=0;shape_ok=True;phone_count=id_count=0
   for person in people:
    if not isinstance(person,dict) or not isinstance(person.get('grupos'),list):shape_ok=False;continue
    phone=str(person.get('telefone') or '');evolution_id=str(person.get('evolution_id') or '')
    phone_count+=int(bool(phone));id_count+=int(bool(evolution_id))
    if phone and not re.fullmatch(r'[0-9]{7,15}',phone):shape_ok=False
    if evolution_id and not re.fullmatch(r'(?:[0-9]{7,20}(?:-[0-9]{5,20})?@(?:s\.whatsapp\.net|lid)|[0-9]{7,15})',evolution_id,re.I):shape_ok=False
    identities.append(('p:'+phone) if phone else ('i:'+evolution_id.lower()))
    non_ifc+=sum(1 for group in person['grupos'] if normalize(group) not in group_names)
   roster_ok=isinstance(groups,list) and len(groups)>0 and all(isinstance(g,dict) and g.get('category')=='IFC' for g in groups) and shape_ok and non_ifc==0 and len(set(identities))==len(people) and directory.get('unique_contacts')==len(people)
   roster_counts={'groups':len(groups) if isinstance(groups,list) else -1,'contacts':len(people),'unique_contacts':len(set(identities)),'phones':phone_count,'evolution_ids':id_count,'non_ifc_memberships':non_ifc}
   clean.pop('participantes_grupos',None)
  return roster_ok and pii.search(json.dumps(clean,ensure_ascii=False).encode()) is None, roster_ok, roster_counts
 pii_clean,roster_ok,participant_counts=pii_safe_without_authorized_ifc_directory(d)
 checks['participant_directory_ifc_only']=roster_ok
 checks["pii_filter"]=pii_clean; stamp=str(d.get("atualizado_em","not supplied"))
except Exception:
 checks.update(panel_shape=False,ata_excluded_from_panel=False,pii_filter=False); stamp="unavailable"
try:
 td=json.loads(todo[2]); allowed_task_fields={"tarefa","prazo","prazo_tipo","prazo_descricao","horario","limite_final","descricao","etiquetas","recorrente","duracao","subtarefa","estado","prioridade"}; checks["todoist_api_shape"]=isinstance(td,dict) and isinstance(td.get("tasks"),list) and all(isinstance(x,dict) and set(x).issubset(allowed_task_fields) and isinstance(x.get("tarefa"),str) and type(x.get("prioridade")) is int and isinstance(x.get("descricao"),str) and isinstance(x.get("etiquetas"),list) and type(x.get("recorrente")) is bool for x in td["tasks"]) and type(td.get("coverage_complete")) is bool and type(td.get("total_open_tasks")) is int; todoist_count=len(td.get("tasks",[])); task_rows=td.get("tasks",[]); todoist_metadata={name:sum(bool(row.get(field)) for row in task_rows) for name,field in {"with_description":"descricao","with_due_time":"horario","with_deadline":"limite_final","with_labels":"etiquetas","recurring":"recorrente","with_duration":"duracao","subtasks":"subtarefa"}.items()}
 checks["todoist_pii_filter"]=pii.search(todo[2]) is None
except Exception:
 checks.update(todoist_api_shape=False,todoist_pii_filter=False); todoist_count=-1
try:
 hd=json.loads(history_today[2]); checks["history_today_shape"]=isinstance(hd,dict) and hd.get("hoje")==today
 history_pii_clean,history_roster_ok,_history_participant_counts=pii_safe_without_authorized_ifc_directory(hd)
 checks['history_participant_directory_ifc_only']=history_roster_ok
 checks["history_today_pii_filter"]=history_pii_clean
except Exception: checks.update(history_today_shape=False,history_today_pii_filter=False)
for k,v in checks.items(): print(k+"="+("ok" if v else "fail"))
print("editorial_coverage_counts="+json.dumps(coverage_counts,sort_keys=True))
print("evidence_rows="+json.dumps({"emails":len(email_rows) if isinstance(email_rows,list) else -1,"drive_files":len(drive_rows) if isinstance(drive_rows,list) else -1,"drive_links":sum(bool(x.get("url")) for x in drive_rows if isinstance(x,dict)) if isinstance(drive_rows,list) else -1},sort_keys=True))
print("participant_directory_counts="+json.dumps(participant_counts,sort_keys=True))
print("server_uid="+str(os.geteuid()))
for line in Path("/proc/self/uid_map").read_text().splitlines(): print("uid_map="+line.strip())
for line in Path("/proc/self/gid_map").read_text().splitlines(): print("gid_map="+line.strip())
for name in ("/data/dados.json","/data/integrantes_ata2026.json","/data/history"):
 try:
  s=os.stat(name); print("file="+name+" exists=1 mode="+oct(stat.S_IMODE(s.st_mode))+" uid="+str(s.st_uid)+" dir="+str(stat.S_ISDIR(s.st_mode)))
  fd=os.open(name,os.O_RDONLY); os.close(fd); print("file_access="+name+" readable=1")
 except Exception as e: print("file="+name+" error="+type(e).__name__)
print("data_timestamp="+stamp)
print("history_today="+today)
print("todoist_open_due_task_count="+str(todoist_count))
print("todoist_context_counts="+json.dumps(todoist_metadata,sort_keys=True))
if not all(checks.values()): raise SystemExit(1)
'''
encoded = base64.b64encode(diagnostic.encode("utf-8")).decode("ascii")
ssh = paramiko.SSHClient()
ssh.load_system_host_keys()
ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
ssh.connect(env["VPS_HOST"], username=env["VPS_USER"], password=env["VPS_PASSWORD"],
            timeout=10, auth_timeout=10, allow_agent=False, look_for_keys=False)
try:
    command = (
        'cid=$(docker ps -q --filter name=ifc-panel | head -1); '
        f'docker exec "$cid" python3 -c "import base64;exec(base64.b64decode(\'{encoded}\'))"'
    )
    _stdin, stdout, stderr = ssh.exec_command(command, timeout=60)
    result = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
finally:
    ssh.close()

print(result, end="")
if code:
    raise SystemExit("One or more private dashboard checks failed" + (": " + error[-300:] if error else ""))
print("VERIFIED: all checks passed")
