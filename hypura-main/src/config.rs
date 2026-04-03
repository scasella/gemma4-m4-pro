const GIB: u64 = 1 << 30;
const DEFAULT_MEMORY_RESERVE_GB: f64 = 2.0;
const DEFAULT_GPU_RUNTIME_OVERHEAD_GB: f64 = 1.0;
const DEFAULT_PRELOAD_EXTRA_GB: f64 = 2.0;

fn env_gib(name: &str) -> Option<f64> {
    let value = std::env::var(name).ok()?;
    let parsed = value.trim().parse::<f64>().ok()?;
    if parsed.is_finite() && parsed >= 0.0 {
        Some(parsed)
    } else {
        None
    }
}

fn gib_to_bytes(gib: f64) -> u64 {
    (gib * GIB as f64).round() as u64
}

pub fn memory_reserve_bytes() -> u64 {
    env_gib("HYPURA_MEMORY_RESERVE_GB")
        .map(gib_to_bytes)
        .unwrap_or_else(|| gib_to_bytes(DEFAULT_MEMORY_RESERVE_GB))
}

pub fn gpu_runtime_overhead_bytes() -> u64 {
    env_gib("HYPURA_GPU_RUNTIME_OVERHEAD_GB")
        .map(gib_to_bytes)
        .unwrap_or_else(|| gib_to_bytes(DEFAULT_GPU_RUNTIME_OVERHEAD_GB))
}

pub fn keep_resident_headroom_bytes() -> u64 {
    env_gib("HYPURA_KEEP_RESIDENT_HEADROOM_GB")
        .map(gib_to_bytes)
        .unwrap_or_else(memory_reserve_bytes)
}

pub fn preload_headroom_bytes() -> u64 {
    env_gib("HYPURA_PRELOAD_HEADROOM_GB")
        .map(gib_to_bytes)
        .unwrap_or_else(|| keep_resident_headroom_bytes() + gib_to_bytes(DEFAULT_PRELOAD_EXTRA_GB))
}
