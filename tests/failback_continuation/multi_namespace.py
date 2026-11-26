"""Two namespaces with identical VM/PVC names, through failback and another full cycle."""
import copy,json,uuid,sys
from pathlib import Path
import run as harness
h=harness
control='--separate-control' in sys.argv
h.OUT=h.ROOT/('work/multi-namespace-control-tests' if control else 'work/multi-namespace-tests');h.OUT.mkdir(parents=True,exist_ok=True)
h.state,h.artifacts=h.OUT/'state.json',h.OUT/'artifacts.json'
seed=h.seed_state()
for site,ns in [('dc','payments'),('dr','payments-dr')]:
    seed[site].append(h.obj('Namespace',ns))
    for item in list(seed[site]):
        if item['kind'] in ['VirtualMachine','VirtualMachineInstance','PersistentVolumeClaim'] and item['metadata'].get('namespace')=='my-vm':
            clone=copy.deepcopy(item);clone['metadata'].update(namespace=ns,uid=str(uuid.uuid4()));seed[site].append(clone)
    app=h.find(seed,site,'Application','legacy');app['spec']['includedNamespaces'].append({'namespace':ns,'labelSelector':{}})
cm=h.find(seed,'dr','ConfigMap','legacy-mirror-plan');plan=json.loads(cm['data']['plan.json'])
plan['namespace_mapping']['payments']='payments-dr'
member=copy.deepcopy(plan['selected_vms'][0]);member.update(vm='payments/test-vm',destination_namespace='payments-dr',uid=next(x['metadata']['uid'] for x in seed['dc'] if x['kind']=='VirtualMachine' and x['metadata'].get('namespace')=='payments'))
plan['selected_vms'].append(member);cm['data']['plan.json']=json.dumps(plan)
if control:
    seed['dr'].append(h.obj('Namespace','dr-control'))
    for row in seed['dr']:
        if row['kind'] in ['Application','AppMirrorRelationship','Schedule','ConfigMap'] and row['metadata'].get('namespace')=='my-vm':
            row['metadata']['namespace']='dr-control'
    plan['mirror_namespace']='dr-control';cm['data']['plan.json']=json.dumps(plan)
target=plan['mirror_namespace']+'/legacy-mirror'
h.write(seed)
passed=h.cycle('multi-first',target=target);first=h.read();fc=passed['continuation_state']
assert len(fc['namespace_mapping'])==(3 if control else 2)
assert len(set(fc['namespace_mapping'].values()))==(3 if control else 2)
assert len(fc['label_plan']['pvc_uids'])==4
assert len(fc['next_catalog']['selected_vms'])==2
assert len({m['vm'] for m in fc['next_catalog']['selected_vms']})==2
for ns in ['my-vm','payments']:
    for kind in ['VirtualMachine','PersistentVolumeClaim']:
        original=[x for x in seed['dc'] if x['kind']==kind and x['metadata'].get('namespace')==ns]
        current=[x for x in first['dc'] if x['kind']==kind and x['metadata'].get('namespace')==ns]
        assert original==current,'Original PROD copy was changed'
for dr_ns,ns in fc['namespace_mapping'].items():
    if control and dr_ns=='dr-control':continue
    assert any(x['kind']=='VirtualMachine' and x['metadata'].get('namespace')==ns and x['status']['ready'] for x in first['dc'])
assert not any(x['kind']=='VirtualMachineInstance' for x in first['dr'])
h.cycle('multi-completed-retry',target=target)
assert h.read()['promotions']==1
new_target=plan['mirror_namespace']+'/'+fc['application']+'-mirror'
forward={}
for stage,site in [('prepare_failover','dr'),('isolate_source','dc'),('promote_failover','dr'),('verify_failover','dr')]:
    forward=h.run('multi-forward-'+stage,stage,site,forward,target=new_target,legacy=True)
again=h.cycle('multi-second',target=new_target)
assert again['continuation_state']['namespace_mapping']==fc['namespace_mapping']
assert h.read()['promotions']==3 and h.read()['shutdowns']==2
# A failure in the non-control namespace must prevent shutdown/promotion.
h.write(seed)
bad=copy.deepcopy(seed);bad['dr'].append(h.obj('VirtualMachine','unrelated','payments-dr'));h.write(bad)
h.run('reject-unrelated-second-namespace','begin','dr',ok=False,target=target)
assert not h.read().get('writes')
checked=json.loads((h.OUT/'multi-first-check-state.json').read_text())
artifacts=json.loads((h.OUT/'multi-first-check-artifacts.json').read_text())
checked['shutdown_fault']='missing-second-disk';h.write(checked)
h.run('reject-final-second-namespace-disk','shutdown','dr',artifacts,ok=False,target=target)
assert not h.read().get('promotions')
# Namespace ownership check covers the second target, not only the Application namespace.
preparing=json.loads((h.OUT/'multi-first-begin-state.json').read_text())
artifacts=json.loads((h.OUT/'multi-first-begin-artifacts.json').read_text())
other=artifacts['continuation_state']['namespace_mapping']['payments-dr']
preparing['dc'].append(h.obj('Namespace',other,labels={'owner':'foreign'}));h.write(preparing)
h.run('reject-second-target-collision','prepare','dc',artifacts,ok=False,target=target)
print('PASS multi-namespace cycles, duplicate names, final disk completeness and target ownership')
