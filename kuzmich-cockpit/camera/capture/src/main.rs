//! Camera capture: RealSense → GStreamer pipeline
//!
//! Reads camera.toml, captures from RealSense via C API,
//! feeds frames into GStreamer pipeline for encoding (H.265) and delivery (WebRTC/MJPEG).

mod config;
mod realsense;
mod gstreamer;

use log::info;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    env_logger::init();

    let config_path = std::env::var("CAMERA_CONFIG")
        .unwrap_or_else(|_| "camera.toml".to_string());

    info!("Loading config from {}", config_path);
    let cfg = config::CameraConfig::from_file(&config_path)?;

    info!("Starting camera capture: {}x{}@{} encoder={}",
        cfg.color.width, cfg.color.height, cfg.color.fps, cfg.encoder.name);

    // Try capture configs from fallback chain
    let mut capture_ok = false;
    for fallback in &cfg.fallbacks {
        info!("Trying: {}", fallback.label);
        match realsense::try_capture(&cfg, &fallback.color, &fallback.depth) {
            Ok(pipeline) => {
                info!("Capture started: {}", fallback.label);
                gstreamer::run_pipeline(pipeline, &cfg).await?;
                capture_ok = true;
                break;
            }
            Err(e) => {
                log::warn!("Failed: {} — {}", fallback.label, e);
            }
        }
    }

    if !capture_ok {
        log::error!("All capture configurations failed");
        return Err("No valid capture configuration".into());
    }

    Ok(())
}
