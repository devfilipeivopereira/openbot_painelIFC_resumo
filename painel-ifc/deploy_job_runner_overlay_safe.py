"""Deploy a corrected job runner over the current MCP image, preserving container settings."""
import base64
import hashlib
import json
import shlex
import sys
from pathlib import Path

import paramiko

source = Path(__file__).with_name("job_runner.py")
digest = hashlib.sha256(source.read_bytes()).hexdigest()
env = {}
for line in Path(sys.argv[1]).read_text(encoding="utf-8-sig").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")

ssh = paramiko.SSHClient()
ssh.load_system_host_keys()
ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
ssh.connect(env["VPS_HOST"], username=env["VPS_USER"], password=env["VPS_PASSWORD"],
            allow_agent=False, look_for_keys=False, timeout=10)
remote_dir = "/opt/ifc-painel/mcp/job-runner-overlay"
try:
    ssh.exec_command("install -d -m 0700 " + shlex.quote(remote_dir))[1].channel.recv_exit_status()
    sftp = ssh.open_sftp()
    try:
        sftp.put(str(source), remote_dir + "/job_runner.py")
        sftp.chmod(remote_dir + "/job_runner.py", 0o644)
    finally:
        sftp.close()
    script = r'''
import hashlib,json,os,subprocess,tempfile,time
from pathlib import Path
root=Path('/opt/ifc-painel/mcp/job-runner-overlay')
source=root/'job_runner.py'
expected='__DIGEST__'
if hashlib.sha256(source.read_bytes()).hexdigest()!=expected:raise SystemExit('upload_hash_mismatch')
old=json.loads(subprocess.check_output(['docker','inspect','ifc-mcp']))[0]
image='ifc-mcp:20261103-91'
dockerfile=root/'Dockerfile'
dockerfile.write_text('FROM '+old['Config']['Image']+'\nCOPY --chown=0:0 job_runner.py /app/job_runner.py\n')
subprocess.run(['docker','build','--pull=false','-t',image,str(root)],check=True,stdout=subprocess.DEVNULL)
h=old['HostConfig'];c=old['Config'];networks=list(old['NetworkSettings']['Networks'])
if len(networks)!=2 or not h['ReadonlyRootfs'] or c['User']!='65534:65534':raise SystemExit('unexpected_container_shape')
fd,envpath=tempfile.mkstemp(prefix='.ifc-mcp-env-',dir=root);os.fchmod(fd,0o600)
with os.fdopen(fd,'w') as stream:stream.write('\n'.join(c['Env'])+'\n')
backup='ifc-mcp-before-job-runner-'+str(int(time.time()))
try:
 subprocess.run(['docker','stop','ifc-mcp'],check=True,stdout=subprocess.DEVNULL)
 subprocess.run(['docker','rename','ifc-mcp',backup],check=True)
 cmd=['docker','run','-d','--name','ifc-mcp','--network',networks[0],'--user',c['User'],'--read-only','--cap-drop','ALL','--env-file',envpath,'--restart',h['RestartPolicy']['Name']]
 if h['Memory']:cmd+=['--memory',str(h['Memory'])]
 if h['NanoCpus']:cmd+=['--cpus',str(h['NanoCpus']/1_000_000_000)]
 for option in h.get('SecurityOpt') or []:cmd+=['--security-opt',option]
 for destination,options in (h.get('Tmpfs') or {}).items():cmd+=['--tmpfs',destination+':'+options]
 for mount in old['Mounts']:
  source_name=mount.get('Name') if mount['Type']=='volume' else mount['Source']
  spec='type='+mount['Type']+',src='+source_name+',dst='+mount['Destination']
  if not mount['RW']:spec+=',readonly'
  cmd+=['--mount',spec]
 cmd.append(image)
 created=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
 if created.returncode:raise RuntimeError('container_create_failed: '+created.stderr[-400:])
 subprocess.run(['docker','network','connect',networks[1],'ifc-mcp'],check=True,stdout=subprocess.DEVNULL)
 time.sleep(4)
 current=json.loads(subprocess.check_output(['docker','inspect','ifc-mcp']))[0]
 if not current['State']['Running']:raise RuntimeError('new_container_not_running')
 print(json.dumps({'deployed':True,'image':image,'running':True,'previous_container':backup,'mounts_preserved':len(current['Mounts'])==len(old['Mounts']),'networks_preserved':set(current['NetworkSettings']['Networks'])==set(networks)}))
except Exception:
 subprocess.run(['docker','rm','-f','ifc-mcp'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 subprocess.run(['docker','rename',backup,'ifc-mcp'],check=True)
 subprocess.run(['docker','start','ifc-mcp'],check=True,stdout=subprocess.DEVNULL)
 raise
finally:
 os.unlink(envpath)
'''.replace('__DIGEST__', digest)
    encoded = base64.b64encode(script.encode()).decode()
    command = 'python3 -c ' + shlex.quote("import base64;exec(base64.b64decode('" + encoded + "'))")
    _, out, err = ssh.exec_command(command, timeout=180)
    result = out.read().decode("utf-8", "replace").strip()
    error = err.read().decode("utf-8", "replace").strip()
    status = out.channel.recv_exit_status()
    if status:
        raise RuntimeError(error[-1000:] or "overlay_deployment_failed")
    print(result)
finally:
    ssh.close()
