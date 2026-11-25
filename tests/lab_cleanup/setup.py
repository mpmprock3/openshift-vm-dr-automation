"""Test the actual AAP setup against a local TLS API, including idempotence."""
import copy, http.server, json, os, ssl, subprocess, sys, threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'work/lab-cleanup-setup-tests';OUT.mkdir(parents=True,exist_ok=True)
cert,key=OUT/'cert.pem',OUT/'key.pem'
subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost','-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
jobs=[dict(id=i,name=name,project=7,inventory=8,execution_environment=9) for i,name in enumerate(['Configure DC Protection','Establish DR Replication','Refresh VM Choices'],1)]
credentials={1:[11],2:[12],3:[11,13]}
workflows=[dict(id=20,name='Enable DR Protection',organization=1),dict(id=21,name='Fail Over to DR',organization=1,description='unchanged')]
original=copy.deepcopy(workflows)
nodes=[];edges={};surveys={}
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def respond(self,body,code=200):
        self.send_response(code);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(body).encode())
    def do_GET(self):
        u=urlparse(self.path);parts=u.path.strip('/').split('/');q=parse_qs(u.query)
        if parts[-1]=='credentials':result=[{'id':i} for i in credentials.get(int(parts[-2]),[])]
        elif parts[-1]=='survey_spec':return self.respond(surveys.get(int(parts[-2]),{'spec':[]}))
        elif parts[-1] in ['success_nodes','failure_nodes','always_nodes']:result=[{'id':i} for i in edges.get((int(parts[-2]),parts[-1]),[])]
        elif parts[-1]=='workflow_nodes':result=[x for x in nodes if x['workflow']==int(parts[-2]) and ('identifier' not in q or x['identifier']==q['identifier'][0])]
        else:result=[x for x in (jobs if parts[-1]=='job_templates' else workflows) if x['name']==q['name'][0]]
        self.respond({'count':len(result),'results':result,'next':None})
    def do_POST(self):
        parts=urlparse(self.path).path.strip('/').split('/');body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if parts[-1]=='credentials':credentials.setdefault(int(parts[-2]),[]).append(body['id'])
        elif parts[-1]=='survey_spec':surveys[int(parts[-2])]=body
        elif parts[-1]=='success_nodes':edges.setdefault((int(parts[-2]),parts[-1]),[]).append(body['id'])
        elif parts[-1]=='workflow_nodes':body.update(id=100+len(nodes),workflow=int(parts[-2]));nodes.append(body)
        elif parts[-1]=='job_templates':body['id']=len(jobs)+1;jobs.append(body)
        elif parts[-1]=='workflow_job_templates':body['id']=len(workflows)+20;workflows.append(body)
        else:raise AssertionError(self.path)
        self.respond(body,201)
    def do_PATCH(self):
        parts=urlparse(self.path).path.strip('/').split('/');body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        obj=next(x for x in workflows if x['id']==int(parts[-1]));obj.update(body);self.respond(obj)
server=http.server.HTTPServer(('localhost',0),Handler)
ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key);server.socket=ctx.wrap_socket(server.socket,server_side=True)
threading.Thread(target=server.serve_forever,daemon=True).start()
seed_text='{}'
state=OUT/'state.json';state.write_text(seed_text)
env=dict(os.environ,K8S_AUTH_HOST='https://dr',K8S_AUTH_API_KEY='fixture',VMDR_PLAN_TEST_STATE=str(state),
    ANSIBLE_ROLES_PATH=str(ROOT/'roles'),ANSIBLE_COLLECTIONS_PATH=str(ROOT/'tests/failback_continuation/collections'),
    ANSIBLE_LOCAL_TEMP=str(OUT/'local'),ANSIBLE_REMOTE_TEMP=str(OUT/'remote'),ANSIBLE_PIPELINING='true',
    AAP_SURVEY_HOST=f'https://localhost:{server.server_port}',AAP_SURVEY_TOKEN='fixture',AAP_SURVEY_CA=str(cert))
vp=OUT/'vars.json';vp.write_text(json.dumps({'ansible_python_interpreter':sys.executable,'awx_project_id':7}))
for attempt in range(2):
    result=subprocess.run(['ansible-playbook','-i','localhost,','configure_lab_cleanup.yml','-e','@'+str(vp)],cwd=ROOT,env=env,capture_output=True,text=True)
    (OUT/f'setup-{attempt}.log').write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stdout[-4000:]+result.stderr
    assert len(jobs)==7 and len(nodes)==4 and len(workflows)==3
    assert workflows[:2]==original
    for job in jobs[3:]:
        assert credentials[job['id']]==([11] if 'PROD' in job['name'] else [12])
        assert not job['allow_simultaneous']
    assert sum(len(v) for v in edges.values())==3
    survey=surveys[workflows[-1]['id']]['spec']
    assert next(x for x in survey if x['variable']=='lab_cleanup_mode')['default']=='Preview'
    assert {x['variable'] for x in survey}=={'lab_cleanup_mode','lab_cleanup_confirmation','lab_cleanup_prod_namespaces','lab_cleanup_dr_namespaces'}
    assert state.read_text()==seed_text
    print(f'PASS setup-{attempt}',flush=True)
server.shutdown()
