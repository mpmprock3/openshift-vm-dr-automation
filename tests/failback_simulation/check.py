"""Assert safety invariants in the disposable state produced by the test."""
import json, sys
s=json.load(open(sys.argv[1]))
assert next(x for x in s['dc'] if x['metadata']['uid']=='unrelated') == s['unrelated']
if '--failed-shutdown' in sys.argv:
    assert next(x for x in s['dc'] if x['kind']=='AppMirrorRelationship')['spec']['desiredState']=='Established'
    assert next(x for x in s['dc'] if x['kind']=='VirtualMachine' and x['metadata']['name']=='centos-vm')['spec']['runStrategy']=='Halted'
    assert not any(x['metadata'].get('labels',{}).get('app.kubernetes.io/component')=='failback-receipt' for x in s['dc'])
else:
    assert any(x['kind']=='VirtualMachineInstance' and x['metadata']['name']=='centos-vm' for x in s['dc'])
    assert not any(x['kind']=='VirtualMachineInstance' and x['metadata']['name']=='centos-vm' for x in s['dr'])
    assert not any(x['kind']=='AppMirrorRelationship' for x in s['dc'])
    forward=next(x for x in s['dr'] if x['kind']=='AppMirrorRelationship')
    assert forward['spec']['desiredState']=='Established'
    catalog=json.loads(next(x for x in s['dr'] if x['metadata']['name']=='app-mirror-plan')['data']['plan.json'])
    assert catalog['mirror_uid']==forward['metadata']['uid']
    assert catalog['selected_vms'][0]['uid']=='recovered-dc-vm'
    journal=json.loads(next(x for x in s['dr'] if x['metadata'].get('labels',{}).get('app.kubernetes.io/component')=='failback-journal')['data']['state.json'])
    assert journal['phase']=='complete'
print('Failback state invariants passed')
