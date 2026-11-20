from ansible.module_utils.basic import AnsibleModule
import json, os, copy
m = AnsibleModule(argument_spec=dict(api_version=dict(type='str'), kind=dict(type='str'), namespace=dict(type='str'), name=dict(type='str'), patch=dict(type='list', elements='dict')))
p = os.environ['VMDR_TEST_STATE']; site = os.environ['VMDR_TEST_SITE']
with open(p) as f: db = json.load(f)
rows = db[site]
o = next(x for x in rows if x['kind'] == m.params['kind'] and x['metadata']['name'] == m.params['name'] and x['metadata'].get('namespace') == m.params['namespace'])
candidate = copy.deepcopy(o)
for patch in m.params['patch']:
    parts = [s.replace('~1','/').replace('~0','~') for s in patch['path'].strip('/').split('/')]; parent = candidate
    for key in parts[:-1]: parent = parent[key]
    if patch['op'] == 'test':
        if parent[parts[-1]] != patch['value']: m.fail_json(msg='Atomic JSON Patch test failed')
    else: parent[parts[-1]] = patch['value']
candidate['metadata']['resourceVersion'] = str(int(o['metadata']['resourceVersion'])+1)
o.clear(); o.update(candidate)
if o['kind'] == 'AppMirrorRelationship' and o['spec']['desiredState'] == 'Promoted':
    if not o['spec'].get('promotedSnapshot'): m.fail_json(msg='Promotion omitted exact final archive')
    if any(x['kind']=='VirtualMachineInstance' and x['metadata']['name']=='centos-vm' for x in db['dr']): m.fail_json(msg='Promotion attempted while DR VM active')
    o['status'] = {'state':'Promoted', 'conditions':[{'type':'Promoted','status':'True'}]}
    rows[:] = [x for x in rows if not (x['kind'] in ['VirtualMachine','VirtualMachineInstance'] and x['metadata']['name']=='centos-vm')]
    vm = copy.deepcopy(db['saved_vm']); vm['metadata'].update(namespace='my-vm',uid='recovered-dc-vm'); rows.append(vm)
    rows.append({'apiVersion':'kubevirt.io/v1','kind':'VirtualMachineInstance','metadata':{'name':'centos-vm','namespace':'my-vm','uid':'recovered-vmi','resourceVersion':'1'},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}]}})
with open(p,'w') as f:json.dump(db,f)
m.exit_json(changed=True,result=o)
