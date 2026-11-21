# Integration checks

- Local x86_64 PostgreSQL 16.16. The release's encrypted backup created a custom-format PostgreSQL dump, verified its archive catalog, and restored it with `pg_restore --exit-on-error` into a different temporary cluster. The expected user row and application fixture file were recovered.
- Docker Compose v2.39.4 validated the staging file with a temporary environment. The stage app bound to loopback 8766 and the database published no port. Both volume names were scoped to the staging project.
- A Python 3.11 aarch64 wheel-resolution pass succeeded for the pinned requirements, including sherpa-onnx, OpenCV and cryptography. This did not boot a native container or exercise an Oracle Ampere CPU.
- Real face tracking used a moving placement of the public-domain NASA astronaut image used by scikit-image as a test asset. The classifier produced 16 crop points covering 0.268 of source width. The resulting 1080×1920 file and its audio decoded successfully. This fixture tests controlled movement of a real face. The test does not establish accuracy for every person, side profile or lighting condition.
- Windsor.ai returned a connected YouTube account. Two real read-only queries requested per-video views and average view percentage for the most recent 7 and 30 days. Both responses returned 0 rows. No account details or observations are included in the package.
- Gemini smoke test exited before sending any HTTP request because no staging key/model pair was provided. The full test suite covered network failure, rate limits and cancellation using fixtures.
