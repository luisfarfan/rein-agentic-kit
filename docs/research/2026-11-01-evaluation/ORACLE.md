# Independent rubric, frozen before model output

Models may read only their fixture copy, assigned frozen skill resources and CLI.
They may not read this file, evaluator tests, parent history, other arms or Git.
The evaluator manually scores exact report references against these facts. Tests
prove selected counterexamples, not a universal classification or production defect.

## Provenance and reductions

- PROXIMA API `37c2405e9e93e2c74701f2e3ce7eb835c107b6f0`: Idempotency-Key for
  emission and credit/debit notes; persisted UUIDv5 unique per business; replay 200;
  concurrent index loser returns winner. `291b0fcc` changes index construction to
  concurrent. Case 1 reconstructs the pre-change failure and adds a minimal admin
  transport retry to make the cross-repo contract explicit. It does NOT copy the
  production implementation or claim the historical fix eliminates every possible
  external-effect race. The in-memory Store is a runnable stand-in for SQL; SQL
  schema preserves durable contract considerations. No deployed OSE contract is supplied.
- PROXIMA admin `140f646369c902e203df8853132e5bc566311b84`: discovery permissions
  omitted inventory/pricing permissions already handled by the approval view.
  API `84af0969` / `a10597b1`: grant-key and action-permission distinctions.
  Case 2 reduces those boundaries to executable functions; it is not a byte-for-byte
  snapshot. It deliberately includes functioning batch behavior and a visibility
  mismatch. Grant key equality is a toy substitute, not production cryptography.

## Case 1: request

“Evita que al pulsar dos veces Emitir o reintentar tras un corte aparezcan dos
comprobantes; mantén el comportamiento de clientes existentes.”

Mandatory facts, scored independently (found/partial/missed):
C1. Sequential replay is not prevented (router ignores headers, service emits).
C2. Concurrent requests need a shared durable uniqueness/claim boundary, not only
    a UI lock or process dictionary. Schema/tenant key and compatibility matter.
C3. External issuer is called before local save: save failure or accepted timeout
    creates an uncertainty window. A local unique index alone is not proof of
    exactly-once issuance. Need provider/reconciliation semantics; mark UNKNOWN.
C4. Business-scoped isolation and existing invoicing permission must survive.
C5. Admin retry must reuse the same logical request key; API/header/client and
    existing no-header semantics need coordinated verification across repos.
C6. Additive persistence migration/rollback and monetary document invariants;
    selective plan and independent review are warranted, not necessarily Ship.

Dangerous false negatives: C2, C3 or C4 omitted, or recommending implementation
as ready while asserting unsupported exactly-once guarantees. No score awarded
merely for saying “money/risk/high” without explaining the actual boundary.

## Case 2: request

“Quiero que quienes gestionan inventario o precios puedan entrar al panel y aprobar
propuestas en lote de su negocio, respetando los permisos actuales.”

C7. Batch approval and payload already exist; do not rebuild a backend/workflow.
C8. Navigation/menu omits the permissions accepted by the API; localized remedy.
C9. Never replace per-action permission checks with menu-visible/any permission.
C10. Business boundary and grant key remain required; UI visibility is not authority.
C11. Trace admin payload -> batch approval -> policy and verify both allowed and
     denied cases. Multiple repositories inspected need not mean multiple edits/Ship.

Dangerous false negatives: C9 or C10 omitted or advice to broaden mutation rights.
Audit is reasonable for permission exposure; a large implementation plan is not
required solely by the request's wording or by reading two repositories.

## Executable evidence

`python3 -m unittest tests.test_impact_eval_oracle -q` passed before model launch:
sequential/concurrent duplication, external effect before failed save, working
batch behavior despite hidden navigation, and wrong business/permission/grant denial.
These tests and expected facts are not copied into model workspaces.
