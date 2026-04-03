# Gemma 4 Runtime Comparison

## Bottom line

- Fastest overall: `hypura` at about `57.01` tokens/second
- Lower-pressure alternate: `flashmoe` at about `13.90` tokens/second in one-shot mode

## Side by side

| Runtime | Usage style | Generation speed | Prompt speed | First answer | Load time | Lowest free memory | Swap growth |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hypura | persistent server | 57.01 tok/s | 67.44 tok/s | 1025.7 ms | 1.97 s | 9.33 GB | 0.00 GB |
| flashmoe | one-shot CLI | 13.90 tok/s | 10.90 tok/s | 71.9 ms | 18.69 s | 8.81 GB | 0.00 GB |
| flashmoe_server | resident server benchmark | 19.11 tok/s | 22.36 tok/s | 3048.2 ms | 9.19 s | 10.70 GB | 0.00 GB |

## Best overall config

- Backend: `hypura`
- Run: `20260402T212311Z-hypura-4096-combo-prompt-stable`
- Context: `4096`
- Threads: `10`
- Prompt threads: `14`
- Batch: `512`
- Micro-batch: `256`
- Artifact: `./autoresearch/results/runs/20260402T212311Z-hypura-4096-combo-prompt-stable.json`

## Best Flash-MoE alternate

- Backend: `flashmoe`
- Run: `20260402T234400Z-flashmoe-slot-bank-16-cpu-dense-`
- Context: `4096`
- Slot bank: `16`
- GPU layers: `0`
- Threads: `10`
- Prompt threads: `10`
- Batch: `1`
- Micro-batch: `1`
- Artifact: `./autoresearch/results/runs/20260402T234400Z-flashmoe-slot-bank-16-cpu-dense-.json`

## Best Flash-MoE resident-server benchmark

- Backend: `flashmoe_server`
- Run: `20260403T004039Z-flashmoe-server-slot-bank-16-cpu`
- Context: `4096`
- Slot bank: `16`
- GPU layers: `0`
- Threads: `10`
- Prompt threads: `10`
- Batch: `1`
- Micro-batch: `1`
- Parallel slots: `1`
- Prompt cache: `False`
- Artifact: `./autoresearch/results/runs/20260403T004039Z-flashmoe-server-slot-bank-16-cpu.json`

## Flash-MoE as a resident server

- Probe: `./autoresearch/results/flashmoe_server_probe_latest.json`
- Resident memory: about `4.32 GB` RSS
- Warm similar short prompts: `3.298 s` wall time, `14.94` prompt tok/s, `28.94` generation tok/s
- Exact repeated prompt: `1.789 s` wall time, `24.42` prompt tok/s, `35.81` generation tok/s
- Interpretation: Flash-MoE is still slow as a fresh one-shot process, but as a resident server it stays much lighter in memory and can answer short warm prompts reasonably quickly.

## Hypura as a resident server

- Probe: `./autoresearch/results/hypura_server_probe_latest.json`
- Resident memory: about `12.51 GB` RSS
- Warm similar short prompts: `4.890 s` wall time, `17.11` prompt tok/s, `54.04` generation tok/s
- Exact repeated prompt: `1.010 s` wall time, `47.50` prompt tok/s, `93.04` generation tok/s
- Interpretation: Hypura is still the throughput leader and the faster path on exact repeated prompts, but it keeps much more memory resident than Flash-MoE.

## Practical reading

- Hypura is still the clear choice if you want the highest raw speed and you can spare the extra resident memory.
- Flash-MoE resident server is the lighter always-on option. On this fresh probe it used much less memory and came back sooner on the two short non-identical prompts, but its steady-state generation speed stayed far lower.

## When to use which

- Use `hypura` when speed matters most and you are comfortable keeping more memory tied up.
- Use `flashmoe` one-shot when you want a roomier fallback and do not care about startup cost.
- Use `flashmoe_server` when you want a resident lower-memory alternate without leaning on exact-prompt cache wins.
- Use the Flash-MoE shell server path when you want the lower-memory alternate and plan to ask more than one question in the same session.
