import sys
from pathlib import Path

import numpy
import onnxruntime
import tensorflow as tf
import tf2onnx

MODULE = Path(sys.argv[1])
TARGET = Path(sys.argv[2])
OUTPUT = "embeddings_apply_default/MudpuppyLite/Conv_18/Conv2D"
MAX_DIFFERENCE = 1e-3

tf.compat.v1.disable_eager_execution()
graph = tf.Graph()
with graph.as_default(), tf.compat.v1.Session(graph=graph) as session:
    tf.compat.v1.saved_model.loader.load(session, [], str(MODULE))
    frozen = tf.compat.v1.graph_util.convert_variables_to_constants(session, graph.as_graph_def(), [OUTPUT])
    audio = (numpy.random.default_rng(1).standard_normal((1, 32000)) * 0.1).astype(numpy.float32)
    reference = session.run(OUTPUT + ":0", {"samples:0": audio})
tf2onnx.convert.from_graph_def(frozen, input_names=["samples:0"], output_names=[OUTPUT + ":0"], opset=17,
                               output_path=str(TARGET))
converted = onnxruntime.InferenceSession(str(TARGET))
mine = converted.run(None, {converted.get_inputs()[0].name: audio})[0]
difference = float(numpy.abs(reference - mine).max())
print(f"converted, largest difference from TensorFlow {difference:.2e}")
if difference > MAX_DIFFERENCE:
    raise SystemExit("the converted model does not match the original")
