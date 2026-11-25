# Plugins

The engine runs with `plugins/` deleted. A plugin is optional code that adds a check or a card
type without changing the core render path.

## Layout

```
plugins/
  islamic/
    verify_scripture.py
    scripture_verified.json
    sensitive_words.py
    tests/
```

## Enabling

Plugins are off by default. Set the environment variable when you want them:

```
FORGE_PLUGINS=islamic ./forge.sh check --profile my-studio
FORGE_PLUGINS=islamic ./forge.sh render --profile my-studio
```

With the plugin off:

- no citation check runs
- no sensitive-word screening runs
- the render path is unchanged

## The Islamic plugin

It adds a citation guard against a local verified table and a screening pass for burned captions.
Both are mechanical checks, not religious judgement. If you use the citation guard, a human must
still review every reference before publishing.

## Writing a plugin

- One folder per plugin.
- No imports from the core at module import time unless the core is on the path.
- No network in tests.
- Never assume a brand, a logo or a palette.
- Add a test beside the plugin and skip it when the plugin is off.
