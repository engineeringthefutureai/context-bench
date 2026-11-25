Different deployments need different return windows. Make the return window a deployment setting
like the shop's other settings: `return_window_days`, default 30, overridable with the
`SHOPKIT_RETURN_DAYS` environment variable, and used by `open_return` instead of the hard-coded 30.
