"""Namespace-aware identities shared by the continuation and label recovery roles."""
import hashlib
import re
from ansible.errors import AnsibleFilterError


def mapping(plan):
    result = plan.get('namespace_mapping', {plan['source_namespace']: plan['destination_namespace']})
    if not isinstance(result, dict) or not result or len(set(result.values())) != len(result):
        raise AnsibleFilterError('Recovery namespace mapping must be nonempty and one-to-one')
    for ns in list(result) + list(result.values()):
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', ns) or ns in ['default', 'trident', 'trident-protect', 'vmdr-control'] or ns.startswith(('openshift', 'kube-', 'aap-')):
            raise AnsibleFilterError('Recovery requires dedicated workload namespaces')
    if result.get(plan['source_namespace']) != plan['destination_namespace']:
        raise AnsibleFilterError('Application namespace must map to the destination application namespace')
    return dict(sorted(result.items()))


def namespaces(plan, side='source'):
    m = mapping(plan)
    return sorted(m if side == 'source' else m.values())


def app_scope(plan, side='source'):
    return [{'namespace': ns, 'labelSelector': {'matchLabels': {plan['label_key']: plan['application']}}} for ns in namespaces(plan, side)]


def unique_refs(refs):
    return len({(r['namespace'], r['name']) for r in refs}) == len(refs)


def vm_refs(names, namespace):
    return [{'name': n, 'namespace': namespace} for n in names]


def vm_ns(plan, member, side='source'):
    ns = member.get('namespace', plan['source_namespace'])
    m = mapping(plan)
    if ns not in m:
        raise AnsibleFilterError('VM namespace is outside the enrolled mapping')
    return ns if side == 'source' else m[ns]


def destination_vms(plan):
    return [dict(m, namespace=vm_ns(plan, m, 'destination')) for m in plan['vms']]


def pvc_refs(plan, side='source'):
    refs = plan.get('pvc_refs', [{'name': n, 'namespace': plan['source_namespace']} for n in plan['pvcs']])
    return [dict(r, namespace=vm_ns(plan, r, side)) for r in refs]


def identities(resources):
    return sorted(x['metadata']['namespace'] + '/' + x['metadata']['name'] for x in resources)


def member_keys(plan, kind='vms', side='source'):
    refs = plan['vms'] if kind == 'vms' else pvc_refs(plan)
    return sorted(vm_ns(plan, r, side) + '/' + r['name'] for r in refs)


def disk_refs(members):
    return [dict(namespace=ns, name=name) for ns, name in sorted({(m['namespace'], p) for m in members for p in m['pvcs']})]


def unselected_vms(resources, selected):
    keys = {(m['namespace'], m['name']) for m in selected}
    return [r for r in resources if (r['metadata']['namespace'], r['metadata']['name']) not in keys]


def queries(plan, side, kinds):
    return [dict(k, namespace=ns) for ns in namespaces(plan, side) for k in kinds]


def return_mapping(plan):
    # The mirror's namespace may be a separate, empty application-control namespace.
    declared = plan['namespace_mapping']
    selected = plan['selected_vms']
    keys = [(m['destination_namespace'], m['vm'].split('/')[-1]) for m in selected]
    if not selected or len(set(keys)) != len(keys):
        raise AnsibleFilterError('Selected DR VM identities must be unique namespace/name pairs')
    if any(declared.get(m['vm'].split('/')[0]) != m['destination_namespace'] for m in selected):
        raise AnsibleFilterError('VM destinations do not match the saved namespace mapping')
    sources = sorted(set(declared.values()) | {plan['mirror_namespace']})
    if 'continuation_application' in plan:
        result = {dst: src for src, dst in plan['namespace_mapping'].items()}
        if len(result) != len(plan['namespace_mapping']) or set(result) != set(sources):
            raise AnsibleFilterError('Continuation catalog has an ambiguous or incomplete namespace mapping')
        return result
    if len(sources) == 1:
        return {sources[0]: 'prod-return-' + hashlib.sha256(plan['mirror_uid'].encode()).hexdigest()[:16]}
    return {ns: 'prod-return-' + hashlib.sha256((plan['mirror_uid'] + '/' + ns).encode()).hexdigest()[:16] for ns in sources}


def dr_members(members):
    return [{'name': m['vm'].split('/')[-1], 'namespace': m['destination_namespace']} for m in members]


def continuation_members(fc):
    active = {(m.get('namespace', fc['destination_namespace']), m['name']): m for m in fc['protection']['vms']}
    result = []
    for old in fc['original']['selected_vms']:
        ns = fc.get('namespace_mapping', {fc['original']['mirror_namespace']:fc['destination_namespace']})[old['destination_namespace']]
        name = old['vm'].split('/')[-1]
        member = active[(ns, name)]
        result.append(dict(old, vm=ns+'/'+name, uid=member['uid']))
    return result


class FilterModule:
    def filters(self):
        return {'lr_mapping':mapping, 'lr_namespaces':namespaces, 'lr_app_scope':app_scope,
                'lr_destination_vms':destination_vms, 'lr_unique_refs':unique_refs, 'lr_vm_refs':vm_refs, 'lr_vm_ns':vm_ns, 'lr_pvc_refs':pvc_refs,
                'lr_keys':identities, 'lr_member_keys':member_keys, 'lr_disk_refs':disk_refs,
                'lr_unselected':unselected_vms, 'lr_queries':queries,
                'fc_dr_members':dr_members, 'fc_return_mapping':return_mapping, 'fc_members':continuation_members}
