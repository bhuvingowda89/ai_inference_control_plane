#!/usr/bin/env python3
"""Finalize final-v1 only after the review owner accepts the scientific proposal."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from saferoll.artifacts import write_json
from saferoll.campaign.final_freeze import DIRECTORY, canonical_freeze_hash, preflight


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--human-reviewed',action='store_true',required=True)
    parser.add_argument('--expected-plan-sha256',required=True)
    args=parser.parse_args()
    proposal=preflight(require_frozen=False)
    if not proposal['valid'] or proposal['plan_sha256']!=args.expected_plan_sha256:
        raise SystemExit('proposal preflight or reviewed plan hash mismatch')
    path=DIRECTORY/'campaign_freeze.json'
    freeze=json.loads(path.read_text())
    if freeze['status']!='PROPOSED': raise SystemExit('final-v1 has already been frozen or invalidated')
    freeze['status']='FROZEN'
    freeze['freeze_sha256']=canonical_freeze_hash(freeze,DIRECTORY)
    write_json(path,freeze)
    review=DIRECTORY/'campaign_freeze.md'
    review.write_text(review.read_text().replace('Status: **PROPOSED**','Status: **FROZEN**')+
                      f"\nFreeze SHA-256: `{freeze['freeze_sha256']}`.\n")
    result=preflight(require_frozen=True)
    write_json(DIRECTORY/'preflight.json',result)
    if not result['valid']: raise SystemExit('post-freeze preflight failed: '+', '.join(result['issues']))
    print(json.dumps({'status':'FROZEN','freeze_sha256':freeze['freeze_sha256'],'plan_sha256':result['plan_sha256']},sort_keys=True))


if __name__=='__main__': main()
