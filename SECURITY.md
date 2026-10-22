# Input and execution boundaries

OrderWitness parses local JSON and performs bounded in-process search. It does
not evaluate input as code, invoke plugins, start services, or contact a network.
The CLI reads files and prints results; it never modifies the supplied history.

Limits: 1,000,000 input bytes, 32 operations, 32 keys, 1,024 characters per string
value, signed 64-bit integer values, and 1–1,000,000 expanded search states
(default 50,000). Search has exponential worst-case cost. A state-count budget is
not a hard wall-clock or memory limit: use an OS/container resource limit when
processing untrusted workloads. The maximum permitted budget may be expensive.

Histories and reports can contain sensitive business values. Sanitize them
before sharing or committing them. A positive certificate verifies behavior
under the documented model, not the authenticity or completeness of the log.
This is an educational diagnostic tool, not a safety-critical certification.

For a suspected security issue, use GitHub's private vulnerability reporting
if available. Do not put secrets in public issues.
