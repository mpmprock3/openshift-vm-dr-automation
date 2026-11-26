"""Test-only Kubernetes transitions, not a storage or guest-data simulator."""
import copy, datetime, fcntl, json, os, uuid
from ansible.module_utils.basic import AnsibleModule
import jsonpatch


def run(action):
    specs = {k: {'type': t} for k,t in {
        'kubeconfig':'str','host':'str','validate_certs':'bool','api_version':'str',
        'kind':'str','namespace':'str','name':'str','state':'str','definition':'dict',
        'label_selectors':'list','patch':'list','delete_options':'dict',
        'wait':'bool','wait_timeout':'int'}.items()}
    m=AnsibleModule(argument_spec=specs)
    p=m.params
    site=(p['host'] or p['kubeconfig'] or os.environ.get('K8S_AUTH_HOST','')).split('//')[-1]
    if site not in ['dc','dr','control']: m.fail_json(msg='Unknown fixture site')
    path=os.environ['VMDR_PLAN_TEST_STATE']
    with open(path,'r+') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        db=json.load(f)
        if db.get('source_unavailable') == site: m.fail_json(msg='Simulated unavailable source')
        rows=db[site]
        obj=p['definition']
        kind=p['kind'] or (obj or {}).get('kind')
        name=p['name'] or (obj or {}).get('metadata',{}).get('name')
        ns=p['namespace'] or (obj or {}).get('metadata',{}).get('namespace')
        matches=[x for x in rows if x['kind']==kind and (not name or x['metadata']['name']==name) and (not ns or x['metadata'].get('namespace')==ns)]
        for selector in p['label_selectors'] or []:
            k,v=selector.split('=',1)
            matches=[x for x in matches if x['metadata'].get('labels',{}).get(k)==v]
        if action=='info':
            if kind=='ShutdownSnapshot' and matches and db.pop('replace_snapshot',False):
                matches[0]['metadata']['uid']=str(uuid.uuid4())
                f.seek(0);json.dump(db,f);f.truncate();f.flush()
            m.exit_json(changed=False,resources=matches)
        def save():
            f.seek(0); json.dump(db,f); f.truncate(); f.flush()
        def metadata(o):
            o['metadata'].setdefault('uid',str(uuid.uuid4()))
            o['metadata'].setdefault('creationTimestamp',datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
            o['metadata']['resourceVersion']=str(int(o['metadata'].get('resourceVersion','0'))+1)
        def merge(a,b):
            for k,v in b.items():
                if isinstance(v,dict) and isinstance(a.get(k),dict): merge(a[k],v)
                else: a[k]=copy.deepcopy(v)
        if action=='patch':
            if len(matches)!=1: m.fail_json(msg='Patch target absent')
            old=matches[0]
            try: new=jsonpatch.apply_patch(old,p['patch'])
            except Exception as e: m.fail_json(msg=str(e))
            # Emulate a competing writer after the lock read, before the CAS.
            if db.pop('race_lock',False) and kind=='ConfigMap' and site=='control':
                old['data']['owner']='other-job';metadata(old);save();m.fail_json(msg='CAS conflict')
            rows[rows.index(old)]=new;obj=new;metadata(obj)
            if kind=='VirtualMachine' and obj['spec'].get('runStrategy')=='Halted':
                rows[:]=[x for x in rows if not (x['kind']=='VirtualMachineInstance' and x['metadata'].get('namespace')==ns and x['metadata']['name']==name)]
                obj['status']['ready']=False
            if kind=='AppMirrorRelationship' and obj['spec']['desiredState']=='Promoted':
                source='dr' if site=='dc' else 'dc'
                for pair in obj['spec']['namespaceMapping']:
                    sns,dns=pair['source'],pair['destination']
                    source_vms=[x for x in db[source] if x['kind']=='VirtualMachine' and x['metadata'].get('namespace')==sns]
                    if not source_vms:
                        source_vms=[x for x in db.get('saved_vms',{}).get(source,[]) if x['metadata'].get('namespace')==sns]
                    rows[:]=[x for x in rows if not (x['kind'] in ['VirtualMachine','VirtualMachineInstance'] and x['metadata'].get('namespace')==dns)]
                    for source_vm in source_vms:
                        restored=copy.deepcopy(source_vm);restored['metadata'].update(namespace=dns,uid=str(uuid.uuid4()))
                        restored['status']={'ready':True};metadata(restored)
                        if db.get('restore_missing_disk'): restored['spec']['template']['spec']['volumes']=[]
                        rows.append(restored)
                        rows.append({'kind':'VirtualMachineInstance','metadata':{'name':restored['metadata']['name'],'namespace':dns,'uid':str(uuid.uuid4())},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}]}})
                obj['status']['state']='Promoted';obj['status']['conditions'].append({'type':'Promoted','status':'True'})
                db['promotions']=db.get('promotions',0)+1
        elif p['state']=='absent':
            for old in matches:
                for k,v in (p['delete_options'] or {}).get('preconditions',{}).items():
                    if old['metadata'][k]!=v: m.fail_json(msg='Delete precondition failed')
                rows.remove(old)
        else:
            if db.get('fail_baseline') and kind=='Snapshot':
                db['fail_baseline']=False;save();m.fail_json(msg='Injected failure after mirror release')
            if matches:
                old=matches[0];merge(old,obj);obj=old
            else:
                obj=copy.deepcopy(obj);rows.append(obj)
            metadata(obj)
            if kind=='Application':obj['status']={'state':'Ready','conditions':[{'type':'Ready','status':'True'}]}
            if kind in ['Snapshot','ShutdownSnapshot'] and 'status' not in obj:
                app=next(x for x in rows if x['kind']=='Application' and x['metadata']['name']==obj['spec']['applicationRef'] and x['metadata'].get('namespace')==ns)
                scopes={x['namespace']:x.get('labelSelector',{}).get('matchLabels',{}) for x in app['spec']['includedNamespaces']}
                def selected(x):
                    n=x['metadata'].get('namespace')
                    return n in scopes and all(x['metadata'].get('labels',{}).get(k)==v for k,v in scopes[n].items())
                disks=[x for x in rows if x['kind']=='PersistentVolumeClaim' and selected(x)]
                obj['status']={'state':'Completed','appArchivePath':app['metadata']['name']+'_'+app['metadata']['uid']+'/snapshots/'+obj['metadata']['name'], 'pvcs':[x['metadata']['uid'] for x in disks], 'volumeSnapshots':[{'name':x['metadata']['name']} for x in disks]}
                if kind=='ShutdownSnapshot':
                    if db.get('fail_shutdown'): obj['status']['state']='Failed'
                    else:
                        selected_vms=[x for x in rows if x['kind']=='VirtualMachine' and selected(x)]
                        db.setdefault('saved_vms',{})[site]=copy.deepcopy(selected_vms)
                        db.setdefault('saved_vm',{})[site]=copy.deepcopy(selected_vms[0])
                        selected_keys={(x['metadata']['namespace'],x['metadata']['name']) for x in selected_vms}
                        rows[:]=[x for x in rows if not (x['kind'] in ['VirtualMachine','VirtualMachineInstance'] and (x['metadata'].get('namespace'),x['metadata']['name']) in selected_keys)]
                        db['shutdowns']=db.get('shutdowns',0)+1
                        fault = db.get('shutdown_fault')
                        if fault == 'missing-disk': obj['status']['pvcs'] = []
                        if fault == 'missing-second-disk': obj['status']['pvcs'] = obj['status']['pvcs'][:-1]
                        if fault == 'duplicate-disk': obj['status']['pvcs'] *= 2
                        if fault == 'missing-volume-snapshot': obj['status']['volumeSnapshots'] = []
                        if fault == 'wrong-archive': obj['status']['appArchivePath'] = app['metadata']['name']+'_'+app['metadata']['uid']+'/backups/not-a-snapshot'
                        if fault == 'restartable-vm': rows.append(copy.deepcopy(db['saved_vm'][site]))
            if kind=='AppMirrorRelationship':
                source='dr' if site=='dc' else 'dc'
                mapping={x['source']:x['destination'] for x in obj['spec']['namespaceMapping']}
                dns=ns
                srcapp=next(x for x in db[source] if x['kind']=='Application' and x['metadata']['uid']==obj['spec']['sourceApplicationUID'])
                expected=copy.deepcopy(srcapp['spec'])
                for included in expected['includedNamespaces']:included['namespace']=mapping[included['namespace']]
                expected['includedNamespaces'].sort(key=lambda x:x['namespace'])
                if not any(x for x in rows if x['kind']=='Application' and x['metadata']['name']==srcapp['metadata']['name'] and x['metadata'].get('namespace')==dns):
                    app=copy.deepcopy(srcapp);app['metadata'].update(namespace=dns,uid=str(uuid.uuid4()));app['spec']=expected;metadata(app);rows.append(app)
                targetapp=next(x for x in rows if x['kind']=='Application' and x['metadata']['name']==srcapp['metadata']['name'] and x['metadata'].get('namespace')==dns)
                if targetapp['spec']!=expected: m.fail_json(msg='Existing app definition differs; refuse reuse')
                for sns,dns in mapping.items():
                    rows[:]=[x for x in rows if not (x['kind'] in ['VirtualMachine','VirtualMachineInstance'] and x['metadata'].get('namespace')==dns)]
                    for disk in [x for x in db[source] if x['kind']=='PersistentVolumeClaim' and x['metadata'].get('namespace')==sns]:
                        if not any(x for x in rows if x['kind']=='PersistentVolumeClaim' and x['metadata'].get('namespace')==dns and x['metadata']['name']==disk['metadata']['name']):
                            disk=copy.deepcopy(disk);disk['metadata'].update(namespace=dns,uid=str(uuid.uuid4()));metadata(disk);rows.append(disk)
                obj['status']={'state':'Established','destinationApplicationRef':srcapp['metadata']['name'],'lastTransferredSnapshot':{'name':'baseline','completionTimestamp':datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')},'conditions':[{'type':'LastTransferSucceeded','status':'True'}]}
        if db.get('fail_establish') and kind=='AppMirrorRelationship' and obj['spec']['desiredState']=='Established':
            obj['status']['state']='Establishing';obj['status']['error']='Injected transfer failure'
        db.setdefault('writes',[]).append([site,action,kind,name]);save()
        m.exit_json(changed=True,result=obj)
