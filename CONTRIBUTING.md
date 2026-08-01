# Contributing

Keep changes small and traceable to one Skill lifecycle or support invariant.

1. Open an issue for a new capability or policy class.
2. Add a focused regression test before changing behavior.
3. Run `python3 -m unittest discover -s tests -v`.
4. Run both Skill validators when Skill files change.
5. Confirm no prompt, command, token, credential, local path, or generated eval
   result entered the diff.
6. Submit a pull request describing behavior, evidence, and rollback impact.

Do not vendor upstream Skills or add application-specific deployment rules.
