import copy
import io
import json
import urllib.error
import unittest
from unittest.mock import patch

import protection_setup as p


class FakeAPI:
    def __init__(self, objects, host='https://dc.example'):
        self.objects = copy.deepcopy(objects)
        self.host = host
        self.created = []
        self.writes = []

    def request(self, path, method='GET', body=None, optional=False):
        if method == 'POST':
            self.writes.append((path, body))
            obj = copy.deepcopy(body)
            obj['metadata']['uid'] = 'generated-uid'
            self.objects[path + '/' + body['metadata']['name']] = obj
            return obj
        if path in self.objects:
            return copy.deepcopy(self.objects[path])
        if optional:
            return None
        raise AssertionError('Unexpected read: ' + path)

    ensure = p.API.ensure

    def wait(self, path, state, timeout):
        self.objects[path]['status'] = {'state': state}
        return copy.deepcopy(self.objects[path])


def fixture():
    vm = {'metadata': {'name': 'vm-a', 'namespace': 'default', 'uid': 'vm-uid'},
          'spec': {'template': {'spec': {'volumes': [{'name': 'root', 'dataVolume': {'name': 'disk-a'}},
            {'name': 'cloud', 'cloudInitNoCloud': {'userData': 'private-inline-data'}}]}}}}
    pvc = {'metadata': {'name': 'disk-a', 'namespace': 'default'}, 'spec': {'storageClassName': 'netapp', 'volumeName': 'pv-a'}, 'status': {'phase': 'Bound'}}
    vault = {'spec': {'providerType': 'generic-s3', 'providerConfig': {'s3': {'bucketName': 'shared'}}}, 'status': {'state': 'Available'}}
    objects = {'/api/v1/persistentvolumeclaims': {'items': [pvc]},
               '/apis/storage.k8s.io/v1/storageclasses': {'items': [{'metadata': {'name': 'netapp'}, 'provisioner': 'csi.trident.netapp.io'}]},
               '/apis/snapshot.storage.k8s.io/v1/volumesnapshotclasses': {'items': [{'metadata': {'name': 'snapclass'}, 'driver': 'csi.trident.netapp.io'}]},
               '/api/v1/persistentvolumes/pv-a': {'spec': {'csi': {'driver': 'csi.trident.netapp.io'}}},
               '/apis/kubevirt.io/v1/namespaces/default/virtualmachines/vm-a': vm,
               '/api/v1/namespaces/default/pods': {'items': []},
               p.GROUP + '/namespaces/trident-protect/appvaults/lab-vault': vault}
    plan = {'source_api': 'https://dc.example', 'protection_plan': 'demo', 'namespace_mapping': {'default': 'vmdr'},
            'replication_interval_minutes': 5, 'selected_vms': [{'vm': 'default/vm-a', 'uid': 'vm-uid',
              'destination_namespace': 'vmdr', 'disks': [{'pvc': 'disk-a', 'storage_class': 'netapp'}]}]}
    return objects, plan


class ProtectionTests(unittest.TestCase):
    def test_api_denial_reports_reason_without_token(self):
        api = p.API.__new__(p.API)
        api.host = 'https://dc.example'
        api.headers = {'Authorization': 'Bearer example-token'}
        with patch.object(p.urllib.request, 'build_opener') as factory:
            api.opener = factory.return_value
            payload = {'kind': 'Status', 'message': 'User test cannot create applications; example-token'}
            api.opener.open.side_effect = urllib.error.HTTPError(api.host, 403, 'Forbidden', {}, io.BytesIO(json.dumps(payload).encode()))
            with self.assertRaises(ValueError) as caught:
                api.request('/applications', 'POST', {})
            self.assertIn('cannot create applications', str(caught.exception))
            self.assertNotIn('example-token', str(caught.exception))

    def test_dependency_filter_excludes_unselected_workloads_and_inline_data(self):
        objects, plan = fixture()
        spec = p.build_application(FakeAPI(objects), plan)
        matches = spec['resourceFilter']['resourceMatchers']
        self.assertTrue(any(m['kind'] == 'VirtualMachine' and m['names'] == ['vm-a'] for m in matches))
        self.assertTrue(any(m['kind'] == 'PersistentVolumeClaim' and m['names'] == ['disk-a'] for m in matches))
        self.assertTrue(any(m['kind'] == 'PersistentVolume' and m['names'] == ['pv-a'] for m in matches))
        self.assertTrue(all('names' in m for m in matches))
        self.assertNotIn('private-inline-data', str(spec))

    def test_vm_replacement_and_disk_change_rejected(self):
        objects, plan = fixture()
        plan['selected_vms'][0]['uid'] = 'different'
        with self.assertRaisesRegex(ValueError, 'recreated'):
            p.build_application(FakeAPI(objects), plan)
        plan['selected_vms'][0]['uid'] = 'vm-uid'
        plan['selected_vms'][0]['disks'] = []
        with self.assertRaisesRegex(ValueError, 'disks changed'):
            p.build_application(FakeAPI(objects), plan)

    def test_dc_setup_reruns_without_additional_writes(self):
        objects, plan = fixture()
        api = FakeAPI(objects)
        data = {'plan': plan, 'vault_namespace': 'trident-protect', 'source_vault': 'lab-vault', 'timeout': 1}
        result = p.setup_dc(api, data)
        self.assertEqual(len(api.writes), 3)
        self.assertEqual(result['application_uid'], 'generated-uid')
        p.setup_dc(api, data)
        self.assertEqual(len(api.writes), 3)
        self.assertFalse(any(body['kind'] == 'VirtualMachine' for _, body in api.writes))

    def test_existing_resource_not_adopted_or_reconfigured(self):
        body = p.resource('Application', 'default', 'test', {'includedNamespaces': []})
        api = FakeAPI({'/apps/test': {'metadata': {}, 'spec': {}}})
        with self.assertRaisesRegex(ValueError, 'unmanaged'):
            api.ensure('/apps', body)
        api.objects['/apps/test'] = p.resource('Application', 'default', 'test', {'includedNamespaces': [{'namespace': 'other'}]})
        with self.assertRaisesRegex(ValueError, 'differs'):
            api.ensure('/apps', body)

    def test_dr_setup_and_promoted_relationship_guard(self):
        objects, plan = fixture()
        dc = p.setup_dc(FakeAPI(objects), {'plan': plan, 'vault_namespace': 'trident-protect', 'source_vault': 'lab-vault', 'timeout': 1})
        objects['/apis/storage.k8s.io/v1/storageclasses/netapp'] = {'provisioner': 'csi.trident.netapp.io'}
        api = FakeAPI(objects, 'https://dr.example')
        data = {'dc_result': dc, 'vault_namespace': 'trident-protect', 'source_vault': 'lab-vault', 'destination_vault': 'lab-vault', 'storage_class': 'netapp', 'timeout': 1}
        result = p.setup_dr(api, data)
        self.assertEqual(result['state'], 'Established')
        self.assertEqual([b['kind'] for _, b in api.writes], ['Namespace', 'AppMirrorRelationship'])
        p.setup_dr(api, data)
        self.assertEqual(len(api.writes), 2)
        path = p.GROUP + '/namespaces/vmdr/appmirrorrelationships/' + dc['mirror_name']
        api.objects[path]['spec']['desiredState'] = 'Promoted'
        with self.assertRaisesRegex(ValueError, 'recovery state'):
            p.setup_dr(api, data)

    def test_wrong_cluster_and_different_vault_rejected(self):
        objects, plan = fixture()
        data = {'plan': plan, 'vault_namespace': 'trident-protect', 'source_vault': 'lab-vault', 'timeout': 1}
        with self.assertRaisesRegex(ValueError, 'does not match'):
            p.setup_dc(FakeAPI(objects, 'https://wrong.example'), data)
        dc = p.setup_dc(FakeAPI(objects), data)
        drdata = dict(data, dc_result=dc, destination_vault='lab-vault', storage_class='netapp')
        with self.assertRaisesRegex(ValueError, 'DC credential'):
            p.setup_dr(FakeAPI(objects), drdata)
        objects[p.GROUP + '/namespaces/trident-protect/appvaults/lab-vault']['spec']['providerConfig'] = {'different': True}
        with self.assertRaisesRegex(ValueError, 'configuration differs'):
            p.setup_dr(FakeAPI(objects, 'https://dr.example'), drdata)


if __name__ == '__main__':
    unittest.main()
