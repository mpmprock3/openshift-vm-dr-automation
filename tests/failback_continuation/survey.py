"""Validate the customer survey against a local TLS AAP API double."""
import http.server, json, os, ssl, subprocess, sys, threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'work/continuation-survey-tests';OUT.mkdir(parents=True,exist_ok=True)
cert,key=OUT/'cert.pem',OUT/'key.pem'
subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost','-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
saved={}
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def respond(self,body):
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(body).encode())
    def do_GET(self):
        self.respond(saved.get('survey',{'spec':[]}) if self.path.endswith('/survey_spec/') else {'count':1,'results':[{'id':42,'name':'Fail Back to DC'}]})
    def do_POST(self):
        saved['survey']=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.respond(saved['survey'])
    def do_PATCH(self):
        saved['settings']=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.respond(saved['settings'])
server=http.server.HTTPServer(('localhost',0),Handler)
ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key);server.socket=ctx.wrap_socket(server.socket,server_side=True)
threading.Thread(target=server.serve_forever,daemon=True).start()
for checkpoint in ['first-begin','first-release']:
    seed_text=(ROOT/'work/continuation-tests'/f'{checkpoint}-state.json').read_text()
    state=OUT/'state.json';state.write_text(seed_text)
    env=dict(os.environ,K8S_AUTH_HOST='https://dr',K8S_AUTH_API_KEY='fixture',VMDR_PLAN_TEST_STATE=str(state),
        ANSIBLE_ROLES_PATH=str(ROOT/'roles'),ANSIBLE_COLLECTIONS_PATH=str(ROOT/'tests/failback_continuation/collections'),
        ANSIBLE_LOCAL_TEMP=str(OUT/'local'),ANSIBLE_REMOTE_TEMP=str(OUT/'remote'),ANSIBLE_PIPELINING='true',
        AAP_SURVEY_HOST=f'https://localhost:{server.server_port}',AAP_SURVEY_TOKEN='fixture',AAP_SURVEY_CA=str(cert))
    variables=dict(test_stage='refresh_failback',ansible_python_interpreter=sys.executable,failback_survey_template_name='Fail Back to DC',failback_survey_template_type='workflow_job_templates')
    vp=OUT/'vars.json';vp.write_text(json.dumps(variables))
    result=subprocess.run(['ansible-playbook','-i','localhost,','tests/failback_continuation/legacy.yml','-e','@'+str(vp)],cwd=ROOT,env=env,capture_output=True,text=True)
    (OUT/f'{checkpoint}.log').write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stdout[-4000:]+result.stderr
    questions={q['variable']:q for q in saved['survey']['spec']}
    assert set(questions)=={'failback_group','failback_destination_policy','failback_health_url'}
    assert questions['failback_group']['choices']==['demo-vm-plan (my-vm/test-vm) | my-vm/legacy-mirror']
    assert questions['failback_destination_policy']['default']=='Dedicated PROD namespace (retain old copy)'
    assert not questions['failback_health_url']['required']
    assert saved['settings']=={'survey_enabled':True,'allow_simultaneous':False}
    assert state.read_text()==seed_text
    print('PASS survey-'+checkpoint,flush=True)
server.shutdown()
