# Historical stable-v1 tests

`historical_stable_v1_gateway.py` and
`historical_stable_v1_metadata.py` preserve retired v1.1.2 test source for
reference. Their names deliberately do not match the repository's `test*.py`
Engineering discovery pattern.

Stable v1.1.2 is operationally retired. These modules are non-gating and must
not be described as dependency-faithful validation when Engineering MCP 1.28.1
dependencies are installed. Do not run them in the Engineering CI or promotion
environment. The unchanged stable image build remains packaging evidence only;
it is not current runtime-support or rollback assurance.
