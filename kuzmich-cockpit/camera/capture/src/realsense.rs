//! RealSense capture via C API
//!
//! Supports color + depth streams:
//! - Color: BGR8 frames → GStreamer appsrc for H.265 encoding
//! - Depth: Z16 (GRAY16_LE) frames → GStreamer appsrc → websocketserver for YOLO
//!
//! Both streams are fed into separate GStreamer appsrc elements.
//! GStreamer handles all delivery (WebRTC, MJPEG, WebSocket).

use crate::config::{DepthConfig, StreamConfig};

pub struct CapturePipeline {
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub has_depth: bool,
    pub depth_width: u32,
    pub depth_height: u32,
}

pub fn try_capture(
    _cfg: &crate::config::CameraConfig,
    color: &StreamConfig,
    depth: &DepthConfig,
) -> Result<CapturePipeline, Box<dyn std::error::Error>> {
    // TODO: RealSense C API
    // 1. rs2_create_context()
    // 2. rs2_create_pipeline()
    // 3. rs2_config_enable_stream(RS2_STREAM_COLOR, width, height, RS2_FORMAT_BGR8, fps)
    // 4. if depth.enabled: rs2_config_enable_stream(RS2_STREAM_DEPTH, dw, dh, RS2_FORMAT_Z16, dfps)
    // 5. rs2_pipeline_start()

    log::info!(
        "RealSense capture: {}x{}@{} depth={} ({}x{}@{})",
        color.width, color.height, color.fps,
        depth.enabled, depth.width, depth.height, depth.fps
    );

    Ok(CapturePipeline {
        width: color.width,
        height: color.height,
        fps: color.fps,
        has_depth: depth.enabled,
        depth_width: if depth.enabled { depth.width } else { 0 },
        depth_height: if depth.enabled { depth.height } else { 0 },
    })
}

impl CapturePipeline {
    /// Read next color+depth frames from RealSense.
    /// Returns (color_bgr, depth_z16) tuples.
    /// depth_z16 is None if depth is disabled.
    pub fn wait_for_frames(&self) -> Result<(Vec<u8>, Option<Vec<u8>>), Box<dyn std::error::Error>> {
        // TODO: RealSense C API
        // let frames = rs2_pipeline_wait_for_frames();
        // let color = rs2_frame_data(rs2_frameset_get_color_frame(frames));
        // let depth = if self.has_depth {
        //     Some(rs2_frame_data(rs2_frameset_get_depth_frame(frames)))
        // } else {
        //     None
        // };
        // Ok((color.to_vec(), depth.map(|d| d.to_vec())))

        // Placeholder — returns empty frames
        let color_size = (self.width * self.height * 3) as usize;
        Ok((vec![0u8; color_size], None))
    }

    pub fn release(&self) {
        // TODO: rs2_pipeline_stop(), rs2_pipeline_release()
        log::info!("RealSense capture released");
    }
}
