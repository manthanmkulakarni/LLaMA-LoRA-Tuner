import os
import sys
import gc
import json
import re

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, LlamaTokenizer
from peft import PeftModel

from .globals import Global
from .lib.get_device import get_device


def get_new_base_model(base_model_name):
    if Global.ui_dev_mode:
        return

    if Global.new_base_model_that_is_ready_to_be_used:
        if Global.name_of_new_base_model_that_is_ready_to_be_used == base_model_name:
            model = Global.new_base_model_that_is_ready_to_be_used
            Global.new_base_model_that_is_ready_to_be_used = None
            Global.name_of_new_base_model_that_is_ready_to_be_used = None
            return model
        else:
            Global.new_base_model_that_is_ready_to_be_used = None
            Global.name_of_new_base_model_that_is_ready_to_be_used = None
            clear_cache()

    device = get_device()

    if device == "cuda":
        model = AutoModelForCausalLM.from_pretrained(
            base_model_name,
            load_in_8bit=Global.load_8bit,
            torch_dtype=torch.float16,
            # device_map="auto",
            # ? https://github.com/tloen/alpaca-lora/issues/21
            device_map={'': 0},
            trust_remote_code=True
        )
    elif device == "mps":
        model = AutoModelForCausalLM.from_pretrained(
            base_model_name,
            device_map={"": device},
            torch_dtype=torch.float16,
            trust_remote_code=True
        )
    else:
        model = AutoModelForCausalLM.from_pretrained(
            base_model_name,
            device_map={"": device},
            low_cpu_mem_usage=True,
            trust_remote_code=True
        )

    tokenizer = get_tokenizer(base_model_name)

    if re.match("[^/]+/llama", base_model_name):
        model.config.pad_token_id = tokenizer.pad_token_id = 0
        model.config.bos_token_id = tokenizer.bos_token_id = 1
        model.config.eos_token_id = tokenizer.eos_token_id = 2

    return model


def get_tokenizer(base_model_name):
    if Global.ui_dev_mode:
        return

    loaded_tokenizer = Global.loaded_tokenizers.get(base_model_name)
    if loaded_tokenizer:
        return loaded_tokenizer

    try:
        tokenizer = AutoTokenizer.from_pretrained(
            base_model_name,
            trust_remote_code=Global.trust_remote_code
        )
    except Exception as e:
        if 'LLaMATokenizer' in str(e):
            tokenizer = LlamaTokenizer.from_pretrained(
                base_model_name,
                trust_remote_code=Global.trust_remote_code
            )
        else:
            raise e

    Global.loaded_tokenizers.set(base_model_name, tokenizer)

    return tokenizer


def get_model(
        base_model_name,
        peft_model_name=None):
    if Global.ui_dev_mode:
        return

    if peft_model_name == "None":
        peft_model_name = None

    model_key = base_model_name
    if peft_model_name:
        model_key = f"{base_model_name}//{peft_model_name}"

    loaded_model = Global.loaded_models.get(model_key)
    if loaded_model:
        return loaded_model

    peft_model_name_or_path = peft_model_name

    if peft_model_name:
        lora_models_directory_path = os.path.join(
            Global.data_dir, "lora_models")
        possible_lora_model_path = os.path.join(
            lora_models_directory_path, peft_model_name)
        if os.path.isdir(possible_lora_model_path):
            peft_model_name_or_path = possible_lora_model_path

            possible_model_info_json_path = os.path.join(
                possible_lora_model_path, "info.json")
            if os.path.isfile(possible_model_info_json_path):
                try:
                    with open(possible_model_info_json_path, "r") as file:
                        json_data = json.load(file)
                        possible_hf_model_name = json_data.get("hf_model_name")
                        if possible_hf_model_name and json_data.get("load_from_hf"):
                            peft_model_name_or_path = possible_hf_model_name
                except Exception as e:
                    raise ValueError(
                        "Error reading model info from {possible_model_info_json_path}: {e}")

    Global.loaded_models.prepare_to_set()
    clear_cache()

    model = get_new_base_model(base_model_name)

    if peft_model_name:
        device = get_device()

        if device == "cuda":
            model = PeftModel.from_pretrained(
                model,
                peft_model_name_or_path,
                torch_dtype=torch.float16,
                # ? https://github.com/tloen/alpaca-lora/issues/21
                device_map={'': 0},
            )
        elif device == "mps":
            model = PeftModel.from_pretrained(
                model,
                peft_model_name_or_path,
                device_map={"": device},
                torch_dtype=torch.float16,
            )
        else:
            model = PeftModel.from_pretrained(
                model,
                peft_model_name_or_path,
                device_map={"": device},
            )

    if re.match("[^/]+/llama", base_model_name):
        model.config.pad_token_id = get_tokenizer(
            base_model_name).pad_token_id = 0
        model.config.bos_token_id = 1
        model.config.eos_token_id = 2

    if not Global.load_8bit:
        model.half()  # seems to fix bugs for some users.

    model.eval()
    if torch.__version__ >= "2" and sys.platform != "win32":
        model = torch.compile(model)

    Global.loaded_models.set(model_key, model)
    clear_cache()

    return model


def prepare_base_model(base_model_name=Global.default_base_model_name):
    Global.new_base_model_that_is_ready_to_be_used = get_new_base_model(
        base_model_name)
    Global.name_of_new_base_model_that_is_ready_to_be_used = base_model_name


def clear_cache():
    gc.collect()

    # if not shared.args.cpu: # will not be running on CPUs anyway
    with torch.no_grad():
        torch.cuda.empty_cache()


def unload_models():
    Global.loaded_models.clear()
    Global.loaded_tokenizers.clear()
    clear_cache()
