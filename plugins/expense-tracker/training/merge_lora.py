from __future__ import annotations
import argparse
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-model", default="microsoft/Phi-3.5-mini-instruct")
    parser.add_argument("--lora", default="expense-agent-lora")
    parser.add_argument("--output", default="expense-agent-merged")
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(args.base_model, device_map="auto", torch_dtype="auto", trust_remote_code=True)
    model = PeftModel.from_pretrained(model, args.lora)
    model = model.merge_and_unload()
    model.save_pretrained(args.output, safe_serialization=True)
    tokenizer.save_pretrained(args.output)
    print(f"Merged model written to {args.output}")


if __name__ == "__main__":
    main()
