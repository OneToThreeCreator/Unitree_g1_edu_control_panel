use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
pub struct CameraConfig {
    pub color: StreamConfig,
    pub depth: DepthConfig,
    pub encoder: EncoderConfig,
    #[serde(default)]
    pub fallbacks: Vec<FallbackConfig>,
    #[serde(default)]
    pub webrtc: WebRtcConfig,
    #[serde(default = "default_teleop")]
    pub teleop: TeleopConfig,
}

#[derive(Debug, Deserialize)]
pub struct StreamConfig {
    pub width: u32,
    pub height: u32,
    pub fps: u32,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
pub struct DepthConfig {
    pub enabled: bool,
    #[serde(default)]
    pub width: u32,
    #[serde(default)]
    pub height: u32,
    #[serde(default)]
    pub fps: u32,
}

#[derive(Debug, Deserialize)]
pub struct EncoderConfig {
    pub name: String,
    #[serde(default = "default_bitrate")]
    pub bitrate: u32,
}

#[derive(Debug, Deserialize)]
pub struct FallbackConfig {
    pub label: String,
    pub color: StreamConfig,
    pub depth: DepthConfig,
}

#[derive(Debug, Deserialize, Default)]
pub struct WebRtcConfig {
    #[serde(default = "default_stun")]
    pub stun_url: String,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
pub struct TeleopConfig {
    #[serde(default = "default_teleop_api")]
    pub api_url: String,
    #[serde(default = "default_teleop_ws")]
    pub ws_url: String,
    #[serde(default = "default_teleop_codec")]
    pub codec: String,
    #[serde(default = "default_poll_interval")]
    pub poll_interval: f64,
}

fn default_bitrate() -> u32 { 4000 }
fn default_stun() -> String { "stun:stun.l.google.com:19302".into() }
fn default_teleop_api() -> String { "http://192.168.1.102".into() }
fn default_teleop_ws() -> String { "ws://192.168.1.102/ws/camera/preview".into() }
fn default_teleop_codec() -> String { "h265".into() }
fn default_poll_interval() -> f64 { 2.0 }
fn default_teleop() -> TeleopConfig {
    TeleopConfig {
        api_url: default_teleop_api(),
        ws_url: default_teleop_ws(),
        codec: default_teleop_codec(),
        poll_interval: default_poll_interval(),
    }
}

impl CameraConfig {
    pub fn from_file(path: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let content = std::fs::read_to_string(path)?;
        let mut cfg: CameraConfig = toml::from_str(&content)?;

        // Env var overrides
        if let Ok(v) = std::env::var("CAM_GST_ENCODER") {
            cfg.encoder.name = v;
        }
        if let Ok(v) = std::env::var("CAM_GST_BITRATE") {
            cfg.encoder.bitrate = v.parse()?;
        }
        if let Ok(v) = std::env::var("CAM_COLOR_WIDTH") {
            cfg.color.width = v.parse()?;
        }
        if let Ok(v) = std::env::var("CAM_COLOR_HEIGHT") {
            cfg.color.height = v.parse()?;
        }
        if let Ok(v) = std::env::var("CAM_COLOR_FPS") {
            cfg.color.fps = v.parse()?;
        }
        if let Ok(v) = std::env::var("TELEOP_API_URL") {
            cfg.teleop.api_url = v;
        }
        if let Ok(v) = std::env::var("TELEOP_WS_URL") {
            cfg.teleop.ws_url = v;
        }

        Ok(cfg)
    }
}
