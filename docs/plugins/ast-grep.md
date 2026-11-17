# ast-grep

Included in the [maki-plugins collection](../../README.md). Requires `ast-grep`
on PATH (or `ASTGREP_BIN`). The implementation is `lua/ast_grep/init.lua`, loaded
by `plugin/ast_grep.lua`.

## Native tools

Run `/reload` in an existing Maki session after installation, or start a new one.
The adapter launches the existing CLI directly with an argument array; no shell,
Node runtime, or MCP server is involved in a tool call.

- `ast_grep_search`: required `pattern` and `language`; optional `paths` and
  `globs`. Paths default to Maki's working directory.
- `ast_grep_rewrite`: the same arguments plus required `rewrite` and optional
  `dry_run`. It previews by default. After reviewing, repeat with
  `dry_run=false` to apply all matches within the selected paths/globs.
  An empty replacement deletes the matched node.
- `ast_grep_syntax_tree`: required `code` and `language`; optional `format`
  (`pattern` by default, `ast`, `cst`, or `sexp`). It prints how ast-grep
  parses a pattern or a snippet of target code, without searching files, so
  a pattern that unexpectedly matches nothing can be compared with the code
  it should match. A multi-statement snippet still shows its `ast`/`cst`
  tree, followed by ast-grep's note that it is not a single pattern.

For example, preview `oldApi($$$ARGS)` → `newApi($$$ARGS)` with `language="cpp"`
and `paths=["src"]`. These are syntax matches, without symbol/type resolution.
The adapter also adds a line to Maki's tool-usage prompt that points the model
at these tools instead of running ast-grep through bash.

The adapter validates arguments, passes patterns literally (attached
`--pattern=` values keep a leading `-` from becoming a flag), and preflights
before writes. It checks stderr, exit codes, and the actual apply report to
confirm a rewrite. Operations are serialized within one Maki instance.
Each subprocess has a 30-second timeout and a 16 MiB output limit. Cancellation
kills the running subprocess and releases the serialization lock. To raise the
timeout for a large repository, use the custom package loader below.

The model sees at most 100 matches plus Maki's configured text limits. Calls in
Maki's full output mode receive all matches within the subprocess limit. The UI
has a compact header and clickable results with colored matches/replacements;
saved sessions restore the same view. The expanded view retains up to 1,000
matches, 5,000 lines, and 512 KiB, with explicit truncation notices. Narrow the
search to inspect omitted results.

Apply respects Maki's permission rules and is blocked in plan mode. If your
configuration enables YOLO, applies do not ask for additional approval. Every apply
reruns the query against current files; a prior preview is guidance, not a stored
transaction. There is no rollback, and a failed apply can leave partial edits.
Inspect the diff and run the relevant project checks afterward.

`ASTGREP_BIN` optionally selects another executable. The collection manifest
grants `run` and `env` for this plugin. The subprocess reads and rewrites files;
Lua filesystem permissions do not restrict its filesystem access.

`just check` runs the regression suite through the installed Maki
binary using a scripted HTTP server on loopback. It uses no external model service or live
Maki configuration. Cases cover multiline captures, Unicode/CRLF, quoting,
leading-hyphen patterns, globs, empty rewrites, invalid inputs, missing/read-only
files, failure reports, output limits, cancellation, permissions, syntax trees
(including that they read neither files nor stdin), timeout setup, the prompt
hint, and native rendering/restoration.

## Configuration

The default subprocess timeout is 30 seconds. To change it, replace the default
package declaration with a custom loader in your global `init.lua` (or
Harness's `settings/maki/init.lua`):

```lua
maki.pack.add({
  { src = "https://github.com/aparcero/maki-plugins" },
}, {
  load = function()
    require("ast_grep.init").setup({ timeout_secs = 120 })
    require("herdr.link")
    require("herdr.lifecycle")
    require("zai_local.init")
  end,
})
```

The loader runs as the package owner and replaces the automatic entry files,
so it explicitly loads all three integrations. Keep it up to date if you want
new integrations added to the collection. `setup` rejects unknown options and
timeouts that are not positive integers. Local `init.lua` modules are isolated
from package modules; do not use the former `require("ast_grep")` setup call.
See Maki's [custom package loading](https://maki.sh/docs/packages/).
