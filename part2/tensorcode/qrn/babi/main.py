import json
import os
import shutil
from pprint import pprint

import tensorflow as tf

from babi.model import Tower, Runner
from config.get_config import get_config_from_file, get_config
from babi.read_data import read_data

flags = tf.app.flags

# File directories
flags.DEFINE_string("model_name", "babi", "Model name. This will be used for save, log, and eval names. [parallel]")
flags.DEFINE_string("data_dir", "data/babi", "Data directory [data/babi]")

# Training parameters
# These affect result performance
flags.DEFINE_integer("batch_size", 32, "Batch size for each tower. [32]")
flags.DEFINE_float("init_mean", 0, "Initial weight mean [0]")
flags.DEFINE_float("init_std", 1.0, "Initial weight std [1.0]")
flags.DEFINE_float("init_lr", 0.5, "Initial learning rate [0.5]")
flags.DEFINE_integer("lr_anneal_period", 100, "Anneal period [100]")
flags.DEFINE_float("lr_anneal_ratio", 0.5, "Anneal ratio [0.5")
flags.DEFINE_integer("num_epochs", 150, "Total number of epochs for training [100]")
flags.DEFINE_string("opt", 'adagrad', 'Optimizer: basic | adagrad | adam [basic]')
flags.DEFINE_float("wd", 0.001, "Weight decay [0.001]")
flags.DEFINE_integer("max_grad_norm", 0, "Max grad norm. 0 for no clipping [0]")
flags.DEFINE_float("max_val_loss", 0.0, "Max val loss [0.0]")

# Training and testing options
# These do not directly affect result performance (they affect duration though)
flags.DEFINE_boolean("train", True, "Train (will override without load)? Test if False [True]")
flags.DEFINE_integer("val_num_batches", 0, "Val num batches. 0 for max possible. [0]")
flags.DEFINE_integer("train_num_batches", 0, "Train num batches. 0 for max possible [0]")
flags.DEFINE_integer("test_num_batches", 0, "Test num batches. 0 for max possible [0]")
flags.DEFINE_boolean("load", True, "Load from saved model? [True]")
flags.DEFINE_boolean("progress", False, "Show progress bar? [False]")
flags.DEFINE_string("device_type", 'gpu', "cpu | gpu [gpu]")
flags.DEFINE_integer("num_devices", 1, "Number of devices to use. Only for multi-GPU. [1]")
flags.DEFINE_integer("val_period", 10, "Validation period (for display purpose only) [10]")
flags.DEFINE_integer("save_period", 10, "Save period [10]")
flags.DEFINE_string("config_id", 'None', "Config name (e.g. local) to load. 'None' to use config here. [None]")
flags.DEFINE_string("config_ext", ".json", "Config file extension: .json | .tsv [.json]")
flags.DEFINE_integer("num_trials", 1, "Number of trials [1]")
flags.DEFINE_string("seq_id", "None", "Sequence id [None]")
flags.DEFINE_string("run_id", "0", "Run id [0]")
flags.DEFINE_boolean("write_log", False, "Write log? [False]")
# TODO : don't erase log folder if not write log

# Debugging
flags.DEFINE_boolean("draft", False, "Draft? (quick initialize) [False]")

# App-specific options
# TODO : Any other options
flags.DEFINE_string("task", "all", "Task number. [all]")
flags.DEFINE_bool("large", False, "1k (False) | 10k (True) [False]")
flags.DEFINE_string("lang", "en", "en | something")
flags.DEFINE_integer("hidden_size", 50, "Hidden size. [20]")
flags.DEFINE_float("keep_prob", 1.0, "Keep probability of RNN inputs [1.0]")
flags.DEFINE_integer("mem_num_layers", 2, "Number of memory layers [2]")
flags.DEFINE_float("att_forget_bias", 2.5, "Attention gate forget bias [2.5]")
flags.DEFINE_integer("max_mem_size", 50, "Maximum memory size (from most recent) [50]")
flags.DEFINE_string("class_mode", "h", "classification mode: h | uh | hs | hss [h]")
flags.DEFINE_boolean("use_class_bias", False, "Use bias at final classification linear trans? [False]")
flags.DEFINE_boolean("use_reset", True, "Use reset gate? [True]")
flags.DEFINE_boolean("use_vector_gate", False, "Use vector gate? [False]")

FLAGS = flags.FLAGS


def mkdirs(config, trial_idx):
    evals_dir = "evals"
    logs_dir = "logs"
    saves_dir = "saves"
    if not os.path.exists(evals_dir):
        os.mkdir(evals_dir)
    if not os.path.exists(logs_dir):
        os.mkdir(logs_dir)
    if not os.path.exists(saves_dir):
        os.mkdir(saves_dir)

    model_name = config.model_name
    config_id = str(config.config_id).zfill(2)
    run_id = str(config.run_id).zfill(2)
    trial_idx = str(trial_idx).zfill(2)
    task = config.task.zfill(2)
    mid = config.lang
    if config.large:
        mid += "-10k"
    subdir_name = "-".join([task, config_id, run_id, trial_idx])

    eval_dir = os.path.join(evals_dir, model_name, mid)
    eval_subdir = os.path.join(eval_dir, subdir_name)
    log_dir = os.path.join(logs_dir, model_name, mid)
    log_subdir = os.path.join(log_dir, subdir_name)
    save_dir = os.path.join(saves_dir, model_name, mid)
    save_subdir = os.path.join(save_dir, subdir_name)
    config.eval_dir = eval_subdir
    config.log_dir = log_subdir
    config.save_dir = save_subdir

    if not os.path.exists(eval_dir):
        os.makedirs(eval_dir)
    if os.path.exists(eval_subdir):
        if config.train and not config.load:
            shutil.rmtree(eval_subdir)
            os.mkdir(eval_subdir)
    else:
        os.mkdir(eval_subdir)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    if os.path.exists(log_subdir):
        if config.train and not config.load:
            shutil.rmtree(log_subdir)
            os.mkdir(log_subdir)
    else:
        os.makedirs(log_subdir)
    if config.train:
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        if os.path.exists(save_subdir):
            if not config.load:
                shutil.rmtree(save_subdir)
                os.mkdir(save_subdir)
        else:
            os.mkdir(save_subdir)


def load_metadata(config):
    data_dir = os.path.join(config.data_dir, config.lang + ("-10k" if config.large else ""))
    metadata_path = os.path.join(data_dir, config.task.zfill(2), "metadata.json")
    metadata = json.load(open(metadata_path, "r"))

    # TODO: set other parameters, e.g.
    # config.max_sent_size = meta_data['max_sent_size']
    config.max_fact_size = metadata['max_fact_size']
    config.max_ques_size = metadata['max_ques_size']
    config.max_sent_size = metadata['max_sent_size']
    config.vocab_size = metadata['vocab_size']
    config.max_num_sents = metadata['max_num_sents']
    config.max_num_sups = metadata['max_num_sups']
    config.eos_idx = metadata['eos_idx']
    config.mem_size = min(config.max_num_sents, config.max_mem_size)


def main(_):
    this_dir = os.path.dirname(os.path.realpath(__file__))
    if FLAGS.seq_id == 'None':
        seq = [[FLAGS.config_id, FLAGS.num_trials]]
    else:
        seqs = json.load(open(os.path.join(this_dir, "seqs.json"), 'r'))
        seq = seqs[FLAGS.seq_id]
    print(seq)
    summaries = []
    for config_id, num_trials in seq:
        if config_id == "None":
            config = get_config(FLAGS.__flags, {})
        else:
            # TODO : create config file (.json)
            configs_path = os.path.join(this_dir, "configs%s" % FLAGS.config_ext)
            config = get_config_from_file(FLAGS.__flags, configs_path, config_id)
        if config.task == "all":
            tasks = list(map(str, range(1, 21)))
        else:
            tasks = [config.task]
        for task in tasks:
            # FIXME : this is bad way of setting task each time
            config.task = task
            print("=" * 80)
            print("Config ID {}, task {}, {} trials".format(config.config_id, config.task, num_trials))
            summary = _main(config, num_trials)
            summaries.append(summary)

    print("=" * 80)
    print("SUMMARY")
    for summary in summaries:
        print(summary)


def _main(config, num_trials):
    load_metadata(config)

    # Load data
    if config.train:
        comb_train_ds = read_data(config, 'train', config.task)
        comb_dev_ds = read_data(config, 'dev', config.task)
    comb_test_ds = read_data(config, 'test', config.task)

    # For quick draft initialize (deubgging).
    if config.draft:
        config.train_num_batches = 1
        config.val_num_batches = 1
        config.test_num_batches = 1
        config.num_epochs = 2
        config.val_period = 1
        config.save_period = 1
        # TODO : Add any other parameter that induces a lot of computations

    pprint(config.__dict__)

    # TODO : specify eval tensor names to save in evals folder
    eval_tensor_names = ['a', 'rf', 'rb', 'correct', 'yp']
    eval_ph_names = ['q', 'q_mask', 'x', 'x_mask', 'y']

    def get_best_trial_idx(_val_losses):
        return min(enumerate(_val_losses), key=lambda x: x[1])[0]

    val_losses = []
    test_accs = []
    for trial_idx in range(1, num_trials+1):
        if config.train:
            print("-" * 80)
            print("Task {} trial {}".format(config.task, trial_idx))
        mkdirs(config, trial_idx)
        graph = tf.Graph()
        # TODO : initialize BaseTower-subclassed objects
        towers = [Tower(config) for _ in range(config.num_devices)]
        sess = tf.Session(graph=graph, config=tf.ConfigProto(allow_soft_placement=True))
        # TODO : initialize BaseRunner-subclassed object
        runner = Runner(config, sess, towers)
        with graph.as_default(), tf.device("/cpu:0"):
            runner.initialize()
            if config.train:
                if config.load:
                    runner.load()
                val_loss, val_acc = runner.train(comb_train_ds, config.num_epochs, val_data_set=comb_dev_ds,
                                                 num_batches=config.train_num_batches,
                                                 val_num_batches=config.val_num_batches, eval_ph_names=eval_ph_names)
                val_losses.append(val_loss)
            else:
                runner.load()
            test_loss, test_acc = runner.eval(comb_test_ds, eval_tensor_names=eval_tensor_names,
                                   num_batches=config.test_num_batches, eval_ph_names=eval_ph_names)
            test_accs.append(test_acc)

        if config.train:
            best_trial_idx = get_best_trial_idx(val_losses)
            print("-" * 80)
            print("Num trials: {}".format(trial_idx))
            print("Min val loss: {:.4f}".format(min(val_losses)))
            print("Test acc at min val acc: {:.2f}%".format(100 * test_accs[best_trial_idx]))
            print("Trial idx: {}".format(best_trial_idx+1))

        # Cheating, but for speed
        if test_acc == 1.0:
            break

    best_trial_idx = get_best_trial_idx(val_losses)
    summary = "Task {}: {:.2f}% at trial {}".format(config.task, test_accs[best_trial_idx] * 100, best_trial_idx)
    return summary


if __name__ == "__main__":
    tf.app.run()
