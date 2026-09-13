# Third-party notices

Threnda's optional built-in Local AI installs the following independently
licensed components. These notices do not grant a license to Threnda itself.

## llama.cpp

- Project: <https://github.com/ggml-org/llama.cpp>
- Pinned runtime: `b10516`
- License: MIT

The official runtime package includes its license file, which Threnda retains
inside the managed runtime directory.

## Qwen2.5 1.5B Instruct GGUF

- Project: <https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF>
- Pinned revision: `91cad51170dc346986eccefdc2dd33a9da36ead9`
- Quantization: Q4_K_M
- License: Apache License 2.0

Threnda installs the model repository's checksum-pinned `LICENSE` file as
`QWEN-MODEL-LICENSE-APACHE-2.0.txt` beside the GGUF model files. Offline and
release bundles must include this file unchanged.
