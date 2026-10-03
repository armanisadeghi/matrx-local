"""Claude Code history importer and capture reconciler, shared by Matrx Local and Matrx 2.

Modules: ``importer`` (discovery, hashing, envelopes), ``reconciler`` (the backfill pass and the
explicit "import missing" run), ``account`` (the Claude account snapshot), ``session_index`` and
``scope`` (Claude desktop's own session labels), ``bridge`` (the envelope models),
``envelopes`` (shared envelope rules and the acknowledgement check), ``identity`` (the complete
cloud identity list), ``delivery`` (direct delivery), ``ports`` (what an app injects).
"""
