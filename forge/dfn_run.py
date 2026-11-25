import sys,types,os; sys.path[:0]=[p for p in os.environ.get('DFN_PYPATH','./dfn:./tf').split(':') if p]
import warnings; warnings.filterwarnings('ignore')
for n in ['torchaudio.backend','torchaudio.backend.common']:
    sys.modules[n]=types.ModuleType(n)
sys.modules['torchaudio.backend.common'].AudioMetaData=object
import numpy as np
np.long=np.int_; np.ulong=np.uint
import torch
from scipy.io import wavfile
from df.enhance import enhance, init_df
inp,outp=sys.argv[1],sys.argv[2]
import glob; _c=glob.glob(os.environ.get("DFN3_DIR","dfn3_model")+"/**/config.ini",recursive=True)
model,df_state,_=init_df(model_base_dir=os.path.dirname(_c[0])) if _c else init_df()   # no local model dir -> auto-download DeepFilterNet3
sr,x=wavfile.read(inp); x=x.astype(np.float32)/32768
assert sr==df_state.sr(),(sr,df_state.sr())
y=enhance(model,df_state,torch.from_numpy(x)[None,:])
y=y.squeeze().numpy()
wavfile.write(outp,sr,(np.clip(y,-1,1)*32767).astype(np.int16)); print('ok')
