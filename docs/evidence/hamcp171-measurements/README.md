# HAMCP-171 offline measurements

Synthetic store only, not production. `measure.py` builds the store through
the `F3ConfigurationActivationTests` fixture: 435 configuration plans (240
applied to `succeeded_verified`), each with 7 actions, giving a mean plan
file of 37,024 bytes and 16.1 MB in total. That is the same scale as the
investigation's 435-plan store.

It then runs:
- an idle event-loop probe;
- health calls (three plain calls on base; plain, plain, plain, deep, plain
  on the head);
- five periodic recovery sweeps, counting `task_repository.get` calls.

During each call, a 1-byte loopback echo client and a 1 ms ticker run on
the same event loop.

```sh
# from the tree under test, Python 3.12, mcp 1.30.0
ACTIONS=7 PYTHONPATH=. python docs/evidence/hamcp171-measurements/measure.py "$PWD" 435 240 out.json
```

The two recorded runs, both on the same host and run back to back:
- `base-119be1b.json`: main at 2.4.1-beta.16 (the script was copied into
  that tree);
- `head-7553dde.json`: the HAMCP-171 runtime.
