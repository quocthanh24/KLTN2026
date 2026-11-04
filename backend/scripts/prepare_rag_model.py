"""Download only the approved pinned local ONNX artifacts, outside source control."""
import argparse
from huggingface_hub import snapshot_download
from app.services.document_pipeline import MODEL_REVISION, MODEL_FILE

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    snapshot_download("intfloat/multilingual-e5-small", revision=MODEL_REVISION,
        allow_patterns=[MODEL_FILE, "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "config.json"],
        local_dir=args.directory)
    print("Approved E5 ONNX artifacts downloaded; worker verifies SHA-256 before inference")
