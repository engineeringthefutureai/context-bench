<!-- rubric_version: 1 -->
# Compare candidate changes

Several candidate changes were written independently for the same task in the same repository.
Judge only the code.

- `task.md`: the task.
- `repo/`: the repository **before** any change. Read it to see what already exists.
- `candidates/<n>.diff`: each candidate's change; `<n>.tests.txt`: hidden acceptance tests passed.

You can read and search files. Don't run anything.

Score every candidate 1-5 on each dimension, comparing them against each other:

- **correctness**: does what the task asks, including edge cases it implies; doesn't break anything.
- **reuse**: uses existing project code where it fits instead of re-implementing it.
- **conventions**: follows how this repo already does things.
- **scope**: changes what the task needs and nothing else.
- **readability**: clear, idiomatic, appropriately small.

Then rank all candidates best-first by which you would merge first (ties allowed).

Reply with JSON only:
```json
{
  "ranking": [[2], [1, 4], [3]],
  "candidates": {
    "1": {"correctness": 4, "reuse": 2, "conventions": 3, "scope": 5, "readability": 4,
          "note": "One or two sentences quoting a line from the diff."}
  }
}
```
