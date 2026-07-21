"""
Convert insightface ONNX models to TensorRT engines for GPU acceleration.
Run this on the robot.
"""

import tensorrt as trt
import os

TRT_LOGGER = trt.Logger(trt.Logger.WARNING)
MODELS_DIR = os.path.expanduser("~/.insightface/models/buffalo_l")

# (onnx_name, engine_name, input_name, min_shape, opt_shape, max_shape)
MODELS = [
    ("det_10g.onnx",      "det_10g.engine",      "input.1", (1,3,320,320),  (1,3,640,640),  (1,3,640,640)),
    ("w600k_r50.onnx",    "w600k_r50.engine",    "input.1", (1,3,112,112),  (1,3,112,112),  (1,3,112,112)),
    ("2d106det.onnx",     "2d106det.engine",     "data",    (1,3,192,192),  (1,3,192,192),  (1,3,192,192)),
    ("1k3d68.onnx",       "1k3d68.engine",       "data",    (1,3,192,192),  (1,3,192,192),  (1,3,192,192)),
    ("genderage.onnx",    "genderage.engine",    "data",    (1,3,96,96),    (1,3,96,96),    (1,3,96,96)),
]


def build_engine(onnx_name, engine_name, input_name, min_s, opt_s, max_s):
    onnx_path = os.path.join(MODELS_DIR, onnx_name)
    engine_path = os.path.join(MODELS_DIR, engine_name)

    if not os.path.exists(onnx_path):
        print(f"SKIP: {onnx_path} not found")
        return False

    print(f"Converting {onnx_name} -> {engine_name}")

    builder = trt.Builder(TRT_LOGGER)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    parser = trt.OnnxParser(network, TRT_LOGGER)

    with open(onnx_path, "rb") as f:
        if not parser.parse(f.read()):
            for i in range(parser.num_errors):
                print(f"  Parse error: {parser.get_error(i)}")
            return False

    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 2 << 30)

    profile = builder.create_optimization_profile()
    profile.set_shape(input_name, min_s, opt_s, max_s)
    config.add_optimization_profile(profile)

    engine = builder.build_serialized_network(network, config)
    if engine is None:
        print(f"  FAILED to build engine")
        return False

    with open(engine_path, "wb") as f:
        f.write(engine)

    size_mb = os.path.getsize(engine_path) / (1024 * 1024)
    print(f"  OK: {size_mb:.1f} MB")
    return True


def main():
    success = 0
    for onnx_name, engine_name, input_name, min_s, opt_s, max_s in MODELS:
        if build_engine(onnx_name, engine_name, input_name, min_s, opt_s, max_s):
            success += 1
    print(f"\nDone: {success}/{len(MODELS)} engines built")


if __name__ == "__main__":
    main()
