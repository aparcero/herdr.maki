# zai-local

Included in the [maki-plugins collection](../../README.md). The implementation
is `lua/zai_local/init.lua`, loaded by `plugin/zai_local.lua`.

## GLM-5.3 reasoning levels

Maki 0.7.0's built-in `zai` adapter converts `max` to `xhigh`, but GLM-5.3
documents `low`, `high`, and `max`. The local `zai-local` provider bypasses that
mapping using the OpenAI-compatible codec and the Z.ai Coding Plan endpoint.
Its thinking picker offers `low`, `high`, and `max`, plus `adaptive` which uses
the model's default `max`. It follows Maki's session thinking preference.
Reasoning stays enabled because GLM-5.3 requires it; an off setting (including
a fresh Maki configuration's default) becomes `low`. The original `zai` provider
remains available; Maki reserves that slug, so this plugin uses `zai-local`.

After installing the collection, start a new Maki process.
Authenticate the alias using `ZHIPU_API_KEY` from your secrets environment,
or run `maki auth login zai-local`
to save its key in Maki's credential store. The alias cannot reuse credentials
saved under the built-in `zai` slug. Never put the key in this repository.

Select `/model zai-local/glm-5.3`, then `/thinking low`, `/thinking high`, or
`/thinking max`. Or launch:

```sh
maki --model zai-local/glm-5.3
```

The alias has no Coding Plan quota display. Remove it when
the native adapter supports GLM-5.3's effort levels correctly.
`just check` verifies its outgoing requests against a loopback server
using dummy credentials, with no external model calls.

