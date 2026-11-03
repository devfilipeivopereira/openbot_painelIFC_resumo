"""Deploy the updated BRT schedule atomically and keep cron comments aligned."""
import base64,hashlib,json,shlex,sys
from pathlib import Path
import paramiko
local=Path(__file__).with_name('digest_schedule.py');digest=hashlib.sha256(local.read_bytes()).hexdigest()
env={}
for line in Path(sys.argv[1]).read_text(encoding='utf-8-sig').splitlines():
 line=line.strip()
 if line and not line.startswith('#') and '=' in line:
  k,v=line.split('=',1);env[k.strip()]=v.strip().strip('"').strip("'")
dest='/opt/ifc-painel/mcp/digest_schedule.py';temp=dest+'.schedule-update.new'
ssh=paramiko.SSHClient();ssh.load_system_host_keys();ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
ssh.connect(env['VPS_HOST'],username=env['VPS_USER'],password=env['VPS_PASSWORD'],timeout=10,auth_timeout=10,banner_timeout=10,allow_agent=False,look_for_keys=False)
try:
 sftp=ssh.open_sftp();sftp.put(str(local),temp);sftp.close()
 remote=r'''import ast,hashlib,os,shutil,stat,datetime,json
from pathlib import Path
p=Path('/opt/ifc-painel/mcp/digest_schedule.py');tmp=Path(str(p)+'.schedule-update.new');data=tmp.read_bytes();ast.parse(data.decode('utf-8'));st=p.stat();stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');backup=p.with_name(p.name+'.before-0720-'+stamp+'.bak');shutil.copy2(p,backup);os.chown(backup,st.st_uid,st.st_gid);os.chmod(backup,stat.S_IMODE(st.st_mode));os.chown(tmp,st.st_uid,st.st_gid);os.chmod(tmp,stat.S_IMODE(st.st_mode));os.replace(tmp,p)
cron=Path('/etc/cron.d/ifc-daily-panel');text=cron.read_text();updated=text.replace('panel 07:05/12:30','panel 07:20/12:30');fd,cron_tmp=__import__('tempfile').mkstemp(dir=str(cron.parent),prefix='.ifc-cron-')
with os.fdopen(fd,'w') as f:f.write(updated)
os.chmod(cron_tmp,0o644);os.replace(cron_tmp,cron)
print(json.dumps({'backup_created':True,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'cron_comment_updated':updated!=text}))'''
 encoded=base64.b64encode(remote.encode()).decode();cmd='python3 -c '+shlex.quote("import base64;exec(base64.b64decode('"+encoded+"'))")
 _,out,err=ssh.exec_command(cmd,timeout=30);result=out.read().decode('utf-8','replace').strip();error=err.read().decode('utf-8','replace').strip();code=out.channel.recv_exit_status()
finally:ssh.close()
if code:raise SystemExit('digest_schedule_deploy_failed '+(error.splitlines()[-1][:120] if error else 'remote_error'))
details=json.loads(result)
if details.get('sha256')!=digest:raise SystemExit('digest_schedule_hash_mismatch')
print(json.dumps({'deployed':True,'backup_created':details['backup_created'],'cron_comment_updated':details['cron_comment_updated'],'sha256_match':True}))
