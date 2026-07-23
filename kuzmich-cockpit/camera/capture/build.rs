use std::env;
use std::path::PathBuf;

fn main() {
    // Tell cargo to re-run if wrapper changes
    println!("cargo:rerun-if-changed=wrapper.h");

    // Find librealsense2
    let output = std::process::Command::new("pkg-config")
        .args(["--cflags", "--libs", "librealsense2"])
        .output()
        .expect("Failed to run pkg-config. Is librealsense2-dev installed?");

    if !output.status.success() {
        // Fallback: try common paths
        println!("cargo:rustc-link-search=native=/usr/lib/aarch64-linux-gnu");
        println!("cargo:rustc-link-lib=dylib=realsense2");
    } else {
        let cflags = String::from_utf8_lossy(&output.stdout);
        for flag in cflags.split_whitespace() {
            if flag.starts_with("-I") {
                println!("cargo:include={}", &flag[2..]);
            }
        }
    }

    // Generate bindings
    let bindings = bindgen::Builder::default()
        .header("wrapper.h")
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .generate()
        .expect("Unable to generate bindings");

    let out_path = PathBuf::from(env::var("OUT_DIR").unwrap());
    bindings
        .write_to_file(out_path.join("realsense_bindings.rs"))
        .expect("Couldn't write bindings!");
}
