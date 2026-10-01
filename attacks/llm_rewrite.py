"""LLM rewriting attack.

The attacker model is asked to rewrite the code with the same behaviour. Decoding is greedy with
at most 2048 new tokens, 8 files per batch. The code is taken from the last closed fenced block
of the answer whose language tag matches; an answer without a code block yields an empty file.

Model: Qwen/Qwen3-Coder-30B-A3B-Instruct (bf16, about 57 GB of GPU memory). Override with the
environment variable ``ATK_LLM_MODEL``; ``ATK_LLM_DEVICE`` selects the device (default "cuda").
Requires ``torch`` and ``transformers``.
"""
import os
import re
import textwrap

_LM = {}
_LANG = {"python": ("Python", "python"), "java": ("Java", "java"), "cpp": ("C++", "cpp")}
_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+#-]*)[ \t]*\r?\n(.*?)```", flags=re.DOTALL)
_FENCE_ALIAS = {"python": {"python", "py", "python3"}, "java": {"java"}, "cpp": {"cpp", "c++", "cc", "cxx"}}
ATK_LLM_MODEL = os.environ.get("ATK_LLM_MODEL", "Qwen/Qwen3-Coder-30B-A3B-Instruct")

PROMPT = ("Rewrite the following {name} code so that it behaves exactly the same but is written differently: "
          "rename variables, restructure statements, and use equivalent but different expressions where possible. "
          "Keep the same function names and signatures. Output only the code in one ```{fence} block.\n\n"
          "```{fence}\n{code}\n```")


def extract_fenced_code(text, language):
    """Last closed fenced block with a matching language tag (else the last closed block);
    an unterminated block takes everything after its opening fence."""
    if not text:
        return ""
    blocks = _FENCE_RE.findall(text)
    if blocks:
        want = _FENCE_ALIAS[language]
        matched = [body for tag, body in blocks if tag.lower() in want]
        body = matched[-1] if matched else blocks[-1][1]
        return textwrap.dedent(body).strip("\n")
    opens = list(re.finditer(r"```[ \t]*[A-Za-z0-9_+#-]*[ \t]*\r?\n", text))
    if opens:
        return textwrap.dedent(text[opens[-1].end():]).strip("\n")
    return ""


def atk_llm(codes, language="python"):
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    if not _LM:
        _LM["tk"] = AutoTokenizer.from_pretrained(ATK_LLM_MODEL)
        _LM["tk"].padding_side = "left"
        _LM["dev"] = os.environ.get("ATK_LLM_DEVICE", "cuda")
        _LM["m"] = AutoModelForCausalLM.from_pretrained(ATK_LLM_MODEL, dtype=torch.bfloat16, device_map=_LM["dev"]).eval()
    tk, m = _LM["tk"], _LM["m"]
    name, fence = _LANG[language]
    outs = []
    for i in range(0, len(codes), 8):
        batch = [tk.apply_chat_template([{"role": "user", "content": PROMPT.format(name=name, fence=fence, code=c)}],
                                        tokenize=False, add_generation_prompt=True) for c in codes[i:i + 8]]
        enc = tk(batch, return_tensors="pt", padding=True).to(_LM["dev"])
        with torch.no_grad():
            g = m.generate(**enc, max_new_tokens=2048, do_sample=False, pad_token_id=tk.pad_token_id)
        for j in range(g.shape[0]):
            txt = tk.decode(g[j, enc.input_ids.shape[1]:], skip_special_tokens=True)
            outs.append(extract_fenced_code(txt, language))
    return outs
