Vendored, unmodified, from k2-fsa/sherpa-onnx at tag v1.13.8
(`sherpa-onnx/kotlin-api/`), Apache-2.0. The native side reads these
classes field by field over JNI, so they must match the libraries in
`app/src/main/jniLibs/arm64-v8a/` (`libsherpa-onnx-jni.so`,
`libonnxruntime.so` -- onnxruntime is MIT), taken from the same release's
`sherpa-onnx-v1.13.8-android.tar.bz2`. Update all of them together.

The keyword model in `app/src/main/assets/kws/` is
`sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01` (int8 files),
Apache-2.0. `keywords.txt` is ours: "MELODY" in the model's BPE tokens,
with the score and threshold measured in DECISIONS.md (2026-10-29).
