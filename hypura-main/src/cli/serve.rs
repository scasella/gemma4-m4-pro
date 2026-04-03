use std::path::Path;
use std::sync::Arc;
use std::time::Instant;

use hypura::compute::inference;
use hypura::model::gguf::GgufFile;
use hypura::model::metadata::ModelMetadata;
use hypura::profiler;
use hypura::scheduler::placement::compute_placement_with_context;
use hypura::server::ollama_types::GgufInfo;
use hypura::server::routes::{self, AppState};
use hypura::telemetry::metrics::TelemetryEmitter;

pub fn run(
    model_path: &str,
    host: &str,
    port: u16,
    context: u32,
    threads: Option<i32>,
    threads_batch: Option<i32>,
    batch: Option<u32>,
    ubatch: Option<u32>,
) -> anyhow::Result<()> {
    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(run_async(
        model_path,
        host,
        port,
        context,
        threads,
        threads_batch,
        batch,
        ubatch,
    ))
}

async fn run_async(
    model_path: &str,
    host: &str,
    port: u16,
    context: u32,
    threads: Option<i32>,
    threads_batch: Option<i32>,
    batch: Option<u32>,
    ubatch: Option<u32>,
) -> anyhow::Result<()> {
    let path = Path::new(model_path);
    anyhow::ensure!(path.exists(), "Model file not found: {model_path}");

    // Load hardware profile
    let hardware = match profiler::load_cached_profile()? {
        Some(p) if !profiler::is_profile_stale(&p) => p,
        _ => {
            println!("No hardware profile found. Running profiler...");
            let p = profiler::run_full_profile()?;
            profiler::save_profile(&p)?;
            p
        }
    };

    // Parse GGUF and compute placement
    let gguf = GgufFile::open(path)?;
    let metadata = ModelMetadata::from_gguf(&gguf)?;
    let file_size = std::fs::metadata(path)?.len();
    let plan = compute_placement_with_context(&gguf, &hardware, context)?;
    let gpu_budget = inference::compute_gpu_budget(&hardware, &metadata, context);
    let n_gpu_layers = inference::gpu_layers_from_placement(&plan, &gguf, gpu_budget);

    let config = inference::InferenceConfig {
        n_ctx: context,
        n_batch: batch.unwrap_or(inference::InferenceConfig::default().n_batch),
        n_ubatch: ubatch.unwrap_or(inference::InferenceConfig::default().n_ubatch),
        n_threads: threads.unwrap_or(inference::InferenceConfig::default().n_threads),
        n_threads_batch: threads_batch
            .unwrap_or(inference::InferenceConfig::default().n_threads_batch),
        ..inference::InferenceConfig::default()
    };
    let runtime_context = config.n_ctx;
    let runtime_batch = config.n_batch;
    let runtime_ubatch = config.n_ubatch;
    let runtime_threads = config.n_threads;
    let runtime_threads_batch = config.n_threads_batch;

    // Load model on a blocking thread
    println!("Loading model...");
    let load_start = Instant::now();

    let path_owned = path.to_path_buf();
    let plan_arc = Arc::new(plan);
    let gguf_arc = Arc::new(gguf);
    let plan_for_load = plan_arc.clone();
    let gguf_for_load = gguf_arc.clone();

    let loaded = tokio::task::spawn_blocking(move || {
        inference::load_model(
            &path_owned,
            &config,
            n_gpu_layers,
            &plan_for_load,
            &gguf_for_load,
        )
    })
    .await??;

    let load_duration_ns = load_start.elapsed().as_nanos() as u64;
    let model_name = loaded.model_name.clone();
    let chat_template = loaded.model.chat_template().or_else(|| {
        gguf_arc
            .get_string("tokenizer.chat_template")
            .map(str::to_owned)
    });

    let gguf_info = GgufInfo {
        file_size,
        architecture: metadata.architecture.clone(),
        parameter_count: metadata.parameter_count,
        quantization: metadata
            .quantization
            .clone()
            .unwrap_or_else(|| "unknown".into()),
        context_length: metadata.context_length,
        chat_template,
    };

    let telemetry = Arc::new(TelemetryEmitter::new(256));

    let state = Arc::new(AppState {
        loaded_model: Arc::new(std::sync::Mutex::new(loaded)),
        model_name: model_name.clone(),
        gguf_info,
        load_duration_ns,
        telemetry,
    });

    let app = routes::router(state);
    let bind_addr = format!("{host}:{port}");
    let listener = tokio::net::TcpListener::bind(&bind_addr).await?;

    println!();
    println!("Hypura serving {model_name}");
    println!("  Endpoint: http://{bind_addr}");
    println!("  Ollama-compatible API: /api/generate, /api/chat, /api/tags");
    println!(
        "  Runtime config: context={}, batch={}, ubatch={}, threads={}, threads_batch={}",
        runtime_context, runtime_batch, runtime_ubatch, runtime_threads, runtime_threads_batch
    );
    println!();

    axum::serve(listener, app).await?;
    Ok(())
}
