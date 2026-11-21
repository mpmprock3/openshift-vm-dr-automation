"""Run native Ansible against disposable API doubles. No real cluster access."""
import argparse
import copy
import datetime
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('output', nargs='?', default=str(ROOT / 'work/recovery-tests'))
parser.add_argument('--vm-only', action='store_true', help='Test planned moves with DC-hosted coordination and no application/fencing adapters')
args = parser.parse_args()
OUT = Path(args.output).resolve()
OUT.mkdir(parents=True, exist_ok=True)
ANSIBLE = shutil.which('ansible-playbook')
assert ANSIBLE, 'Install ansible-core and add its bin directory to PATH'
cert, key = OUT / 'cert.pem', OUT / 'key.pem'
subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
api_jobs = [{'id': 1, 'name': 'Fail Over to DR', 'description': 'legacy sentinel'}]
api_credentials = {}
api_surveys = {}
health_requests = []

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        if parsed.path == '/health':
            health_requests.append(self.path); body = {'healthy': True}
        elif parsed.path.endswith('/credentials/'):
            jid = int(parsed.path.split('/')[-3]); body = {'results': [{'id': c} for c in api_credentials.get(jid, [])], 'next': None}
        else:
            name = parse_qs(parsed.query)['name'][0]
            matches = [j for j in api_jobs if j['name'] == name]
            body = {'count': len(matches), 'results': matches}
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(json.dumps(body).encode())
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path.endswith('/credentials/'):
            api_credentials.setdefault(int(self.path.split('/')[-3]), []).append(body['id'])
        elif self.path.endswith('/survey_spec/'):
            api_surveys[int(self.path.split('/')[-3])] = body
        else:
            body['id'] = len(api_jobs) + 1; api_jobs.append(body)
        self.send_response(201); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(json.dumps(body).encode())

server = http.server.HTTPServer(('localhost', 0), Handler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(cert, key)
server.socket = ctx.wrap_socket(server.socket, server_side=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f'https://localhost:{server.server_port}'
state = OUT / 'state.json'
vars_path = OUT / 'vars.json'
variables = {
    'ansible_python_interpreter': sys.executable,
    'recovery_plan_name': 'finance', 'recovery_target_site': 'dr',
    'recovery_control_kubeconfig': 'control',
    'recovery_site_kubeconfigs': {'dc': 'dc', 'dr': 'dr'},
    'recovery_poll_seconds': 0, 'recovery_poll_retries': 1,
    'recovery_aap_project_id': 11, 'recovery_aap_inventory_id': 12,
    'recovery_aap_ee_id': 13, 'recovery_aap_credential_id': 14,
    'recovery_plans': {'finance': {
        'application': 'finance', 'health_url': url + '/health', 'health_ca': str(cert),
        'fence_tasks': str(ROOT / 'tests/recovery_plan/fence.yml'),
        'sites': {'dc': {'api': 'https://dc', 'namespace': 'dc-vms'},
                  'dr': {'api': 'https://dr', 'namespace': 'dr-vms'}}}}
}
if args.vm_only:
    variables['recovery_control_kubeconfig'] = 'dc'
    for field in ['health_url', 'health_ca', 'fence_tasks']:
        variables['recovery_plans']['finance'].pop(field, None)
vars_path.write_text(json.dumps(variables))
env = dict(os.environ, VMDR_PLAN_TEST_STATE=str(state),
           ANSIBLE_ROLES_PATH=str(ROOT / 'roles'),
           ANSIBLE_COLLECTIONS_PATH=str(ROOT / 'tests/recovery_plan/collections'),
           ANSIBLE_PIPELINING='true', ANSIBLE_LOCAL_TEMP=str(OUT / 'local'), ANSIBLE_REMOTE_TEMP=str(OUT / 'remote'),
           AAP_SURVEY_HOST=url, AAP_SURVEY_TOKEN='fixture-token', AAP_SURVEY_CA=str(cert))

def obj(kind, name, ns=None, **fields):
    return {'kind': kind, 'metadata': {'name': name, 'uid': str(uuid.uuid4()), 'resourceVersion': '1',
                                      **({'namespace': ns} if ns else {})}, **fields}

def write(db): state.write_text(json.dumps(db))
def read(): return json.loads(state.read_text())
def record(db): return next(x for x in db[variables['recovery_control_kubeconfig']] if x['kind'] == 'ConfigMap' and x['metadata']['name'].startswith('recovery-'))

def run(label, playbook='run_recovery_plan.yml', extra=None, success=True):
    command = [ANSIBLE, '-i', 'localhost,', playbook, '-e', '@' + str(vars_path)]
    if extra: command += ['-e', json.dumps(extra)]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    log = OUT / (label + '.log'); log.write_text(result.stdout + result.stderr)
    if (result.returncode == 0) != success:
        print(result.stdout[-7000:] + result.stderr[-2000:]); raise AssertionError(f'{label}: unexpected exit {result.returncode}; {log}')
    print(f'PASS {label}', flush=True)

seed = {'control': [], 'dc': [], 'dr': []}
for site in ['dc', 'dr']:
    seed[site] = [obj('StorageClass', 'storage-class-iscsi', provisioner='csi.trident.netapp.io'),
                  obj('AppVault', 'lab-vault', 'trident-protect', status={'state': 'Available'}),
                  obj('Node', 'worker', spec={}, status={'allocatable': {'devices.kubevirt.io/kvm': '4'}, 'conditions': [{'type': 'Ready', 'status': 'True'}]}),
                  obj('KubeVirt', 'kubevirt', 'openshift-cnv', spec={'configuration': {'developerConfiguration': {}}}),
                  obj('VirtualMachine', 'unrelated', 'other', spec={'sentinel': 'untouched'})]
seed['dc'] += [obj('Namespace', 'dc-vms'),
               obj('VirtualMachine', 'database', 'dc-vms', spec={'runStrategy': 'Always', 'template': {'spec': {'volumes': [{'name': 'root', 'persistentVolumeClaim': {'claimName': 'disk'}}]}}}, status={'ready': True}),
               obj('VirtualMachineInstance', 'database', 'dc-vms', status={'phase': 'Running'}),
               obj('PersistentVolumeClaim', 'disk', 'dc-vms', spec={'storageClassName': 'storage-class-iscsi'}, status={'phase': 'Bound'})]
for site in ['dc', 'dr']:
    next(x for x in seed[site] if x['kind'] == 'Node')['metadata']['labels'] = {'kubevirt.io/schedulable': 'true'}
    next(x for x in seed[site] if x['kind'] == 'KubeVirt')['status'] = {'conditions': [{'type': 'Available', 'status': 'True'}]}
write(seed)
run('setup', 'configure_recovery_plans.yml')
run('setup-retry', 'configure_recovery_plans.yml')
assert len(api_jobs) == (3 if args.vm_only else 5) and api_jobs[0] == {'id': 1, 'name': 'Fail Over to DR', 'description': 'legacy sentinel'}
assert len(api_surveys) == (2 if args.vm_only else 4)
for survey in api_surveys.values():
    assert survey['spec'][0]['choices'] == 'finance'
    if len(survey['spec']) == 2: assert survey['spec'][1]['choices'] == 'false\ntrue'
run('enroll', 'tests/recovery_plan/enroll.yml')
base = read()
if args.vm_only:
    run('vm-only-switch')
    assert record(read())['data']['phase'] == 'protected'
    run('vm-only-failback', extra={'recovery_target_site': 'dc'})
    assert record(read())['data']['phase'] == 'protected'
    assert read()['promotions'] == 2 and read()['shutdowns'] == 2
    assert not health_requests
    assert 'not configured; validate inside the guest' in (OUT / 'vm-only-failback.log').read_text()
    assert any(x['kind'] == 'VirtualMachine' and x['metadata'].get('namespace') == 'dc-vms' for x in read()['dc'])
    assert not any(x['kind'] == 'VirtualMachine' and x['metadata'].get('namespace') == 'dr-vms' for x in read()['dr'])
    print('VM-only planned recovery passed with DC-hosted coordination and no application/fencing adapters.')
    server.shutdown()
    raise SystemExit(0)
run('journal-guards', 'tests/recovery_plan/guards.yml', extra={'recovery_target_site': 'dc'})
# Atomic lock contention: no source/target writes are permitted.
busy = copy.deepcopy(base); record(busy)['data']['owner'] = 'other'; write(busy)
run('busy-lock', success=False); assert read() == busy
race = copy.deepcopy(base); race['race_lock'] = True; write(race)
run('lock-cas-race', success=False); assert record(read())['data']['owner'] == 'other-job'
assert read()['dc'] == base['dc'] and read()['dr'] == base['dr']
# A target collision must stop before any source mutation.
collision = copy.deepcopy(base)
collision['dr'].append(obj('VirtualMachine', 'unexpected', 'dr-vms', spec={}))
write(collision)
run('target-collision', success=False)
assert read()['dc'] == base['dc'] and read().get('promotions', 0) == 0
# Snapshot failure must prevent promotion.
failed = copy.deepcopy(base); failed['fail_shutdown'] = True; write(failed)
run('failed-shutdown', success=False); assert read().get('promotions', 0) == 0
assert record(read())['data']['owner'] == ''
# Interruption after mirror release is resumable from the persisted promotion receipt.
failed = copy.deepcopy(base); failed['fail_baseline'] = True; write(failed)
run('interrupted-after-release', success=False)
assert record(read())['data']['phase'] == 'promoted'
assert read()['promotions'] == 1 and read()['shutdowns'] == 1
run('resume-after-release')
assert record(read())['data']['phase'] == 'protected'
run('completed-retry')
assert read()['promotions'] == 1 and read()['shutdowns'] == 1
run('failback', extra={'recovery_target_site': 'dc'})
run('second-failover')
assert read()['promotions'] == 3 and read()['shutdowns'] == 3
for site in ['dc', 'dr']:
    assert next(x for x in read()[site] if x['metadata']['name'] == 'unrelated') == next(x for x in base[site] if x['metadata']['name'] == 'unrelated')
# Application failure leaves a resumable activated checkpoint, never a protected result.
write(copy.deepcopy(base))
health_plans = copy.deepcopy(variables['recovery_plans'])
health_plans['finance']['network_health_tasks'] = str(ROOT / 'tests/recovery_plan/health_gate.yml')
run('application-unhealthy', extra={'recovery_plans': health_plans}, success=False)
assert record(read())['data']['phase'] == 'activated'
run('application-health-resume', extra={'recovery_plans': health_plans, 'test_application_healthy': True})
assert read()['promotions'] == 1 and read()['shutdowns'] == 1
# Disaster recovery must never access the failed source before recovering the target.
disaster = copy.deepcopy(base); disaster['source_unavailable'] = 'dc'; write(disaster)
run('disaster-source-down', extra={'recovery_mode': 'disaster', 'recovery_accept_data_loss': True}, success=False)
db = read()
assert db.get('promotions') == 1 and db.get('shutdowns', 0) == 0
assert record(db)['data']['phase'] == 'healthy' and record(db)['data']['owner'] == ''
# Source retirement is an explicit reconciliation step, never automated deletion.
db.pop('source_unavailable')
db['dc'] = [x for x in db['dc'] if not (x['kind'] in ['VirtualMachine', 'VirtualMachineInstance'] and x['metadata'].get('namespace') == 'dc-vms')]
for item in db['dc']:
    if item['kind'] == 'Schedule' and item['metadata'].get('namespace') == 'dc-vms':
        item['spec']['enabled'] = False
write(db)
run('disaster-reprotect', extra={'recovery_mode': 'disaster', 'recovery_accept_data_loss': True})
assert read()['promotions'] == 1 and record(read())['data']['phase'] == 'protected'
# An unconfigured provider and stale replica must stop before promotion.
write(copy.deepcopy(base))
run('disaster-no-acceptance', extra={'recovery_mode': 'disaster'}, success=False)
assert read() == base
write(copy.deepcopy(base))
no_fence = copy.deepcopy(variables['recovery_plans'])
no_fence['finance']['fence_tasks'] = str(ROOT / 'tests/recovery_plan/unverified_fence.yml')
run('unverified-fence', extra={'recovery_plans': no_fence, 'recovery_mode': 'disaster', 'recovery_accept_data_loss': True}, success=False)
assert read().get('promotions', 0) == 0 and read()['dc'] == base['dc']
stale = copy.deepcopy(base)
next(x for x in stale['dr'] if x['kind'] == 'AppMirrorRelationship')['status']['lastTransferredSnapshot']['completionTimestamp'] = '2000-01-01T00:00:00Z'
write(stale)
run('stale-replica', extra={'recovery_mode': 'disaster', 'recovery_accept_data_loss': True}, success=False)
assert read().get('promotions', 0) == 0 and read().get('shutdowns', 0) == 0
print(f'All recovery control-flow tests passed. Logs: {OUT}')
server.shutdown()
