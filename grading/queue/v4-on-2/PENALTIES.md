<!-- catalogue_version: 1 -->
# Review one change

One change was written for a task in this repository. Its hidden acceptance tests passed, so assume the
main behaviour works. Your job is to find defects the tests would not catch. Judge only the code.

- `task.md`: the task.
- `patch.diff`: the change.
- `repo/`: the repository **before** the change. Read it to see what already exists and how this
  repository does things.

You can read and search files. Don't run anything.

Cite only defects from this list, each at most once per distinct place in the change:

| code | defect |
|---|---|
| `scope` | changes unrelated to the task |
| `reimplemented` | re-implements logic the repository already has, where the existing code fits |
| `convention` | breaks a convention visible in the repository (money as integer cents, error handling, events, settings, ...) |
| `api` | changes a public signature or behaviour that other code in the repository relies on |
| `wrong_untested` | logic that looks wrong in a place the task's tests are unlikely to exercise |
| `risk` | a security or data problem (personal data in logs, unvalidated input, secrets) |

For each defect, quote one line of the change exactly as it appears in `patch.diff`, without the leading
`+` or `-`. A defect you can't tie to a line of the change doesn't count. If you find nothing, say so with
an empty list: that is a normal answer.

Reply with JSON only:
```json
{"penalties": [{"code": "convention", "quote": "total = price * 1.2", "why": "one sentence"}]}
```
