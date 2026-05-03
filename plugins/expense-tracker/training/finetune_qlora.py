from __future__ import annotations
import argparse
from datasets import load_dataset
from peft import LoraConfig
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import SFTTrainer, SFTConfig


def format_example(example: dict) -> str:
    return (
        "<|system|>\nYou are Expense Tracker's local finance agent. Learn to answer by choosing the correct app tools.\n"
        "<|user|>\n" + example["instruction"] + "\n"
        "<|assistant|>\n" + example["output"]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-model", default="microsoft/Phi-3.5-mini-instruct")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", default="expense-agent-lora")
    parser.add_argument("--epochs", type=float, default=2)
    parser.add_argument("--batch-size", type=int, default=2)
    args = parser.parse_args()

    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=quant,
        device_map="auto",
        trust_remote_code=True,
    )
    dataset = load_dataset("json", data_files=args.dataset, split="train")
    dataset = dataset.map(lambda item: {"text": format_example(item)})

    lora = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    config = SFTConfig(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=8,
        learning_rate=2e-4,
        logging_steps=10,
        save_strategy="epoch",
        max_seq_length=2048,
        dataset_text_field="text",
    )
    trainer = SFTTrainer(model=model, tokenizer=tokenizer, train_dataset=dataset, peft_config=lora, args=config)
    trainer.train()
    trainer.save_model(args.output)
    tokenizer.save_pretrained(args.output)


if __name__ == "__main__":
    main()
