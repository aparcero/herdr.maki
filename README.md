# maki-plugins

A collection of plugins for [Maki](https://maki.sh), installed as one Lua
package. Herdr is the first integration; future plugins get their own entry
files, module namespaces, and documentation in this repository.

| Plugin | Purpose |
| --- | --- |
| [Herdr](docs/plugins/herdr.md) | Report agent status to Herdr, discover peers, exchange messages, and start or close agents |

## Install

Add the collection to your global Maki `init.lua`:

```lua
maki.pack.add({
  { src = "https://github.com/aparcero/maki-plugins" },
})
```

Maki shows the install prompt before the UI starts, then records the resolved
commit in `pack-lock.json`. Commit that file to pin the revision on other
machines. Permission requests in `plugin.toml` are approved separately.

All plugins ship and update together. Each plugin controls when its integration
activates; Herdr activates only inside a Herdr-managed pane.

### Migrating from herdr.maki

Replace the old `maki.pack.add` entry with the one above, rather than adding a
second entry. Restart Maki to install the renamed package and approve its
permissions. Maki associates approvals with the package name and source, so the
new name requires a new approval. The Herdr tool and `/herdr` command keep their
existing names.

## Layout

```text
plugin/
  herdr.lua             Herdr entry point
lua/
  herdr/                Herdr modules
docs/
  plugins/
    herdr.md            Herdr usage and design
plugin.toml             Permissions requested by the collection
README.md               Plugin catalog and package instructions
```

Maki loads the `plugin/*.lua` entry files and resolves modules under `lua/`.
The entry files share a package environment. This follows Maki's native package
layout while keeping each integration's code in its own namespace.

## Permissions

Permissions apply to the whole collection. The current requests are all used
by Herdr:

| Permission | Used for |
| --- | --- |
| `env` | Reading `HERDR_ENV`, `HERDR_BIN_PATH`, and `HERDR_PANE_ID` |
| `run` | Calling the `herdr` CLI |
| `fs_read` | Reading `<cwd>/.agents/agent_config.json` for configured agent starts |

The collection currently requests no file-write or network permissions.

## Adding a plugin

1. Add a small `plugin/<name>.lua` entry file and keep its implementation in
   `lua/<name>/`, using `require("<name>.<module>")` for its modules.
2. Keep plugins independent. Use local variables, a distinct module namespace,
   and unique tool and command names. An integration that requires a particular
   environment should return early when that environment is absent.
3. Document setup, behavior, and limitations in `docs/plugins/<name>.md`, then
   add it to the catalog above.
4. Declare any additional permissions in the shared `plugin.toml` and explain
   their use here. Package revisions and releases cover the whole collection.

Keep user configuration, runtime state, and credentials outside this checkout.

## Development

Edit the installed package's files, then run `/reload` in Maki to rebuild the
plugin host. Edits in a separate checkout must first reach the installed copy.
For the Herdr integration, follow its [verification steps](docs/plugins/herdr.md#verify-it).

See Maki's [package documentation](https://maki.sh/docs/packages/) for package
loading, lockfiles, updates, and permissions.
