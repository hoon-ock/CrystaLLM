"""
Sample from a trained model using Beam Search
"""
import sys

sys.path.append(".")
import os
from contextlib import nullcontext
import torch
from model import GPTConfig, GPT
from mcts_sampler import MCTSSampler, MCTSEvaluator

from lib import get_cif_tokenizer, ZMQScorer

THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# -----------------------------------------------------------------------------
out_dir = 'out' # ignored if init_from is not 'resume'
temperature = 1.0 # 1.0 = no change, < 1.0 = less random, > 1.0 = more random, in predictions
start = "\n" # or "<|endoftext|>" or etc. Can also specify a file, use as: "FILE:prompt.txt"
seed = 1337
device = 'cuda' # examples: 'cpu', 'cuda', 'cuda:0', 'cuda:1', etc.
dtype = 'bfloat16' # 'float32' or 'bfloat16' or 'float16'
compile = False # use PyTorch 2.0 to compile the model to be faster
symmetrized = True # whether the CIF files are symmetrized
includes_props = True # whether CIF files contain an atomic properties section
tree_width = 10  # the tree width
max_depth = 1000  # the maximum depth of the tree
cpuct = 5  # the c_puct constant
num_simulations = 200  # the number of simulations to perform during search
min_len = 90  # the minimum length the sequence should have
bond_length_acceptability_cutoff = 1.0
reward_k = 2.0
mcts_out_dir = 'mcts'
use_zmq_scorer = True  # must be True, for now
zmq_port = 5555
exec(open(os.path.join(THIS_DIR, 'configurator.py')).read()) # overrides from command line or config file
# -----------------------------------------------------------------------------

torch.manual_seed(seed)
torch.cuda.manual_seed(seed)
torch.backends.cuda.matmul.allow_tf32 = True # allow tf32 on matmul
torch.backends.cudnn.allow_tf32 = True # allow tf32 on cudnn
device_type = 'cuda' if 'cuda' in device else 'cpu' # for later use in torch.autocast
ptdtype = {'float32': torch.float32, 'bfloat16': torch.bfloat16, 'float16': torch.float16}[dtype]
ctx = nullcontext() if device_type == 'cpu' else torch.amp.autocast(device_type=device_type, dtype=ptdtype)

tokenizer = get_cif_tokenizer(symmetrized=symmetrized, includes_props=includes_props)
encode = tokenizer.encode
decode = tokenizer.decode

# init from a model saved in a specific directory
ckpt_path = os.path.join(out_dir, 'ckpt.pt')
checkpoint = torch.load(ckpt_path, map_location=device)
gptconf = GPTConfig(**checkpoint['model_args'])
model = GPT(gptconf)
state_dict = checkpoint['model']
unwanted_prefix = '_orig_mod.'
for k,v in list(state_dict.items()):
    if k.startswith(unwanted_prefix):
        state_dict[k[len(unwanted_prefix):]] = state_dict.pop(k)
model.load_state_dict(state_dict)

model.eval()
model.to(device)
if compile:
    model = torch.compile(model) # requires PyTorch 2.0 (optional)

# encode the beginning of the prompt
if start.startswith('FILE:'):
    with open(start[5:], 'r', encoding='utf-8') as f:
        start = f.read()

scorer = None
if use_zmq_scorer:
    scorer = ZMQScorer(port=zmq_port)

evaluator = MCTSEvaluator(
    scorer=scorer,
    tokenizer=tokenizer,
    bond_length_acceptability_cutoff=bond_length_acceptability_cutoff,
    reward_k=reward_k,
    out_dir=mcts_out_dir,
)

sampler = MCTSSampler(
    model=model,
    config=gptconf,
    width=tree_width,
    max_depth=max_depth,
    eval_function=evaluator,
    cpuct=cpuct,
    tokenizer=tokenizer,
    temperature=temperature,
    device=device,
)

sampler.search(start, num_simulations)
