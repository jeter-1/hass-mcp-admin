# Staged Engineering releases

`next-version` is a single-use declaration consumed only by the protected
release-promotion workflow after a feature change reaches `main`.

The feature pull request deliberately leaves Home Assistant add-on metadata at
the currently published version. The promotion workflow validates the declared
candidate with AwesomeVersion, creates a local release commit, publishes and
anonymously verifies immutable images from that commit, and only then atomically
pushes the release commit and annotated tag.

If any publication or verification step fails, the local commit and tag are not
pushed. A published-but-unpromoted image is treated as an immutable
reconciliation case and is never silently reused.
