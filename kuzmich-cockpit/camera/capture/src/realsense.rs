//! RealSense capture via C API

use crate::config::{StreamConfig, DepthConfig};

pub struct CapturePipeline {
    // RealSense pipeline handle (C pointer)
    // GStreamer appsrc for feeding frames
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub has_depth: bool,
}

pub fn try_capture(
    _cfg: &crate::config::CameraConfig,
    color: &StreamConfig,
    depth: &DepthConfig,
) -> Result<CapturePipeline, Box<dyn std::error::Error>> {
    // TODO: Implement RealSense C API capture
    // 1. rs2_create_context()
    // 2. rs2_create_pipeline(ctx, &error)
    // 3. rs2_config_create()
    // 4. rs2_config_enable_stream(config, RS2_STREAM_COLOR, width, height, RS2_FORMAT_BGR8, fps, &error)
    // 5. If depth.enabled: rs2_config_enable_stream(config, RS2_STREAM_DEPTH, ...)
    // 6. rs2_pipeline_start(config, &error)

    log::info!("RealSense capture: {}x{}@{} depth={}",
        color.width, color.height, color.fps, depth.enabled);

    Ok(CapturePipeline {
        width: color.width,
        height: color.height,
        fps: color.fps,
        has_depth: depth.enabled,
    })
}

impl CapturePipeline {
    pub fn release(&self) {
        // TODO: rs2_pipeline_stop(), rs2_pipeline_release()
        log::info!("RealSense capture released");
    }
}
