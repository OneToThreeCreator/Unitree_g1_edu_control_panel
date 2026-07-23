use std::env;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=wrapper.h");

    // Try pkg-config first
    let found = std::process::Command::new("pkg-config")
        .args(["--cflags", "librealsense2"])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);

    if found {
        // pkg-config found it — use its flags
        let output = std::process::Command::new("pkg-config")
            .args(["--cflags", "--libs", "librealsense2"])
            .output()
            .unwrap();
        let flags = String::from_utf8_lossy(&output.stdout);
        for flag in flags.split_whitespace() {
            if flag.starts_with("-I") {
                println!("cargo:include={}", &flag[2..]);
            } else if flag.starts_with("-L") {
                println!("cargo:rustc-link-search=native={}", &flag[2..]);
            } else if flag.starts_with("-l") {
                println!("cargo:rustc-link-lib=dylib={}", &flag[2..]);
            }
        }
    } else {
        // Fallback: search common paths
        let mut found_path = false;
        for p in &["/usr/include", "/usr/local/include", "/opt/librealsense2/include"] {
            if std::path::Path::new(&format!("{}/librealsense2/rs.h", p)).exists() {
                println!("cargo:include={}", p);
                found_path = true;
                break;
            }
        }
        if !found_path {
            eprintln!("WARNING: librealsense2 headers not found.");
            eprintln!("Install: sudo apt install librealsense2-dev");
            eprintln!("Or: build librealsense2 from source");
            eprintln!("Build will likely fail without RealSense headers.");
        }
        println!("cargo:rustc-link-lib=dylib=realsense2");
    }

    // Generate bindings
    let bindings = bindgen::Builder::default()
        .header("wrapper.h")
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .generate()
        .expect("Unable to generate bindings. Is librealsense2-dev installed?");

    let out_path = PathBuf::from(env::var("OUT_DIR").unwrap());
    bindings
        .write_to_file(out_path.join("realsense_bindings.rs"))
        .expect("Couldn't write bindings!");
}
