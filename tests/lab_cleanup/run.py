"""Exercise actual cleanup tasks with disposable cluster API doubles, never live clusters."""
import copy, json, os, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'work/lab-cleanup-tests'; OUT.mkdir(parents=True,exist_ok=True)
state=OUT/'state.json'; artifacts=OUT/'artifacts.json'
def obj(kind,name,ns=None):
    return {'kind':kind,'metadata':{'name':name,'uid':kind+'-'+name,**({'namespace':ns} if ns else {})}}
seed={s:[obj('Namespace','my-vm'),obj('Namespace','trident-protect'),obj('AppVault','lab-vault','trident-protect'),
         obj('VirtualMachine','test-vm','my-vm'),obj('PersistentVolumeClaim','test-vm','my-vm'),
         obj('Schedule','plan','my-vm'),obj('AppMirrorRelationship','mirror','my-vm'),obj('Application','app','my-vm')]
      for s in ['prod','dr']}
base=dict(lab_cleanup_prod_namespaces='my-vm',lab_cleanup_dr_namespaces='my-vm',lab_cleanup_mode='Preview')
def reset(db=None):state.write_text(json.dumps(db or seed))
def run(label,site,phase,variables,ok=True,env_overrides=None):
    env=dict(os.environ,LAB_CLEANUP_STATE=str(state),K8S_AUTH_HOST='https://'+site,K8S_AUTH_API_KEY='fixture',
        ANSIBLE_COLLECTIONS_PATH=str(ROOT/'tests/lab_cleanup/collections'),ANSIBLE_ROLES_PATH=str(ROOT/'roles'),
        ANSIBLE_LOCAL_TEMP=str(OUT/'local'),ANSIBLE_REMOTE_TEMP=str(OUT/'remote'),ANSIBLE_PIPELINING='true',
        ANSIBLE_CALLBACK_PLUGINS=str(ROOT/'tests/failback_continuation/callback_plugins'),
        ANSIBLE_CALLBACKS_ENABLED='vmdr_artifacts',VMDR_TEST_ARTIFACTS=str(artifacts))
    env.update(env_overrides or {})
    vp=OUT/'vars.json';vp.write_text(json.dumps(dict(variables,ansible_python_interpreter=sys.executable)))
    artifacts.write_text('{}')
    result=subprocess.run(['ansible-playbook','-i','localhost,',f'lab_cleanup_{site}_{phase}.yml','-e','@'+str(vp)],cwd=ROOT,env=env,capture_output=True,text=True)
    (OUT/(label+'.log')).write_text(result.stdout+result.stderr)
    assert (result.returncode==0)==ok, result.stdout[-4000:]+result.stderr
    print('PASS '+label,flush=True)
    return dict(variables,**json.loads(artifacts.read_text()))
reset(); original=state.read_text(); passed=base
for site in ['prod','dr']:passed=run('preview-'+site,site,'preview',passed)
for site in ['prod','dr']:passed=run('preview-no-delete-'+site,site,'delete',passed)
assert state.read_text()==original
for label,changes in [('confirmation',{'lab_cleanup_mode':'Delete'}),('reserved',{'lab_cleanup_prod_namespaces':'trident-protect'}),('control',{'lab_cleanup_prod_namespaces':'vmdr-control'}),('wildcard',{'lab_cleanup_prod_namespaces':'my-*'}),('empty',{'lab_cleanup_prod_namespaces':''})]:
    run('reject-'+label,'prod','preview',dict(base,**changes),False)
    assert state.read_text()==original
run('reject-direct-stage','prod','delete',dict(base,lab_cleanup_mode='Delete',lab_cleanup_confirmation='DELETE LAB DATA'),False)
for kind in ['AppVault','Deployment','StatefulSet','DaemonSet']:
    db=copy.deepcopy(seed);db['prod'].append(obj(kind,'shared','my-vm'));reset(db)
    run('reject-shared-'+kind,'prod','preview',base,False)
reset(); db=copy.deepcopy(seed);db['prod'][0]['metadata']['uid']='replacement';reset(db)
run('reject-replaced-namespace','prod','delete',dict(passed,lab_cleanup_mode='Delete',lab_cleanup_confirmation='DELETE LAB DATA'),False)
assert json.loads(state.read_text())==db
reset()
wrong=copy.deepcopy(passed);wrong['lab_cleanup_dr_manifest']['api']='https://prod'
run('reject-same-cluster','prod','delete',wrong,False)
passed.update(lab_cleanup_mode='Delete',lab_cleanup_confirmation='DELETE LAB DATA')
for site in ['prod','dr']:run('delete-'+site,site,'delete',passed)
expected={s:[x for x in seed[s] if x['metadata']['name']=='trident-protect' or x['kind']=='AppVault'] for s in seed}
assert json.loads(state.read_text())==expected
for site in ['prod','dr']:run('repeat-delete-'+site,site,'delete',passed)
for site in ['prod','dr']:passed=run('absent-preview-'+site,site,'preview',passed)
for site in ['prod','dr']:run('absent-delete-'+site,site,'delete',passed)
assert json.loads(state.read_text())==expected
print('All cleanup checks passed; shared AppVault and infrastructure preserved.')

for label,override,expected in [
    ('missing-api', {'K8S_AUTH_HOST':''}, 'no valid K8S_AUTH_HOST'),
    ('missing-token', {'K8S_AUTH_API_KEY':''}, 'no K8S_AUTH_API_KEY'),
    ('bad-api', {'K8S_AUTH_HOST':'http://prod'}, 'no valid K8S_AUTH_HOST')]:
    before=state.read_text()
    run(label,'prod','preview',base,False,dict(override))
    log=(OUT/(label+'.log')).read_text()
    assert expected in log and 'censored' not in log
    assert state.read_text()==before
