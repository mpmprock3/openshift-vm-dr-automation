"""Seed a disposable API fixture. No cluster or network access."""
import json, hashlib, sys, copy
H=lambda x: hashlib.sha256(x.encode()).hexdigest()
api='protect.trident.netapp.io/v1'; owner={'app.kubernetes.io/managed-by':'aap-dr-poc'}
def obj(kind,name,ns,uid,spec=None,status=None,av=api,labels=None):
    return {'apiVersion':av,'kind':kind,'metadata':{'name':name,'namespace':ns,'uid':uid,'resourceVersion':'1','labels':labels or owner},'spec':spec or {},'status':status or {}}
member={'vm':'my-vm/centos-vm','uid':'dc-vm','destination_namespace':'vmdr','disks':[{'pvc':'centos-vm','storage_class':'storage-class-iscsi'}]}
plan={'schema_version':1,'source_api':'https://dc','destination_api':'https://dr','protection_plan':'demo','application_namespace':'my-vm','application_name':'app','application_uid':'dc-app','mirror_namespace':'vmdr','mirror_name':'app-mirror','mirror_uid':'original-amr','schedule':'old-schedule','selected_vms':[member],'namespace_mapping':{'my-vm':'vmdr'}}
filt={'resourceSelectionCriteria':'Include','resourceMatchers':[{'kind':'VirtualMachine','names':['centos-vm'],'namespaces':['vmdr']},{'kind':'PersistentVolumeClaim','names':['centos-vm'],'namespaces':['vmdr']}]}
orig={'sourceApplicationName':'app','sourceApplicationUID':'dc-app','sourceAppVaultRef':'lab-vault','destinationAppVaultRef':'lab-vault','desiredState':'Promoted','namespaceMapping':[{'source':'my-vm','destination':'vmdr'}],'storageClassName':'storage-class-iscsi','recurrenceRule':'DTSTART:20260101T000000Z\nRRULE:FREQ=MINUTELY;INTERVAL=5'}
vaultspec={'providerType':'generic-s3','providerConfig':{'bucket':'same'}}
rplan={'assessment':{'plan':plan,'dr_application_name':'app','dr_application_uid':'dr-app','original_amr_uid':'original-amr','original_amr_spec':orig},'source_api':'https://dr','target':'vmdr/app-mirror','request_id':'auto-one','source_filter':filt,'source_vault_fingerprint':H(json.dumps(vaultspec,sort_keys=True)),'baseline':'baseline','reverse_name':'reverse-test'}
cm=obj('ConfigMap','reverse-'+H(rplan['target'])[:24],'vmdr','reverse-record',av='v1',labels={**owner,'app.kubernetes.io/component':'reverse-replication-plan'});cm['data']={'plan.json':json.dumps(rplan)}
cat=obj('ConfigMap','app-mirror-plan','vmdr','catalog',av='v1',labels={**owner,'app.kubernetes.io/component':'recovery-plan'});cat['data']={'plan.json':json.dumps(plan)}
clusters=[{'groupVersionKind':{'group':'snapshot.storage.k8s.io','kind':'VolumeSnapshotClass','version':'v1'},'labelSelector':{}},{'groupVersionKind':{'group':'storage.k8s.io','kind':'StorageClass','version':'v1'},'labelSelector':{}}]
app=lambda ns,uid:obj('Application','app',ns,uid,{'includedNamespaces':[{'namespace':ns,'labelSelector':{}}],'includedClusterScopedResources':clusters,'resourceFilter':filt},status={'conditions':[{'type':'Ready','status':'True'}]})
vm=lambda ns,uid,strategy:obj('VirtualMachine','centos-vm',ns,uid,{'runStrategy':strategy,'template':{'spec':{'volumes':[{'name':'rootdisk','persistentVolumeClaim':{'claimName':'centos-vm'}}]}}},av='kubevirt.io/v1')
pvc=lambda ns,uid:obj('PersistentVolumeClaim','centos-vm',ns,uid,{'volumeName':'pvc-'+uid,'storageClassName':'storage-class-iscsi'},status={'phase':'Bound'},av='v1')
vault=obj('AppVault','lab-vault','trident-protect','vault',vaultspec,status={'state':'Available'})
snap=obj('Snapshot','baseline','vmdr','baseline-uid',{'applicationRef':'app'},status={'state':'Completed','pvcs':['dr-pvc'],'volumeSnapshots':[{'namespace':'vmdr','name':'baseline-vs'}]});snap['metadata']['ownerReferences']=[{'uid':'dr-app'}]
vs=obj('VolumeSnapshot','baseline-vs','vmdr','vs',{'source':{'persistentVolumeClaimName':'centos-vm'}},status={'readyToUse':True},av='snapshot.storage.k8s.io/v1',labels={'protect.trident.netapp.io/created-by-snapshot-uid':'baseline-uid'})
vmi=obj('VirtualMachineInstance','centos-vm','vmdr','dr-vmi',status={'phase':'Running','conditions':[{'type':'Ready','status':'True'}]},av='kubevirt.io/v1')
sc=obj('StorageClass','storage-class-iscsi',None,'sc',av='storage.k8s.io/v1');sc['provisioner']='csi.trident.netapp.io'
vsc=obj('VolumeSnapshotClass','csi-trident-vsc',None,'vsc',av='snapshot.storage.k8s.io/v1');vsc['driver']='csi.trident.netapp.io'
pv=obj('PersistentVolume','pvc-dc-pvc',None,'pv',{'csi':{'driver':'csi.trident.netapp.io'}},av='v1')
node=obj('Node','worker',None,'node',status={'conditions':[{'type':'Ready','status':'True'}],'allocatable':{'devices.kubevirt.io/kvm':'1000'}},av='v1')
rspec={**orig,'desiredState':'Established','sourceApplicationUID':'dr-app','namespaceMapping':[{'source':'vmdr','destination':'my-vm'}]}
amr=obj('AppMirrorRelationship','reverse-test','my-vm','reverse-amr',rspec,status={'state':'Established','lastTransfer':{'completionTimestamp':'2026-11-20T00:00:00Z'},'lastTransferredSnapshot':{'name':'baseline'},'conditions':[{'type':'LastTransferSucceeded','status':'True'}]})
unrelated=vm('my-vm','unrelated','Always');unrelated['metadata']['name']='test-vm';unrelated['spec']['template']['spec']['volumes'][0]['persistentVolumeClaim']['claimName']='test-vm'
if '--shared-disk' in sys.argv: unrelated['spec']['template']['spec']['volumes'][0]['persistentVolumeClaim']['claimName']='centos-vm'
s={'dc':[app('my-vm','dc-app'),vm('my-vm','dc-vm','Halted'),pvc('my-vm','dc-pvc'),vault,sc,vsc,pv,node,amr,unrelated,obj('Schedule','old-schedule','my-vm','old-schedule',{'applicationRef':'app'})], 'dr':[cm,cat,app('vmdr','dr-app'),vm('vmdr','dr-vm','Always'),pvc('vmdr','dr-pvc'),vault,snap,vs,vmi], 'unrelated':copy.deepcopy(unrelated),'fail_shutdown': '--fail-shutdown' in sys.argv}
json.dump(s,open(sys.argv[1],'w'))
