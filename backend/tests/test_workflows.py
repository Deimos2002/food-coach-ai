import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_ci_contract():
    raw = (ROOT / '.github/workflows/ci.yml').read_text()
    workflow = yaml.safe_load(raw)
    assert set(workflow.get('on', workflow.get(True))) == {'push', 'pull_request'}
    assert workflow['permissions'] == {'contents': 'read'}
    jobs = workflow['jobs']
    assert {'backend', 'eval', 'web', 'image', 'infra'} <= jobs.keys()
    assert jobs['backend']['services']['postgres']['image'] == 'postgres:16'
    assert 'TEST_DATABASE_URL' in jobs['backend']['env']
    for job_name, job in jobs.items():
        if job_name == 'infra':
            assert job['permissions'] == {'contents': 'read', 'id-token': 'write'}
        else:
            assert 'id-token' not in job.get('permissions', {})
        for step in job['steps']:
            if 'uses' in step:
                assert re.fullmatch(r'[^@]+@v\d+', step['uses'])
    assert set(re.findall(r'secrets\.([A-Za-z_]+)', raw)) <= {'GITHUB_TOKEN'}
    runs = '\n'.join(s.get('run', '') for s in jobs['eval']['steps'])
    for split in ('dev', 'test'):
        assert f'--split {split} --source llm --replay' in runs
    for key in ('hard_failures', 'seeded_catch_rate', 'first_try_pass_rate'):
        assert key in runs
    infra = jobs['infra']
    assert "needs.changes.outputs.infra == 'true'" in infra['if']
    assert 'infra/**' in raw
    infra_runs = '\n'.join(s.get('run', '') for s in infra['steps'])
    assert 'pytest -m infra' in infra_runs
    for template in ('bootstrap', 'main'):
        assert f'az bicep build --file infra/{template}.bicep' in infra_runs
    what_if = next(s for s in infra['steps'] if 'what-if' in s.get('run', ''))
    assert what_if['if'] == "github.event_name == 'pull_request'"


def test_ci_uses_node_22():
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    setup = next(s for s in workflow['jobs']['web']['steps']
                 if s.get('uses', '').startswith('actions/setup-node@'))
    assert setup['with']['node-version'] == '22'


def test_what_if_derives_images_after_login():
    raw = (ROOT / '.github/workflows/ci.yml').read_text()
    assert not re.search(r'vars\.[A-Za-z_]*IMAGE_DIGEST', raw)
    steps = yaml.safe_load(raw)['jobs']['infra']['steps']
    login = next(i for i, s in enumerate(steps) if s.get('uses') == 'azure/login@v2')
    index = next(i for i, s in enumerate(steps) if 'what-if' in s.get('run', ''))
    assert index > login
    step = steps[index]
    assert 'az containerapp show' in step['run']
    assert 'az containerapp job show' in step['run']
    assert 'properties.template.containers[0].image' in step['run']
    assert '::notice::' in step['run']
    assert '::error::' in step['run']
    for name in BOOTSTRAP_NAMES:
        assert step['env'][name] == '${{ vars.' + name + ' }}'
        assert name in step['run']


BOOTSTRAP_NAMES = (
    'BOOTSTRAP_ID_API_CLIENT_ID', 'BOOTSTRAP_ID_OPS_CLIENT_ID',
    'BOOTSTRAP_BACKUP_ACCOUNT_NAME', 'BOOTSTRAP_LEDGER_ACCOUNT_NAME',
)


@pytest.mark.parametrize('scenario', ['existing', 'first-deploy', 'azure-error', *BOOTSTRAP_NAMES])
def test_what_if_runtime(scenario, tmp_path):
    bash = Path('C:/Program Files/Git/bin/bash.exe')
    executable = str(bash) if bash.exists() else shutil.which('bash')
    if executable is None:
        pytest.skip('bash is required to execute workflow step')
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    step = next(s for s in workflow['jobs']['infra']['steps']
                if 'what-if' in s.get('run', ''))
    setup = '\n'.join(f"export {name}='{' ' if name == scenario else 'identifier'}'"
                      for name in BOOTSTRAP_NAMES)
    # Stub the CLI boundary: no Azure/network access, and verify the exported images.
    stub = r'''
    az() {
      case "$*" in
        'containerapp list '*)
          if [[ "$scenario" == azure-error ]]; then return 1; fi
          if [[ "$scenario" == first-deploy ]]; then echo 0; else echo 1; fi ;;
        'containerapp show '*) echo 'ghcr.io/example/api@sha256:api' ;;
        'containerapp job show '*) echo 'ghcr.io/example/job@sha256:job' ;;
        'deployment group what-if '*)
          [[ "$API_IMAGE_DIGEST" == 'ghcr.io/example/api@sha256:api' ]] || return 2
          [[ "$JOB_IMAGE_DIGEST" == 'ghcr.io/example/job@sha256:job' ]] || return 2
          echo WHAT_IF_CALLED ;;
        *) return 3 ;;
      esac
    }
    '''
    script = tmp_path / 'what-if.sh'
    script.write_text(f"set -euo pipefail\nscenario='{scenario}'\n" + setup + '\n'
                      + stub + '\n' + step['run'], encoding='utf-8', newline='\n')
    result = subprocess.run([executable, str(script)], capture_output=True,
                            text=True, check=False)
    if scenario == 'existing':
        assert result.returncode == 0, result.stderr
        assert 'WHAT_IF_CALLED' in result.stdout
    elif scenario == 'first-deploy':
        assert result.returncode == 0, result.stderr
        assert '::notice::' in result.stdout
        assert 'WHAT_IF_CALLED' not in result.stdout
    else:
        assert result.returncode != 0
        assert 'WHAT_IF_CALLED' not in result.stdout
        if scenario in BOOTSTRAP_NAMES:
            assert f'::error::Required bootstrap variable {scenario} is empty' in result.stdout


def test_deploy_contract():
    raw = (ROOT / '.github/workflows/deploy.yml').read_text()
    workflow = yaml.safe_load(raw)
    triggers = workflow.get('on', workflow.get(True))
    assert triggers['workflow_run'] == {
        'workflows': ['CI'], 'types': ['completed'], 'branches': ['main'],
    }
    assert triggers['workflow_dispatch']['inputs']['digest']['required'] is True
    assert workflow['concurrency'] == {'group': 'deploy-prod', 'cancel-in-progress': False}
    assert workflow['permissions'] == {
        'contents': 'read', 'id-token': 'write', 'packages': 'write',
    }
    job = workflow['jobs']['deploy']
    assert job['environment'] == 'prod'
    assert "github.event.workflow_run.conclusion == 'success'" in job['if']
    assert "github.event.workflow_run.event == 'push'" in job['if']
    assert 'github.event.workflow_run.head_repository.full_name == github.repository' in job['if']
    assert "github.event.workflow_run.head_branch == 'main'" in job['if']
    assert "inputs.digest != ''" in job['if']
    steps = job['steps']
    ids = [s.get('id') for s in steps]
    ordered = ['registry', 'registry-login', 'build', 'image', 'login', 'model', 'current',
               'preflight', 'deploy', 'migrate', 'update', 'smoke']
    assert [key for key in ids if key is not None] == ordered
    by_id = {s.get('id'): s for s in steps}
    for key in ('registry-login', 'build', 'migrate'):
        assert by_id[key]['if'] == "github.event_name == 'workflow_run'"
    assert by_id['update']['env']['IMAGE'] == '${{ steps.image.outputs.image }}'
    assert by_id['deploy']['env']['JOB_IMAGE_DIGEST'] == by_id['update']['env']['IMAGE']
    assert '--image "$IMAGE"' in by_id['update']['run']
    for key in ('ID_API_CLIENT_ID', 'ID_OPS_CLIENT_ID', 'BACKUP_ACCOUNT_NAME',
                'LEDGER_ACCOUNT_NAME'):
        name = 'BOOTSTRAP_' + key
        assert job['env'][name] == '${{ vars.' + name + ' }}'
        assert name in by_id['preflight']['run']
    for key in ('preflight', 'deploy'):
        assert 'steps.current.outputs.image' in by_id[key]['env']['API_IMAGE_DIGEST']
        assert 'steps.image.outputs.image' in by_id[key]['env']['JOB_IMAGE_DIGEST']
    for name in ('API_IMAGE_DIGEST', 'JOB_IMAGE_DIGEST'):
        assert name in by_id['preflight']['run']
    assert '::error::' in by_id['preflight']['run']
    assert 'exit 1' in by_id['preflight']['run']
    assert 'execution show' in by_id['migrate']['run']
    assert 'Succeeded' in by_id['migrate']['run']
    assert '900' in by_id['migrate']['run']
    assert 'sleep 10' in by_id['migrate']['run']
    assert set(re.findall(r'secrets\.([A-Za-z_]+)', raw)) <= {'GITHUB_TOKEN'}
    for step in steps:
        if 'uses' in step:
            assert re.fullmatch(r'[^@]+@v\d+', step['uses'])


def test_ci_threshold_check_accepts_boundary_metrics(tmp_path):
    # CLI format from eval.main: a path line followed by indented summary JSON.
    summary = {'hard_failures': 0, 'seeded_catch_rate': '1.00',
               'first_try_pass_rate': '0.90'}
    for name in ('dev', 'test'):
        (tmp_path / f'{name}-summary.txt').write_text(
            'wrote run.jsonl\n' + json.dumps(summary, indent=2))
    assert run_ci_threshold_check(tmp_path).returncode == 0


def run_ci_threshold_check(directory):
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    command = next(s['run'] for s in workflow['jobs']['eval']['steps']
                   if 'hard_failures' in s.get('run', ''))
    program = command.split("python - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    return subprocess.run([sys.executable, '-c', program], cwd=directory,
                          capture_output=True, text=True, check=False)


@pytest.mark.parametrize('override', [
    {'hard_failures': 1}, {'seeded_catch_rate': '0.99'},
    {'first_try_pass_rate': '0.89'}, {'seeded_catch_rate': None},
])
def test_ci_threshold_check_rejects_bad_metrics(override, tmp_path):
    summary = {'hard_failures': 0, 'seeded_catch_rate': '1.00',
               'first_try_pass_rate': '0.90', **override}
    for split in ('dev', 'test'):
        (tmp_path / f'{split}-summary.txt').write_text('wrote run.jsonl\n' + json.dumps(summary))
    assert run_ci_threshold_check(tmp_path).returncode != 0


@pytest.mark.parametrize('missing', [None, 'BOOTSTRAP_ID_API_CLIENT_ID',
    'BOOTSTRAP_ID_OPS_CLIENT_ID', 'BOOTSTRAP_BACKUP_ACCOUNT_NAME',
    'BOOTSTRAP_LEDGER_ACCOUNT_NAME', 'API_IMAGE_DIGEST', 'JOB_IMAGE_DIGEST'])
def test_deploy_preflight_rejects_each_empty_value(missing, tmp_path):
    bash = Path('C:/Program Files/Git/bin/bash.exe')
    executable = str(bash) if bash.exists() else shutil.which('bash')
    if executable is None:
        pytest.skip('bash is required to execute workflow preflight')
    workflow = yaml.safe_load((ROOT / '.github/workflows/deploy.yml').read_text())
    step = next(s for s in workflow['jobs']['deploy']['steps'] if s.get('id') == 'preflight')
    names = ['BOOTSTRAP_ID_API_CLIENT_ID', 'BOOTSTRAP_ID_OPS_CLIENT_ID',
             'BOOTSTRAP_BACKUP_ACCOUNT_NAME', 'BOOTSTRAP_LEDGER_ACCOUNT_NAME',
             'API_IMAGE_DIGEST', 'JOB_IMAGE_DIGEST']
    setup = '\n'.join(f"{name}='{' ' if name == missing else 'test-value'}'" for name in names)
    script = tmp_path / 'preflight.sh'
    script.write_text(setup + '\n' + step['run'], encoding='utf-8', newline='\n')
    result = subprocess.run([executable, str(script)], capture_output=True,
                            text=True, check=False)
    if missing is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 1
        assert f'Required deployment variable {missing} is empty' in result.stdout


def test_deploy_build_passes_web_config_and_sha():
    steps = yaml.safe_load((ROOT / '.github/workflows/deploy.yml').read_text())
    build = next(s for job in steps['jobs'].values() for s in job['steps']
                 if s.get('uses', '').startswith('docker/build-push-action@'))
    args = dict(line.split('=', 1) for line in build['with']['build-args'].splitlines())
    assert args == {
        'VITE_ENTRA_CLIENT_ID': '${{ vars.VITE_ENTRA_CLIENT_ID }}',
        'VITE_ENTRA_TENANT_ID': '${{ vars.VITE_ENTRA_TENANT_ID }}',
        'VITE_API_SCOPE': '${{ vars.VITE_API_SCOPE }}',
        'GIT_SHA': '${{ github.event.workflow_run.head_sha || github.sha }}',
    }


def test_deploy_model_gate_uses_aoai_resource_group():
    steps = yaml.safe_load((ROOT / '.github/workflows/deploy.yml').read_text())
    gate = next(s for job in steps['jobs'].values() for s in job['steps']
                if s.get('id') == 'model')
    assert '--resource-group rg-foodcoach ' in gate['run']
