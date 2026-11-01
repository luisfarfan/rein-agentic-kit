# REIN development

One shared core lives in `plugins/rein`; provider manifests must point at its
existing skills. Keep runtime dependencies empty and preserve CLI contracts.
Use native execution when sufficient; Orca owns delegated execution.

Run `python3 -m unittest discover -s tests -q` for regression validation.
For REIN workflows, load only the requested skill and its provider guide.
