"""Native Ansible workflow jobs with disposable API doubles; never accesses a cluster."""
import copy, json, os, subprocess, sys, uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'work/continuation-tests'
OUT.mkdir(parents=True, exist_ok=True)
state, artifacts = OUT / 'state.json', OUT / 'artifacts.json'
owner = {'app.kubernetes.io/managed-by': 'aap-dr-poc'}
def obj(kind, name, ns=None, spec=None, status=None, labels=None):
    return dict(kind=kind, metadata=dict(name=name, uid=str(uuid.uuid4()), resourceVersion='1', labels=labels or owner, **({'namespace': ns} if ns else {})), spec=spec or {}, status=status or {})
def write(db): state.write_text(json.dumps(db))
def read(): return json.loads(state.read_text())
def find(db, site, kind, name): return next(x for x in db[site] if x['kind']==kind and x['metadata']['name']==name)
def run(label, stage, site, passed=None, ok=True, target='my-vm/legacy-mirror', legacy=False):
    env = dict(os.environ, VMDR_PLAN_TEST_STATE=str(state), ANSIBLE_ROLES_PATH=str(ROOT/'roles'), ANSIBLE_FILTER_PLUGINS=str(ROOT/'filter_plugins'),
               ANSIBLE_COLLECTIONS_PATH=str(ROOT/'tests/failback_continuation/collections'),
               ANSIBLE_LOCAL_TEMP=str(OUT/'local'), ANSIBLE_REMOTE_TEMP=str(OUT/'remote'),
               ANSIBLE_PIPELINING='true', ANSIBLE_CALLBACK_PLUGINS=str(ROOT/'tests/failback_continuation/callback_plugins'), ANSIBLE_CALLBACKS_ENABLED='vmdr_artifacts', VMDR_TEST_ARTIFACTS=str(artifacts), K8S_AUTH_HOST='https://'+site, K8S_AUTH_API_KEY='test-only')
    variables = dict(passed or {})
    variables.update(cluster_api='https://'+site, ansible_python_interpreter=sys.executable, test_stage=stage, test_artifacts_path=str(artifacts),
                     recovery_target=target, failback_destination_policy='Dedicated PROD namespace (retain old copy)',
                     recovery_poll_seconds=0, recovery_poll_retries=1, failover_request_id=variables.get('failover_request_id','test-forward-2'), accept_replica_data_loss='Yes', source_isolation_mode='Stop source VMs')
    artifacts.write_text('{}')
    vp=OUT/'vars.json'; vp.write_text(json.dumps(variables))
    result=subprocess.run(['ansible-playbook','-i','localhost,','tests/failback_continuation/legacy.yml' if legacy else 'tests/failback_continuation/stage.yml','-e','@'+str(vp)],cwd=ROOT,env=env,text=True,capture_output=True)
    (OUT/(label+'.log')).write_text(result.stdout+result.stderr)
    if (result.returncode==0)!=ok:
        print(result.stdout[-6500:]+result.stderr[-2000:]);raise AssertionError(label)
    if result.returncode==0:
        (OUT/(label+'-state.json')).write_text(state.read_text())
        (OUT/(label+'-artifacts.json')).write_text(json.dumps({**(passed or {}), **json.loads(artifacts.read_text())}))
    print('PASS '+label,flush=True)
    return {**(passed or {}), **json.loads(artifacts.read_text())} if result.returncode==0 else None

def cycle(prefix, target='my-vm/legacy-mirror'):
    passed={}
    for stage,site in [('begin','dr'),('prepare','dc'),('release','dr'),('check','dc'),('shutdown','dr'),('recover','dc'),('reprotect','dr')]:
        passed=run(prefix+'-'+stage,stage,site,passed,target=target)
    return passed

def seed_state():
    seed={'dc':[], 'dr':[], 'control':[]}
    for site in ['dc','dr']:
        sc=obj('StorageClass','storage-class-iscsi');sc['provisioner']='csi.trident.netapp.io'
        seed[site]=[obj('Namespace','my-vm'),sc,obj('AppVault','lab-vault','trident-protect',status={'state':'Available'}),
            obj('Node','worker',status={'allocatable':{'devices.kubevirt.io/kvm':'4'},'conditions':[{'type':'Ready','status':'True'}]}, labels={'kubevirt.io/schedulable':'true'}),
            obj('KubeVirt','kubevirt','openshift-cnv',{'configuration':{'developerConfiguration':{}}},{'conditions':[{'type':'Available','status':'True'}]}),
            obj('Application','legacy','my-vm',{'includedNamespaces':[{'namespace':'my-vm','labelSelector':{}}], 'resourceFilter':{'resourceSelectionCriteria':'Include','resourceMatchers':[{'kind':'PersistentVolume','names':['original-prod-pv']}]}},{'conditions':[{'type':'Ready','status':'True'}]}),
            obj('VirtualMachine','test-vm','my-vm',{'runStrategy':'Always' if site=='dr' else 'Halted','template':{'spec':{'volumes':[{'name':'rootdisk','dataVolume':{'name':'test-vm'}},{'name':'data','persistentVolumeClaim':{'claimName':'data'}}]}}},{'ready':site=='dr'}),
            obj('PersistentVolumeClaim','test-vm','my-vm',{'storageClassName':'storage-class-iscsi'},{'phase':'Bound'}),
            obj('PersistentVolumeClaim','data','my-vm',{'storageClassName':'storage-class-iscsi'},{'phase':'Bound'}),
            obj('Schedule','old-schedule','my-vm',{'applicationRef':'legacy','enabled':site=='dr'})]
    seed['dr'].append(obj('VirtualMachineInstance','test-vm','my-vm',status={'phase':'Running','conditions':[{'type':'Ready','status':'True'}]}))
    for kind,props in [('shutdownsnapshots',{'appVaultRef':{},'applicationRef':{}}),('appmirrorrelationships',{'promotedSnapshot':{}})]:
        seed['dr'].append(obj('CustomResourceDefinition',kind+'.protect.trident.netapp.io',spec={'versions':[{'name':'v1','served':True,'schema':{'openAPIV3Schema':{'properties':{'spec':{'properties':props}}}}}]}))
    plan={'schema_version':1,'protection_plan':'demo-vm-plan','source_api':'https://dc','destination_api':'https://dr',
          'application_name':'legacy','application_namespace':'my-vm','application_uid':find(seed,'dc','Application','legacy')['metadata']['uid'],
          'mirror_name':'legacy-mirror','mirror_namespace':'my-vm','mirror_uid':str(uuid.uuid4()),'schedule':'old-schedule',
          'namespace_mapping':{'my-vm':'my-vm'},'selected_vms':[{'vm':'my-vm/test-vm','uid':find(seed,'dc','VirtualMachine','test-vm')['metadata']['uid'], 'destination_namespace':'my-vm','disks':[{'pvc':'test-vm','storage_class':'storage-class-iscsi'},{'pvc':'data','storage_class':'storage-class-iscsi'}]}]}
    amr=obj('AppMirrorRelationship','legacy-mirror','my-vm',{'desiredState':'Promoted','sourceApplicationUID':plan['application_uid'],'sourceApplicationName':'legacy','sourceAppVaultRef':'lab-vault','destinationAppVaultRef':'lab-vault','recurrenceRule':'DTSTART:20260101T000000Z\nRRULE:FREQ=MINUTELY;INTERVAL=10'}, {'state':'Promoted','destinationApplicationRef':'legacy'})
    amr['metadata']['uid']=plan['mirror_uid'];seed['dr'].append(amr)
    cm=obj('ConfigMap','legacy-mirror-plan','my-vm',labels={**owner,'app.kubernetes.io/component':'recovery-plan'});cm['data']={'plan.json':json.dumps(plan)};seed['dr'].append(cm)
    return seed

def main():
    seed=seed_state()
    write(seed)
    passed=cycle('first')
    completed=read()
    assert find(completed,'dc','VirtualMachine','test-vm')==find(seed,'dc','VirtualMachine','test-vm'), 'Original stopped VM changed'
    assert find(completed,'dc','PersistentVolumeClaim','test-vm')==find(seed,'dc','PersistentVolumeClaim','test-vm'), 'Original disk changed'
    assert int(passed['continuation_state']['label_plan']['interval_minutes'])==10
    assert completed['promotions']==1 and completed['shutdowns']==1
    cycle('completed-retry')
    assert read()['promotions']==1 and read()['shutdowns']==1
    print('First failback and completed retry passed. API doubles do not validate storage or guest data.',flush=True)

    # Exercise the EXISTING failover implementation against the published legacy catalog.
    new_target=passed['continuation_state']['original']['mirror_namespace']+'/'+passed['continuation_state']['application']+'-mirror'
    forward={}
    for stage,site in [('prepare_failover','dr'),('isolate_source','dc'),('promote_failover','dr'),('verify_failover','dr')]:
        forward=run('existing-failover-'+stage,stage,site,forward,target=new_target,legacy=True)
    again=cycle('second',target=new_target)
    assert again['continuation_state']['destination_namespace']==passed['continuation_state']['destination_namespace']
    assert read()['promotions']==3 and read()['shutdowns']==2
    cycle('second-retry',target=new_target)
    assert read()['promotions']==3 and read()['shutdowns']==2
    forward={'failover_request_id':'test-forward-3'}
    for stage,site in [('prepare_failover','dr'),('isolate_source','dc'),('promote_failover','dr'),('verify_failover','dr')]:
        forward=run('third-failover-'+stage,stage,site,forward,target=new_target,legacy=True)
    third=cycle('third',target=new_target)
    assert third['continuation_state']['destination_namespace']==passed['continuation_state']['destination_namespace']
    assert read()['promotions']==5 and read()['shutdowns']==3
    assert any(x['kind']=='ConfigMap' and x['metadata'].get('labels',{}).get('app.kubernetes.io/component')=='failback-continuation-history' for x in read()['dr'])
    print('Existing failover -> continuation failback repeated successfully in the API fixture.',flush=True)

    from guards import exercise
    exercise(OUT, run, cycle, read, write)
    print('All continuation safety gates and interruption tests passed.',flush=True)

if __name__ == "__main__":
    main()
