"""Run after multi_namespace.py --separate-control; verify interrupted multi-namespace cutover."""
import copy,json
import run as h
h.OUT=h.ROOT/'work/multi-namespace-control-tests'
h.state,h.artifacts=h.OUT/'state.json',h.OUT/'artifacts.json'
seed=json.loads((h.OUT/'multi-first-shutdown-state.json').read_text())
passed=json.loads((h.OUT/'multi-first-shutdown-artifacts.json').read_text())
target=passed['continuation_state']['target']
seed['fail_baseline']=True;h.write(seed)
h.run('multi-interrupted-after-promotion','recover','dc',passed,ok=False,target=target)
interrupted=h.read();assert interrupted['promotions']==1
# Receipt must reject a replacement disk in the non-control namespace.
bad=copy.deepcopy(interrupted)
second=passed['continuation_state']['namespace_mapping']['payments-dr']
next(x for x in bad['dc'] if x['kind']=='PersistentVolumeClaim' and x['metadata'].get('namespace')==second)['metadata']['uid']='replacement-disk'
h.write(bad)
resuming=dict(passed,continuation_recovered=True)
h.run('multi-reject-replaced-second-disk','recover','dc',resuming,ok=False,target=target)
assert h.read()['promotions']==1
h.write(interrupted)
result=h.cycle('multi-resume-after-promotion',target=target)
assert h.read()['promotions']==1 and h.read()['shutdowns']==1
assert result['continuation_state']['phase']=='complete'
print('PASS multi-namespace receipt retry without duplicate promotion; replaced secondary disk rejected')
