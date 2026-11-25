"""Failure gates replayed from real native-Ansible stage checkpoints."""
import copy, json

def exercise(out, run, cycle, read, write):
    def checkpoint(label):
        return json.loads((out/(label+'-state.json')).read_text()), json.loads((out/(label+'-artifacts.json')).read_text())
    original=read()
    before, handoff=checkpoint('first-check')
    for fault in ['missing-disk','duplicate-disk','missing-volume-snapshot','wrong-archive','restartable-vm']:
        db=copy.deepcopy(before);db['shutdown_fault']=fault;write(db)
        run('reject-'+fault,'shutdown','dr',handoff,ok=False)
        assert read().get('promotions',0)==0
    db=copy.deepcopy(before);db['fail_shutdown']=True;write(db)
    run('reject-failed-shutdown','shutdown','dr',handoff,ok=False)
    assert read().get('promotions',0)==0
    db=copy.deepcopy(before);db['replace_snapshot']=True;write(db)
    run('reject-replaced-snapshot','shutdown','dr',handoff,ok=False)
    assert read().get('promotions',0)==0
    db, handoff=checkpoint('first-release');db['fail_establish']=True;write(db)
    run('reject-failed-transfer','check','dc',handoff,ok=False)
    assert read().get('shutdowns',0)==0
    db,handoff=checkpoint('first-begin')
    target=handoff['continuation_state']['destination_namespace']
    db['dc'].append({'kind':'Namespace','metadata':{'name':target,'uid':'foreign','labels':{}}})
    write(db);run('reject-target-collision','prepare','dc',handoff,ok=False)
    assert read()['dr']==db['dr']
    db,handoff=checkpoint('first-shutdown')
    next(x for x in db['dr'] if x['kind']=='ShutdownSnapshot')['metadata']['uid']='replaced-between-jobs'
    write(db);run('reject-final-snapshot-replacement-on-retry','shutdown','dr',handoff,ok=False)
    assert read().get('promotions',0)==0
    db,handoff=checkpoint('first-shutdown');db['restore_missing_disk']=True;write(db)
    run('reject-restored-missing-disk','recover','dc',handoff,ok=False)
    assert not any(x['kind']=='ConfigMap' and x['metadata'].get('labels',{}).get('app.kubernetes.io/component')=='label-recovery-receipt' for x in read()['dc'])
    db,handoff=checkpoint('first-shutdown');db['fail_baseline']=True;write(db)
    run('interrupted-after-promotion','recover','dc',handoff,ok=False)
    assert read()['promotions']==1
    cycle('resume-after-promotion')
    assert read()['promotions']==1 and read()['shutdowns']==1
    db,handoff=checkpoint('first-reprotect')
    ns=handoff['continuation_state']['destination_namespace']
    vm=next(x for x in db['dc'] if x['kind']=='VirtualMachine' and x['metadata'].get('namespace')==ns)
    vm['metadata']['uid']='unexpected-replacement';write(db)
    # Start with saved complete-operation artifacts; recovery must never adopt the replacement.
    run('reject-replaced-recovered-vm','recover','dc',dict(handoff,continuation_recovered=True),ok=False)
    write(original)
