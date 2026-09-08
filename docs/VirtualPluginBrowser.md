# Browsing virtuals and plugins

`fastdyn help virtuals` opens the same small, transient terminal picker used by
`fastdyn help platforms`:

```bash
fastdyn help virtuals
```

The first menu separates two different configuration concepts:

- **Virtual instructions** run a named callback when a configured guest PC is
  executed. Selecting one prints a `[[CPU.cpu0.virtuals]]` TOML block.
- **Run-wide plugins** prepare a firmware-wide feature before QEMU starts.
  Selecting one prints its `[CPU.cpu0.plugins.<name>]` TOML block.

For example, selecting VariableWatch shows:

```toml
[CPU.cpu0.plugins.variable_watch]
enabled = true
variable = "motor_state.temperature"
access = "write"
```

The picker redraws in place. Use Up/Down (or `j`/`k`) to move, Enter to
select, Backspace to go back, and `q` to cancel. On selection it disappears
and prints only the selected TOML and documentation path.

`fastdyn help plugins` is an alias for the same browser. For non-interactive use:

```bash
fastdyn help virtuals --no-browse
```

Choose **Documentation** inside the virtual/plugin or modifier browser for the
documentation relevant to that feature family.
