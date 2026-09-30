# ADR-024: Sanitized integration-specific configuration inspection

Date: 2026-09-27. Status: source implementation authorized; activation pending.
ADR-023 was already allocated, so the contract's permitted next number is used.

Josh approved HA-INT-INSPECT-01 revision 2 plus HA-INT-INSPECT-01-AUTH: one native,
read-only Alarmo inspector using reviewed specific callbacks through the existing
Engineering endpoint. Generic ha-mcp 8.5.0 `ha_get_integration` can initialize an
options flow and internally fall back; its form values are not Alarmo's stored
mode/sensor authority. It remains unexposed and unchanged.

Use a fixed positive projection, isolated bounded transport, typed evidence and
frozen private pagination. Add one separate Core metadata-read applicability
reference without altering old profiles, compiled exact admission or signed
registry mechanisms. The Alarmo adapter has its own code-owned source contract;
Core authority is not certification of Alarmo or installed bytes.

The alternative is a separately reviewed strict upstream Alarmo reader. Revisit
adapter ownership when such a released upstream contract can satisfy these same
read-only, source-authority, privacy, bounds and no-fallback requirements.

Consequences: one maintained native adapter, one catalog addition, explicit
partial/non-atomic evidence, and no new access or write authority. Existing
signed data withholds it until separately authorized activation. R3's registry
renewal limitation and P0b's installed identity qualification remain unresolved.
See [contract](../INTEGRATION_INSPECTION.md) and
[acceptance](../ALARMO_INSPECTION_ACCEPTANCE.md) for exact stage boundaries.
