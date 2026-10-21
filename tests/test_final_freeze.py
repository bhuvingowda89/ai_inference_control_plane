"""Scientific-input and immutable-freeze contract tests."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from saferoll.artifacts import canonical_json
from saferoll.campaign.final_freeze import DIRECTORY, canonical_freeze_hash, preflight
from saferoll.campaign.plan import derived_seed
from saferoll.research.configuration import POLICY_NAMES, POLICY_VERSION


def test_canonical_freeze_hash_stable_and_mutation_detected(tmp_path: Path) -> None:
    for name in ('campaign_plan.json','seed_design.json','history_index.json','real_campaign_spec.json'):
        (tmp_path/name).write_text(canonical_json({'name':name})+'\n')
    spec={'status':'FROZEN','thresholds':{'max_error_rate':0.05},'policies':list(POLICY_NAMES)}
    first=canonical_freeze_hash(spec,tmp_path)
    assert len(first)==64 and first==canonical_freeze_hash(deepcopy(spec),tmp_path)
    spec['freeze_sha256']=first
    assert canonical_freeze_hash(spec,tmp_path)==first
    spec['thresholds']['max_error_rate']=0.06
    assert canonical_freeze_hash(spec,tmp_path)!=first
    spec['thresholds']['max_error_rate']=0.05
    (tmp_path/'seed_design.json').write_text(canonical_json({'name':'changed'})+'\n')
    assert canonical_freeze_hash(spec,tmp_path)!=first
    with pytest.raises(ValueError,match='reviewed FROZEN'):
        canonical_freeze_hash({'status':'PROPOSED'},tmp_path)


def test_final_plan_seed_policy_and_history_contract() -> None:
    freeze=json.loads((DIRECTORY/'campaign_freeze.json').read_text())
    plan=json.loads((DIRECTORY/'campaign_plan.json').read_text())
    history=json.loads((DIRECTORY/'history_index.json').read_text())
    assert freeze['status'] in ('PROPOSED','FROZEN')
    assert len(plan['rows'])==freeze['planned_mock_runs']
    assert set(plan['policy_order'])==set(POLICY_NAMES)
    assert all(row['policy_version']==POLICY_VERSION for row in plan['rows'])
    assert all(row['seed']==derived_seed(7300,row['scenario'],'paired',row['repetition'],{'severity':row['severity']})
               for row in plan['rows'])
    assert all(row['seed']==next(x['seed'] for x in plan['rows'] if x['pairing_key']==row['pairing_key'])
               for row in plan['rows'])
    assert all(str(row['seed']) in history['entries'] for row in plan['rows'] if row['scenario']=='historical_attribution')
    assert all(x['validated']=='VALIDATED' and not ({'expected_decision','expected_policy_decision'} & set(x))
               for x in freeze['scenarios'])


def test_dirty_tree_rejected_for_actual_freeze() -> None:
    result=preflight(check_clean=True)
    if result['git_dirty']:
        assert not result['valid'] and 'DIRTY_TREE' in result['issues']
    else:
        assert 'DIRTY_TREE' not in result['issues']
    assert not preflight(check_clean=False,require_frozen=True)['valid'] if json.loads((DIRECTORY/'campaign_freeze.json').read_text())['status']=='PROPOSED' else True
