# Examples

| File | What it shows |
| --- | --- |
| [`demo.sh`](demo.sh) | adopt agent-lint in a temp repo, make the usual agent shortcuts (a `# noqa`, a skipped and a deleted test, a rule family switched off) and watch each one get caught; then fix an old finding and ratchet. CI runs it. |
| [`config.toml`](config.toml) | every configuration key, commented |

```bash
pip install "agent-lint @ git+https://github.com/MartinCastroAlvarez/agent-lint" ruff
bash examples/demo.sh
```
