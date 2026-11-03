"""Extract bounded text from received attachments, without executing embedded content."""
import base64,io,zipfile,xml.etree.ElementTree as ET
import os,subprocess,tempfile,warnings,time,posixpath,re
from pathlib import Path
from html.parser import HTMLParser
MAX_BYTES=5_000_000
MAX_VIDEO_SECONDS=600
MAX_VIDEO_AUDIO_BYTES=16_000_000
MAX_VIDEO_BYTES=40_000_000
VISION_URL=os.environ.get('IFC_VISION_URL','http://ifc-transcription-gateway:8776/v1/media/describe')
VISION_TOKEN_FILE=Path(os.environ.get('IFC_TRANSCRIPTION_TOKEN_FILE','/run/secrets/litellm-transcription.token'))
VISION_LAST_ERROR='not_called'

def describe_visuals(images,context=''):
 """Ask the private LiteLLM gateway to describe a bounded set of JPEG frames."""
 global VISION_LAST_ERROR
 try:
  import json,urllib.request
  if not images or len(images)>6:return None
  prepared=[]
  from PIL import Image
  for label,data in images:
   with Image.open(io.BytesIO(data)) as source:
    source.thumbnail((1600,1600));out=io.BytesIO();source.convert('RGB').save(out,format='JPEG',quality=82,optimize=True)
   raw=out.getvalue()
   if len(raw)>1_500_000:return None
   prepared.append({'label':str(label)[:100],'mime_type':'image/jpeg','base64':base64.b64encode(raw).decode('ascii')})
  token=VISION_TOKEN_FILE.read_text(encoding='utf-8').strip()
  payload=json.dumps({'images':prepared,'context':str(context)[:6000]},ensure_ascii=False).encode()
  req=urllib.request.Request(VISION_URL,data=payload,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Cache-Control':'no-store'},method='POST')
  with urllib.request.urlopen(req,timeout=210) as response:result=json.loads(response.read(10000))
  text=result.get('description') if isinstance(result,dict) else None
  if isinstance(text,str) and text.strip():
   VISION_LAST_ERROR='ok';return text.strip()[:4000]
  VISION_LAST_ERROR='empty_response';return None
 except Exception as error:
  VISION_LAST_ERROR=type(error).__name__
  return None

def _image_jpeg(data):
 from PIL import Image
 with Image.open(io.BytesIO(data)) as source:
  source.thumbnail((1600,1600));out=io.BytesIO();source.convert('RGB').save(out,format='JPEG',quality=82,optimize=True)
 return out.getvalue()
class HTMLText(HTMLParser):
 def __init__(self):super().__init__(convert_charrefs=True);self.out=[];self.hidden=0
 def handle_starttag(self,tag,attrs):
  if tag.lower() in ('script','style','head'):self.hidden+=1
  if tag.lower() in ('p','div','br','li','tr'):self.out.append(' ')
 def handle_endtag(self,tag):
  if tag.lower() in ('script','style','head') and self.hidden:self.hidden-=1
  if tag.lower() in ('p','div','li','tr'):self.out.append(' ')
 def handle_data(self,data):
  if not self.hidden:self.out.append(data)
def _temp_root():
 root=Path(os.environ.get('IFC_OCR_TEMP_ROOT',str(Path(os.environ.get('IFC_JOB_ROOT','/var/lib/ifc-job/data'))/'ocr-tmp')))
 root.mkdir(parents=True,exist_ok=True,mode=0o700)
 os.chmod(root,0o700)
 return root

def _run_ocr(path):
 env=dict(os.environ,OMP_THREAD_LIMIT='1')
 proc=subprocess.run(['tesseract',str(path),'stdout','-l','por+eng','--psm','6'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=25,env=env)
 if proc.returncode:return ''
 return proc.stdout.decode('utf-8','replace').strip()

def ocr_image(data):
 from PIL import Image
 Image.MAX_IMAGE_PIXELS=20_000_000
 with warnings.catch_warnings():
  warnings.simplefilter('error',Image.DecompressionBombWarning)
  with Image.open(io.BytesIO(data)) as image:
   image.verify()
  with Image.open(io.BytesIO(data)) as image:
   if image.width*image.height>20_000_000:raise ValueError('imagem acima do limite')
   image.thumbnail((2600,2600))
   with tempfile.TemporaryDirectory(prefix='ifc-ocr-',dir=_temp_root()) as folder:
    path=Path(folder)/'image.png';image.convert('RGB').save(path,format='PNG')
    return _run_ocr(path)

def ocr_pdf_pages(data, indexes):
 results={};failed=[]
 with tempfile.TemporaryDirectory(prefix='ifc-ocr-',dir=_temp_root()) as folder:
  root=Path(folder);source=root/'source.pdf';source.write_bytes(data)
  started=time.monotonic()
  for position,index in enumerate(indexes):
   if time.monotonic()-started>120:
    failed.extend(i+1 for i in indexes[position:]);break
   number=index+1;target=root/'page'
   try:
    proc=subprocess.run(['pdftoppm','-f',str(number),'-l',str(number),'-singlefile','-scale-to','2500','-png',str(source),str(target)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
    image=target.with_suffix('.png')
    if proc.returncode or not image.exists():failed.append(number);continue
    text=_run_ocr(image)
    if text:results[index]=text
    else:failed.append(number)
    image.unlink(missing_ok=True)
   except (OSError,subprocess.TimeoutExpired):failed.append(number)
 return results,failed

def _office_archive(data):
 archive=zipfile.ZipFile(io.BytesIO(data))
 infos=archive.infolist()
 if len(infos)>3000 or sum(item.file_size for item in infos)>20_000_000:
  archive.close();raise ValueError('office package above extraction limit')
 return archive

def _office_xml(archive,name):
 raw=archive.read(name)
 if len(raw)>10_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
  raise ValueError('office XML not accepted')
 return ET.fromstring(raw)

def _xlsx_text(data):
 main='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
 with _office_archive(data) as archive:
  names=set(archive.namelist());shared=[]
  if 'xl/sharedStrings.xml' in names:
   root=_office_xml(archive,'xl/sharedStrings.xml')
   for item in root.findall(main+'si'):
    shared.append(''.join(t.text or '' for t in item.iter(main+'t')))
  workbook=_office_xml(archive,'xl/workbook.xml')
  rels=_office_xml(archive,'xl/_rels/workbook.xml.rels')
  rel_ns='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
  targets={item.get('Id'):item.get('Target') for item in rels if item.get('Id') and item.get('Target')}
  sheets=[]
  for sheet in workbook.findall('.//'+main+'sheet'):
   target=targets.get(sheet.get(rel_ns+'id'))
   if not target:continue
   path=posixpath.normpath(posixpath.join('xl',target.lstrip('/')) if not target.startswith('/') else target.lstrip('/'))
   if path.startswith('xl/') and path in names:sheets.append((sheet.get('name','Sheet'),path))
  if not sheets:sheets=[(f'Sheet {i+1}',name) for i,name in enumerate(sorted((n for n in names if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',n)),key=lambda n:int(re.search(r'sheet(\d+)',n).group(1))))]
  out=[]
  for sheet_name,path in sheets:
   root=_office_xml(archive,path);rows=[]
   for row in root.findall('.//'+main+'row'):
    cells=[]
    for cell in row.findall(main+'c'):
     value=''
     if cell.get('t')=='inlineStr':value=''.join(t.text or '' for t in cell.iter(main+'t'))
     else:
      node=cell.find(main+'v');raw=node.text if node is not None else ''
      if cell.get('t')=='s' and raw:
       try:value=shared[int(raw)]
       except (ValueError,IndexError):value=''
      else:value=raw or ''
     if value:cells.append(f"{cell.get('r','?')}={value}")
    if cells:rows.append(' | '.join(cells))
   if rows:out.append(f"Folha {sheet_name}: "+'\n'.join(rows))
  return '\n'.join(out)

def _pptx_text(data):
 ns='{http://schemas.openxmlformats.org/presentationml/2006/main}'
 drawing='{http://schemas.openxmlformats.org/drawingml/2006/main}'
 with _office_archive(data) as archive:
  names=sorted((n for n in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)),key=lambda n:int(re.search(r'slide(\d+)',n).group(1)))
  slides=[]
  for index,name in enumerate(names,1):
   root=_office_xml(archive,name);parts=[node.text or '' for node in root.iter(drawing+'t')]
   text=' '.join(part for part in parts if part.strip())
   if text:slides.append(f"Slide {index}: {text}")
  return '\n'.join(slides)

def _decode_content_bytes(value):
 if not isinstance(value,str):return None
 encoded=''.join(value.split())
 if encoded[:5].casefold()=='data:':
  header,comma,payload=encoded.partition(',')
  if not comma or ';base64' not in header.casefold():return None
  encoded=payload
 if not encoded or len(encoded)>((MAX_BYTES+2)//3)*4+8:return None
 # Base64 from Graph-like providers may be URL-safe or omit terminal padding.
 padded=encoded+'='*((-len(encoded))%4)
 try:return base64.b64decode(padded,altchars=b'-_',validate=True)
 except (ValueError,base64.binascii.Error):return None

def extract_video(data):
 """Extract sampled frame OCR and a bounded audio track from a WhatsApp video.

 The source video remains only in a mode-0700 temporary directory and is
  deleted on return. At most 40 MB, ten minutes and six evenly-spaced frames
  are read.
 """
 if not isinstance(data,bytes) or not data or len(data)>MAX_VIDEO_BYTES:
  return '',None,'video_empty_or_above_limit'
 try:
  with tempfile.TemporaryDirectory(prefix='ifc-video-',dir=_temp_root()) as folder:
   root=Path(folder);source=root/'source.bin';source.write_bytes(data)
   probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-show_entries','stream=codec_type','-of','json',str(source)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15)
   if probe.returncode:return '',None,'video_probe_failed'
   info=__import__('json').loads(probe.stdout.decode('utf-8','replace'))
   streams=info.get('streams',[]) if isinstance(info,dict) else []
   duration=float((info.get('format') or {}).get('duration') or 0) if isinstance(info,dict) else 0
   if duration<=0 or not any(isinstance(x,dict) and x.get('codec_type')=='video' for x in streams):return '',None,'video_stream_unavailable'
   clip=min(duration,MAX_VIDEO_SECONDS)
   # Use the full six-frame budget for short and long clips alike. The previous
   # duration-based count under-sampled brief church announcements (often only
   # one or two frames), even though the vision gateway accepts six.
   frame_count=6;fps=frame_count/clip
   frame_prefix=root/'frame'
   frames=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-i',str(source),'-an','-vf',f'fps={fps:.8f},scale=1280:-2','-frames:v',str(frame_count),'-y',str(frame_prefix)+'-%02d.png'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120)
   recognized=[];visual_frames=[]
   if frames.returncode==0:
    for frame_index,frame in enumerate(sorted(root.glob('frame-*.png'))[:6]):
     text=_run_ocr(frame)
     if text:recognized.append(text[:12000])
     try:visual_frames.append((f'amostragem {frame_index+1}/{frame_count}',frame.read_bytes()))
     except OSError:pass
   visual_description=describe_visuals(visual_frames,'OCR extraído dos quadros: '+' | '.join(recognized)[:5000]) if visual_frames else None
   audio_data=None;has_audio=any(isinstance(x,dict) and x.get('codec_type')=='audio' for x in streams)
   audio_status='sem faixa de áudio'
   if has_audio:
    audio_path=root/'audio.mp3'
    converted=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-i',str(source),'-map','0:a:0','-vn','-t',str(MAX_VIDEO_SECONDS),'-ac','1','-ar','16000','-b:a','32k','-f','mp3','-y',str(audio_path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=150)
    if converted.returncode==0 and audio_path.is_file() and 0<audio_path.stat().st_size<=MAX_VIDEO_AUDIO_BYTES:
     audio_data=audio_path.read_bytes();audio_status='faixa de áudio extraída'
    else:audio_status='faixa de áudio indisponível para extração'
   partial=duration>MAX_VIDEO_SECONDS
   visual_state=(f'{len(visual_frames)}/{frame_count} quadro(s) descritos por IA' if visual_description else f'{len(visual_frames)}/{frame_count} quadro(s) amostrados; descrição visual por IA indisponível ({VISION_LAST_ERROR})')
   state=('amostragem parcial; ' if partial else '')+visual_state+'; '+audio_status
   extracted='\n'.join(recognized)
   if visual_description:extracted+='\n[Descrição visual por IA; baseada somente nos quadros amostrados, sujeita a incerteza] '+visual_description
   return extracted,audio_data,state
 except (OSError,ValueError,subprocess.TimeoutExpired):
  return '',None,'video_extraction_failed'


def extract(item):
 mime=str(item.get('contentType') or '').lower().split(';')[0].strip();name=str(item.get('name') or '')
 if item.get('isInline') is True:return None,'imagem ou conteúdo incorporado não analisado'
 b64=item.get('contentBytes')
 if not isinstance(b64,str):return None,'arquivo não retornado pelo provedor'
 data=_decode_content_bytes(b64)
 if data is None:return None,'arquivo codificado inválido'
 if not data or len(data)>MAX_BYTES:return None,'arquivo vazio ou acima do limite'
 try:
  if mime=='application/pdf' or name.lower().endswith('.pdf'):
   from pypdf import PdfReader
   reader=PdfReader(io.BytesIO(data),strict=True)
   if reader.is_encrypted:return None,'PDF protegido por senha'
   if len(reader.pages)>100:return None,'PDF acima do limite de páginas'
   pages=[page.extract_text() or '' for page in reader.pages]
   missing=[i for i,text in enumerate(pages) if not text.strip()]
   if missing:
    try:recognized,failed=ocr_pdf_pages(data,missing[:15])
    except (OSError,ValueError):recognized,failed={},[i+1 for i in missing[:15]]
    for i,value in recognized.items():pages[i]='[OCR; texto sujeito a erros] '+value
    unprocessed=len(failed)+max(0,len(missing)-15)
    text='\n'.join(pages)
    if not text.strip():return None,'PDF sem texto recuperável por OCR'
    if unprocessed:
     if len(text)>500000:return None,'conteúdo extraído acima do limite'
     return text,'texto extraído parcialmente; '+str(unprocessed)+' página(s) sem texto recuperável'
    if recognized:
     if len(text)>500000:return None,'conteúdo extraído acima do limite'
     return text,'texto extraído com OCR; conferir possíveis erros'
   text='\n'.join(pages)
  elif mime=='application/vnd.openxmlformats-officedocument.wordprocessingml.document' or name.lower().endswith('.docx'):
   with zipfile.ZipFile(io.BytesIO(data)) as z:
    member=z.getinfo('word/document.xml')
    if member.file_size>10_000_000:return None,'documento acima do limite de conteúdo'
    xml=z.read(member)
    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():return None,'documento XML não aceito'
    root=ET.fromstring(xml);text=' '.join(t.text or '' for t in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
  elif mime in ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','application/vnd.ms-excel.sheet.macroEnabled.12') or name.lower().endswith(('.xlsx','.xlsm')):
   text=_xlsx_text(data)
  elif mime=='application/vnd.openxmlformats-officedocument.presentationml.presentation' or name.lower().endswith('.pptx'):
   text=_pptx_text(data)
  elif mime.startswith('image/') or name.lower().endswith(('.jpg','.jpeg','.png','.webp','.tif','.tiff')):
   text=ocr_image(data)
   description=describe_visuals([('imagem recebida',data)],'Texto OCR sujeito a erros: '+text[:5000])
   if description:return ((('[OCR; texto sujeito a erros] '+text+'\n') if text else '')+'[Descrição visual por IA; pode haver incerteza] '+description,'imagem analisada visualmente por IA'+('; OCR sujeito a erros' if text else '; sem texto OCR reconhecível'))
   if not text:return None,'imagem sem texto reconhecível por OCR; análise visual por IA indisponível ('+VISION_LAST_ERROR+')'
   return text,'OCR da imagem; conteúdo visual não analisado e texto sujeito a erros; gateway visual: '+VISION_LAST_ERROR
  elif mime in ('text/plain','text/csv','text/html') or name.lower().endswith(('.txt','.csv','.html')):
   text=data.decode('utf-8-sig')
   if mime=='text/html' or name.lower().endswith('.html'):
    parser=HTMLText();parser.feed(text);text=' '.join(parser.out)
  else:return None,'formato de anexo não suportado'
  if len(text)>500000:return None,'conteúdo extraído acima do limite'
  return text,'texto extraído' if text.strip() else 'anexo sem texto'
 except ValueError as error:
  if str(error)=='office package above extraction limit':return None,'pacote Office acima do limite de extração'
  if str(error)=='office XML not accepted':return None,'XML do documento não aceito'
  return None,'não foi possível extrair o texto do anexo'
 except Exception:return None,'não foi possível extrair o texto do anexo'
