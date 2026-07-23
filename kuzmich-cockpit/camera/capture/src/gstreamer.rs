//! GStreamer pipeline management
//!
//! Unified pipeline for LOCAL and RELAY modes:
//! - Color: appsrc → encode → tee → webrtcbin / MJPEG / websocketserver (raw BGR)
//! - Depth: appsrc → websocketserver (raw GRAY16_LE)
//!
//! GStreamer serves WebSocket natively via `websocketserver` element.
//! Python/PyGObject only manages pipeline lifecycle (create/start/stop).

use crate::config::CameraConfig;
use crate::realsense::CapturePipeline;

pub async fn run_pipeline(
    capture: CapturePipeline,
    cfg: &CameraConfig,
) -> Result<(), Box<dyn std::error::Error>> {
    log::info!(
        "Starting GStreamer pipeline: encoder={}, {}x{}@{} depth={}",
        cfg.encoder.name,
        capture.width, capture.height, capture.fps,
        capture.has_depth,
    );

    // --- Color pipeline ---
    // appsrc → videoconvert → nvvideoconvert → encoder → tee
    //   ├── webrtcbin → WebRTC (browser)
    //   ├── jpegenc → multipartmux → websocketserver:8084 (MJPEG fallback)
    //   └── videoconvert → websocketserver:8082 (raw BGR for YOLO)
    let color_pipeline = format!(
        "appsrc name=src is-live=true format=time \
         video/x-raw,format=BGR,width={},height={},framerate={}/1 \
         ! videoconvert \
         ! nvvideoconvert \
         ! video/x-raw(memory:NVMM),format=NV12 \
         ! {} bitrate={} \
         ! h265parse \
         ! tee name=t \
         t. ! queue ! webrtcbin stun-server={} \
         t. ! queue ! jpegenc ! multipartmux boundary=frame \
         ! websocketserver host=0.0.0.0 port=8084 \
         t. ! queue ! videoconvert ! video/x-raw,format=BGR \
         ! websocketserver host=0.0.0.0 port=8082",
        capture.width, capture.height, capture.fps,
        cfg.encoder.name, cfg.encoder.bitrate,
        cfg.webrtc.stun_url,
    );

    // --- Depth pipeline (LOCAL mode only) ---
    // appsrc → websocketserver:8083 (raw GRAY16_LE for YOLO+3D)
    let depth_pipeline = if capture.has_depth {
        format!(
            "appsrc name=depth_src is-live=true format=time \
             video/x-raw,format=GRAY16_LE,width={},height={},framerate={}/1 \
             ! videoconvert \
             ! websocketserver host=0.0.0.0 port=8083",
            capture.depth_width, capture.depth_height, capture.fps,
        )
    } else {
        String::new()
    };

    log::info!("Color pipeline: {}", color_pipeline);
    if !depth_pipeline.is_empty() {
        log::info!("Depth pipeline: {}", depth_pipeline);
    }

    // TODO: Implement with gstreamer-rs:
    //
    // // Start color pipeline
    // let pipeline = gst::parse_launch(&color_pipeline)?;
    // let appsrc = pipeline.by_name("src").unwrap();
    // pipeline.set_state(gst::State::Playing)?;
    //
    // // Start depth pipeline (if enabled)
    // let depth_appsrc = if !depth_pipeline.is_empty() {
    //     let dp = gst::parse_launch(&depth_pipeline)?;
    //     let ds = dp.by_name("depth_src").unwrap();
    //     dp.set_state(gst::State::Playing)?;
    //     Some(ds)
    // } else {
    //     None
    // };
    //
    // // Capture loop: feed RealSense frames into appsrc
    // loop {
    //     let (color_frame, depth_frame) = rs2_wait_for_frames();
    //
    //     // Color → appsrc
    //     let buf = gst::Buffer::from_slice(color_frame);
    //     appsrc.emit("push-buffer", &[&buf]);
    //
    //     // Depth → appsrc (if enabled)
    //     if let (Some(ds), Some(df)) = (&depth_appsrc, depth_frame) {
    //         let buf = gst::Buffer::from_slice(df);
    //         ds.emit("push-buffer", &[&buf]);
    //     }
    // }

    // For now, just keep running
    loop {
        tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
    }
}
