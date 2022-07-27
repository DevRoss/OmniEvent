import copy
import os
from pathlib import Path
import pdb
import sys
sys.path.append("../../")
import json
import torch
import logging

import numpy as np
from tqdm import tqdm
from collections import defaultdict

from transformers import set_seed
from transformers.integrations import TensorBoardCallback
from transformers import EarlyStoppingCallback

from OpenEE.arguments import DataArguments, ModelArguments, TrainingArguments, ArgumentParser
from OpenEE.backbone.backbone import get_backbone
from OpenEE.input_engineering.seq2seq_processor import (
    EAESeq2SeqProcessor
)
from OpenEE.model.model import get_model
from OpenEE.evaluation.metric import (
    compute_seq_F1,
)
from OpenEE.evaluation.dump_result import (
    get_leven_submission,
    get_leven_submission_sl,
    get_leven_submission_seq2seq,
    get_maven_submission,
    get_maven_submission_sl,
    get_maven_submission_seq2seq,
    get_duee_submission,
    get_duee_submission_sl,
)
from OpenEE.evaluation.convert_format import (
    get_ace2005_argument_extraction_sl
)

from OpenEE.evaluation.utils import (
    predict_eae,
    predict_sub_eae,
)

from OpenEE.input_engineering.input_utils import get_bio_labels
from OpenEE.trainer_seq2seq import Seq2SeqTrainer, ConstrainedSeq2SeqTrainer

# from torch.utils.tensorboard import SummaryWriter

# argument parser
parser = ArgumentParser((ModelArguments, DataArguments, TrainingArguments))
if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
    # If we pass only one argument to the script and it's the path to a json file,
    # let's parse it to get our arguments.
    model_args, data_args, training_args = parser.parse_json_file(json_file=os.path.abspath(sys.argv[1]))
elif len(sys.argv) >= 2 and sys.argv[1].endswith(".yaml"):
    model_args, data_args, training_args = parser.parse_yaml_file(yaml_file=os.path.abspath(sys.argv[1]))
else:
    model_args, data_args, training_args = parser.parse_args_into_dataclasses()

# output dir
model_name_or_path = model_args.model_name_or_path.split("/")[-1]
output_dir = Path(
    os.path.join(os.path.join(os.path.join(training_args.output_dir, training_args.task_name), model_args.paradigm),
                 model_name_or_path))
output_dir.mkdir(exist_ok=True, parents=True)
training_args.output_dir = output_dir

# local rank
# training_args.local_rank = int(os.environ["LOCAL_RANK"])

# logging config 
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    datefmt="%m/%d/%Y %H:%M:%S",
    level=logging.INFO,
)

# markers 
markers = ["<event>", "</event>", "<ace>", "<duee>", "<fewfc>"]
data_args.markers = markers
print(data_args, model_args, training_args)

# set seed
set_seed(training_args.seed)

# writter 
earlystoppingCallBack = EarlyStoppingCallback(early_stopping_patience=training_args.early_stopping_patience, \
                                              early_stopping_threshold=training_args.early_stopping_threshold)

# model 
backbone, tokenizer, config = get_backbone(model_args.model_type, model_args.checkpoint_path, \
                                           model_args.model_name_or_path, data_args.markers, new_tokens=data_args.markers)
model = get_model(model_args, backbone)
model.cuda()

data_class = EAESeq2SeqProcessor
metric_fn = compute_seq_F1

# dataset 
train_dataset = data_class(data_args, tokenizer, data_args.train_file, data_args.train_pred_file, True)
eval_dataset = data_class(data_args, tokenizer, data_args.validation_file, data_args.validation_pred_file, False)

# set event types
training_args.data_for_evaluation = eval_dataset.get_data_for_evaluation()

# Trainer 
trainer = Seq2SeqTrainer(
    args=training_args,
    model=model,
    train_dataset=train_dataset,
    eval_dataset=eval_dataset,
    compute_metrics=metric_fn,
    data_collator=train_dataset.collate_fn,
    tokenizer=tokenizer,
    callbacks=[earlystoppingCallBack],
    # decoding_type_schema={"role_list": all_roles_except_na}
)

if training_args.do_train:
    trainer.train()

if training_args.do_predict:
    if not data_args.split_infer:
        logits, labels, metrics, test_dataset = predict_eae(trainer, tokenizer, data_class, data_args, training_args)
    else:
        logits, labels, test_dataset = predict_sub_eae(trainer, tokenizer, data_class, data_args, training_args)

    # pdb.set_trace()
    preds = np.argmax(logits, axis=-1)
    if data_args.test_exists_labels:
        # writer.add_scalar(tag="test_accuracy", scalar_value=metrics["test_accuracy"])
        print(metrics)
        if model_args.paradigm == "sequence_labeling":
            get_ace2005_argument_extraction_sl(preds, labels, data_args.test_file, data_args, test_dataset.is_overflow)
        else:
            pass
    else:
        # save name
        aggregation = model_args.aggregation
        save_path = os.path.join(training_args.output_dir, f"{model_name_or_path}-{aggregation}.jsonl")
        if model_args.paradigm == "token_classification":
            pass

        elif model_args.paradigm == "sequence_labeling":
            if data_args.dataset_name == "DuEE1.0":
                print("Start get duee submission++++++++++++++++++")
                get_duee_submission_sl(preds, labels, test_dataset.is_overflow, save_path, data_args)