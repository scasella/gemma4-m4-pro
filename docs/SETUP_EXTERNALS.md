# External Setup

This lean public repo keeps the tuned scripts and curated results, but it does not bundle everything from the private working workspace.

## Required local pieces

1. Put the model file at `./models/gemma-4-26B-A4B-it-Q4_K_M.gguf`
2. Build the included `hypura-main/` checkout if you want the fast path

## Optional local pieces

- Clone the Flash-MoE runtime into `./anemll-flash-llama.cpp-gemma4` if you want the lower-memory path
- Clone a baseline `llama.cpp` build into `./llama-baseline-build` if you want the comparison baseline

The front-door commands will still work for the fast Hypura path without those optional pieces.
