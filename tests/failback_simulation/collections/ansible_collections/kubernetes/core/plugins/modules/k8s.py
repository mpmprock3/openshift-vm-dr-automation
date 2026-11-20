# Test-only controller side effects; this is not a NetApp simulator.
from ansible.module_utils.basic import AnsibleModule
import json, os, copy, uuid
m = AnsibleModule(argument_spec=dict(state=dict(type='str'), definition=dict(type='dict'), api_version=dict(type='str'), kind=dict(type='str'), namespace=dict(type='str'), name=dict(type='str'), delete_options=dict(type='dict'), wait=dict(type='bool'), wait_timeout=dict(type='int')))
p = os.environ['VMDR_TEST_STATE']; site = os.environ['VMDR_TEST_SITE']
with open(p) as f: db = json.load(f)
o = m.params['definition']; rows = db[site]
if m.params['state'] == 'absent':
    matches = [x for x in rows if x['kind'] == m.params['kind'] and x['metadata']['name'] == m.params['name'] and x['metadata'].get('namespace') == m.params['namespace']]
    for x in matches:
        for k, v in (m.params['delete_options'] or {}).get('preconditions', {}).items():
            if x['metadata'][k] != v: m.fail_json(msg='Deletion precondition failed')
        rows.remove(x)
    changed = bool(matches)
else:
    matches = [x for x in rows if x['kind'] == o['kind'] and x['metadata']['name'] == o['metadata']['name'] and x['metadata'].get('namespace') == o['metadata'].get('namespace')]
    if matches: m.fail_json(msg='Unexpected direct overwrite of existing object')
    o['metadata'].update(uid=str(uuid.uuid4()), resourceVersion='1')
    if o['kind'] in ['Snapshot', 'ShutdownSnapshot']:
        app = next(x for x in rows if x['kind'] == 'Application' and x['metadata']['name'] == o['spec']['applicationRef'])
        disks = [x for x in rows if x['kind'] == 'PersistentVolumeClaim' and x['metadata']['name'] == 'centos-vm']
        o['status'] = {'state': 'Completed', 'appArchivePath': app['metadata']['name']+'_'+app['metadata']['uid']+'/snapshots/20261120000000_'+o['metadata']['name']+'_'+o['metadata']['uid'], 'pvcs': [x['metadata']['uid'] for x in disks], 'volumeSnapshots': [{'namespace': x['metadata']['namespace'], 'name': 'vs-'+o['metadata']['uid']} for x in disks]}
        if o['kind'] == 'ShutdownSnapshot':
            if db.get('fail_shutdown'): o['status']['state'] = 'Failed'
            else:
                db['saved_vm'] = copy.deepcopy(next(x for x in rows if x['kind'] == 'VirtualMachine' and x['metadata']['name'] == 'centos-vm'))
                rows[:] = [x for x in rows if not (x['kind'] in ['VirtualMachine', 'VirtualMachineInstance', 'Pod'] and x['metadata']['name'] == 'centos-vm')]
    if o['kind'] == 'AppMirrorRelationship':
        o['status'] = {'state': 'Established', 'lastTransfer': {'completionTimestamp': '2026-11-20T00:00:00Z'}, 'lastTransferredSnapshot': {'name': 'baseline'}, 'conditions': [{'type': 'LastTransferSucceeded', 'status': 'True'}]}
        # Like Trident Protect: an existing same-name destination Application is reused
        # only when its definition matches the source Application.
        other = 'dr' if site == 'dc' else 'dc'
        dest = next((x for x in rows if x['kind'] == 'Application' and x['metadata']['name'] == o['spec']['sourceApplicationName'] and x['metadata'].get('namespace') == o['metadata']['namespace']), None)
        src = next((x for x in db[other] if x['kind'] == 'Application' and x['metadata']['uid'] == o['spec']['sourceApplicationUID']), None)
        if dest and src and dest['spec'].get('resourceFilter') != src['spec'].get('resourceFilter'):
            msg = "Existing app definition is different that expected, cannot reuse the application '%s'" % dest['metadata']['name']
            o['status'] = {'state': 'Establishing', 'error': msg, 'lastTransfer': {}, 'lastTransferredSnapshot': {}, 'conditions': [{'type': 'Established', 'status': 'False', 'reason': 'Error', 'message': msg}]}
    rows.append(o); changed = True
with open(p, 'w') as f: json.dump(db, f)
m.exit_json(changed=changed, result=o)
