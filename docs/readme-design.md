# README structure and references

The README introduces REIN to someone who has not used the kit. The main version
is English; README.es.md carries the same structure and examples in Spanish.
This editorial record separates presentation guidance from evidence about REIN.

## References consulted

| Reference | Relevant guidance or observed pattern | Application here |
| --- | --- | --- |
| [GitHub: About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes) | Explain purpose, usefulness, getting started, and where to get help | A short purpose statement, visible benefits, setup, and documentation links |
| [Make a README](https://www.makeareadme.com/) | Use concrete examples, show expected results, consider visuals, and move extensive detail into separate documentation | A recorded example, a small reproducible check, diagrams, and the operational reference |
| [Google documentation guide: READMEs](https://github.com/google/styleguide/blob/gh-pages/docguide/READMEs.md) | Include or link to usage, contact, status, and relevant documentation | Keep the overview navigable and link to setup, evidence, and the full reference |
| [uv README](https://github.com/astral-sh/uv) | Observed example: a visual supporting the main value proposition precedes installation and detailed capabilities | Put the execution overview near the top; demonstrate REIN using its own evidence |
| [Superpowers README](https://github.com/obra/superpowers) | Observed example in the same domain: describe how the method operates and expose provider-specific setup | Explain the session/skills/CLI/runtime roles and preserve both provider entry points |
| [GitHub: Creating diagrams](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams) | GitHub renders fenced Mermaid diagrams in Markdown | Keep both diagrams editable in the README and renderable on GitHub |

These sources offer guidance and examples, rather than one mandatory README
format. The ordering below is an editorial choice for REIN.

## Reader journey

1. Identify what REIN does and which agents it works with.
2. See the default session path and the optional Orca delegation path.
3. Connect practical problems to inspectable outputs.
4. Follow one recorded request through different code realities.
5. Run three small checks without a model turn or Orca setup.
6. Understand state-bound review and the responsibility of each component.
7. Install, adopt the project guidance, and find the full reference.
8. Read the evidence and remaining limitations.

## Evidence behind the examples

The EUR table summarizes the bounded development fixtures in the
[entry-policy evaluation](research/2026-11-01-entry/RESULTS.md). It preserves the
successful native control and the pending monetary feature/review. No new model
run, production outcome, general quality improvement, or savings is implied.

The runnable example uses existing regression tests:

- An empty gate cannot pass.
- A missing test tool is a setup problem.
- Editing a reviewed file makes its prior approval stale.

The diagrams summarize [project entry guidance](../plugins/rein/ENTRY.md) and
[review checks](../plugins/rein/lib/gate.py). They depict workflow and review
validity; project instructions and code checks have different enforcement scopes.
No diagram adds a mandatory Discover → Plan → Ship sequence.
