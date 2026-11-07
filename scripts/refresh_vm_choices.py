"""Populate two existing AAP survey questions from live DC VM inventory."""
import copy
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def client(host, token, ca=None, verify=True):
    parsed = urllib.parse.urlsplit(host)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/') or not token:
        raise ValueError('Credential requires an HTTPS base URL without an API path and a token.')
    context = ssl.create_default_context(cafile=ca or None)
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    def request(path, payload=None):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Unexpected API path.')
        req = urllib.request.Request(host.rstrip('/') + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'Content-Type': 'application/json'})
        try:
            with opener.open(req, timeout=60) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            raise ValueError('API request failed for ' + path.split('?')[0] + ': HTTP ' + str(exc.code) + '. Check token, permissions and API URL.') from None
        except urllib.error.URLError:
            raise ValueError('Connection/TLS failure. Check API reachability and credential CA configuration.') from None
    return request


def update_choices(survey, vm_names, namespaces):
    updated = copy.deepcopy(survey)
    questions = updated.get('spec', [])
    for variable, choices in [('source_vms', vm_names), ('source_namespaces', namespaces)]:
        matches = [q for q in questions if q.get('variable') == variable]
        if len(matches) != 1 or matches[0].get('type') != 'multiselect':
            raise ValueError(variable + ' must exist exactly once as a multiple-select survey question.')
        question = matches[0]
        question['choices'] = '\n'.join(choices)
        # Deliberately clear defaults so a newly discovered VM is never selected implicitly.
        question['default'] = ''
    return updated


def main():
    try:
        config = json.load(sys.stdin)
        kube = client(os.environ.get('K8S_AUTH_HOST', ''), os.environ.get('K8S_AUTH_API_KEY', ''),
                      os.environ.get('K8S_AUTH_SSL_CA_CERT'),
                      os.environ.get('K8S_AUTH_VERIFY_SSL', 'true').lower() not in ('false', '0', 'no'))
        aap = client(os.environ.get('AAP_SURVEY_HOST', ''), os.environ.get('AAP_SURVEY_TOKEN', ''),
                     os.environ.get('AAP_SURVEY_CA'))
        # No phase filter: stopped VMs are also legitimate DR protection candidates.
        vms = kube('/apis/kubevirt.io/v1/virtualmachines')['items']
        vm_names = sorted({v['metadata']['namespace'] + '/' + v['metadata']['name'] for v in vms})
        namespaces = sorted({v['metadata']['namespace'] for v in vms})
        if not vm_names:
            raise ValueError('DC returned zero VMs. Survey was not changed; do not rely on its previous choices.')
        query = urllib.parse.urlencode({'name': config.get('workflow_name', 'Enable DR Protection')})
        matches = aap('/api/controller/v2/workflow_job_templates/?' + query)
        if matches.get('count') != 1:
            raise ValueError('Expected exactly one visible workflow with this name. Check workflow name and token permissions.')
        workflow_id = int(matches['results'][0]['id'])
        path = '/api/controller/v2/workflow_job_templates/' + str(workflow_id) + '/survey_spec/'
        original = aap(path)
        updated = update_choices(original, vm_names, namespaces)
        changed = updated != original
        if changed:
            # Avoid overwriting a survey edited during this discovery run.
            if aap(path) != original:
                raise ValueError('Survey changed during discovery. Retry after completing survey edits.')
            aap(path, updated)
        verified = aap(path)
        for variable, expected in [('source_vms', vm_names), ('source_namespaces', namespaces)]:
            field = next(q for q in verified['spec'] if q['variable'] == variable)
            actual = field.get('choices', [])
            actual = actual.splitlines() if isinstance(actual, str) else actual
            if sorted(actual) != expected:
                raise ValueError('Survey update could not be verified for ' + variable)
        print(json.dumps({'changed': changed, 'workflow_id': workflow_id, 'vm_count': len(vm_names),
                          'source_vms': vm_names, 'source_namespaces': namespaces,
                          'result': 'Choices verified. Open a NEW Enable DR Protection launch form. Defaults are cleared; choose the resources to protect.',
                          'scope': 'All discovered VMs, including stopped VMs; namespace choices contain only namespaces with VMs.'}, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        print(json.dumps({'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
