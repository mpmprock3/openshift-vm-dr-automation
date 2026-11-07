"""POC Trident Protect 26.02 setup. API credentials and VM specs never enter output."""
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GROUP = '/apis/protect.trident.netapp.io/v1'
OWNER = 'aap-dr-poc'


class API:
    def __init__(self):
        self.host = os.environ.get('K8S_AUTH_HOST', '').rstrip('/')
        token = os.environ.get('K8S_AUTH_API_KEY', '')
        url = urllib.parse.urlsplit(self.host)
        if url.scheme != 'https' or not url.netloc or url.path or url.username or not token:
            raise ValueError('Attach the correct OpenShift API credential.')
        self.headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'Content-Type': 'application/json'}
        ctx = ssl.create_default_context(cafile=os.environ.get('K8S_AUTH_SSL_CA_CERT') or None)
        if os.environ.get('K8S_AUTH_VERIFY_SSL', 'true').lower() in ('false', '0', 'no'):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ctx))
        self.created = []

    def request(self, path, method='GET', body=None, optional=False):
        req = urllib.request.Request(self.host + path, method=method, headers=self.headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(req, timeout=60) as response:
                payload = response.read()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            if optional and exc.code == 404:
                return None
            detail = 'No Kubernetes denial message returned.'
            try:
                status = json.loads(exc.read())
                if isinstance(status, dict) and status.get('kind') == 'Status':
                    detail = str(status.get('message') or status.get('reason') or detail)
            except (ValueError, UnicodeError):
                pass
            # Never emit headers, request bodies or the bearer token.
            token = self.headers['Authorization'].removeprefix('Bearer ')
            detail = detail.replace(token, '[REDACTED]')[:3000]
            raise ValueError(method + ' ' + path + ' returned HTTP ' + str(exc.code) + ': ' + detail) from None
        except urllib.error.URLError:
            raise ValueError('API network or TLS failure; check the attached credential and connectivity.') from None

    def ensure(self, collection, body):
        path = collection + '/' + body['metadata']['name']
        current = self.request(path, optional=True)
        if current:
            if current['metadata'].get('labels', {}).get('app.kubernetes.io/managed-by') != OWNER:
                raise ValueError('Refusing to adopt an existing unmanaged resource: ' + path)
            if not contains(current.get('spec', {}), body.get('spec', {})):
                raise ValueError('Existing protection specification differs: ' + path + '. Use a new plan or review the existing plan before modifying it.')
            return current
        result = self.request(collection, 'POST', body)
        self.created.append(body['kind'] + '/' + body['metadata'].get('namespace', '') + '/' + body['metadata']['name'])
        return result

    def wait(self, path, state, timeout):
        deadline = time.monotonic() + timeout
        while True:
            obj = self.request(path)
            actual = obj.get('status', {}).get('state', 'Unknown')
            if actual == state:
                return obj
            if actual in ('Failed', 'Error', 'Invalid') or time.monotonic() >= deadline:
                # Only status, not the VM or credentials, is returned for troubleshooting.
                raise ValueError('Waiting for ' + path + ' to become ' + state + '; status=' + json.dumps(obj.get('status', {})))
            time.sleep(10)


def contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    return actual == expected


def resource(kind, namespace, name, spec):
    return {'apiVersion': 'protect.trident.netapp.io/v1', 'kind': kind,
            'metadata': {'name': name, 'namespace': namespace, 'labels': {'app.kubernetes.io/managed-by': OWNER}}, 'spec': spec}


def names(plan):
    base = plan[:40] + '-' + hashlib.sha256(plan.encode()).hexdigest()[:8]
    return base, base + '-initial', base + '-schedule', base + '-mirror'


def vault(api, namespace, name):
    obj = api.request(GROUP + '/namespaces/' + namespace + '/appvaults/' + name)
    if obj.get('status', {}).get('state') != 'Available':
        raise ValueError('AppVault is not Available: ' + name)
    # Fingerprint lets DR check that its source vault addresses the same bucket without exporting its settings.
    settings = {k: obj['spec'].get(k) for k in ('providerType', 'providerConfig')}
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()


def build_application(api, plan):
    """Explicitly include VM dependencies; never select the whole default namespace."""
    matches = set()
    cluster_kinds = set()
    def add(group, version, kind, ns, name):
        if name:
            matches.add((group, version, kind, ns or '', name))
            if not ns:
                cluster_kinds.add((group, version, kind))
    pvcs = {(p['metadata']['namespace'], p['metadata']['name']): p for p in api.request('/api/v1/persistentvolumeclaims')['items']}
    scs = {s['metadata']['name']: s for s in api.request('/apis/storage.k8s.io/v1/storageclasses')['items']}
    vscs = api.request('/apis/snapshot.storage.k8s.io/v1/volumesnapshotclasses')['items']
    snapshots = [s for s in vscs if s.get('driver') == 'csi.trident.netapp.io']
    if not snapshots:
        raise ValueError('DC has no VolumeSnapshotClass for csi.trident.netapp.io.')
    for s in snapshots:
        add('snapshot.storage.k8s.io', 'v1', 'VolumeSnapshotClass', '', s['metadata']['name'])
    disk_owners = {}
    for selected in plan['selected_vms']:
        ns, vm_name = selected['vm'].split('/', 1)
        vm = api.request('/apis/kubevirt.io/v1/namespaces/' + ns + '/virtualmachines/' + vm_name)
        if vm['metadata']['uid'] != selected['uid']:
            raise ValueError('VM was recreated since validation: ' + selected['vm'])
        add('kubevirt.io', 'v1', 'VirtualMachine', ns, vm_name)
        add('kubevirt.io', 'v1', 'VirtualMachineInstance', ns, vm_name)
        spec = vm['spec']['template']['spec']
        current_disks = set()
        for vol in spec.get('volumes', []):
            supported = {'name', 'dataVolume', 'persistentVolumeClaim', 'cloudInitNoCloud', 'cloudInitConfigDrive', 'secret', 'configMap', 'serviceAccount', 'containerDisk'}
            if set(vol) - supported:
                raise ValueError('Unsupported volume dependency in this POC for ' + selected['vm'] + ': ' + vol.get('name', 'unnamed'))
            claim = vol.get('persistentVolumeClaim', {}).get('claimName') or vol.get('dataVolume', {}).get('name')
            if claim:
                current_disks.add(claim)
                pvc = pvcs.get((ns, claim))
                if not pvc or pvc.get('status', {}).get('phase') != 'Bound':
                    raise ValueError('Missing/unbound source disk: ' + ns + '/' + claim)
                sc = pvc['spec'].get('storageClassName', '')
                if scs.get(sc, {}).get('provisioner') != 'csi.trident.netapp.io':
                    raise ValueError('Source disk is not on Trident: ' + ns + '/' + claim)
                pv = api.request('/api/v1/persistentvolumes/' + pvc['spec']['volumeName'])
                if pv['spec'].get('csi', {}).get('driver') != 'csi.trident.netapp.io':
                    raise ValueError('Source PV is not a Trident CSI volume.')
                disk_owners[(ns, claim)] = selected['vm']
                add('', 'v1', 'PersistentVolumeClaim', ns, claim)
                add('', 'v1', 'PersistentVolume', '', pvc['spec']['volumeName'])
                add('storage.k8s.io', 'v1', 'StorageClass', '', sc)
                if 'dataVolume' in vol:
                    add('cdi.kubevirt.io', 'v1beta1', 'DataVolume', ns, claim)
            for key in ('cloudInitNoCloud', 'cloudInitConfigDrive'):
                cloud = vol.get(key, {})
                for ref in ('secretRef', 'userDataSecretRef', 'networkDataSecretRef'):
                    add('', 'v1', 'Secret', ns, cloud.get(ref, {}).get('name'))
            add('', 'v1', 'Secret', ns, vol.get('secret', {}).get('secretName'))
            add('', 'v1', 'ConfigMap', ns, vol.get('configMap', {}).get('name'))
            add('', 'v1', 'ServiceAccount', ns, vol.get('serviceAccount', {}).get('serviceAccountName'))
            add('', 'v1', 'Secret', ns, vol.get('containerDisk', {}).get('imagePullSecret'))
        if current_disks != {d['pvc'] for d in selected['disks']}:
            raise ValueError('VM disks changed since validation; rerun validation: ' + selected['vm'])
        if spec.get('accessCredentials') or spec.get('domain', {}).get('firmware', {}).get('kernelBoot'):
            raise ValueError('Access credentials/kernel boot need additional dependency handling before protecting ' + selected['vm'])
        for network in spec.get('networks', []):
            ref = network.get('multus', {}).get('networkName')
            if ref:
                if '/' in ref:
                    ref_ns, ref = ref.split('/', 1)
                    if ref_ns != ns:
                        raise ValueError('Cross-namespace network attachment requires an explicit DR network mapping: ' + selected['vm'])
                api.request('/apis/k8s.cni.cncf.io/v1/namespaces/' + ns + '/network-attachment-definitions/' + ref)
                add('k8s.cni.cncf.io', 'v1', 'NetworkAttachmentDefinition', ns, ref)
        for field, default_kind in [('instancetype', 'VirtualMachineClusterInstancetype'), ('preference', 'VirtualMachineClusterPreference')]:
            ref = vm['spec'].get(field, {})
            add('apps', 'v1', 'ControllerRevision', ns, ref.get('revisionName'))
            if ref.get('name'):
                kinds = {'virtualmachineinstancetype': 'VirtualMachineInstancetype', 'virtualmachineclusterinstancetype': 'VirtualMachineClusterInstancetype', 'virtualmachinepreference': 'VirtualMachinePreference', 'virtualmachineclusterpreference': 'VirtualMachineClusterPreference'}
                kind = kinds[ref.get('kind', default_kind).lower()]
                add('instancetype.kubevirt.io', 'v1beta1', kind, '' if 'Cluster' in kind else ns, ref['name'])
        # Launcher pods are owned by the VMI (whose UID differs from the VM UID).
        for pod in api.request('/api/v1/namespaces/' + ns + '/pods')['items']:
            if any(ref.get('kind') == 'VirtualMachineInstance' and ref.get('name') == vm_name
                   for ref in pod['metadata'].get('ownerReferences', [])):
                add('', 'v1', 'Pod', ns, pod['metadata']['name'])
    matchers = []
    for group, version, kind, ns, item_name in sorted(matches):
        matcher = {'group': group, 'version': version, 'kind': kind, 'names': [item_name]}
        if ns:
            matcher['namespaces'] = [ns]
        matchers.append(matcher)
    return {'includedNamespaces': [{'namespace': ns, 'labelSelector': {}} for ns in sorted(plan['namespace_mapping'])],
            'includedClusterScopedResources': [{'groupVersionKind': {'group': g, 'version': v, 'kind': k}} for g, v, k in sorted(cluster_kinds)],
            'resourceFilter': {'resourceSelectionCriteria': 'Include', 'resourceMatchers': matchers}}


def setup_dc(api, data):
    plan = data['plan']
    if api.host != plan['source_api'].rstrip('/'):
        raise ValueError('DC job credential does not match the cluster used during validation.')
    name, snap_name, schedule_name, mirror_name = names(plan['protection_plan'])
    source_ns = sorted(plan['namespace_mapping'])[0]
    base = GROUP + '/namespaces/' + source_ns
    fingerprint = vault(api, data['vault_namespace'], data['source_vault'])
    app_spec = build_application(api, plan)
    app = api.ensure(base + '/applications', resource('Application', source_ns, name, app_spec))
    api.wait(base + '/applications/' + name, 'Ready', data['timeout'])
    api.ensure(base + '/snapshots', resource('Snapshot', source_ns, snap_name,
               {'applicationRef': name, 'appVaultRef': data['source_vault']}))
    api.wait(base + '/snapshots/' + snap_name, 'Completed', data['timeout'])
    rule = 'DTSTART:20260101T000000Z\nRRULE:FREQ=MINUTELY;INTERVAL=' + str(plan['replication_interval_minutes'])
    api.ensure(base + '/schedules', resource('Schedule', source_ns, schedule_name,
               {'applicationRef': name, 'appVaultRef': data['source_vault'], 'backupRetention': '0',
                'snapshotRetention': '3', 'enabled': True, 'granularity': 'Custom', 'recurrenceRule': rule}))
    return {'plan': plan, 'application_name': name, 'application_namespace': source_ns,
            'application_uid': app['metadata']['uid'], 'source_vault': data['source_vault'],
            'vault_fingerprint': fingerprint, 'initial_snapshot': snap_name, 'schedule': schedule_name,
            'recurrence_rule': rule, 'mirror_name': mirror_name,
            'result': 'DC Application Ready and initial Snapshot Completed; snapshot schedule configured.'}


def setup_dr(api, data):
    dc = data['dc_result']
    plan = dc['plan']
    if api.host == plan['source_api'].rstrip('/'):
        raise ValueError('DR job is using the DC credential; refusing to replicate into the source cluster.')
    fingerprint = vault(api, data['vault_namespace'], data['source_vault'])
    if fingerprint != dc['vault_fingerprint']:
        raise ValueError('DR source AppVault configuration differs from DC. DR must access the source snapshot metadata bucket.')
    vault(api, data['vault_namespace'], data['destination_vault'])
    sc = api.request('/apis/storage.k8s.io/v1/storageclasses/' + data['storage_class'])
    if sc.get('provisioner') != 'csi.trident.netapp.io':
        raise ValueError('Destination storage class must use Trident.')
    target_ns = plan['namespace_mapping'][dc['application_namespace']]
    base = GROUP + '/namespaces/' + target_ns
    existing = api.request(base + '/appmirrorrelationships/' + dc['mirror_name'], optional=True)
    if existing and (existing.get('spec', {}).get('desiredState') != 'Established' or existing.get('status', {}).get('state') in ('Promoted', 'Promoting', 'Reestablishing')):
        raise ValueError('Existing relationship is in a recovery state. Protection setup will not reset it.')
    for ns in sorted(set(plan['namespace_mapping'].values())):
        obj = api.request('/api/v1/namespaces/' + ns, optional=True)
        if obj and obj.get('status', {}).get('phase') == 'Terminating':
            raise ValueError('Destination namespace is terminating: ' + ns)
        if not obj:
            api.request('/api/v1/namespaces', 'POST', {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns}})
            api.created.append('Namespace/' + ns)
    if not existing:
        for selected in plan['selected_vms']:
            ns = selected['destination_namespace']
            vm_name = selected['vm'].split('/', 1)[1]
            if api.request('/apis/kubevirt.io/v1/namespaces/' + ns + '/virtualmachines/' + vm_name, optional=True):
                raise ValueError('Destination VM already exists: ' + ns + '/' + vm_name)
            for disk in selected['disks']:
                if api.request('/api/v1/namespaces/' + ns + '/persistentvolumeclaims/' + disk['pvc'], optional=True):
                    raise ValueError('Destination PVC already exists: ' + ns + '/' + disk['pvc'])
    spec = {'desiredState': 'Established', 'destinationAppVaultRef': data['destination_vault'],
            'sourceAppVaultRef': data['source_vault'], 'sourceApplicationName': dc['application_name'],
            'sourceApplicationUID': dc['application_uid'], 'storageClassName': data['storage_class'],
            'namespaceMapping': [{'source': s, 'destination': d} for s, d in sorted(plan['namespace_mapping'].items())],
            'recurrenceRule': dc['recurrence_rule']}
    api.ensure(base + '/appmirrorrelationships', resource('AppMirrorRelationship', target_ns, dc['mirror_name'], spec))
    amr = api.wait(base + '/appmirrorrelationships/' + dc['mirror_name'], 'Established', data['timeout'])
    return {'result': 'Replication relationship Established. No failover was performed.',
            'relationship': target_ns + '/' + dc['mirror_name'], 'state': amr['status']['state'],
            'namespace_mapping': plan['namespace_mapping'], 'selected_vms': [v['vm'] for v in plan['selected_vms']],
            'note': 'Verify recovery in a separate test; Established alone does not demonstrate guest/application recovery or an achieved RPO.'}


def main():
    api = None
    try:
        data = json.load(sys.stdin)
        data['timeout'] = int(data.get('timeout', 1800))
        api = API()
        result = setup_dc(api, data) if sys.argv[1] == 'dc' else setup_dr(api, data)
        print(json.dumps({'success': True, 'changed': bool(api.created), 'created_resources': api.created, 'output': result}, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        print(json.dumps({'success': False, 'error': str(exc), 'created_resources': api.created if api else [],
                          'note': 'No automatic rollback. Completed resources are retained for diagnosis and safe reruns.'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
