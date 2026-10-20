---
name: answer
description: "Show and answer the questions a running `rein ship` is waiting on (Orca decision gates). Use when the user says: qué me pregunta rein, rein pregunta, responde a rein, responde al gate, answer the gate, pending questions."
license: MIT
---

# /answer

Relays the owner's words to `rein ship`'s open gates; also used by /ship's watcher. Owner never types Orca commands.
Never edits code, and **never invents an answer the user did not give.**

## Steps

1. Show what is pending: `rein answer [<change>] --root <plan repo>`. Explain each gate to the
   user in plain language, in their language: the question, the task, the options.
   "no questions pending": say so and stop.
2. Wait for the user's answer. When they give it, run
   `rein answer <change> "<text>" [--gate <id>] --root <plan repo>`.
   Several gates open: ask which one, then pass `--gate`.
3. Option gates (retry/skip, approve/reject, ...): map the user's words to exactly one listed
   option and confirm it back before running ("¿retry?"). Ambiguous: ask again. Question gates
   take the user's own words, verbatim.
4. Report the output. Exit 2 means a wrong option or gate: show the listed choices and ask
   again. Never retry blindly.
