# Guarded self-hosted SFU candidate — no automatic deployment

Pinned upstream: LiveKit v1.9.11, commit `0508ee9f9ce71b497e65bbe66037cd2a2090ce5e`.

Apply `patch_source.py PATH_TO_DISPOSABLE_CLONE` to a clean clone of that commit. The script refuses another version or an already patched tree. Build with this Dockerfile using the patched upstream clone as build context; no KLTN secrets belong in that context. No commit/push is required. The staging binary/image and source hashes are recorded separately; a build recipe is not a claim of byte-for-byte binary reproducibility.

Changed upstream files: `pkg/service/{roommanager.go,utils.go,auth.go}`, `pkg/rtc/{participant.go,transport.go,transportmanager.go}`. New files: `pkg/service/{mediaauthority.go,mediaack.go}`. Guard unit tests in this folder can be copied into the corresponding package directories. Existing full `rtc`/`service` suites run in an independent Docker-in-Docker test engine; never point them at application Redis.

Required candidate-only environment:

- `KLTN_MEDIA_AUTHORITY`: backend `/api/v1/internal/media/authorize` on a private path.
- `KLTN_MEDIA_AUTHORITY_KEY_FILE`: read-only file, at least 32 random bytes; same dedicated service secret as backend. Keep outside repo/build context. No LiveKit API key rotation.
- `KLTN_MEDIA_CONTROL_ADDR`: internal HTTP listener address, e.g. `:7940`. Publish only to backend/localhost, never a public media management endpoint.

Backend candidate flags: `MEDIA_AUTHORITY_ENABLED=true`, `MEDIA_SERVICE_KEY_FILE`, `MEDIA_CONTROL_URL`, optional `MEDIA_AUTHORITY_POOL_SIZE` (10 lease connections default; separate bounded admission pool of 4). Current defaults keep the new path off until an approved cutover. Stage launch does not read application `.env`, including descendant Python workers, and uses separate DB/S3/signing/service credentials.

Every join/resume registers under a read barrier. Revoke takes the write barrier, fences immutable grant IDs and waits for confirmed subscription shutdown and successful publisher/subscriber Pion transport closes. `IsClosed` and participant-list absence are insufficient. Cleanup only forgets a live entry after confirmed close. Restart clears in-memory state; fresh backend authorization still denies every durable revoked grant.

Lease max 2 s, renew 700 ms, callback timeout 450 ms. Only a verified allow extends a lease, measured from request start. A transient failure can consume its existing lease but never extend it. Explicit deny closes immediately; unavailable authority cannot admit new joins or refresh. A signed purpose (`join`, `lease`, `refresh`) reserves DB capacity for real renewals. Browser clients cannot select that field. Admission overload returns failure, never an invented allow.

HMAC authenticates requests/responses with fresh nonces; management requests also have a short timestamp/replay window. Backend caches its service key until restart: secret rotation requires a coordinated procedure. Internal HTTP is acceptable only within the reviewed local/private boundary; TLS/network policy must be reviewed before moving across hosts. No raw JWT is included in patched auth errors.

This is a single-SFU/single-backend-worker candidate. SIP/WHIP/agents/multi-node and high publisher fan-out are not certified by the 21-peer single-publisher staging test. Rebase requires reviewing all participant ingress, the close completion hooks and all security/regression gates; never silently change the pinned image.

Rollback must stop/block media if a guarded image is unavailable. Starting stock SFU with still-valid keys reopens replay and is not a safe rollback. Keep additive DB tables/audit/outbox; do not downgrade the application DB to hide pending revocations.

Candidate files here do not change the running compose configuration. Integration has passed the isolated staging gates only; target cutover needs separate owner approval and a verified backup/restore, media outage window, target replay/lease/ACK regression and rollback review. Phase 7C is not accepted on the target runtime. Local reports and raw evidence are intentionally not distributed with this source repository.
