import json, os
from pathlib import Path
from ansible.module_utils.basic import AnsibleModule

def run(operation):
    module = AnsibleModule(argument_spec={k: {'type': t} for k,t in {
        'api_version':'str','kind':'str','name':'str','namespace':'str','state':'str',
        'delete_options':'dict','wait':'bool','wait_timeout':'int','wait_sleep':'int'}.items()})
    p=module.params
    path=Path(os.environ['LAB_CLEANUP_STATE'])
    db=json.loads(path.read_text())
    site=os.environ['K8S_AUTH_HOST'].split('//')[1]
    matches=[x for x in db[site] if x['kind']==p['kind'] and
             (not p['name'] or x['metadata']['name']==p['name']) and
             (not p['namespace'] or x['metadata'].get('namespace')==p['namespace'])]
    if operation=='info': module.exit_json(changed=False, resources=matches, api_found=True)
    if p['state']!='absent': module.fail_json(msg='Only deletion is supported in cleanup')
    for obj in matches:
        if obj['metadata']['uid'] != p['delete_options']['preconditions']['uid']:
            module.fail_json(msg='UID precondition failed')
        db[site].remove(obj)
        if obj['kind']=='Namespace':
            db[site]=[x for x in db[site] if x['metadata'].get('namespace')!=p['name']]
    path.write_text(json.dumps(db))
    module.exit_json(changed=bool(matches))
