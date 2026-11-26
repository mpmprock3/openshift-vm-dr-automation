"""Pure identity/mapping checks complement the native Ansible workflow tests."""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'filter_plugins'))
from ansible.errors import AnsibleFilterError
import recovery_scope as scope

class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.plan=dict(source_namespace='dr-a',destination_namespace='prod-a',application='app',label_key='group',
            namespace_mapping={'dr-a':'prod-a','dr-b':'prod-b'},pvcs=['disk','disk'],
            pvc_refs=[dict(namespace='dr-a',name='disk'),dict(namespace='dr-b',name='disk')],
            vms=[dict(namespace='dr-a',name='vm'),dict(namespace='dr-b',name='vm')])
        self.legacy=dict(namespace_mapping={'old-a':'dr-a','old-b':'dr-b'},mirror_namespace='dr-a',mirror_uid='uid',
            selected_vms=[dict(vm='old-a/vm',destination_namespace='dr-a'),dict(vm='old-b/vm',destination_namespace='dr-b')])
    def test_same_names_remain_distinct(self):
        self.assertEqual(scope.member_keys(self.plan,'pvcs','destination'),['prod-a/disk','prod-b/disk'])
        self.assertEqual(scope.member_keys(self.plan,'vms','destination'),['prod-a/vm','prod-b/vm'])
    def test_duplicate_selection_is_rejected(self):
        self.assertFalse(scope.unique_refs([self.plan['vms'][0]]*2))
        self.assertTrue(scope.unique_refs(self.plan['vms']))
    def test_scopes_cover_both_namespaces(self):
        self.assertEqual([x['namespace'] for x in scope.app_scope(self.plan,'destination')],['prod-a','prod-b'])
    def test_many_to_one_new_mapping_is_rejected(self):
        self.plan['namespace_mapping']['dr-b']='prod-a'
        with self.assertRaises(AnsibleFilterError):scope.mapping(self.plan)
    def test_infrastructure_mapping_is_rejected(self):
        self.plan['namespace_mapping']['dr-b']='trident-protect'
        with self.assertRaises(AnsibleFilterError):scope.mapping(self.plan)
    def test_vm_outside_mapping_is_rejected(self):
        with self.assertRaises(AnsibleFilterError):scope.vm_ns(self.plan,dict(name='vm',namespace='other'))
    def test_control_namespace_must_map_correctly(self):
        self.plan['destination_namespace']='wrong'
        with self.assertRaises(AnsibleFilterError):scope.mapping(self.plan)
    def test_return_mapping_is_stable_and_distinct(self):
        m=scope.return_mapping(self.legacy)
        self.assertEqual(len(set(m.values())),2)
        self.assertEqual(m,scope.return_mapping(copy.deepcopy(self.legacy)))
    def test_separate_control_namespace_is_included(self):
        self.legacy['mirror_namespace']='dr-control'
        self.assertEqual(len(scope.return_mapping(self.legacy)),3)
    def test_repeat_cycle_reuses_namespace_mapping(self):
        first=scope.return_mapping(self.legacy)
        self.legacy.update(namespace_mapping={v:k for k,v in first.items()},continuation_application='app')
        for m in self.legacy['selected_vms']:m['vm']=first[m['destination_namespace']]+'/vm'
        self.assertEqual(scope.return_mapping(self.legacy),first)
    def test_wrong_catalog_mapping_is_rejected(self):
        self.legacy['selected_vms'][1]['destination_namespace']='dr-a'
        with self.assertRaises(AnsibleFilterError):scope.return_mapping(self.legacy)
    def test_legacy_single_namespace_plan_still_works(self):
        p=dict(source_namespace='dr-a',destination_namespace='prod-a',pvcs=['disk'],vms=[dict(name='vm')])
        self.assertEqual(scope.member_keys(p,'pvcs','destination'),['prod-a/disk'])
        self.assertEqual(scope.member_keys(p,'vms','destination'),['prod-a/vm'])

if __name__=='__main__':unittest.main()
