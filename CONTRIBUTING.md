# Contributing

Keep the core independent of every CAD product. Host-specific imports belong
only inside their adapter package.

Every new action must include validation, capability metadata, refusal tests,
transaction tests and documentation. Generated source belongs only in the macro
artifact pipeline and must never be smuggled into a typed action payload. Never
use raw topology indices as public semantic identifiers.

Run `python -m pytest` before submitting a change.

Update the public report when a change affects the architecture, evaluation
method, or stated result. Never include credentials, local tracking artifacts,
private benchmark ground truth, or sensitive model responses.
