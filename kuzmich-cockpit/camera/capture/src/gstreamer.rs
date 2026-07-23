//! GStreamer pipeline management

use crate::config::CameraConfig;
use crate::realsense::CapturePipeline;

pub async fn run_pipeline(
    capture: CapturePipeline,
    cfg: &CameraConfig,
) -> Result<(), Box<dyn std::error::Error>> {
    log::info!("Starting GStreamer pipeline: encoder={}", cfg.encoder.name);

    // Build GStreamer pipeline:
    // appsrc → videoconvert → nvvideoconvert → nvv4l2h265enc → h265parse → tee
    //   ├── webrtcbin → WebRTC delivery
    //   ├── appsink → MJPEG encoder → HTTP
    //   └── appsink → raw BGR → WebSocket (for YOLO)
    //
    // The pipeline runs in the GStreamer thread.
    // RealSense capture feeds frames into appsrc via gst_app_src_push_buffer().

    let pipeline_str = format!(
        "appsrc name=src is-live=true format=time \
         video/x-raw,format=BGR,width={},height={},framerate={}/1 \
         ! videoconvert \
         ! nvvideoconvert \
         ! video/x-raw(memory:NVMM),format=NV12 \
         ! {} bitrate={} \
         ! h265parse \
         ! tee name=t \
         t. ! queue ! webrtcbin stun-server={} \
         t. ! queue ! jpegenc ! multipartmux boundary=frame ! appsink name=mjpeg \
         t. ! queue ! appsink name=raw",
        capture.width, capture.height, capture.fps,
        cfg.encoder.name, cfg.encoder.bitrate,
        cfg.webrtc.stun_url,
    );

    log::info!("Pipeline: {}", pipeline_str);

    // TODO: Implement with gstreamer-rs:
    // let pipeline = gst::parse_launch(&pipeline_str)?;
    // let appsrc = pipeline.by_name("src").unwrap();
    // pipeline.set_state(gst::State::Playing)?;
    //
    // Capture loop:
    // loop {
    //     let frame = rs2_pipeline_wait_for_frames();
    //     let buffer = gst::Buffer::from_slice(frame_data);
    //     appsrc.emit("push-buffer", &[&buffer]);
    // }

    // For now, just keep running
    loop {
        tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
    }
}
