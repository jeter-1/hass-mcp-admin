"""Copies only repository inputs; never mutates the working tree under test."""
import json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path.cwd(); OUT=ROOT/'.artifacts/mutation'; OUT.mkdir(exist_ok=True)
COPY=OUT/'copy';COPY.mkdir(exist_ok=True)
paths=set(subprocess.check_output(['git','ls-files','-z']).decode().split('\0'))|set(subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z']).decode().split('\0'))
for name in sorted(paths- {''}):
 p=ROOT/name
 if p.is_file():
  target=COPY/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
mutants=[
 ('never-add result injection','scripts/core_release_evidence.py','    return selected','    return selected + [{"capability_id": "core.synthetic_unapproved"}]','Withholding.test_never_add_from_larger_passed_set'),
 ('O3 false equality','scripts/core_release_evidence.py','"inherited": not differing','"inherited": True','O3.test_differing_names_file_and_refuses'),
 ('O3 missing ignored','scripts/core_release_evidence.py','"differing_files": differing','"differing_files": []','O3.test_missing_file_refuses_even_both_missing'),
 ('tag identity bypass','scripts/core_release_evidence.py','source.get("tag_commit") != source["commit"]','False','O3.test_tag_sha_mismatch'),
 ('proof admission bypass','scripts/core_release_evidence.py','verify_proof(value["proof"], value["predecessor"], provenance["version"], provenance["source_commit"])','None','ProofAdmission.test_binding_without_proof_refuses'),
 ('proof byte equality bypass','scripts/core_release_evidence.py','r.get("predecessor_sha256") != r.get("release_sha256")','False','ProofAdmission.test_forged_equal_flag_refuses'),
 ('proof run bypass','scripts/core_release_evidence.py','value.get("run") != expected','False','ProofAdmission.test_wrong_run_attempt_head_refuses'),
 ('withholding bypass','scripts/core_release_evidence.py','if results.get(name) is True for p in values','if True for p in values','Withholding.test_general_nineteen_stand_or_fall'),
 ('truthy success accepted','scripts/core_release_evidence.py','if results.get(name) is True for p in values','if results.get(name) for p in values','Withholding.test_no_missing_or_truthy_pass'),
 ('withholding membership bypass','scripts/core_release_evidence.py','for p in predecessor if p["capability_id"] in allowed','for p in predecessor','Withholding.test_each_group_independently_withheld'),
 ('candidate branch target','scripts/core_release_evidence.py','"ref": "registry-candidates/" + version','"ref": "registry/" + version','HandoffExecution.test_new_branch_exact_push_and_dispatch'),
 ('dispatch target','scripts/core_evidence_handoff.py','WORKFLOW = "core-release-registry-publish.yml"','WORKFLOW = "publish-rc-image.yml"','HandoffExecution.test_new_branch_exact_push_and_dispatch'),
 ('precutover bypass','scripts/core_release_evidence.py','if dry_run or not registry_exists:','if dry_run:','Handoff.test_pre_cutover_and_dry_run_never_handoff'),
 ('existing content overwrite','scripts/core_evidence_handoff.py','if run(["git", "show", "FETCH_HEAD:" + name], temp) != raw:','if False:','HandoffExecution.test_existing_different_no_push_or_dispatch'),
]
results=[]
for i,(name,file,before,after,test) in enumerate(mutants,1):
 path=COPY/file; original=path.read_text();assert original.count(before)==1,(name,original.count(before))
 path.write_text(original.replace(before,after))
 # Avoid same-size/same-second pyc cache masking the actual mutant.
 for pycache in COPY.rglob('__pycache__'):shutil.rmtree(pycache)
 proc=subprocess.run([sys.executable,'-B','-m','unittest','tests.test_core_release_evidence.'+test],cwd=COPY,capture_output=True,timeout=60)
 raw=proc.stdout+proc.stderr;(OUT/f'{i:02d}.log').write_bytes(raw)
 classification='assertion' if proc.returncode and b'FAIL:' in raw and b'AssertionError' in raw else ('invalid' if proc.returncode else 'survived')
 results.append({'id':i,'name':name,'file':file,'test':test,'classification':classification,'exit_code':proc.returncode})
 path.write_text(original)
 print(i,name,classification,flush=True)
(OUT/'results.json').write_text(json.dumps(results,indent=2)+'\n')
if any(r['classification']=='survived' for r in results):sys.exit(1)
