# Test-only Kubernetes API double; never used by production playbooks.
from ansible.module_utils.basic import AnsibleModule
import json, os
m = AnsibleModule(argument_spec=dict(api_version=dict(type='str'), kind=dict(type='str'), namespace=dict(type='str'), name=dict(type='str'), label_selectors=dict(type='list', elements='str')))
with open(os.environ['VMDR_TEST_STATE']) as f: db = json.load(f)
rows = [x for x in db[os.environ['VMDR_TEST_SITE']] if x['kind'] == m.params['kind'] and x['apiVersion'] == m.params['api_version'] and (not m.params['namespace'] or x['metadata'].get('namespace') == m.params['namespace']) and (not m.params['name'] or x['metadata']['name'] == m.params['name'])]
for label in m.params['label_selectors'] or []:
    k, v = label.split('=', 1)
    rows = [x for x in rows if x['metadata'].get('labels', {}).get(k) == v]
m.exit_json(changed=False, resources=rows, api_found=True)
